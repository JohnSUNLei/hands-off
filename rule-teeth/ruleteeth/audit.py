# -*- coding: utf-8 -*-
"""实测榜：拿 Claude Code 存在本机的会话记录，把历史工具调用对着现在的规则重放一遍。

规则的先后不再只凭判断，按真实违规次数排（思路来自 dead-rules-audit 和「吵 / 静」规则报表）：
- 读 `~/.claude/projects/<项目路径编码>/*.jsonl`（含 `subagents/` 和这个项目 worktree 的会话）；
- 对「能自动检查」的规则，重放改文件（Write / Edit / MultiEdit）、跑命令（Bash）、每轮最后一条回复；
- 另外数 rule-teeth 真拦过几次（hook 的拦截信息就在会话记录里）；
- 排出三张榜：最常被违反的、从没触发的（候选删掉或降级）、能查却还没装牙的。

只读：会话记录只打开来读，不写、不挪、不删。不跑任何命令，不调模型。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import checkup, classify, engine, report
from .model import CHECKABLE, JUDGMENT, VAGUE

REPLAYABLE = {"max_lines", "forbid_pattern", "no_secrets", "protected_path", "forbid_command", "reply_prefix"}
FILE_CHECKS = ("max_lines", "forbid_pattern", "no_secrets", "protected_path")
FILE_TOOLS = ("Write", "Edit", "MultiEdit")
REPLY, COMMAND = "<reply>", "<command>"          # Tally.where 里的两个特殊位置
_ENTRY = re.compile(r'"type":\s*"(?:user|assistant)"')
_IS_ERROR = re.compile(r'"is_error":\s*true')
_HOOK = re.compile(r"(?:PreToolUse|PostToolUse)\S*.{0,300}?\bhook\b", re.S)
_WORKTREE = re.compile(r"^\.claude/worktrees/[^/]+/")
_VLINE = re.compile(r"^- \[(RT\d+)\]")
_SRC = re.compile(r"^\s+\S+: (.*?)\s+\((?:出处|source): ([^()]+)\)\s*$")
HEADERS = tuple(engine.MSG[lg][k] for lg in ("zh", "en") for k in ("block_edit", "block_bash", "block_stop"))
SELF = tuple(engine.MSG[lg]["self"] for lg in ("zh", "en"))


class AuditError(Exception):
    """会话记录找不到、读不了：显式报错停住，不出一份「什么都没违反」的假报告。"""


@dataclass
class Tally:
    """一条能自动检查的规则在历史会话里的战绩。"""
    rule: object                                  # model.Rule
    checks: list                                  # 这条规则生成的检查（重放时全开着）
    teeth: str = ""                               # 已装的 rule-teeth 检查编号；空 = 没装牙
    violations: int = 0                           # 重放出来违反了几次（一次工具调用 / 一条回复算一次）
    blocks: int = 0                               # rule-teeth 真拦过几次
    last: str = ""                                # 最近一次违反或被拦的时间（UTC）
    where: Counter = field(default_factory=Counter)
    sessions: set = field(default_factory=set)

    @property
    def replayable(self) -> bool:
        return any(c["type"] in REPLAYABLE for c in self.checks)

    @property
    def others(self) -> list:
        """rule-teeth 以外已有的机器检查（测试 / CI / hook 文件）。"""
        return [x for x in self.rule.checked_by if not x.startswith(".rule-teeth")]

    @property
    def score(self) -> tuple:
        return max(self.violations, self.blocks), self.blocks


@dataclass
class Audit:
    root: Path
    lang: str
    rules: list                                   # 全部规则（model.Rule）
    tallies: list                                 # 能自动检查的规则（Tally）
    dirs: list                                    # 读了哪些会话目录
    files: int = 0                                # 会话文件数（主会话 + 子 agent）
    subagents: int = 0
    calls: int = 0                                # 重放了几次改文件 / 跑命令（去重后）
    outside: int = 0                              # 其中改的是项目外的文件，不算
    replies: int = 0                              # 重放了几条回复
    bad_lines: int = 0                            # 不是合法 JSON 的行（多半是正在写的会话）
    unreadable: list = field(default_factory=list)
    first: str = ""
    last: str = ""
    self_blocks: int = 0                          # AI 想改 rule-teeth 自己的清单 / hook 配置，被拦
    orphans: Counter = field(default_factory=Counter)   # (检查编号, 出处) → 次数：拦截记录对不上现在的规则
    since: str = ""
    chain: set = field(default_factory=set)       # 每次必读链上的规则文件（从没触发的规则在这上面，最值得降级）

    def ranked(self) -> list:
        hit = [t for t in self.tallies if t.violations or t.blocks]
        return sorted(hit, key=lambda t: (-t.score[0], -t.score[1], t.rule.cand.where))

    def never(self) -> list:
        return [t for t in self.tallies if t.replayable and not t.violations and not t.blocks]

    def untoothed(self) -> list:
        return sorted((t for t in self.tallies if not t.teeth), key=lambda t: (-t.violations, t.rule.cand.where))

    def blind(self) -> list:
        return [t for t in self.tallies if not t.replayable]


# ---------------------------------------------------------------------------
# 会话记录在哪
# ---------------------------------------------------------------------------
def project_key(root) -> str:
    """Claude Code 给项目起的目录名：路径里字母数字以外的字符都换成 -（`/home/me/proj` → `-home-me-proj`）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"


def session_dirs(root, base: Path | None = None) -> list:
    """这个项目自己的会话目录，加上它 `.claude/worktrees/` 下各个 worktree 的会话目录。"""
    base = base or projects_dir()
    key = project_key(Path(root).resolve())
    if not base.is_dir():
        return []
    own = [base / key] if (base / key).is_dir() else []
    trees = sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith(key + "--claude-worktrees-"))
    return own + trees


def log_files(dirs: list) -> list:
    out = []
    for d in dirs:
        out += sorted(d.glob("*.jsonl")) + sorted(d.glob("*/subagents/*.jsonl"))
    return out


# ---------------------------------------------------------------------------
# 重放
# ---------------------------------------------------------------------------
def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())[:40]


def _texts(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c or [] if isinstance(x, dict))


class _Replay:
    def __init__(self, au: Audit):
        self.au = au
        self.root = str(au.root)
        self.lang = au.lang
        pairs = [(t, c) for t in au.tallies for c in t.checks]
        self.file_checks = [(t, c) for t, c in pairs if c["type"] in FILE_CHECKS]
        self.cmd_checks = [(t, c) for t, c in pairs if c["type"] == "forbid_command"]
        self.reply_doc = {"lang": self.lang, "checks": [c for _t, c in pairs if c["type"] == "reply_prefix"]}
        self.by_id = {c["id"]: t for t, c in pairs}
        self.by_where = {t.rule.cand.where: t for t in au.tallies}
        self.by_text = {_key(classify.snippet(t.rule.text, 120)): t for t in au.tallies}
        self.seen_calls, self.seen_replies, self.seen_blocks, self.hits = set(), set(), set(), set()

    # -- 一个会话文件 -------------------------------------------------------
    def read(self, path: Path):
        side = path.parent.name == "subagents"
        self.au.files += 1
        self.au.subagents += side
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")      # 只读
        except OSError as e:
            self.au.unreadable.append(f"{path}: {e}")
            return
        pending = None                    # 本轮到目前为止最后一条回复：(消息 id, 文字, 时间, 会话)
        with fh:
            for n, line in enumerate(fh, 1):
                if not _ENTRY.search(line):
                    continue
                if '"tool_result"' in line and not _IS_ERROR.search(line):
                    continue              # 工具的正常输出：不是新一轮，也不是拦截
                try:
                    d = json.loads(line)
                except ValueError:
                    self.au.bad_lines += 1
                    continue
                if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
                    continue
                ts = str(d.get("timestamp") or "")
                if self.au.since and ts and ts[:10] < self.au.since:
                    continue
                if ts:
                    self.au.first = min(self.au.first or ts, ts)
                    self.au.last = max(self.au.last, ts)
                sid = str(d.get("sessionId") or path.stem)
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                if d["type"] == "assistant":
                    pending = self._assistant(d, msg, ts, sid, pending, f"{path}:{n}")
                else:
                    pending = self._user(d, msg, ts, pending, side or bool(d.get("isSidechain")))
        self._end_turn(pending, side)

    def _assistant(self, d: dict, msg: dict, ts: str, sid: str, pending, fallback: str):
        if msg.get("model") == "<synthetic>":
            return pending                # 客户端自己补的「API 出错」之类，不是 AI 说的
        mid = str(msg.get("id") or d.get("uuid") or fallback)
        for i, b in enumerate(msg.get("content") or []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                if not (pending and pending[0] == mid):
                    pending = (mid, b["text"], ts, sid)       # 同一条消息分几段写进来，开头那段才决定回复开头
            elif b.get("type") == "tool_use":
                pending = None            # 说完话又去调工具：刚才那句不是这一轮的最后一句
                self._call(b, d, ts, sid, f"{fallback}:{i}")
        return pending

    def _user(self, d: dict, msg: dict, ts: str, pending, side: bool):
        content = msg.get("content")
        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
            if isinstance(content, list) else []
        if results:
            for b in results:
                text = _result_text(b)
                if b.get("is_error") and _HOOK.search(text[:400]) and any(h in text for h in HEADERS + SELF):
                    self._block(text, "tool:" + str(b.get("tool_use_id") or d.get("uuid")), ts)
            return pending
        text = _texts(content).lstrip()
        if text.startswith("[Request interrupted"):
            return None                   # 被人打断的半句话，Stop hook 不会看它
        self._end_turn(pending, side)     # 人（或 hook）说话了：上一轮结束
        if text.startswith("Stop hook feedback") and any(h in text for h in HEADERS):
            self._block(text, "stop:" + str(d.get("uuid") or ts), ts)
        return None

    # -- 三种重放 -----------------------------------------------------------
    def _hit(self, t: Tally, event: str, ts: str, where: str, sid: str):
        key = (t.rule.cand.where, event)
        if key in self.hits:
            return                        # 同一条规则的几个检查在同一次调用上都命中，只算一次
        self.hits.add(key)
        t.violations += 1
        t.where[where] += 1
        t.sessions.add(sid)
        t.last = max(t.last, ts)

    def _call(self, b: dict, d: dict, ts: str, sid: str, fallback: str):
        tid = str(b.get("id") or fallback)
        if tid in self.seen_calls:
            return                        # 续接 / 分叉的会话会把前面的记录抄一遍
        self.seen_calls.add(tid)
        name, inp = b.get("name"), b.get("input") if isinstance(b.get("input"), dict) else {}
        if name == "Bash":
            self.au.calls += 1
            cmd = str(inp.get("command") or "")
            for t, c in self.cmd_checks:
                if engine.scan_command(c, cmd, self.lang):
                    self._hit(t, tid, ts, COMMAND, sid)
            return
        if name not in FILE_TOOLS:
            return
        self.au.calls += 1
        rel = self._rel(str(inp.get("file_path") or ""), str(d.get("cwd") or ""))
        if rel is None:
            self.au.outside += 1
            return
        if name == "Write":
            pieces, whole = [("", str(inp.get("content") or ""))], True
        elif name == "Edit":
            pieces, whole = [(str(inp.get("old_string") or ""), str(inp.get("new_string") or ""))], False
        else:
            pieces, whole = [(str(e.get("old_string") or ""), str(e.get("new_string") or ""))
                             for e in inp.get("edits") or [] if isinstance(e, dict)], False
        for t, c in self.file_checks:
            if self._breaks(c, rel, pieces, whole):
                self._hit(t, tid, ts, rel, sid)

    def _rel(self, file_path: str, cwd: str):
        if not file_path:
            return None
        p = file_path if os.path.isabs(file_path) else os.path.join(cwd or self.root, file_path)
        rel = os.path.relpath(os.path.normpath(p), self.root).replace("\\", "/")
        if rel.startswith(".."):
            real = os.path.realpath(p)                 # 走了软链接（/var → /private/var）的再试一次
            rel = os.path.relpath(real, os.path.realpath(self.root)).replace("\\", "/")
            if rel.startswith(".."):
                return None
        return _WORKTREE.sub("", rel)                  # worktree 里的文件按它在项目里的位置算

    def _breaks(self, c: dict, rel: str, pieces: list, whole: bool) -> bool:
        if c["type"] == "protected_path":
            return any(engine.match_glob(rel, p) for p in c.get("paths") or [])
        if not engine.applies(c, rel):
            return False
        if c["type"] == "max_lines":
            return whole and bool(engine.scan_text(c, rel, pieces[0][1], self.lang))   # Edit 看不出整个文件多长
        for old, new in pieces:
            added = Counter(v.msg for v in engine.scan_text(c, rel, new, self.lang))
            if old:
                added.subtract(Counter(v.msg for v in engine.scan_text(c, rel, old, self.lang)))
            if any(n > 0 for n in added.values()):
                return True               # 棘轮：改之前就有的同样写法不算这次违反
        return False

    def _end_turn(self, pending, side: bool):
        if side or not pending:
            return                        # 子 agent 的话是说给主会话听的，不是回复用户
        mid, text, ts, sid = pending
        if mid in self.seen_replies:
            return
        self.seen_replies.add(mid)
        self.au.replies += 1
        for v in engine.check_reply(self.reply_doc, text):
            self._hit(self.by_id[v.check], "reply:" + mid, ts, REPLY, sid)

    # -- 真拦过的 -----------------------------------------------------------
    def _block(self, text: str, event: str, ts: str):
        if event in self.seen_blocks:
            return
        self.seen_blocks.add(event)
        if any(s in text for s in SELF):
            self.au.self_blocks += 1
        lines = text.splitlines()
        counted = set()
        for i, ln in enumerate(lines):
            m = _VLINE.match(ln.lstrip())
            if not m:
                continue
            m2 = _SRC.match(lines[i + 1]) if i + 1 < len(lines) else None
            rule, src = (m2.group(1), m2.group(2).strip()) if m2 else ("", "")
            t = self.by_where.get(src) or (self.by_text.get(_key(rule)) if rule else None)
            if t is None:
                self.au.orphans[(m.group(1), src or "?")] += 1
            elif t.rule.cand.where not in counted:
                counted.add(t.rule.cand.where)
                t.blocks += 1
                t.last = max(t.last, ts)


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def run(root, dirs=(), since: str = "", lang: str = "", include=(), base: Path | None = None) -> Audit:
    root = Path(root).resolve()
    if since:
        try:
            since = _dt.date.fromisoformat(since).isoformat()
        except ValueError:
            raise AuditError(f"--since 要写成 YYYY-MM-DD，收到的是 {since!r} / --since must be YYYY-MM-DD") from None
    if dirs:
        found = [Path(d).expanduser().resolve() for d in dirs]
        missing = [str(d) for d in found if not d.is_dir()]
        if missing:
            raise AuditError("找不到会话记录目录 / session folder not found: " + ", ".join(missing))
    else:
        found = session_dirs(root, base)
        if not found:
            where = (base or projects_dir()) / project_key(root)
            raise AuditError(f"找不到这个项目的会话记录：{where}（Claude Code 在这个目录下开过会话才会有；"
                             f"别的位置用 --sessions 指定）/ no Claude Code session logs for this project at {where}; "
                             f"pass --sessions DIR")
    logs = log_files(found)
    if not logs:
        raise AuditError("会话记录目录里没有 .jsonl 文件 / no .jsonl session logs in: " + ", ".join(map(str, found)))
    cu = checkup.run(root, include, lang)
    every = [{**c, "enabled": True} for c in checkup.build_checks(cu.rules, cu.lang)]
    by_src: dict = {}
    for c in every:
        by_src.setdefault(c["source"], []).append(c)
    tallies = [Tally(r, by_src.get(r.cand.where, []), cu.teeth.get(r.cand.where, ""))
               for r in cu.rules if r.category == CHECKABLE and by_src.get(r.cand.where)]
    au = Audit(root, cu.lang, cu.rules, tallies, found, since=since, chain={x.path for x in cu.chain})
    rp = _Replay(au)
    for f in logs:
        rp.read(f)
    return au


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
T = {
    "zh": {
        "title": "规则实测榜 · {name}",
        "meta": "rule-teeth {v} · {d} · 只读：会话记录只读不改，没跑任何命令，没调模型",
        "oneline": ("**一句话**：{total} 条规则，{checkable} 条能自动检查。重放了 {files} 个会话文件里的 {calls} 次改文件 / 跑命令"
                    "和 {replies} 条回复：{violated} 条被违反过{top}，rule-teeth 真拦下 {blocks} 次；"
                    "{never} 条从没触发；{untoothed} 条能查却还没装牙。"),
        "top_one": "（最多的是 `{w}`，{n} 次）",
        "read": "读了什么",
        "read_body": ("- 会话目录：{dirs}\n- 会话文件 {files} 个（其中子 agent {sub} 个），时间 {first} ~ {last}（UTC）{since}\n"
                      "- 重放改文件 / 跑命令 {calls} 次（其中 {outside} 次改的是项目外的文件，不算），回复 {replies} 条"),
        "since": "，只看 {d} 以后",
        "bad": "- ⚠️ {n} 行不是合法 JSON，跳过了（多半是正在写的会话的最后一行）",
        "unreadable": "- ⚠️ {n} 个文件读不了：{files}",
        "self_blocks": "- AI 想改 rule-teeth 自己的检查清单 / hook 配置，被拦了 {n} 次",
        "rules": "规则一共 {total} 条",
        "rules_body": ("能自动检查 {c} 条（其中能拿会话记录重放的 {r} 条）、要人判断 {j} 条、太模糊 {v} 条。"
                       "要人判断和太模糊的，机器数不出违反次数，不上榜。"),
        "rank": "最常被违反的", "rank_cols": "| # | 位置 | 规则 | 违反 | 被拦 | 牙 | 最近一次 | 多半在哪 |",
        "rank_none": "重放下来，没有一条能自动检查的规则被违反过。",
        "rank_note": ("「违反」= 拿现在的规则重放历史上的工具调用 / 回复，算出来会违反的次数（被拦下的那次尝试也算）；"
                      "「被拦」= 会话记录里 rule-teeth 真拦下的次数。被拦多、违反也多，是这条牙在干活；"
                      "被拦多但大家都觉得拦错了，是这条牙太吵，该改规则或改检查。"),
        "never": "从没触发的（候选删掉或降级）", "never_cols": "| 位置 | 规则 | 牙 | 建议 |",
        "never_teeth_chain": "牙一直没咬过，原文又在每次必读链上：缩成一行指针，牙留着",
        "never_teeth": "牙一直没咬过：牙留着，原文不用动",
        "never_chain": "候选降级：从每次必读的文件挪到按需读的那层，或者删掉",
        "never_plain": "候选删掉",
        "never_note": "只看了 {files} 个会话文件。从没触发 ≠ 没用：也可能是 AI 一直守着它，或者那种情况还没出现过。删之前看一眼。",
        "untoothed": "能自动检查、却还没装牙的", "untoothed_cols": "| 位置 | 规则 | 能生成的检查 | 违反 | 别的检查 |",
        "untoothed_note": "装牙：`python3 rule_teeth.py init {root} --hook`（命令类检查默认关，要你亲手打开）。违反次数多的先装。",
        "blind": "能自动检查、但会话记录重放不了的", "blind_cols": "| 位置 | 规则 | 检查 | 被拦 | 为什么重放不了 |",
        "blind_why": {"run_command": "要当时跑一遍命令，会话记录里没有当时的代码",
                      "load_budget": "要当时整条每次必读链的样子，会话记录里没有"},
        "orphans": "拦截记录对不上现在的规则", "orphans_cols": "| 检查编号 | 当时写的出处 | 次数 |",
        "orphans_note": "规则改过、挪过行或删了。要按新位置算，重新装一次牙（`init`）。",
        "how": "怎么算的",
        "how_body": ("- 规则用的是**现在**规则文件里的规则，检查和 `init` 生成的一样，全部打开重放（包括你手动关掉的）。\n"
                     "- Write 按整个新文件算（会话记录里看不出改之前长什么样）；Edit 只看新增的那段，改之前就有的同样写法不算（棘轮）；"
                     "行数上限只有 Write 能算。\n"
                     "- Bash 只看命令本身；用 Bash 写文件（`cat > a.py`）的内容看不到。\n"
                     "- 回复：主会话每一轮最后一条 AI 的话；被人打断的不算，子 agent 说给主会话的话不算。\n"
                     "- 同一次工具调用在续接 / 分叉的会话里抄了几遍，按调用编号去重，只算一次。\n"
                     "- 「被拦」从会话记录里的 hook 拦截信息数，按里面写的出处对到规则上；出处对不上的按规则原文对。"),
        "teeth_yes": "🦷 {id}", "teeth_other": "测试 / CI", "teeth_no": "—",
        "reply": "回复", "command": "命令", "dash": "—", "n_broken": "违反", "n_blocked": "被拦",
    },
    "en": {
        "title": "Rule audit · {name}",
        "meta": "rule-teeth {v} · {d} · read-only: session logs were read, never changed; no commands run, no model calls",
        "oneline": ("**In one line**: {total} rules, {checkable} checkable. Replaying {calls} file edits / commands and "
                    "{replies} replies from {files} session files: {violated} rules were broken{top}, and rule-teeth "
                    "actually blocked {blocks} times; {never} never fired; {untoothed} are checkable but have no teeth yet."),
        "top_one": " (most often `{w}`, {n} times)",
        "read": "What was read",
        "read_body": ("- Session folders: {dirs}\n- {files} session files ({sub} from subagents), {first} ~ {last} (UTC){since}\n"
                      "- Replayed {calls} file edits / commands ({outside} touched files outside the project and were "
                      "skipped) and {replies} replies"),
        "since": ", only since {d}",
        "bad": "- ⚠️ {n} lines were not valid JSON and were skipped (usually the last line of a session still being written)",
        "unreadable": "- ⚠️ {n} files could not be read: {files}",
        "self_blocks": "- The AI tried to edit rule-teeth's own check list / hook config and was blocked {n} times",
        "rules": "{total} rules in all",
        "rules_body": ("{c} checkable ({r} of them can be replayed from session logs), {j} need human judgment, {v} too "
                       "vague. A machine cannot count violations of the last two, so they are not ranked."),
        "rank": "Broken most often", "rank_cols": "| # | Where | Rule | Broken | Blocked | Teeth | Last | Mostly in |",
        "rank_none": "Replaying the history, no checkable rule was ever broken.",
        "rank_note": ("\"Broken\" = replaying past tool calls / replies against today's rules, how many would break it "
                      "(an attempt that was blocked counts too). \"Blocked\" = times rule-teeth actually blocked it in the "
                      "logs. Many blocks and many breaks: the teeth are working. Many blocks that everyone thinks were "
                      "wrong: the check is noisy — fix the rule or the check."),
        "never": "Never fired (candidates to delete or demote)", "never_cols": "| Where | Rule | Teeth | Suggestion |",
        "never_teeth_chain": "the teeth never bit, and the text sits in the always-read chain: shrink it to a one-line pointer, keep the teeth",
        "never_teeth": "the teeth never bit: keep them, leave the text as it is",
        "never_chain": "candidate to demote: move it from an always-read file to a read-on-demand layer, or delete it",
        "never_plain": "candidate to delete",
        "never_note": ("Based on {files} session files. Never fired ≠ useless: the AI may have kept it every time, or the "
                       "situation never came up. Take a look before deleting."),
        "untoothed": "Checkable but no teeth yet", "untoothed_cols": "| Where | Rule | Check it would get | Broken | Other checks |",
        "untoothed_note": ("Install: `python3 rule_teeth.py init {root} --hook` (command checks start switched off; you "
                           "turn them on by hand). Start with the ones broken most."),
        "blind": "Checkable, but not replayable from session logs", "blind_cols": "| Where | Rule | Check | Blocked | Why not |",
        "blind_why": {"run_command": "it would mean running the command against the code as it was then, which the logs do not hold",
                      "load_budget": "it needs the whole always-read chain as it was then, which the logs do not hold"},
        "orphans": "Blocks that no longer match a rule", "orphans_cols": "| Check ID | Source as recorded | Times |",
        "orphans_note": "The rule was edited, moved or deleted. To count them under the new location, install the teeth again (`init`).",
        "how": "How it is counted",
        "how_body": ("- Rules are **today's** rules, with the same checks `init` generates, all switched on for the replay "
                     "(including ones you turned off).\n"
                     "- A Write counts as the whole new file (the logs do not show what it replaced); an Edit counts only what "
                     "it adds — the same pattern already there before is not a new break (ratchet). Line limits can only be "
                     "checked on Writes.\n"
                     "- Bash: only the command text; file contents written through Bash (`cat > a.py`) are not visible.\n"
                     "- Replies: the last thing the AI said in each turn of the main session; interrupted ones and subagent "
                     "messages to the main session do not count.\n"
                     "- A tool call copied into resumed / forked sessions is counted once, by its call ID.\n"
                     "- \"Blocked\" is counted from the hook messages in the logs, matched to rules by the source they name, "
                     "or by the rule text when the source has moved."),
        "teeth_yes": "🦷 {id}", "teeth_other": "tests / CI", "teeth_no": "—",
        "reply": "reply", "command": "command", "dash": "—", "n_broken": "broken", "n_blocked": "blocked",
    },
}


def _home(p) -> str:
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _teeth(t: Tally, L: dict) -> str:
    if t.teeth:
        return L["teeth_yes"].format(id=t.teeth)
    return L["teeth_other"] if t.others else L["teeth_no"]


def _never_hint(au: Audit, t: Tally, L: dict) -> str:
    key = ("never_teeth" if t.teeth or t.others else "never") + ("_chain" if t.rule.cand.file in au.chain else "")
    return L[key if key != "never" else "never_plain"]


def _place(w: str, L: dict) -> str:
    return L["reply"] if w == REPLY else (L["command"] if w == COMMAND else f"`{w}`")


def oneline(au: Audit) -> str:
    L = T[au.lang if au.lang in T else "en"]
    ranked = au.ranked()
    top_one = L["top_one"].format(w=ranked[0].rule.cand.where, n=ranked[0].score[0]) if ranked else ""
    return L["oneline"].format(total=len(au.rules), checkable=len(au.tallies), files=au.files, calls=au.calls,
                               replies=au.replies, violated=len([t for t in au.tallies if t.violations]), top=top_one,
                               blocks=sum(t.blocks for t in au.tallies), never=len(au.never()),
                               untoothed=len(au.untoothed()))


def render(au: Audit, name: str = "", top: int = 10) -> str:
    L = T[au.lang if au.lang in T else "en"]
    lang = au.lang
    ranked, never, untoothed, blind = au.ranked(), au.never(), au.untoothed(), au.blind()
    cell = report._cell
    out = [f"# {L['title'].format(name=name or au.root.name)}", "",
           f"> {L['meta'].format(v=engine.VERSION, d=_dt.date.today().isoformat())}", "",
           oneline(au), "",
           f"## {L['read']}", "",
           L["read_body"].format(dirs=", ".join(f"`{_home(d)}`" for d in au.dirs), files=au.files, sub=au.subagents,
                                 first=au.first[:16].replace("T", " ") or "—", last=au.last[:16].replace("T", " ") or "—",
                                 since=L["since"].format(d=au.since) if au.since else "", calls=au.calls,
                                 outside=au.outside, replies=au.replies)]
    if au.bad_lines:
        out.append(L["bad"].format(n=au.bad_lines))
    if au.unreadable:
        out.append(L["unreadable"].format(n=len(au.unreadable), files="; ".join(au.unreadable[:5])))
    if au.self_blocks:
        out.append(L["self_blocks"].format(n=au.self_blocks))
    out += ["", f"## {L['rules'].format(total=len(au.rules))}", "",
            L["rules_body"].format(c=len(au.tallies), r=len([t for t in au.tallies if t.replayable]),
                                   j=len([r for r in au.rules if r.category == JUDGMENT]),
                                   v=len([r for r in au.rules if r.category == VAGUE])), ""]

    out += [f"## {L['rank']}", ""]
    if ranked:
        rows = []
        for i, t in enumerate(ranked[:top], 1):
            mostly = ", ".join(_place(w, L) for w, _n in t.where.most_common(2)) or L["dash"]
            rows.append((i, t.rule.cand.where, cell(t.rule.text, True, 60),
                         t.violations if t.replayable else L["dash"], t.blocks, _teeth(t, L),
                         t.last[:10] or L["dash"], mostly))
        out += report._simple_table("", L["rank_cols"], rows)[2:]
        if len(ranked) > top:
            out += [f"_… +{len(ranked) - top}_", ""]
        out += [L["rank_note"], ""]
    else:
        out += [L["rank_none"], ""]
    if never:
        rows = [(t.rule.cand.where, cell(t.rule.text, True, 60), _teeth(t, L), _never_hint(au, t, L)) for t in never]
        out += report._simple_table(L["never"], L["never_cols"], rows) + [L["never_note"].format(files=au.files), ""]
    if untoothed:
        rows = [(t.rule.cand.where, cell(t.rule.text, True, 60),
                 "; ".join(report.describe_check(c, lang) for c in t.checks),
                 t.violations if t.replayable else L["dash"], ", ".join(t.others) or L["dash"]) for t in untoothed]
        out += report._simple_table(L["untoothed"], L["untoothed_cols"], rows) + [L["untoothed_note"].format(root="."), ""]
    if blind:
        rows = [(t.rule.cand.where, cell(t.rule.text, True, 60),
                 "; ".join(report.describe_check(c, lang) for c in t.checks), t.blocks,
                 "; ".join(L["blind_why"].get(c["type"], c["type"]) for c in t.checks)) for t in blind]
        out += report._simple_table(L["blind"], L["blind_cols"], rows)
    if au.orphans:
        rows = [(cid, f"`{src}`", n) for (cid, src), n in au.orphans.most_common()]
        out += report._simple_table(L["orphans"], L["orphans_cols"], rows) + [L["orphans_note"], ""]
    out += [f"## {L['how']}", "", L["how_body"], ""]
    return "\n".join(out)


def summary(au: Audit, n: int = 5) -> str:
    """给命令行打印的几行：规则总数、最常被违反的前 n 条。"""
    L = T[au.lang if au.lang in T else "en"]
    lines = [oneline(au).replace("**", "")]
    for i, t in enumerate(au.ranked()[:n], 1):
        lines.append(f"  {i}. {t.rule.cand.where}  {classify.snippet(t.rule.text, 50)}  "
                     f"{L['n_broken']} {t.violations} · {L['n_blocked']} {t.blocks}")
    return "\n".join(lines)
