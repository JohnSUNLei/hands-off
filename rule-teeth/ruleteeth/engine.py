#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule-teeth 运行时 —— 规则的牙齿长在这里。

这个文件故意写成「单文件、只用标准库、不 import 本包别的模块」：
`rule_teeth.py init` 会把它原样复制到目标项目的 `.rule-teeth/engine.py`，
再把 Claude Code 的 hook 指向它。这样目标项目不用安装任何东西，队友拉下代码就有牙。

它只做三件事：
1. 读 `.rule-teeth/rules.json`（检查清单，由 init 从 CLAUDE.md / AGENTS.md 生成）；
2. `check`：扫整个项目，报违规；
3. `hook`：被 Claude Code 调用（stdin 是 JSON），违规就 exit 2 —— 当场拦下。

检查一律是确定性的（正则、计数、跑命令），不调任何模型：同样的输入永远得到同样的结论。
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"
RULES_DIR = ".rule-teeth"
RULES_FILE = "rules.json"
STATE_FILE = "state.json"          # Stop 防死循环的小状态，不进 git
MAX_FILE_BYTES = 1_000_000          # 更大的文件不扫（多半是生成物）
STOP_RETRIES = 2                    # 同一批违规连拦几次后放行（并明说没解决）

CHECK_TYPES = ("max_lines", "forbid_pattern", "no_secrets", "protected_path",
               "forbid_command", "run_command", "reply_prefix")
# 每种检查在哪个时机生效。写死在这里，不让每条检查各配各的 —— 少一个旋钮就少一种配错。
EVENTS = {
    "max_lines": ("edit", "scan"),
    "forbid_pattern": ("edit", "scan"),
    "no_secrets": ("edit", "scan"),
    "protected_path": ("edit",),
    "forbid_command": ("bash",),
    "run_command": ("stop",),
    "reply_prefix": ("stop",),
}

LANG_EXTS = {
    "python": (".py",),
    "typescript": (".ts", ".tsx", ".mts", ".cts"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "swift": (".swift",),
    "ruby": (".rb",),
    "php": (".php",),
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp", ".cxx", ".hpp", ".hh"),
    "csharp": (".cs",),
    "shell": (".sh", ".bash", ".zsh", ".command"),
    "vue": (".vue",),
    "svelte": (".svelte",),
}
CODE_EXTS = tuple(sorted({e for exts in LANG_EXTS.values() for e in exts}))
CONFIG_EXTS = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".next", "target", "vendor", "coverage", ".mypy_cache", ".pytest_cache",
             RULES_DIR, ".claude"}
COMMENT_PREFIX = ("#", "//", "/*", "*", "--", "<!--", ";")

SECRET_PATTERNS = (
    r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_\-]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"gh[pousr]_[A-Za-z0-9]{36}",
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    # 通用写法：key = "值"。值要像密钥（ASCII、同时有字母和数字），'current-password' 这种表单字样不算
    r"(?i)(?:api[_\-]?key|secret|token|passw(?:or)?d)[\"']?\s*[:=]\s*[\"'](?=[^\"']*\d)(?=[^\"']*[A-Za-z])[A-Za-z0-9_\-+/=.]{16,}[\"']",
)
# 一看就是占位符的值不算泄密：your_key、xxx、${VAR}、<token> ……
_PLACEHOLDER_VALUE = re.compile(r"(?i)your|xxx|\.\.\.|\$\{|<[^>]*>|example|placeholder|changeme|dummy|fake|test")

MSG = {
    "zh": {
        "block_edit": "rule-teeth 拦下了这次修改 —— 它会违反项目规则文件里写着的规则：",
        "block_bash": "rule-teeth 拦下了这条命令 —— 项目规则不许这么做：",
        "block_stop": "rule-teeth：先别结束，下面这些规则还没满足：",
        "self": "这是 rule-teeth 自己的检查清单 / hook 配置。AI 不能自己给自己松绑 —— 要改请让用户亲手改。",
        "how": "怎么办：按规则改好再试。如果你认为这条规则在这里确实不适用，停下来告诉用户，由用户决定改不改规则。",
        "rule": "规则",
        "from": "出处",
        "lines": "{n} 行，超过上限 {limit} 行",
        "bytes": "{n} 字节，超过上限 {limit} 字节",
        "grows": "（文件本来就超了，这次还在变长）",
        "match": "出现了 `{m}`",
        "secret": "疑似把密钥 / 口令写进了代码：{m}",
        "protected": "这个文件 / 目录规则不许改",
        "command": "命令里有 `{m}`",
        "run_fail": "`{cmd}` 没跑通（退出码 {code}），最后几行输出：\n{tail}",
        "run_timeout": "`{cmd}` 超时（{t} 秒）",
        "prefix": "回复要以「{p}」开头，这次没有",
        "gave_up": "rule-teeth：同一批违规已经拦了 {n} 次还没解决，这次放行。请人来看：python3 .rule-teeth/engine.py check",
        "held": "已登记暂缓（欠条）",
        "expired": "暂缓已过期，重新算违规",
        "over": "比登记暂缓时又多了",
        "total": "合计违规 {n} 处",
        "none": "没有违规。",
    },
    "en": {
        "block_edit": "rule-teeth blocked this edit — it would break a rule written in your project's rule file:",
        "block_bash": "rule-teeth blocked this command — the project's rules forbid it:",
        "block_stop": "rule-teeth: don't stop yet — these rules are not satisfied:",
        "self": "This is rule-teeth's own check list / hook config. The AI may not loosen its own leash — ask the user to edit it by hand.",
        "how": "What to do: fix it so the rule holds, then retry. If you believe the rule truly does not apply here, stop and tell the user; the user decides whether to change the rule.",
        "rule": "rule",
        "from": "source",
        "lines": "{n} lines, over the {limit}-line limit",
        "bytes": "{n} bytes, over the {limit}-byte limit",
        "grows": " (already over the limit, and this edit makes it longer)",
        "match": "contains `{m}`",
        "secret": "looks like a secret / password written into code: {m}",
        "protected": "the rules say this file / folder must not be edited",
        "command": "the command contains `{m}`",
        "run_fail": "`{cmd}` failed (exit {code}); last lines of output:\n{tail}",
        "run_timeout": "`{cmd}` timed out ({t}s)",
        "prefix": "replies must start with \"{p}\"; this one does not",
        "gave_up": "rule-teeth: the same violations were blocked {n} times and are still there; letting this stop through. A human should look: python3 .rule-teeth/engine.py check",
        "held": "deferred (IOU)",
        "expired": "deferral expired, counts again",
        "over": "more than when it was deferred",
        "total": "{n} violation(s)",
        "none": "No violations.",
    },
}


@dataclass
class Violation:
    check: str          # 检查编号，如 RT3
    file: str           # 相对路径；命令 / 回复类检查为空
    line: int
    msg: str
    rule: str = ""      # 源规则原文（截短）
    source: str = ""    # 源规则位置，如 CLAUDE.md:42

    def __str__(self):
        where = f"{self.file}:{self.line}" if self.file else "-"
        return f"[{self.check}] {where}  {self.msg}"


# ---------------------------------------------------------------------------
# 文件清单与路径匹配
# ---------------------------------------------------------------------------
_FILES_CACHE: dict = {}


def list_files(root, skip=None) -> list:
    """项目里该看的文件（posix 相对路径）。git 仓库就尊重 .gitignore，否则自己走目录。"""
    root = Path(root).resolve()
    skip = SKIP_DIRS if skip is None else set(skip)
    key = (str(root), tuple(sorted(skip)))
    if key in _FILES_CACHE:
        return _FILES_CACHE[key]
    files = _git_files(root)
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fn in filenames:
                files.append(Path(dirpath, fn).relative_to(root).as_posix())
    files = sorted(f for f in files if not (set(f.split("/")[:-1]) & skip))
    _FILES_CACHE[key] = files
    return files


def _git_files(root: Path):
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"],
                             cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    return [n for n in names if (root / n).is_file()]


def match_glob(rel: str, pat: str) -> bool:
    """`*.py` 匹配任意深度；带 / 的按整条相对路径匹配；`dir/` 表示整个目录。"""
    rel = rel.replace("\\", "/")
    rel = rel[2:] if rel.startswith("./") else rel
    pat = pat.replace("\\", "/")
    if not re.search(r"[*?\[]", pat):              # 没有通配符：要么就是这个文件，要么是这个目录下的一切
        bare = pat.rstrip("/")
        if rel == bare or rel.startswith(bare + "/") or ("/" + bare + "/") in ("/" + rel):
            return True
    if pat.endswith("/"):
        return rel.startswith(pat) or ("/" + pat) in ("/" + rel)
    if "/" not in pat:
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat) or fnmatch.fnmatch(rel, pat)
    core = pat[3:] if pat.startswith("**/") else pat
    return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, core) or fnmatch.fnmatch(rel, "*/" + core)


def applies(check: dict, rel: str) -> bool:
    """files（什么类型的文件）和 where（在哪儿）都要满足，且不在 allow 里。"""
    for key in ("files", "where"):
        pats = check.get(key) or []
        if pats and not any(match_glob(rel, p) for p in pats):
            return False
    return not any(match_glob(rel, p) for p in check.get("allow") or [])


def read_text(path) -> str | None:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:4096]:
        return None
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 单条检查：对一段文字 / 一条命令 / 一个路径给出违规
# ---------------------------------------------------------------------------
def _t(lang: str, key: str, **kw) -> str:
    return MSG.get(lang, MSG["en"])[key].format(**kw)


_RE_CACHE: dict = {}


def _compiled(check: dict):
    pats = tuple(SECRET_PATTERNS if check["type"] == "no_secrets" else check.get("patterns") or ())
    if pats not in _RE_CACHE:
        _RE_CACHE[pats] = [re.compile(p) for p in pats]
    return _RE_CACHE[pats]


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(COMMENT_PREFIX)


def _line_hits(check: dict, text: str):
    """[(行号, 命中片段)]。注释行默认不算 —— 规则管的是代码，不是对代码的说明。"""
    hits = []
    skip_comments = check.get("skip_comments", True)
    secrets = check["type"] == "no_secrets"
    for i, ln in enumerate(text.splitlines(), 1):
        if skip_comments and not secrets and _is_comment(ln):
            continue
        for rx in _compiled(check):
            m = rx.search(ln)
            if m and not (secrets and _PLACEHOLDER_VALUE.search(m.group(0))):
                hits.append((i, m.group(0)))
                break
    return hits


def scan_text(check: dict, rel: str, text: str, lang: str = "en") -> list:
    """一个文件的内容违反了这条检查的哪些地方。"""
    kind = check["type"]
    out = []
    if kind == "max_lines":
        n = len(text.splitlines())
        if check.get("limit") and n > check["limit"]:
            out.append(Violation(check["id"], rel, n, _t(lang, "lines", n=n, limit=check["limit"])))
        nbytes = len(text.encode("utf-8"))
        if check.get("max_bytes") and nbytes > check["max_bytes"]:
            out.append(Violation(check["id"], rel, n, _t(lang, "bytes", n=nbytes, limit=check["max_bytes"])))
    elif kind in ("forbid_pattern", "no_secrets"):
        label = "secret" if kind == "no_secrets" else "match"
        for line, frag in _line_hits(check, text):
            shown = frag if kind == "forbid_pattern" else frag[:6] + "…"
            out.append(Violation(check["id"], rel, line, _t(lang, label, m=shown)))
    return _stamp(check, out)


def _stamp(check: dict, vs: list) -> list:
    for v in vs:
        v.rule = check.get("rule", "")
        v.source = check.get("source", "")
    return vs


def scan_command(check: dict, command: str, lang: str = "en") -> list:
    for rx in _compiled(check):
        m = rx.search(command)
        if m:
            return _stamp(check, [Violation(check["id"], "", 0, _t(lang, "command", m=m.group(0).strip()))])
    return []


# ---------------------------------------------------------------------------
# 规则清单
# ---------------------------------------------------------------------------
def rules_path(root) -> Path:
    return Path(root) / RULES_DIR / RULES_FILE


def load_rules(root) -> dict:
    p = rules_path(root)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("checks"), list):
        raise ValueError(f"{p}: 缺少 checks 列表 / missing 'checks' list")
    for c in doc["checks"]:
        if c.get("type") not in CHECK_TYPES:
            raise ValueError(f"{p}: 未知检查类型 / unknown check type {c.get('type')!r}")
    return doc


def active(doc: dict, event: str) -> list:
    return [c for c in doc.get("checks", []) if c.get("enabled", True) and event in EVENTS[c["type"]]]


def find_root(start) -> Path | None:
    """从 start 往上找带 .rule-teeth/rules.json 的目录。"""
    p = Path(start).resolve()
    for d in (p, *p.parents):
        if rules_path(d).is_file():
            return d
    return None


# ---------------------------------------------------------------------------
# 三种用法：扫全项目 / 改文件之前 / 跑命令之前
# ---------------------------------------------------------------------------
def check_project(root, doc: dict, only=None) -> list:
    """扫项目。only 给了就只扫这些相对路径（Stop 时只看这次动过的文件）。"""
    root = Path(root)
    lang = doc.get("lang", "en")
    checks = active(doc, "scan")
    out = []
    names = list_files(root) if only is None else sorted(set(only) & set(list_files(root)))
    for rel in names:
        mine = [c for c in checks if applies(c, rel)]
        if not mine:
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        for c in mine:
            out.extend(scan_text(c, rel, text, lang))
    return out


def check_edit(root, doc: dict, rel: str, before: str, after: str) -> list:
    """棘轮：只拦「这次修改新增的」违规。文件里早就有的老问题不拦这次修改，留给全量检查和暂缓清单。"""
    lang = doc.get("lang", "en")
    out = []
    for c in active(doc, "edit"):
        if c["type"] == "protected_path":
            if any(match_glob(rel, p) for p in c.get("paths") or []):
                out.extend(_stamp(c, [Violation(c["id"], rel, 0, _t(lang, "protected"))]))
            continue
        if not applies(c, rel):
            continue
        new = scan_text(c, rel, after, lang)
        if not new:
            continue
        old = scan_text(c, rel, before, lang) if before else []
        if c["type"] == "max_lines":
            grows = len(after.splitlines()) > len(before.splitlines()) or len(after) > len(before)
            if grows:
                for v in new:
                    v.msg += _t(lang, "grows") if old else ""
                out.extend(new)
            continue
        seen = Counter(v.msg for v in old)       # 改前就有的同样命中，不算这次新增
        for v in new:
            if seen[v.msg] > 0:
                seen[v.msg] -= 1
            else:
                out.append(v)
    return out


def check_bash(doc: dict, command: str) -> list:
    lang = doc.get("lang", "en")
    out = []
    for c in active(doc, "bash"):
        out.extend(scan_command(c, command, lang))
    return out


def changed_files(root, under: str = "") -> list | None:
    """git 里相对 HEAD 有改动（含未跟踪）的文件；不是 git 仓库返回 None。"""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                             cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    names = [e[3:] for e in out.stdout.decode("utf-8", "replace").split("\0") if len(e) > 3]
    under = under.strip("/")
    return [n for n in names if not under or n == under or n.startswith(under + "/")]


def run_check(root, check: dict, lang: str = "en") -> list:
    """跑规则里要求的命令（比如「改完代码跑测试」）。只在相关目录有改动时才跑。"""
    cwd = Path(root) / (check.get("cwd") or "")
    changed = changed_files(root, check.get("cwd") or "")
    if changed is not None and not changed:
        return []
    timeout = int(check.get("timeout", 300))
    cmd = check["command"]
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _stamp(check, [Violation(check["id"], check.get("cwd", ""), 0, _t(lang, "run_timeout", cmd=cmd, t=timeout))])
    if r.returncode == 0:
        return []
    tail = "\n".join((r.stdout + r.stderr).decode("utf-8", "replace").strip().splitlines()[-15:])
    return _stamp(check, [Violation(check["id"], check.get("cwd", ""), 0,
                                    _t(lang, "run_fail", cmd=cmd, code=r.returncode, tail=tail))])


def check_reply(doc: dict, message: str) -> list:
    lang = doc.get("lang", "en")
    out = []
    for c in active(doc, "stop"):
        if c["type"] == "reply_prefix" and message.strip() and not message.lstrip().startswith(c["prefix"]):
            out.extend(_stamp(c, [Violation(c["id"], "", 0, _t(lang, "prefix", p=c["prefix"]))]))
    return out


# ---------------------------------------------------------------------------
# 暂缓清单：欠条，不是豁免（照搬代码宪法的做法）
# ---------------------------------------------------------------------------
def split_baseline(doc: dict, violations: list, today: _dt.date | None = None):
    """返回 (还算失败的, 已登记暂缓的)。到期或比登记时更多 → 重新算违规。"""
    today = today or _dt.date.today()
    lang = doc.get("lang", "en")
    allowed = {}
    for b in doc.get("baseline") or []:
        allowed[(b["check"], b["file"])] = (int(b.get("count", 0)), b.get("until", "1970-01-01"))
    groups: dict = {}
    for v in violations:
        groups.setdefault((v.check, v.file), []).append(v)
    real, held = [], []
    for key, vs in groups.items():
        if key not in allowed:
            real.extend(vs)
            continue
        count, until = allowed[key]
        if _dt.date.fromisoformat(until) < today:
            for v in vs:
                v.msg += f"  ⏰ {_t(lang, 'expired')}"
            real.extend(vs)
        elif len(vs) > count:
            for v in vs:
                v.msg += f"  ({_t(lang, 'over')}: {count} → {len(vs)})"
            real.extend(vs)
        else:
            held.extend(vs)
    return real, held


def make_baseline(violations: list, days: int = 30, today: _dt.date | None = None) -> list:
    until = ((today or _dt.date.today()) + _dt.timedelta(days=days)).isoformat()
    counts: dict = {}
    for v in violations:
        counts[(v.check, v.file)] = counts.get((v.check, v.file), 0) + 1
    return [{"check": c, "file": f, "count": n, "until": until} for (c, f), n in sorted(counts.items())]


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def format_violations(vs: list, lang: str = "en", limit: int = 20) -> str:
    lines = []
    for v in vs[:limit]:
        lines.append(f"- {v}")
        if v.rule:
            lines.append(f"    {_t(lang, 'rule')}: {v.rule}  ({_t(lang, 'from')}: {v.source})")
    if len(vs) > limit:
        lines.append(f"- … +{len(vs) - limit}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude Code hook 入口
# ---------------------------------------------------------------------------
SELF_PATHS = (RULES_DIR + "/", ".claude/settings.json", ".claude/settings.local.json")


def _edit_texts(root: Path, tool: str, ti: dict):
    """推算这次 Write / Edit 之后文件会变成什么样。返回 (改前, 改后) 或 None。"""
    path = Path(ti.get("file_path") or "")
    before = read_text(path) if path.is_file() else ""
    before = before or ""
    if tool == "Write":
        return before, ti.get("content") or ""
    old, new = ti.get("old_string"), ti.get("new_string")
    if tool != "Edit" or old is None or new is None or old not in before:
        return None
    after = before.replace(old, new) if ti.get("replace_all") else before.replace(old, new, 1)
    return before, after


def _rel_to(root: Path, file_path: str) -> str | None:
    try:
        rel = os.path.relpath(Path(file_path).resolve(), root.resolve())
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    return None if rel.startswith("..") else rel


def _stop_guard(root: Path, vs: list) -> bool:
    """同一批违规连续拦了 STOP_RETRIES 次就放行。返回 True = 这次还要拦。"""
    sig = hashlib.sha1("|".join(sorted(str(v) for v in vs)).encode()).hexdigest()[:16]
    p = root / RULES_DIR / STATE_FILE
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    n = state.get("count", 0) + 1 if state.get("sig") == sig else 1
    try:
        p.write_text(json.dumps({"sig": sig, "count": n}), encoding="utf-8")
    except OSError:
        pass
    return n <= STOP_RETRIES


def hook(data: dict, engine_dir: Path | None = None):
    """返回 (exit code, stderr 文本, stdout 文本)。拆出来是为了能直接测。"""
    event = data.get("hook_event_name", "")
    root = find_root(data.get("cwd") or os.getcwd())
    if root is None and engine_dir is not None and rules_path(engine_dir.parent).is_file():
        root = engine_dir.parent
    if root is None:
        return 0, "", ""
    doc = load_rules(root)
    lang = doc.get("lang", "en")
    tool, ti = data.get("tool_name", ""), data.get("tool_input") or {}

    if event == "PreToolUse" and tool in ("Write", "Edit"):
        rel = _rel_to(root, ti.get("file_path") or "")
        if rel is None:
            return 0, "", ""
        if any(rel == p or rel.startswith(p) for p in SELF_PATHS):
            return 2, _t(lang, "self"), ""
        texts = _edit_texts(root, tool, ti)
        if texts is None:
            return 0, "", ""
        vs = check_edit(root, doc, rel, *texts)
        if vs:
            return 2, "\n".join([_t(lang, "block_edit"), format_violations(vs, lang), _t(lang, "how")]), ""
        return 0, "", ""

    if event == "PreToolUse" and tool in ("Bash", "PowerShell"):
        vs = check_bash(doc, ti.get("command") or "")
        if vs:
            return 2, "\n".join([_t(lang, "block_bash"), format_violations(vs, lang), _t(lang, "how")]), ""
        return 0, "", ""

    if event == "Stop":
        vs = check_reply(doc, data.get("last_assistant_message") or "")
        real, _held = split_baseline(doc, check_project(root, doc, only=changed_files(root)))
        vs.extend(real)
        for c in active(doc, "stop"):
            if c["type"] == "run_command":
                vs.extend(run_check(root, c, lang))
        if not vs:
            _stop_guard(root, [])
            return 0, "", ""
        if _stop_guard(root, vs):
            return 2, "\n".join([_t(lang, "block_stop"), format_violations(vs, lang), _t(lang, "how")]), ""
        return 0, "", json.dumps({"systemMessage": _t(lang, "gave_up", n=STOP_RETRIES)}, ensure_ascii=False)

    return 0, "", ""


def _main_hook() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        code, err, out = hook(data, Path(__file__).resolve().parent)
    except Exception as e:      # noqa: BLE001 —— hook 自己坏了要显式报出来，但不能拦住用户干活
        print(f"rule-teeth hook error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


def _main_check(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="engine.py check")
    ap.add_argument("root", nargs="?", default=None)
    ap.add_argument("--run", action="store_true", help="也跑规则要求的命令 / also run commands the rules require")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else (find_root(os.getcwd()) or Path(__file__).resolve().parent.parent)
    doc = load_rules(root)
    lang = doc.get("lang", "en")
    real, held = split_baseline(doc, check_project(root, doc))
    if a.run:
        for c in active(doc, "stop"):
            if c["type"] == "run_command":
                real.extend(run_check(root, c, lang))
    print(format_violations(real, lang, limit=200) if real else _t(lang, "none"))
    if held:
        print(f"\n{_t(lang, 'held')}: {len(held)}")
    print("\n" + _t(lang, "total", n=len(real)))
    return 1 if real else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    if cmd == "hook":
        return _main_hook()
    if cmd == "check":
        return _main_check(argv[1:])
    if cmd in ("-V", "--version", "version"):
        print(VERSION)
        return 0
    print("usage: engine.py [check [ROOT] [--run] | hook | version]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
