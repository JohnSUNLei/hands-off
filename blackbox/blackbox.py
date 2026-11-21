#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blackbox — a flight recorder for your AI agent, with a one-command undo for any step.

One file, Python standard library only, no model calls. `init` copies this file into
`<project>/.blackbox/blackbox.py`, takes a first snapshot of the project, and adds Claude Code hooks.

What it records, step by step:
  - what you asked (UserPromptSubmit) and what the AI said when it stopped (Stop)
  - every command it ran (Bash), with its description and the tail of its output
  - every file it created, edited or deleted — including files changed by shell commands —
    with the content before and after (content-addressed, deduplicated, compressed)
  - every web page it read and search it ran; every file it looked at; MCP tools it used
  - files that changed *outside* the agent between steps (you, your editor, another program)

What you get:
  - `report`  → `.blackbox/timeline.html`: one page, plain language, newest session first
  - `list` / `show N` in the terminal
  - `undo N`  → puts every file step N touched back the way it was (refuses if a later step changed
               the same file, unless --force). The undo is itself a step, so it can be undone.

The AI may read the record but cannot edit or delete it: a black box the pilot can erase is not one.
Same input, same record. No network, no daemon.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows: no advisory lock, still works single-threaded
    fcntl = None

VERSION = "0.1.0"
HOME_DIR = ".blackbox"
ENGINE_FILE = "blackbox.py"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
STEPS_FILE = "steps.jsonl"
REPORT_FILE = "timeline.html"
HOOK_MARK = HOME_DIR + "/" + ENGINE_FILE
HOOK_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.blackbox/blackbox.py" hook'
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop")
HOOK_TIMEOUT = 60
DIFF_STEPS = 400           # the page shows diffs for this many most recent steps (keeps it fast and small)
DEFAULT_CONFIG = {
    "lang": "en",
    "max_file_mb": 2,          # bigger files are noted but not kept (so they cannot be undone)
    "skip_dirs": [],           # extra folders never snapshotted (added to the built-in list)
    "output_chars": 1200,      # how much of a command's output to keep for the timeline
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".next",
             "dist", "build", "target", ".cache", "coverage", ".tox", ".gradle", ".idea", ".turbo", ".parcel-cache",
             ".svelte-kit", "Pods", "DerivedData", ".terraform", ".tripwire", ".brake", HOME_DIR}
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
MCP_WRITES = re.compile(r"(?:^|_)(?:write|edit|create|move|delete|remove|rename|copy|patch|apply)(?:$|_)", re.I)
RISKY_CMD = re.compile(r"(?:^|[;&|(`]\s*|\bsudo\s+)(?:curl|wget|scp|ssh|rsync|nc|git\s+push|npm\s+publish|twine\s+upload|"
                       r"docker\s+push|gh\s+(?:pr|issue|release)\s+create|rm\s+-\w*[rf]|git\s+reset\s+--hard|"
                       r"git\s+clean\s+-\w*f|git\s+checkout\s+--\s|dd\s|mkfs|chmod\s+-R|chown\s+-R)\b")
READ_ONLY_CLI = re.compile(r"^\s*(?:python3?\s+)?['\"]?\S*\.blackbox/blackbox\.py['\"]?\s+(?:list|show|report|status|version|help|undo)\b"
                           r"[^;&|`$<>]*$|^\s*(?:cat|less|head|tail|ls|grep|rg|wc|open)\s[^;&|`$<>]*$")

# ---------------------------------------------------------------------------
# Words. Every user-visible sentence lives here, once.
# ---------------------------------------------------------------------------
MSG = {
    "en": {
        "prompt": "You asked: “{text}”",
        "said": "The AI stopped and said: “{text}”",
        "session": "Session started",
        "session_resume": "Session resumed",
        "bash": "Ran `{cmd}`",
        "bash_desc": "{desc} — `{cmd}`",
        "write_new": "Created {path}",
        "write": "Rewrote {path}",
        "edit": "Edited {path}",
        "fetch": "Read the web page {url}",
        "search": "Searched the web for “{q}”",
        "read": "Looked at {path}",
        "reads": "Looked at {n} files: {paths}",
        "grep": "Searched the code for “{q}”",
        "agent": "Handed a task to a helper agent: {desc}",
        "mcp": "Used {server} → {tool}",
        "tool": "Used {tool}",
        "outside": "Files changed outside the AI (you, your editor, or another program)",
        "undo": "Undid step {n}",
        "failed": "failed",
        "files": "{n} file(s): ",
        "added": "added", "modified": "changed", "deleted": "deleted", "big": "too big to keep",
        "risky": "check this",
        "self": "blackbox: the AI may read the flight record but not change or delete it. Ask the user to do this.",
        "no_step": "No step {n}.",
        "nothing": "Step {n} changed no files; nothing to undo.",
        "already": "Step {n} was already undone (by step {by}).",
        "conflict": "Not undone: these files changed again after step {n}:",
        "conflict_by": "  {path}  (changed later by step {by})",
        "conflict_tip": "Undo the later step(s) first, or run again with --force to overwrite them.",
        "plan": "Undo step {n} — {summary}:",
        "plan_restore": "  restore  {path}",
        "plan_delete": "  delete   {path}  (it did not exist before)",
        "plan_recreate": "  recreate {path}  (it was deleted)",
        "plan_skip": "  skip     {path}  (too big; it was not kept)",
        "confirm": "Go ahead? [y/N] ",
        "cancel": "Cancelled. Nothing changed.",
        "done": "Done. Step {n} is undone (recorded as step {m}; `undo {m}` brings it back).",
        "report": "Timeline written: {path}",
        "installed": "blackbox installed in {root}\n  engine:   {engine}\n  hooks:    {settings} ({events})\n"
                     "  snapshot: {files} files kept as the starting point\nStart a new Claude Code session in this project "
                     "to load the hooks. Timeline: python3 .blackbox/blackbox.py report",
        "already_installed": "blackbox hooks were already in {settings}; engine refreshed.",
        "removed": "blackbox removed from {root}: {n} hook entr(ies) deleted{data}.",
        "removed_data": ", {dir} deleted", "kept_data": ", {dir} kept",
        "bad_settings": "{path} is not valid JSON ({err}). Fix it first; blackbox will not overwrite it.",
        "empty": "Nothing recorded yet.",
        # timeline page
        "h_title": "What your AI did", "h_project": "Project", "h_steps": "steps", "h_cmds": "commands",
        "h_files": "files changed", "h_pages": "web pages read", "h_deleted": "files deleted", "h_failed": "failed steps",
        "h_risky": "worth a look", "h_session": "Session", "h_dur": "ran for", "h_show_reads": "show file reads",
        "h_undo": "Undo", "h_copy": "Copy the undo command", "h_copied": "Copied — paste it in your terminal",
        "h_diff": "show changes", "h_out": "output", "h_undone": "undone", "h_agent": "helper agent",
        "h_generated": "Generated {t} by blackbox {v}. Everything stays on this computer.",
        "h_binary": "(binary or too large to show)",
        "dur_h": "{h}h {m}m", "dur_m": "{m} min",
    },
    "zh": {
        "prompt": "你说：「{text}」",
        "said": "AI 停下来，说：「{text}」",
        "session": "会话开始",
        "session_resume": "会话继续",
        "bash": "运行了 `{cmd}`",
        "bash_desc": "{desc} —— `{cmd}`",
        "write_new": "新建了 {path}",
        "write": "重写了 {path}",
        "edit": "修改了 {path}",
        "fetch": "读了网页 {url}",
        "search": "上网搜了「{q}」",
        "read": "看了 {path}",
        "reads": "看了 {n} 个文件：{paths}",
        "grep": "在代码里找「{q}」",
        "agent": "把一个子任务交给了助手：{desc}",
        "mcp": "用了 {server} → {tool}",
        "tool": "用了 {tool}",
        "outside": "在 AI 之外被改动的文件（你本人、编辑器或别的程序）",
        "undo": "撤销了第 {n} 步",
        "failed": "失败",
        "files": "{n} 个文件：",
        "added": "新增", "modified": "改动", "deleted": "删除", "big": "太大没保存",
        "risky": "值得看一眼",
        "self": "blackbox：AI 可以读黑匣子记录，但不能改、不能删。请让用户自己操作。",
        "no_step": "没有第 {n} 步。",
        "nothing": "第 {n} 步没有改动文件，没什么可撤销的。",
        "already": "第 {n} 步已经撤销过了（第 {by} 步）。",
        "conflict": "没有撤销：下面这些文件在第 {n} 步之后又被改过：",
        "conflict_by": "  {path}  （后来被第 {by} 步改过）",
        "conflict_tip": "先撤销后面那几步，或者加 --force 强行覆盖。",
        "plan": "撤销第 {n} 步 —— {summary}：",
        "plan_restore": "  恢复  {path}",
        "plan_delete": "  删除  {path}（这一步之前它不存在）",
        "plan_recreate": "  找回  {path}（它被删掉了）",
        "plan_skip": "  跳过  {path}（太大，当时没保存）",
        "confirm": "确定吗？[y/N] ",
        "cancel": "已取消，什么都没改。",
        "done": "好了，第 {n} 步已撤销（记为第 {m} 步；`undo {m}` 可以再改回来）。",
        "report": "时间线已生成：{path}",
        "installed": "blackbox 已装进 {root}\n  引擎：{engine}\n  hooks：{settings}（{events}）\n  快照：{files} 个文件作为起点\n"
                     "在这个项目里新开一个 Claude Code 会话，hooks 就生效了。看时间线：python3 .blackbox/blackbox.py report",
        "already_installed": "{settings} 里已经有 blackbox 的 hooks；引擎已刷新。",
        "removed": "已从 {root} 卸载 blackbox：删掉 {n} 条 hook{data}。",
        "removed_data": "，已删除 {dir}", "kept_data": "，保留了 {dir}",
        "bad_settings": "{path} 不是合法的 JSON（{err}）。请先修好它；blackbox 不会覆盖它。",
        "empty": "还没有任何记录。",
        "h_title": "你的 AI 都干了什么", "h_project": "项目", "h_steps": "步", "h_cmds": "条命令",
        "h_files": "个文件有改动", "h_pages": "个网页", "h_deleted": "个文件被删", "h_failed": "步失败",
        "h_risky": "值得看一眼", "h_session": "会话", "h_dur": "持续", "h_show_reads": "显示看文件的步骤",
        "h_undo": "撤销", "h_copy": "复制撤销命令", "h_copied": "已复制，粘贴到终端运行",
        "h_diff": "看改了什么", "h_out": "输出", "h_undone": "已撤销", "h_agent": "助手",
        "h_generated": "{t} 由 blackbox {v} 生成。所有内容都留在这台电脑上。",
        "h_binary": "（二进制文件或太大，不显示）",
        "dur_h": "{h} 小时 {m} 分", "dur_m": "{m} 分钟",
    },
}


def t(lang: str, key: str, **kw) -> str:
    return MSG.get(lang, MSG["en"])[key].format(**kw)


def _now() -> str:
    return os.environ.get("BLACKBOX_NOW") or _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _short(text, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Storage: content-addressed blobs + a manifest of the project as last seen
# ---------------------------------------------------------------------------
class Box:
    def __init__(self, home: Path):
        self.home = home
        self.root = home.parent
        self.cfg = self._config()
        self.lang = self.cfg.get("lang", "en")
        self._lockf = None

    def _config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        p = self.home / CONFIG_FILE
        if p.is_file():
            user = json.loads(p.read_text(encoding="utf-8") or "{}")
            if isinstance(user, dict):
                cfg.update(user)
        return cfg

    # -- lock (hooks for parallel tool calls can overlap)
    def __enter__(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self._lockf = open(self.home / "lock", "w")
        if fcntl:
            fcntl.flock(self._lockf, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl and self._lockf:
            fcntl.flock(self._lockf, fcntl.LOCK_UN)
        if self._lockf:
            self._lockf.close()

    # -- blobs
    def put(self, data: bytes) -> str:
        sha = hashlib.sha1(data).hexdigest()
        p = self.home / "objects" / sha[:2] / sha[2:]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(zlib.compress(data, 6))
            os.replace(tmp, p)
        return sha

    def get(self, sha: str) -> bytes | None:
        if not sha or sha.startswith("big:"):
            return None
        try:
            return zlib.decompress((self.home / "objects" / sha[:2] / sha[2:]).read_bytes())
        except (OSError, zlib.error):
            return None

    # -- project files
    def skip(self) -> set:
        return SKIP_DIRS | set(self.cfg.get("skip_dirs") or [])

    def list_files(self) -> list:
        """Every file under the project except the skipped folders — ignored files too (.env matters)."""
        skip = self.skip()
        names = []
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in skip)
            for fn in sorted(fns):
                p = os.path.join(dp, fn)
                if os.path.isfile(p) and not os.path.islink(p):
                    names.append(os.path.relpath(p, self.root).replace("\\", "/"))
        return names

    def stat(self, rel: str):
        try:
            st = (self.root / rel).stat()
        except OSError:
            return None
        return [st.st_mtime_ns, st.st_size]

    def snap(self, rel: str) -> str | None:
        """Store the file's current content; returns its id (None if missing, `big:` if over the size limit)."""
        p = self.root / rel
        try:
            size = p.stat().st_size
            if size > float(self.cfg["max_file_mb"]) * 1_000_000:
                return f"big:{size}"
            return self.put(p.read_bytes())
        except OSError:
            return None

    # -- state
    def load_state(self) -> dict:
        try:
            return json.loads((self.home / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"files": {}, "next": 1}

    def save_state(self, st: dict) -> None:
        tmp = self.home / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.home / STATE_FILE)

    def refresh(self, st: dict, only: list | None = None) -> list:
        """Bring the manifest up to date with disk. Returns [file change] for whatever differs."""
        files = st["files"]
        names = self.list_files() if only is None else [r for r in only if r]
        changes = []
        seen = set()
        for rel in names:
            seen.add(rel)
            s = self.stat(rel)
            old = files.get(rel)
            if s is None:
                if old is not None:
                    changes.append(file_change(rel, old[2], None))
                    del files[rel]
                continue
            if old and old[0] == s[0] and old[1] == s[1]:
                continue
            sha = self.snap(rel)
            if sha is None:
                continue
            if not old or old[2] != sha:
                changes.append(file_change(rel, old[2] if old else None, sha))
            files[rel] = [s[0], s[1], sha]
        if only is None:
            for rel in [r for r in files if r not in seen and not (self.root / r).is_file()]:
                changes.append(file_change(rel, files[rel][2], None))
                del files[rel]
        return changes

    # -- steps
    def steps(self) -> list:
        out = []
        try:
            lines = (self.home / STEPS_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

    def append(self, st: dict, rec: dict) -> dict:
        rec = {"n": st.get("next", 1), "t": rec.pop("t", None) or _now(), **rec}
        st["next"] = rec["n"] + 1
        with open(self.home / STEPS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def pending_path(self, tool_use_id) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(tool_use_id or "none"))[:100]
        return self.home / "pending" / f"{safe}.json"


def file_change(rel: str, before, after) -> dict:
    change = "added" if before is None else "deleted" if after is None else "modified"
    return {"path": rel, "change": change, "before": before, "after": after}


def count_lines(box: Box, ch: dict) -> dict:
    a, b = box.get(ch.get("before")), box.get(ch.get("after"))
    try:
        la = a.decode("utf-8").splitlines() if a is not None else []
        lb = b.decode("utf-8").splitlines() if b is not None else []
    except UnicodeDecodeError:
        return ch
    plus = minus = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            minus += i2 - i1
        if op in ("replace", "insert"):
            plus += j2 - j1
    ch["plus"], ch["minus"] = plus, minus
    return ch


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------
def _rel(box: Box, path: str) -> str | None:
    if not path:
        return None
    p = os.path.normpath(os.path.join(str(box.root), os.path.expanduser(path)))
    rel = os.path.relpath(p, str(box.root)).replace("\\", "/")
    return None if rel.startswith("..") else rel


def touches_self(tool: str, ti: dict, box: Box) -> bool:
    if tool in FILE_TOOLS:
        rel = _rel(box, str(ti.get("file_path") or ti.get("notebook_path") or ""))
        return rel is not None and (rel == HOME_DIR or rel.startswith(HOME_DIR + "/"))
    if tool in SHELL_TOOLS:
        cmd = str(ti.get("command") or "")
        return (HOME_DIR in cmd or "blackbox.py" in cmd) and not READ_ONLY_CLI.match(cmd)
    return False


def _mcp(tool: str):
    parts = tool.split("__")
    return (parts[1], "__".join(parts[2:])) if len(parts) >= 3 and parts[0] == "mcp" else (None, None)


def scans_everything(tool: str) -> bool:
    if tool in SHELL_TOOLS:
        return True
    server, name = _mcp(tool)
    return server is not None and bool(MCP_WRITES.search(name))


def _output_tail(resp, n: int) -> str:
    if isinstance(resp, dict):
        parts = [str(resp.get(k) or "") for k in ("stdout", "stderr")]
        text = "\n".join(p for p in parts if p.strip())
    elif isinstance(resp, str):
        text = resp
    else:
        text = ""
    text = text.strip()
    return text if len(text) <= n else "…" + text[-n:]


def _pre_deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}, ensure_ascii=False)


def hook(data: dict, engine_path: Path | None = None) -> tuple:
    home = find_home(data.get("cwd"), engine_path)
    if home is None:
        return 0, "", ""
    box = Box(home)
    event = data.get("hook_event_name", "")
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    sid = str(data.get("session_id") or "")
    common = {"session": sid}
    if data.get("agent_id"):
        common["agent"] = str(data.get("agent_type") or data.get("agent_id"))

    if event == "PreToolUse" and touches_self(tool, ti, box):
        return 0, _pre_deny(t(box.lang, "self")), ""

    with box:
        st = box.load_state()
        if event == "SessionStart":
            outside = [count_lines(box, c) for c in box.refresh(st)]
            if outside:
                box.append(st, {**common, "kind": "outside", "files": outside})
            box.append(st, {**common, "kind": "session", "source": data.get("source", "startup")})
        elif event == "UserPromptSubmit":
            box.append(st, {**common, "kind": "prompt", "text": _short(data.get("prompt"), 500)})
        elif event == "PreToolUse":
            pend = {"t0": _now(), "tool": tool}
            if tool in FILE_TOOLS:
                rel = _rel(box, str(ti.get("file_path") or ti.get("notebook_path") or ""))
                if rel:
                    outside = [count_lines(box, c) for c in box.refresh(st, only=[rel])]
                    if outside:
                        box.append(st, {**common, "kind": "outside", "files": outside})
                    pend["files"] = {rel: (st["files"].get(rel) or [0, 0, None])[2]}
                else:
                    pend["outside_path"] = str(ti.get("file_path") or "")
            elif scans_everything(tool):
                outside = [count_lines(box, c) for c in box.refresh(st)]
                if outside:
                    box.append(st, {**common, "kind": "outside", "files": outside})
                pend["manifest"] = {k: v[2] for k, v in st["files"].items()}
            p = box.pending_path(data.get("tool_use_id"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(pend), encoding="utf-8")
        elif event in ("PostToolUse", "PostToolUseFailure"):
            p = box.pending_path(data.get("tool_use_id"))
            try:
                pend = json.loads(p.read_text(encoding="utf-8"))
                p.unlink()
            except (OSError, ValueError):
                pend = {}
            files = []
            if tool in FILE_TOOLS and "files" in pend:
                for rel, before in pend["files"].items():
                    box.refresh(st, only=[rel])
                    after = (st["files"].get(rel) or [0, 0, None])[2]
                    if after != before:
                        files.append(file_change(rel, before, after))
            elif "manifest" in pend:
                before = pend["manifest"]
                box.refresh(st)
                now = {k: v[2] for k, v in st["files"].items()}
                for rel in sorted(set(before) | set(now)):
                    if before.get(rel) != now.get(rel):
                        files.append(file_change(rel, before.get(rel), now.get(rel)))
                if tool in SHELL_TOOLS and re.search(r"blackbox\.py['\"]?\s+undo\b", str(ti.get("command") or "")):
                    files = []                                   # the undo recorded its own step
            rec = {**common, "kind": "tool", "tool": tool, "t0": pend.get("t0"),
                   "input": _keep_input(box, tool, ti), "files": [count_lines(box, c) for c in files]}
            if pend.get("outside_path"):
                rec["outside_path"] = pend["outside_path"]
            if event == "PostToolUseFailure":
                rec["status"] = "error"
                rec["error"] = _short(data.get("error"), 400)
            if tool in SHELL_TOOLS:
                out = _output_tail(data.get("tool_response"), int(box.cfg.get("output_chars", 1200)))
                if out:
                    rec["output"] = out
            box.append(st, rec)
        elif event == "Stop":
            msg = data.get("last_assistant_message")
            if msg:
                box.append(st, {**common, "kind": "said", "text": _short(msg, 600)})
        box.save_state(st)
        if event == "Stop":
            try:
                write_report(box)
            except OSError:
                pass
    return 0, "", ""


def _keep_input(box: Box, tool: str, ti: dict) -> dict:
    keep = ("command", "description", "file_path", "notebook_path", "url", "query", "pattern", "path", "prompt",
            "subagent_type")
    out = {k: _short(ti[k], 400) for k in keep if k in ti and ti[k] not in (None, "")}
    for k in ("file_path", "notebook_path", "path"):          # project paths are kept relative
        if k in out and _rel(box, out[k]) is not None:
            out[k] = _rel(box, out[k])
    if tool == "Agent" or tool == "Task":
        out.pop("prompt", None)
    return out


def find_home(start, engine_path: Path | None = None) -> Path | None:
    if engine_path is not None and engine_path.parent.name == HOME_DIR:
        return engine_path.parent
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / HOME_DIR / ENGINE_FILE).is_file():
            return d / HOME_DIR
    return None


# ---------------------------------------------------------------------------
# Plain-language summaries (shared by list, show and the HTML page)
# ---------------------------------------------------------------------------
def undone_by(steps: list) -> dict:
    """{step n: the undo step that undid it}. Undoing an undo (a redo) brings the original back."""
    by_n = {s.get("n"): s for s in steps}
    out: dict = {}
    for s in steps:
        if s.get("kind") != "undo":
            continue
        out[s["of"]] = s["n"]
        target = by_n.get(s["of"])
        if target is not None and target.get("kind") == "undo":
            out.pop(target.get("of"), None)
    return out


def summary(lang: str, s: dict, root: Path | None = None) -> str:
    k = s.get("kind")
    if k == "prompt":
        return t(lang, "prompt", text=_short(s.get("text"), 200))
    if k == "said":
        return t(lang, "said", text=_short(s.get("text"), 200))
    if k == "session":
        return t(lang, "session_resume" if s.get("source") in ("resume", "compact") else "session")
    if k == "outside":
        return t(lang, "outside")
    if k == "undo":
        return t(lang, "undo", n=s.get("of"))
    tool, ti = s.get("tool", ""), s.get("input") or {}
    path = _display_path(ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "", root)
    if tool in SHELL_TOOLS:
        cmd = _short(ti.get("command"), 120)
        text = t(lang, "bash_desc", desc=ti["description"], cmd=cmd) if ti.get("description") else t(lang, "bash", cmd=cmd)
    elif tool == "Write":
        new = any(f.get("change") == "added" for f in s.get("files") or [])
        text = t(lang, "write_new" if new else "write", path=path)
    elif tool in FILE_TOOLS:
        text = t(lang, "edit", path=path)
    elif tool == "WebFetch":
        text = t(lang, "fetch", url=_short(ti.get("url"), 120))
    elif tool == "WebSearch":
        text = t(lang, "search", q=_short(ti.get("query"), 100))
    elif tool == "Read":
        text = t(lang, "read", path=path)
    elif tool in ("Grep", "Glob"):
        text = t(lang, "grep", q=_short(ti.get("pattern"), 80))
    elif tool in ("Agent", "Task"):
        text = t(lang, "agent", desc=_short(ti.get("description") or ti.get("subagent_type") or "", 100))
    elif tool.startswith("mcp__"):
        server, name = _mcp(tool)
        text = t(lang, "mcp", server=server, tool=name)
    else:
        text = t(lang, "tool", tool=tool)
    if s.get("status") == "error":
        text += f" — {t(lang, 'failed')}"
    return text


def _display_path(path: str, root: Path | None) -> str:
    if not path:
        return ""
    if root is not None:
        p = os.path.normpath(os.path.join(str(root), os.path.expanduser(path)))
        rel = os.path.relpath(p, str(root)).replace("\\", "/")
        if not rel.startswith(".."):
            return rel
    return path


def is_risky(s: dict, root: Path | None = None) -> bool:
    if s.get("status") == "error":
        return True
    if any(f.get("change") == "deleted" for f in s.get("files") or []):
        return True
    if s.get("outside_path"):
        return True
    ti = s.get("input") or {}
    return s.get("tool") in SHELL_TOOLS and bool(RISKY_CMD.search(str(ti.get("command") or "")))


def files_line(lang: str, files: list) -> str:
    bits = []
    for f in files[:6]:
        mark = {"added": "+", "deleted": "−", "modified": "~"}[f["change"]]
        extra = f" (+{f['plus']} −{f['minus']})" if "plus" in f and f["change"] == "modified" else ""
        bits.append(f"{mark}{f['path']}{extra}")
    more = f" … +{len(files) - 6}" if len(files) > 6 else ""
    return t(lang, "files", n=len(files)) + ", ".join(bits) + more


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
def undo(box: Box, n: int, force: bool = False, assume_yes: bool = False, out=print, ask=input) -> int:
    lang = box.lang
    with box:
        st = box.load_state()
        steps = box.steps()
        step = next((s for s in steps if s.get("n") == n), None)
        if step is None:
            out(t(lang, "no_step", n=n))
            return 2
        files = step.get("files") or []
        if not files:
            out(t(lang, "nothing", n=n))
            return 1
        done = undone_by(steps)
        if n in done:
            out(t(lang, "already", n=n, by=done[n]))
            return 1
        box.refresh(st, only=[f["path"] for f in files])
        conflicts = []
        for f in files:
            cur = (st["files"].get(f["path"]) or [0, 0, None])[2]
            if cur != f.get("after"):
                later = [s["n"] for s in steps if s.get("n", 0) > n and any(g["path"] == f["path"] for g in s.get("files") or [])]
                conflicts.append((f["path"], ", ".join(map(str, later)) or "?"))
        if conflicts and not force:
            out(t(lang, "conflict", n=n))
            for path, by in conflicts:
                out(t(lang, "conflict_by", path=path, by=by))
            out(t(lang, "conflict_tip"))
            return 1
        out(t(lang, "plan", n=n, summary=summary(lang, step, box.root)))
        for f in files:
            if str(f.get("before") or "").startswith("big:"):
                key = "plan_skip"
            elif f.get("before") is None:
                key = "plan_delete"
            elif f.get("after") is None:
                key = "plan_recreate"
            else:
                key = "plan_restore"
            out(t(lang, key, path=f["path"]))
        if not assume_yes:
            try:
                if ask(t(lang, "confirm")).strip().lower() not in ("y", "yes", "是", "好"):
                    out(t(lang, "cancel"))
                    return 1
            except EOFError:
                out(t(lang, "cancel"))
                return 1
        changes = []
        for f in files:
            before = f.get("before")
            p = box.root / f["path"]
            if str(before or "").startswith("big:"):
                continue
            cur = (st["files"].get(f["path"]) or [0, 0, None])[2]
            if before is None:
                if p.exists():
                    p.unlink()
            else:
                data = box.get(before)
                if data is None:
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            box.refresh(st, only=[f["path"]])
            changes.append(count_lines(box, file_change(f["path"], cur, (st["files"].get(f["path"]) or [0, 0, None])[2])))
        rec = box.append(st, {"kind": "undo", "of": n, "session": "", "files": changes})
        box.save_state(st)
    out(t(lang, "done", n=n, m=rec["n"]))
    return 0


# ---------------------------------------------------------------------------
# Terminal views
# ---------------------------------------------------------------------------
def _hhmm(ts: str) -> str:
    return str(ts or "")[11:16]


def cmd_list(box: Box, limit: int = 40, out=print) -> int:
    steps = box.steps()
    if not steps:
        out(t(box.lang, "empty"))
        return 0
    done = undone_by(steps)
    for s in steps[-limit:]:
        mark = "!" if is_risky(s, box.root) else " "
        und = f"  [{t(box.lang, 'h_undone')}]" if s["n"] in done else ""
        out(f"{s['n']:>4} {_hhmm(s.get('t'))} {mark} {summary(box.lang, s, box.root)}{und}")
        if s.get("files"):
            out(f"           {files_line(box.lang, s['files'])}")
    return 0


def diff_text(box: Box, f: dict, limit: int = 400) -> str | None:
    a, b = box.get(f.get("before")), box.get(f.get("after"))
    try:
        la = a.decode("utf-8").splitlines() if a is not None else []
        lb = b.decode("utf-8").splitlines() if b is not None else []
    except UnicodeDecodeError:
        return None
    if (f.get("before") and a is None) or (f.get("after") and b is None):
        return None
    lines = list(difflib.unified_diff(la, lb, f"a/{f['path']}", f"b/{f['path']}", lineterm="", n=2))
    if len(lines) > limit:
        lines = lines[:limit] + [f"… ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def cmd_show(box: Box, n: int, out=print) -> int:
    s = next((x for x in box.steps() if x.get("n") == n), None)
    if s is None:
        out(t(box.lang, "no_step", n=n))
        return 2
    out(f"#{n}  {s.get('t')}  {summary(box.lang, s, box.root)}")
    if s.get("output"):
        out(f"--- {t(box.lang, 'h_out')}\n{s['output']}")
    for f in s.get("files") or []:
        d = diff_text(box, f)
        out(d if d is not None else f"{f['path']}: {t(box.lang, 'h_binary')}")
    return 0


# ---------------------------------------------------------------------------
# The timeline page
# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1f23;--dim:#6b7078;--line:#e4e4e0;--add:#1a7f37;--del:#cf222e;--mod:#9a6700;
--risk:#fff4e5;--riskb:#f0b45b;--undone:#eef0f3;--code:#f2f3f1;--accent:#0b62d6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#161b22;--fg:#e6edf3;--dim:#8b949e;--line:#2a3038;
--add:#3fb950;--del:#f85149;--mod:#d29922;--risk:#2b2111;--riskb:#9e6a03;--undone:#1c2128;--code:#0d1117;--accent:#58a6ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI","PingFang SC","Hiragino Sans GB",sans-serif}main{max-width:980px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:28px;margin:0 0 4px}.sub{color:var(--dim);margin:0 0 18px}.stats{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 22px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:112px}
.stat b{display:block;font-size:22px}.stat span{color:var(--dim);font-size:13px}.stat.warn{border-color:var(--riskb)}
.session{margin:26px 0 8px;font-weight:600;color:var(--dim)}.task{background:var(--card);border:1px solid var(--line);
border-radius:12px;margin:12px 0;overflow:hidden}.ask{padding:12px 16px;font-weight:600;border-bottom:1px solid var(--line)}
.step{display:grid;grid-template-columns:52px 44px 1fr auto;gap:8px;padding:9px 16px;border-bottom:1px solid var(--line);align-items:start}
.step:last-child{border-bottom:0}.n{color:var(--dim);font-variant-numeric:tabular-nums}.time{color:var(--dim);font-size:13px;padding-top:1px}
.what{min-width:0;overflow-wrap:anywhere}.what code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:13px}
.risky{background:var(--risk)}.risky .n::after{content:" ⚑";color:var(--riskb)}.undone{background:var(--undone);opacity:.75}
.undone .what{text-decoration:line-through}.files{margin-top:4px;display:flex;flex-wrap:wrap;gap:6px}
.f{font:12px ui-monospace,Menlo,monospace;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.f.added{color:var(--add)}.f.deleted{color:var(--del)}.f.modified{color:var(--mod)}.helper{font-size:12px;color:var(--dim);
border:1px solid var(--line);border-radius:6px;padding:0 5px;margin-right:6px}button{font:inherit;font-size:13px;cursor:pointer;
border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:7px;padding:3px 9px}button:hover{border-color:var(--accent)}
details{margin-top:6px}summary{cursor:pointer;color:var(--accent);font-size:13px}pre{background:var(--code);padding:10px;border-radius:8px;
overflow:auto;font:12px/1.45 ui-monospace,Menlo,monospace;max-height:420px;margin:6px 0 0}.ins{color:var(--add)}.rem{color:var(--del)}
.said{color:var(--dim);font-style:italic}.reads{display:none}body.showreads .reads{display:grid}.toolbar{margin:0 0 8px}
.foot{color:var(--dim);font-size:12px;margin-top:30px}@media (max-width:640px){.step{grid-template-columns:38px 1fr;}
.time{display:none}.step>div:last-child{grid-column:2}}
"""
JS = """
function cp(b,c){navigator.clipboard&&navigator.clipboard.writeText(c);b.textContent=b.dataset.ok;}
function reads(x){document.body.classList.toggle('showreads',x.checked);}
"""


def _esc(x) -> str:
    return html.escape(str(x), quote=True)


def _rich(text: str) -> str:
    """Escape, then show `code` spans."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", _esc(text))


def _diff_html(box: Box, f: dict, lang: str) -> str:
    d = diff_text(box, f, limit=300)
    if d is None:
        return f"<pre>{_esc(t(lang, 'h_binary'))}</pre>"
    rows = []
    for ln in d.splitlines():
        cls = "ins" if ln.startswith("+") and not ln.startswith("+++") else "rem" if ln.startswith("-") and not ln.startswith("---") else ""
        rows.append(f'<span class="{cls}">{_esc(ln)}</span>' if cls else _esc(ln))
    return "<pre>" + "\n".join(rows) + "</pre>"


def _duration(lang: str, a: str, b: str) -> str:
    try:
        d = _dt.datetime.fromisoformat(b) - _dt.datetime.fromisoformat(a)
    except (TypeError, ValueError):
        return ""
    m = int(d.total_seconds() // 60)
    if m >= 60:
        return t(lang, "dur_h", h=m // 60, m=f"{m % 60:02d}")
    return t(lang, "dur_m", m=m)


def render_html(box: Box) -> str:
    lang, root = box.lang, box.root
    steps = box.steps()
    done = undone_by(steps)
    with_diff = {s["n"] for s in steps[-DIFF_STEPS:]}
    tools = [s for s in steps if s.get("kind") == "tool"]
    changed = {f["path"] for s in steps if s.get("kind") in ("tool", "undo") for f in s.get("files") or []}
    stats = [
        (len([s for s in steps if s.get("kind") in ("tool", "undo")]), t(lang, "h_steps"), False),
        (len([s for s in tools if s.get("tool") in SHELL_TOOLS]), t(lang, "h_cmds"), False),
        (len(changed), t(lang, "h_files"), False),
        (len([s for s in tools if s.get("tool") == "WebFetch"]), t(lang, "h_pages"), False),
        (sum(1 for s in steps for f in s.get("files") or [] if f["change"] == "deleted"), t(lang, "h_deleted"), True),
        (len([s for s in tools if s.get("status") == "error"]), t(lang, "h_failed"), True),
        (len([s for s in tools if is_risky(s, root)]), t(lang, "h_risky"), True),
    ]
    out = [f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(t(lang, "h_title"))} · {_esc(root.name)}</title>'
           f"<style>{CSS}</style><script>{JS}</script></head><body><main>",
           f"<h1>{_esc(t(lang, 'h_title'))}</h1><p class=\"sub\">{_esc(t(lang, 'h_project'))}: <b>{_esc(root.name)}</b></p>",
           '<div class="stats">']
    for num, label, warn in stats:
        out.append(f'<div class="stat{" warn" if warn and num else ""}"><b>{num}</b><span>{_esc(label)}</span></div>')
    out.append("</div>")
    out.append(f'<div class="toolbar"><label><input type="checkbox" onchange="reads(this)"> {_esc(t(lang, "h_show_reads"))}</label></div>')
    # newest session first; inside a session, oldest step first
    sessions: list = []
    for s in steps:
        if s.get("kind") == "session" or not sessions:
            sessions.append([])
        sessions[-1].append(s)
    for group in reversed(sessions):
        first, last = group[0], group[-1]
        dur = _duration(lang, first.get("t"), last.get("t"))
        out.append(f'<div class="session">{_esc(t(lang, "h_session"))} · {_esc(str(first.get("t", ""))[:16].replace("T", " "))}'
                   f'{" · " + _esc(t(lang, "h_dur")) + " " + _esc(dur) if dur else ""}</div>')
        task_open = False
        for s in group:
            k = s.get("kind")
            if k == "session":
                continue
            if k == "prompt" or not task_open:
                if task_open:
                    out.append("</div>")
                out.append('<div class="task">')
                task_open = True
                if k == "prompt":
                    out.append(f'<div class="ask">{_esc(summary(lang, s, root))}</div>')
                    continue
            out.append(_step_html(box, s, done, lang, s.get("n") in with_diff))
        if task_open:
            out.append("</div>")
    out.append(f'<p class="foot">{_esc(t(lang, "h_generated", t=_now()[:16].replace("T", " "), v=VERSION))}</p></main></body></html>')
    return "\n".join(out)


def _step_html(box: Box, s: dict, done: dict, lang: str, diffs_ok: bool = True) -> str:
    k = s.get("kind")
    if k == "said":
        return (f'<div class="step"><div class="n"></div><div class="time">{_esc(_hhmm(s.get("t")))}</div>'
                f'<div class="what said">{_esc(summary(lang, s, box.root))}</div><div></div></div>')
    cls = ["step"]
    if s.get("tool") in ("Read", "Grep", "Glob") and not s.get("files"):
        cls.append("reads")
    if is_risky(s, box.root):
        cls.append("risky")
    if s["n"] in done:
        cls.append("undone")
    helper = f'<span class="helper">{_esc(t(lang, "h_agent"))}</span>' if s.get("agent") else ""
    body = [f'{helper}{_rich(summary(lang, s, box.root))}']
    files = s.get("files") or []
    if files:
        chips = []
        for f in files[:30]:
            pm = f" +{f['plus']} −{f['minus']}" if "plus" in f else ""
            if str(f.get("after") or f.get("before") or "").startswith("big:"):
                pm = f" · {t(lang, 'big')}"
            chips.append(f'<span class="f {f["change"]}">{_esc(t(lang, f["change"]))} {_esc(f["path"])}{_esc(pm)}</span>')
        if len(files) > 30:
            chips.append(f'<span class="f">+{len(files) - 30}</span>')
        body.append('<div class="files">' + "".join(chips) + "</div>")
        if diffs_ok:
            diffs = "".join(f"<div><b>{_esc(f['path'])}</b>{_diff_html(box, f, lang)}</div>" for f in files[:8])
            body.append(f"<details><summary>{_esc(t(lang, 'h_diff'))}</summary>{diffs}</details>")
    if s.get("output"):
        body.append(f"<details><summary>{_esc(t(lang, 'h_out'))}</summary><pre>{_esc(s['output'])}</pre></details>")
    action = ""
    if files and s["n"] not in done:
        cmd = f"python3 .blackbox/blackbox.py undo {s['n']}"
        action = (f'<button data-ok="{_esc(t(lang, "h_copied"))}" title="{_esc(t(lang, "h_copy"))}: {_esc(cmd)}" '
                  f'onclick="cp(this,{_esc(json.dumps(cmd))})">{_esc(t(lang, "h_undo"))} #{s["n"]}</button>')
    elif s["n"] in done:
        action = f'<span class="f">{_esc(t(lang, "h_undone"))}</span>'
    return (f'<div class="{" ".join(cls)}"><div class="n">#{s["n"]}</div><div class="time">{_esc(_hhmm(s.get("t")))}</div>'
            f'<div class="what">{"".join(body)}</div><div>{action}</div></div>')


def write_report(box: Box) -> Path:
    p = box.home / REPORT_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(render_html(box), encoding="utf-8")
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------------------
# Install / uninstall: touch only our own entries in .claude/settings*.json
# ---------------------------------------------------------------------------
def _load_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw) if raw.strip() else {}
    if not isinstance(doc, dict):
        raise ValueError("top level must be an object")
    return doc


def _is_ours(handler) -> bool:
    return isinstance(handler, dict) and HOOK_MARK in str(handler.get("command", ""))


def add_hooks(doc: dict) -> bool:
    hooks = doc.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" must be an object')
    changed = False
    for ev in HOOK_EVENTS:
        groups = hooks.setdefault(ev, [])
        if not isinstance(groups, list):
            raise ValueError(f'"hooks.{ev}" must be a list')
        if any(_is_ours(h) for g in groups if isinstance(g, dict) for h in g.get("hooks", [])):
            continue
        entry = {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": HOOK_TIMEOUT}]}
        if ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            entry = {"matcher": "*", **entry}
        groups.append(entry)
        changed = True
    return changed


def remove_hooks(doc: dict) -> int:
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for ev in list(hooks):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        keep = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                mine = [h for h in g["hooks"] if _is_ours(h)]
                if mine:
                    removed += len(mine)
                    g = {**g, "hooks": [h for h in g["hooks"] if not _is_ours(h)]}
                    if not g["hooks"]:
                        continue
            keep.append(g)
        if keep:
            hooks[ev] = keep
        else:
            del hooks[ev]
    if removed and not hooks:
        del doc["hooks"]
    return removed


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(root: Path, lang: str = "en", local: bool = False) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    home.mkdir(parents=True, exist_ok=True)
    src, dst = Path(__file__).resolve(), home / ENGINE_FILE
    if src != dst.resolve():
        shutil.copy2(src, dst)
    if not (home / CONFIG_FILE).is_file():
        _write_json(home / CONFIG_FILE, {**DEFAULT_CONFIG, "lang": lang})
    # the record is personal and can be large: keep everything but the engine and config out of git
    (home / ".gitignore").write_text("*\n!.gitignore\n!blackbox.py\n!config.json\n", encoding="utf-8")
    settings = root / ".claude" / ("settings.local.json" if local else "settings.json")
    try:
        doc = _load_settings(settings)
        changed = add_hooks(doc)
    except ValueError as e:
        raise SystemExit(t(lang, "bad_settings", path=settings, err=e))
    if changed:
        _write_json(settings, doc)          # before the snapshot, so installing is not an "outside change"
    box = Box(home)
    with box:
        st = box.load_state()
        box.refresh(st)
        box.save_state(st)
        nfiles = len(st["files"])
    if changed:
        return t(box.lang, "installed", root=root, engine=dst, settings=settings, events=", ".join(HOOK_EVENTS), files=nfiles)
    return t(box.lang, "already_installed", settings=settings)


def uninstall(root: Path, keep_data: bool = False) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    lang = Box(home).lang if (home / CONFIG_FILE).is_file() else "en"
    n = 0
    for name in ("settings.json", "settings.local.json"):
        p = root / ".claude" / name
        try:
            doc = _load_settings(p)
        except ValueError as e:
            raise SystemExit(t(lang, "bad_settings", path=p, err=e))
        k = remove_hooks(doc)
        if k:
            _write_json(p, doc)
            n += k
    data = ""
    if home.is_dir():
        if keep_data:
            data = t(lang, "kept_data", dir=home)
        else:
            shutil.rmtree(home)
            data = t(lang, "removed_data", dir=home)
    return t(lang, "removed", root=root, n=n, data=data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main_hook() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        code, out, err = hook(data, Path(__file__).resolve())
    except Exception as e:  # noqa: BLE001 — say it loudly, but never block the user's work
        print(f"blackbox hook error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


USAGE = """usage: blackbox.py <command>
  init [DIR] [--lang en|zh] [--local]   install into a project (default: current dir)
  uninstall [DIR] [--keep-data]         remove our hooks (and .blackbox/, unless --keep-data)
  report [--open]                       write .blackbox/timeline.html (and open it)
  list [N]                              the last N steps in the terminal
  show N                                one step: output and full diff
  undo N [--force] [--yes]              put back every file step N changed
  hook                                  (called by Claude Code)
  version"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    rest = argv[1:]
    flags = {a for a in rest if a.startswith("--") and a not in ("--lang",)}
    args = [a for a in rest if not a.startswith("--")]
    if cmd == "hook":
        return _main_hook()
    if cmd in ("version", "--version", "-V"):
        print(VERSION)
        return 0
    if cmd == "init":
        lang = rest[rest.index("--lang") + 1] if "--lang" in rest and rest.index("--lang") + 1 < len(rest) else "en"
        if lang not in MSG:
            print(USAGE, file=sys.stderr)
            return 2
        args = [a for a in args if a != lang]
        print(install(Path(args[0] if args else "."), lang=lang, local="--local" in flags))
        return 0
    if cmd == "uninstall":
        print(uninstall(Path(args[0] if args else "."), keep_data="--keep-data" in flags))
        return 0
    if cmd in ("report", "list", "show", "undo"):
        home = find_home(os.getcwd(), Path(__file__).resolve())
        if home is None:
            print("blackbox is not installed here (no .blackbox/ found). Run: python3 blackbox.py init", file=sys.stderr)
            return 2
        box = Box(home)
        if cmd == "report":
            p = write_report(box)
            print(t(box.lang, "report", path=p))
            if "--open" in flags:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.run([opener, str(p)], check=False)
            return 0
        if cmd == "list":
            return cmd_list(box, int(args[0]) if args and args[0].isdigit() else 40)
        if not args or not args[0].lstrip("#").isdigit():
            print(USAGE, file=sys.stderr)
            return 2
        n = int(args[0].lstrip("#"))
        if cmd == "show":
            return cmd_show(box, n)
        return undo(box, n, force="--force" in flags, assume_yes="--yes" in flags or not sys.stdin.isatty())
    print(USAGE, file=sys.stderr if cmd != "help" else sys.stdout)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
