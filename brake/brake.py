#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brake — spend limits for your AI agent: it stops the task, on the spot, and says why.

One file, Python standard library only, no model calls. `init` copies this file into
`<project>/.brake/brake.py` and adds Claude Code hooks.

Per task (everything since your last message) and, optionally, per session, it limits:
  - estimated cost in dollars   (tokens from the session transcript × a local price table)
  - tokens                      (new tokens: fresh input + cache writes + output)
  - wall-clock minutes
  - the same tool call repeated (a loop: N identical calls among the last W)

Before every tool call (PreToolUse) it adds up the task so far. Over a limit → the tool call is denied and
the agent is told to stop (`continue: false`), with the reason shown to you. At 80% it warns you and tells
the agent to wrap up. Your next message starts a new task with a fresh budget — the stop hands control back
to you, it doesn't lock you out. A session limit, if set, holds until you raise it.

Dollar figures are estimates at API list prices (a Pro/Max subscription is not billed this way; read them as
"what this would cost on the API"). The transcript format is internal to Claude Code: if brake cannot read
usage from it, it says so instead of silently allowing everything.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

VERSION = "0.1.0"
HOME_DIR = ".brake"
ENGINE_FILE = "brake.py"
CONFIG_FILE = "config.json"
STATE_DIR = "state"
HOOK_MARK = HOME_DIR + "/" + ENGINE_FILE
HOOK_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.brake/brake.py" hook'
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse")
HOOK_TIMEOUT = 20

# USD per million tokens: input, output, cache write 5 min, cache write 1 h, cache read.
# Anthropic first-party list prices as of 2026-11 (fast mode bills 2×). Override in .brake/config.json → "prices".
PRICES = {
    "claude-fable-5-2": (8.00, 40.00, 10.00, 16.00, 0.20),
    "claude-mythos-5-2": (8.00, 40.00, 10.00, 16.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 12.50, 20.00, 1.00),
    "claude-mythos-5": (10.00, 50.00, 12.50, 20.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 8.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 4.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 6.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.20, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 2.00, 0.10),
}
UNKNOWN_MODEL = "claude-fable-5-1"      # price unknown models at the top of the range: err on the side of stopping
DEFAULT_CONFIG = {
    "lang": "en",
    "task": {"usd": 10.0, "tokens": None, "minutes": 120, "repeat": 6},
    "session": {"usd": None, "tokens": None, "minutes": None},
    "warn_at": 0.8,          # warn (and tell the agent to wrap up) at this share of any limit
    "loop_window": 12,       # repeat = this many identical calls among the last loop_window calls
    "loop_exempt": ["TodoWrite", "TaskOutput", "BashOutput"],
    "prices": {},            # {"model-id": [in, out, write5m, write1h, read]} — adds to / overrides the table
}

# ---------------------------------------------------------------------------
# Words. Every user-visible sentence lives here, once.
# ---------------------------------------------------------------------------
MSG = {
    "en": {
        "stop": "brake: stopped this task — {why}.",
        "r_usd": "estimated cost {used} reached the {limit} {scope} limit",
        "r_tokens": "{used:,} new tokens reached the {limit:,} {scope} limit",
        "r_minutes": "it has run {used} min, past the {limit}-minute {scope} limit",
        "r_repeat": "the same call was made {n} times in the last {w} calls: {what}",
        "task": "task", "session": "session",
        "line": "This task: ~{usd} · {tok:,} new tokens · {mins} min · {calls} tool calls · since “{prompt}”",
        "line_s": "This session: ~{usd} · {tok:,} new tokens · {mins} min",
        "how": "To continue, send a new message — a new task gets a fresh budget. To change the limits, edit .brake/config.json.",
        "to_ai": "Do not try to get around this. Stop, and tell the user what you were doing, what is done and what is left.",
        "warn": "brake: {pct}% of this task's {what} used ({used}).",
        "warn_ai": "[brake] {pct}% of this task's {what} is used. Wrap up: finish the current step, then tell the "
                   "user what is done and what is left.",
        "session_block": "brake: this session reached its {why}. Raise \"session\" in .brake/config.json or start a new session.",
        "no_usage": "brake: can't read token usage from the session transcript ({err}). Dollar and token limits are OFF for "
                    "this session; time and loop limits still apply.",
        "self": "brake: the AI may not change or remove its own limits. Ask the user to do it.",
        "status_none": "No sessions recorded yet.",
        "status_head": "Recent sessions (newest first):",
        "status_row": "- {sid}  task: ~{tusd} · {ttok:,} tok · {tmin} min   session: ~{susd} · {smin} min{tripped}",
        "tripped": "   ⛔ {why}",
        "limits": "Limits — task: {task}   session: {session}",
        "off": "off",
        "installed": "brake installed in {root}\n  engine:  {engine}\n  hooks:   {settings} ({events})\n  limits:  {config}\n"
                     "    task: {task}   session: {session}\nStart a new Claude Code session in this project to load the hooks.",
        "already": "brake hooks were already in {settings}; engine refreshed.",
        "removed": "brake removed from {root}: {n} hook entr(ies) deleted{data}.",
        "removed_data": ", {dir} deleted", "kept_data": ", {dir} kept",
        "bad_settings": "{path} is not valid JSON ({err}). Fix it first; brake will not overwrite it.",
    },
    "zh": {
        "stop": "brake：已叫停这个任务——{why}。",
        "r_usd": "估算花费 {used}，到了{scope}上限 {limit}",
        "r_tokens": "新 token {used:,} 个，到了{scope}上限 {limit:,}",
        "r_minutes": "已经跑了 {used} 分钟，超过{scope}上限 {limit} 分钟",
        "r_repeat": "最近 {w} 次调用里，同一个调用出现了 {n} 次：{what}",
        "task": "单个任务", "session": "整个会话",
        "line": "本任务：约 {usd} · 新 token {tok:,} · {mins} 分钟 · {calls} 次工具调用 · 起点「{prompt}」",
        "line_s": "本会话：约 {usd} · 新 token {tok:,} · {mins} 分钟",
        "how": "要继续，发一条新消息——新任务有新的额度。要改上限，编辑 .brake/config.json。",
        "to_ai": "不要设法绕过。停下来，告诉用户你在做什么、做完了什么、还剩什么。",
        "warn": "brake：本任务的{what}已用掉 {pct}%（{used}）。",
        "warn_ai": "[brake] 本任务的{what}已用掉 {pct}%。请收尾：做完手头这一步，然后告诉用户做完了什么、还剩什么。",
        "session_block": "brake：本会话已到上限：{why}。请在 .brake/config.json 里调高 \"session\"，或新开一个会话。",
        "no_usage": "brake：读不到会话记录里的 token 用量（{err}）。本会话的金额和 token 上限暂时失效；时长和死循环上限照常生效。",
        "self": "brake：AI 不能改或删自己的限额。请让用户自己操作。",
        "status_none": "还没有记录任何会话。",
        "status_head": "最近的会话（新的在前）：",
        "status_row": "- {sid}  任务：约 {tusd} · {ttok:,} tok · {tmin} 分钟   会话：约 {susd} · {smin} 分钟{tripped}",
        "tripped": "   ⛔ {why}",
        "limits": "上限——任务：{task}   会话：{session}",
        "off": "不限",
        "installed": "brake 已装进 {root}\n  引擎：{engine}\n  hooks：{settings}（{events}）\n  上限：{config}\n"
                     "    任务：{task}   会话：{session}\n在这个项目里新开一个 Claude Code 会话，hooks 就生效了。",
        "already": "{settings} 里已经有 brake 的 hooks；引擎已刷新。",
        "removed": "已从 {root} 卸载 brake：删掉 {n} 条 hook{data}。",
        "removed_data": "，已删除 {dir}", "kept_data": "，保留了 {dir}",
        "bad_settings": "{path} 不是合法的 JSON（{err}）。请先修好它；brake 不会覆盖它。",
    },
}
WHAT = {"en": {"usd": "{limit} budget", "tokens": "{limit:,}-token budget", "minutes": "{limit}-minute budget"},
        "zh": {"usd": "{limit} 预算", "tokens": "{limit:,} token 预算", "minutes": "{limit} 分钟时长"}}


def t(lang: str, key: str, **kw) -> str:
    return MSG.get(lang, MSG["en"])[key].format(**kw)


def money(x: float) -> str:
    """$12.34 — or, for small budgets, enough digits to be meaningful ($0.0046)."""
    return f"${x:.2f}" if abs(x) >= 0.1 or x == 0 else f"${x:.4f}"


def _now() -> _dt.datetime:
    fixed = os.environ.get("BRAKE_NOW")
    return _dt.datetime.fromisoformat(fixed) if fixed else _dt.datetime.now().astimezone()


def _parse_t(ts) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _short(text, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Usage from the transcript (incremental, deduplicated by message id)
# ---------------------------------------------------------------------------
def price_of(model: str, prices: dict) -> tuple:
    if model in prices:
        return tuple(prices[model])
    base = re.sub(r"-\d{8}$", "", model or "")           # tolerate dated ids
    if base in prices:
        return tuple(prices[base])
    return tuple(prices[UNKNOWN_MODEL])


def message_cost(usage: dict, model: str, prices: dict) -> tuple:
    """(new tokens, usd) for one assistant message's usage block."""
    p_in, p_out, p_w5, p_w1, p_read = price_of(model, prices)
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    cc = usage.get("cache_creation") or {}
    w1 = int(cc.get("ephemeral_1h_input_tokens") or 0) if isinstance(cc, dict) else 0
    w5 = max(write - w1, 0)
    usd = (inp * p_in + out * p_out + w5 * p_w5 + w1 * p_w1 + read * p_read) / 1_000_000
    if usage.get("speed") == "fast":
        usd *= 2
    return inp + write + out, usd


class UsageReader:
    """Reads only what's new in each transcript since last time; remembers per-message cost records."""

    def __init__(self, state: dict, prices: dict):
        self.files = state.setdefault("files", {})
        self.prices = prices

    def update(self, path: Path) -> None:
        key = str(path)
        f = self.files.setdefault(key, {"offset": 0, "recs": {}})
        try:
            size = path.stat().st_size
        except OSError:
            raise FileNotFoundError(str(path))
        if size < f["offset"]:                           # rewritten: start over
            f["offset"], f["recs"] = 0, {}
        if size == f["offset"]:
            return
        with open(path, "rb") as fh:
            fh.seek(f["offset"])
            chunk = fh.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return                                        # a line still being written
        for raw in chunk[: end + 1].splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            usage = m.get("usage")
            if not isinstance(usage, dict):
                continue
            mid = m.get("id") or d.get("requestId") or d.get("uuid")
            if not mid:
                continue
            tok, usd = message_cost(usage, str(m.get("model") or ""), self.prices)
            f["recs"][mid] = [d.get("timestamp"), tok, round(usd, 8)]   # same id again → same numbers, kept once
        f["offset"] += end + 1

    def totals(self, since: _dt.datetime | None) -> tuple:
        tok, usd = 0, 0.0
        for f in self.files.values():
            for ts, k, u in f["recs"].values():
                when = _parse_t(ts)
                if since is None or (when is not None and when >= since):
                    tok += k
                    usd += u
        return tok, usd


def transcripts(path: str) -> list:
    """The session transcript plus its subagents' transcripts (<session>/subagents/*.jsonl)."""
    p = Path(os.path.expanduser(path))
    out = [p]
    sub = p.with_suffix("") / "subagents"
    if sub.is_dir():
        out += sorted(sub.glob("*.jsonl"))
    return out


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class Brake:
    def __init__(self, home: Path):
        self.home = home
        self.cfg = self._config()
        self.lang = self.cfg.get("lang", "en")
        self.prices = {**{k: list(v) for k, v in PRICES.items()}, **(self.cfg.get("prices") or {})}
        self._lock = None

    def _config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        p = self.home / CONFIG_FILE
        if p.is_file():
            user = json.loads(p.read_text(encoding="utf-8") or "{}")
            if not isinstance(user, dict):
                raise ValueError(f"{p}: must be a JSON object")
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        return cfg

    def __enter__(self):
        (self.home / STATE_DIR).mkdir(parents=True, exist_ok=True)
        self._lock = open(self.home / STATE_DIR / ".lock", "w")
        if fcntl:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._lock:
            if fcntl:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()

    def path(self, sid) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or "default"))[:80].lstrip(".") or "default"
        return self.home / STATE_DIR / f"{safe}.json"

    def load(self, sid) -> dict:
        try:
            return json.loads(self.path(sid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, sid, st: dict) -> None:
        p = self.path(sid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def call_signature(tool: str, ti: dict) -> str:
    ti = {k: v for k, v in (ti or {}).items() if k not in ("description", "timeout", "run_in_background")}
    return hashlib.sha1(f"{tool}|{json.dumps(ti, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()[:16]


def call_label(tool: str, ti: dict) -> str:
    arg = ti.get("command") or ti.get("file_path") or ti.get("url") or ti.get("pattern") or ti.get("query") or ""
    return f"{tool}({_short(arg, 70)})" if arg else tool


def _minutes(a: _dt.datetime | None, b: _dt.datetime) -> int:
    return int((b - a).total_seconds() // 60) if a else 0


def over(lang: str, scope: str, limits: dict, usd: float, tok: int, mins: int, usage_ok: bool) -> str | None:
    s = t(lang, scope)
    if usage_ok and limits.get("usd") is not None and usd >= float(limits["usd"]):
        return t(lang, "r_usd", used=money(usd), limit=money(float(limits["usd"])), scope=s)
    if usage_ok and limits.get("tokens") is not None and tok >= int(limits["tokens"]):
        return t(lang, "r_tokens", used=tok, limit=int(limits["tokens"]), scope=s)
    if limits.get("minutes") is not None and mins >= int(limits["minutes"]):
        return t(lang, "r_minutes", used=mins, limit=int(limits["minutes"]), scope=s)
    return None


def near(lang: str, limits: dict, share: float, usd: float, tok: int, mins: int, usage_ok: bool, warned: list):
    """(key, message) for the first limit past `share` that we haven't warned about yet."""
    checks = [("usd", usd, money), ("tokens", tok, lambda v: f"{v:,}"), ("minutes", mins, lambda v: f"{v} min")]
    for key, used, fmt in checks:
        limit = limits.get(key)
        if limit is None or key in warned or (key != "minutes" and not usage_ok):
            continue
        if used >= share * float(limit):
            what = WHAT.get(lang, WHAT["en"])[key].format(limit=money(float(limit)) if key == "usd" else int(limit))
            return key, what, int(round(100 * used / float(limit))), fmt(used)
    return None


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------
READ_ONLY_CLI = re.compile(r"^\s*(?:python3?\s+)?['\"]?\S*\.brake/brake\.py['\"]?\s+(?:status|version|help)\b[^;&|`$<>]*$|"
                           r"^\s*(?:cat|less|head|tail|ls|grep|rg|wc)\s[^;&|`$<>]*$")


def touches_self(tool: str, ti: dict, home: Path) -> bool:
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = os.path.normpath(os.path.expanduser(str(ti.get("file_path") or ti.get("notebook_path") or "")))
        return bool(p) and (p + "/").startswith(os.path.normpath(str(home)) + "/")
    if tool in ("Bash", "PowerShell"):
        cmd = str(ti.get("command") or "")
        return (HOME_DIR in cmd or "brake.py" in cmd) and not READ_ONLY_CLI.match(cmd)
    return False


def _stop_json(reason_user: str, reason_ai: str) -> str:
    return json.dumps({"continue": False, "stopReason": reason_user, "systemMessage": reason_user,
                       "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason_ai}}, ensure_ascii=False)


def measure(b: Brake, st: dict, data: dict) -> tuple:
    """Refresh usage from the transcripts. Returns (usage_ok, error text)."""
    path = data.get("transcript_path")
    if not path:
        return False, "no transcript_path"
    reader = UsageReader(st, b.prices)
    try:
        for p in transcripts(path):
            reader.update(p)
    except (OSError, FileNotFoundError) as e:
        return False, f"{type(e).__name__}: {_short(e, 120)}"
    return True, ""


def hook(data: dict, engine_path: Path | None = None) -> tuple:
    home = find_home(data.get("cwd"), engine_path)
    if home is None:
        return 0, "", ""
    b = Brake(home)
    lang = b.lang
    event = data.get("hook_event_name", "")
    sid = data.get("session_id")
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    now = _now()

    if event == "PreToolUse" and touches_self(tool, ti, home):
        return 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": t(lang, "self")}}, ensure_ascii=False), ""

    with b:
        st = b.load(sid)
        st.setdefault("session_start", now.isoformat())
        st.setdefault("task", {"start": st["session_start"], "prompt": "", "warned": [], "tripped": None})
        st.setdefault("calls", [])
        out = ""
        if event == "SessionStart":
            if data.get("source") in (None, "startup", "clear"):
                st["session_start"] = now.isoformat()
                st["task"] = {"start": now.isoformat(), "prompt": "", "warned": [], "tripped": None}
                st["calls"] = []
        elif event == "UserPromptSubmit":
            usage_ok, _err = measure(b, st, data)
            reader = UsageReader(st, b.prices)
            stok, susd = reader.totals(_parse_t(st["session_start"]))
            why = over(lang, "session", b.cfg.get("session") or {}, susd, stok, _minutes(_parse_t(st["session_start"]), now), usage_ok)
            if why:
                b.save(sid, st)
                return 0, json.dumps({"decision": "block", "reason": t(lang, "session_block", why=why)}, ensure_ascii=False), ""
            st["task"] = {"start": now.isoformat(), "prompt": _short(data.get("prompt"), 80), "warned": [], "tripped": None}
            st["calls"] = []
        elif event == "PreToolUse":
            out = _pre(b, st, data, tool, ti, now)
        b.save(sid, st)
    return 0, out, ""


def _pre(b: Brake, st: dict, data: dict, tool: str, ti: dict, now: _dt.datetime) -> str:
    lang = b.lang
    task = st["task"]
    usage_ok, err = measure(b, st, data)
    notes = []
    if not usage_ok and not st.get("usage_warned"):
        st["usage_warned"] = True
        notes.append(t(lang, "no_usage", err=err))
    reader = UsageReader(st, b.prices)
    t0, s0 = _parse_t(task["start"]), _parse_t(st["session_start"])
    ttok, tusd = reader.totals(t0)
    stok, susd = reader.totals(s0)
    tmin, smin = _minutes(t0, now), _minutes(s0, now)
    task["ncalls"] = int(task.get("ncalls", 0)) + 1
    line = t(lang, "line", usd=money(tusd), tok=ttok, mins=tmin, calls=task["ncalls"], prompt=task.get("prompt") or "…")
    if task.get("tripped"):                     # once stopped, this task stays stopped until your next message
        why = task["tripped"]
        return _stop_json("\n".join([t(lang, "stop", why=why), line, t(lang, "how")]),
                          "\n".join([t(lang, "stop", why=why), line, t(lang, "to_ai")]))
    # loop: record this call first, then look at the window
    if tool not in (b.cfg.get("loop_exempt") or []):
        st["calls"].append([call_signature(tool, ti), call_label(tool, ti)])
        st["calls"] = st["calls"][-int(b.cfg.get("loop_window", 12)):]
    sig = call_signature(tool, ti)
    n_same = sum(1 for c in st["calls"] if c[0] == sig)
    tl = b.cfg.get("task") or {}
    why = over(lang, "task", tl, tusd, ttok, tmin, usage_ok) or over(lang, "session", b.cfg.get("session") or {}, susd, stok, smin, usage_ok)
    if not why and tl.get("repeat") and tool not in (b.cfg.get("loop_exempt") or []) and n_same >= int(tl["repeat"]):
        why = t(lang, "r_repeat", n=n_same, w=len(st["calls"]), what=call_label(tool, ti))
    if why:
        task["tripped"] = why
        user = "\n".join([*notes, t(lang, "stop", why=why), line, t(lang, "how")])
        return _stop_json(user, "\n".join([t(lang, "stop", why=why), line, t(lang, "to_ai")]))
    hit = near(lang, tl, float(b.cfg.get("warn_at", 0.8)), tusd, ttok, tmin, usage_ok, task.setdefault("warned", []))
    ctx = ""
    if hit:
        key, what, pct, used = hit
        task["warned"].append(key)
        notes.append(t(lang, "warn", pct=pct, what=what, used=used))
        ctx = t(lang, "warn_ai", pct=pct, what=what)
    if not notes:
        return ""
    res = {"systemMessage": "\n".join(notes)}
    if ctx:
        res["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "additionalContext": ctx}
    return json.dumps(res, ensure_ascii=False)


def find_home(start, engine_path: Path | None = None) -> Path | None:
    if engine_path is not None and engine_path.parent.name == HOME_DIR:
        return engine_path.parent
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / HOME_DIR / ENGINE_FILE).is_file():
            return d / HOME_DIR
    return None


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
        if ev == "PreToolUse":
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


def _limits_text(lang: str, lim: dict) -> str:
    bits = []
    if lim.get("usd") is not None:
        bits.append(money(float(lim["usd"])))
    if lim.get("tokens") is not None:
        bits.append(f"{int(lim['tokens']):,} tok")
    if lim.get("minutes") is not None:
        bits.append(f"{lim['minutes']} min")
    if lim.get("repeat"):
        bits.append(f"loop ×{lim['repeat']}")
    return " · ".join(bits) or t(lang, "off")


def install(root: Path, lang: str = "en", local: bool = False, task_limits: dict | None = None) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    home.mkdir(parents=True, exist_ok=True)
    src, dst = Path(__file__).resolve(), home / ENGINE_FILE
    if src != dst.resolve():
        shutil.copy2(src, dst)
    cfg_path = home / CONFIG_FILE
    if not cfg_path.is_file():
        cfg = {k: v for k, v in json.loads(json.dumps(DEFAULT_CONFIG)).items() if k != "prices"}
        cfg["lang"] = lang
        cfg["task"].update({k: v for k, v in (task_limits or {}).items() if v is not None})
        _write_json(cfg_path, cfg)
    (home / ".gitignore").write_text(STATE_DIR + "/\n", encoding="utf-8")
    settings = root / ".claude" / ("settings.local.json" if local else "settings.json")
    try:
        doc = _load_settings(settings)
        changed = add_hooks(doc)
    except ValueError as e:
        raise SystemExit(t(lang, "bad_settings", path=settings, err=e))
    b = Brake(home)
    if changed:
        _write_json(settings, doc)
        return t(b.lang, "installed", root=root, engine=dst, settings=settings, events=", ".join(HOOK_EVENTS),
                 config=cfg_path, task=_limits_text(b.lang, b.cfg["task"]), session=_limits_text(b.lang, b.cfg["session"]))
    return t(b.lang, "already", settings=settings)


def uninstall(root: Path, keep_data: bool = False) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    try:
        lang = Brake(home).lang if (home / CONFIG_FILE).is_file() else "en"
    except (OSError, ValueError):
        lang = "en"
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
def cmd_status(b: Brake, limit: int = 8) -> int:
    lang = b.lang
    print(t(lang, "limits", task=_limits_text(lang, b.cfg["task"]), session=_limits_text(lang, b.cfg["session"])))
    files = sorted((b.home / STATE_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    if not files:
        print(t(lang, "status_none"))
        return 0
    print(t(lang, "status_head"))
    now = _now()
    for p in files:
        st = json.loads(p.read_text(encoding="utf-8"))
        r = UsageReader(st, b.prices)
        task = st.get("task") or {}
        t0, s0 = _parse_t(task.get("start")), _parse_t(st.get("session_start"))
        ttok, tusd = r.totals(t0)
        _stok, susd = r.totals(s0)
        trip = t(lang, "tripped", why=task["tripped"]) if task.get("tripped") else ""
        print(t(lang, "status_row", sid=p.stem[:8], tusd=money(tusd), ttok=ttok, tmin=_minutes(t0, now), susd=money(susd),
                smin=_minutes(s0, now), tripped=trip))
    return 0


def _main_hook() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        code, out, err = hook(data, Path(__file__).resolve())
    except Exception as e:  # noqa: BLE001 — say it loudly, but never wedge the session
        print(f"brake hook error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


USAGE = """usage: brake.py <command>
  init [DIR] [--lang en|zh] [--local] [--usd N] [--tokens N] [--minutes N] [--repeat N]
                                   install into a project with these task limits (default: current dir)
  uninstall [DIR] [--keep-data]    remove our hooks (and .brake/)
  status                           limits, and what recent sessions / tasks have used
  hook                             (called by Claude Code)
  version"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    rest = argv[1:]

    def opt(name, conv=str):
        if name in rest:
            i = rest.index(name)
            if i + 1 >= len(rest):
                raise SystemExit(USAGE)
            v = rest[i + 1]
            del rest[i:i + 2]
            return conv(v)
        return None

    if cmd == "hook":
        return _main_hook()
    if cmd in ("version", "--version", "-V"):
        print(VERSION)
        return 0
    if cmd == "init":
        lang = opt("--lang") or "en"
        lim = {"usd": opt("--usd", float), "tokens": opt("--tokens", int), "minutes": opt("--minutes", int),
               "repeat": opt("--repeat", int)}
        local = "--local" in rest
        rest = [a for a in rest if a != "--local"]
        if lang not in MSG:
            print(USAGE, file=sys.stderr)
            return 2
        print(install(Path(rest[0] if rest else "."), lang=lang, local=local, task_limits=lim))
        return 0
    if cmd == "uninstall":
        keep = "--keep-data" in rest
        rest = [a for a in rest if a != "--keep-data"]
        print(uninstall(Path(rest[0] if rest else "."), keep_data=keep))
        return 0
    if cmd == "status":
        home = find_home(os.getcwd(), Path(__file__).resolve())
        if home is None:
            print("brake is not installed here (no .brake/ found). Run: python3 brake.py init", file=sys.stderr)
            return 2
        return cmd_status(Brake(home))
    print(USAGE, file=sys.stderr if cmd != "help" else sys.stdout)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
