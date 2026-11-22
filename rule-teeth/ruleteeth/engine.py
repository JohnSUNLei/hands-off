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

另外它会算「每次必读链」：每个 session 开工前 AI 固定要读的规则（根 CLAUDE.md、@导入、不带 paths 的
.claude/rules、以及规则里写着「每个 session 都读」的文件）。这是规则的固定成本，load_budget 检查管它的上限。

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

VERSION = "0.3.0"
RULES_DIR = ".rule-teeth"
RULES_FILE = "rules.json"
STATE_FILE = "state.json"          # Stop 防死循环的小状态，不进 git
MAX_FILE_BYTES = 1_000_000          # 更大的文件不扫（多半是生成物）
STOP_RETRIES = 2                    # 同一批违规连拦几次后放行（并明说没解决）

CHECK_TYPES = ("max_lines", "forbid_pattern", "no_secrets", "protected_path",
               "forbid_command", "run_command", "reply_prefix", "load_budget", "forbid_tool_input")
# 每种检查在哪个时机生效。写死在这里，不让每条检查各配各的 —— 少一个旋钮就少一种配错。
EVENTS = {
    "max_lines": ("edit", "scan"),
    "forbid_pattern": ("edit", "scan"),
    "no_secrets": ("edit", "scan"),
    "protected_path": ("edit",),
    "forbid_command": ("bash",),
    "run_command": ("stop",),
    "reply_prefix": ("stop",),
    "load_budget": ("edit", "stop"),      # 管的是整条「每次必读链」，不是单个文件
    "forbid_tool_input": ("tool",),       # MCP / 其它工具的入参（Write / Edit / Bash 之外的工具）
}
# forbid_tool_input 的 sql_deny 能认的类别。写死在这里：README、报错信息、测试都从这一处来。
SQL_CATEGORIES = ("truncate", "drop_table", "drop_schema", "drop_database", "delete_no_where", "update_no_where")
NORMALIZERS = ("sql",)

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
        "block_tool": "rule-teeth 拦下了这次工具调用 —— 项目规则不许这么做：",
        "block_stop": "rule-teeth：先别结束，下面这些规则还没满足：",
        "self": "这是 rule-teeth 自己的检查清单 / hook 配置。AI 不能自己给自己松绑 —— 要改请让用户亲手改。",
        "how": "怎么办：按规则改好再试。如果你认为这条规则在这里确实不适用，停下来告诉用户，由用户决定改不改规则。",
        "how_tool": "怎么办：改成不触发规则的做法再试（比如给 DELETE / UPDATE 加上 WHERE）。确实要这样做的话，停下来告诉用户，"
                    "由用户亲手执行或决定改规则 —— 不要换个写法去绕。",
        "tool_match": "{tool} 的参数里有 `{m}`",
        "sql_found": "{tool} 的 SQL 里有：{what}",
        "sql_truncate": "TRUNCATE（清空整张表）",
        "sql_drop_table": "DROP TABLE（删表）",
        "sql_drop_schema": "DROP SCHEMA（删整个 schema）",
        "sql_drop_database": "DROP DATABASE（删整个数据库）",
        "sql_delete_no_where": "DELETE 没有 WHERE（会删光整张表）",
        "sql_update_no_where": "UPDATE 没有 WHERE（会改光整张表）",
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
        "budget": "每个 session 固定要读的规则约 {n:,} token，超过预算 {limit:,}：{parts}",
        "budget_grows": "（本来就超了，这次还在变多：{before:,} → {n:,}）",
        "budget_fix": "省法：只服务某个子项目的规则，搬进它自己的 CLAUDE.md 或带 paths: 的 .claude/rules/*.md；"
                      "已经装了牙的规则，根里只留一行指针；「每个 session 都读 X」改成「做 Y 时读 X」；变更记录、历史移出加载链。",
        "how_root": "根文件",
        "how_import": "@导入",
        "how_rules": "不带 paths 的 .claude/rules",
        "how_soft": "规则里写着每次都读",
        "how_user": "用户级 ~/.claude",
        "how_ancestor": "上级目录",
        "how_memory": "自动记忆 MEMORY.md",
    },
    "en": {
        "block_edit": "rule-teeth blocked this edit — it would break a rule written in your project's rule file:",
        "block_bash": "rule-teeth blocked this command — the project's rules forbid it:",
        "block_tool": "rule-teeth blocked this tool call — the project's rules forbid it:",
        "block_stop": "rule-teeth: don't stop yet — these rules are not satisfied:",
        "self": "This is rule-teeth's own check list / hook config. The AI may not loosen its own leash — ask the user to edit it by hand.",
        "how": "What to do: fix it so the rule holds, then retry. If you believe the rule truly does not apply here, stop and tell the user; the user decides whether to change the rule.",
        "how_tool": "What to do: change it so the rule holds, then retry (e.g. give DELETE / UPDATE a WHERE). If it truly has to be done "
                    "this way, stop and tell the user; the user runs it by hand or changes the rule — do not reword it to get around the rule.",
        "tool_match": "the input of {tool} contains `{m}`",
        "sql_found": "the SQL sent to {tool} contains: {what}",
        "sql_truncate": "TRUNCATE (empties a whole table)",
        "sql_drop_table": "DROP TABLE",
        "sql_drop_schema": "DROP SCHEMA",
        "sql_drop_database": "DROP DATABASE",
        "sql_delete_no_where": "DELETE without WHERE (deletes every row)",
        "sql_update_no_where": "UPDATE without WHERE (changes every row)",
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
        "budget": "rules read at the start of every session total ~{n:,} tokens, over the {limit:,} budget: {parts}",
        "budget_grows": " (already over, and this edit adds more: {before:,} → {n:,})",
        "budget_fix": "How to cut: move rules that serve one subproject into its own CLAUDE.md or a .claude/rules/*.md "
                      "with paths:; once a rule has teeth, keep only a one-line pointer at the root; turn \"read X every "
                      "session\" into \"read X when doing Y\"; move changelogs and history out of the load chain.",
        "how_root": "root file",
        "how_import": "@import",
        "how_rules": ".claude/rules without paths",
        "how_soft": "told to read every session",
        "how_user": "user-level ~/.claude",
        "how_ancestor": "parent directory",
        "how_memory": "auto memory MEMORY.md",
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
        where = (f"{self.file}:{self.line}" if self.line else self.file) if self.file else "-"
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
# 看 MCP 工具的入参（forbid_tool_input）
#
# Write / Edit / Bash 之外的工具（MCP 工具）也会改东西，hook 的 matcher 只要写上它们的名字，这里就能看入参。
# 规则要的是「这个工具的这个入参里出现了什么」，不是「这条命令长什么样」，所以单独一种类型。
# SQL 是最常见的一种：正则直接扫 SQL 原文会被注释、字符串、子查询里的 WHERE 骗，
# 所以先把 SQL 拆成「一条条语句」（sql_units），再按类别判断（sql_findings）。
# ---------------------------------------------------------------------------
_DOLLAR_TAG = re.compile(r"\$(?:[^\W\d]\w*)?\$")          # $$ 或 $tag$（tag 不能以数字开头）
_SQL_QUICK = re.compile(r"truncate|drop|delete|update", re.I)   # 一个相关词都没有就不用拆
_SQL_DEPTH = 32                                           # 美元引号嵌套的递归上限（再深的写法不会是正常 SQL）


def _word(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def sql_units(text: str, _depth: int = 0) -> list:
    """把一段 SQL 拆成一条条「语句」，每条是去掉注释、字符串内容、括号里内容之后的纯文字。

    - `--` 行注释、`/* */` 块注释（可嵌套）→ 一个空格；`DELETE/**/FROM` 也认得出来。
    - `'...'`（含 `''`、`E'..\\'..'`）、`"..."` 标识符 → 内容换成 x，里面的关键字、分号、WHERE 都不算数。
    - `$$...$$` / `$tag$...$tag$`：DO 块和函数体是会执行的代码，所以里面的内容当成另一段 SQL 递归拆开。
    - `(...)` 里的内容单独成一条语句，外层只留 `()`。这样「子查询里的 WHERE」不会被算成外层语句的 WHERE，
      `WITH d AS (DELETE FROM t RETURNING *)` 里的 DELETE 也不会被藏起来。
    - `;` 分句。
    """
    out: list = []
    frames: list = [[]]                       # 每进一层括号压一层；当前层里是正在读的这条语句的字符
    n, i = len(text), 0

    def flush():
        s = " ".join("".join(frames[-1]).split())      # 空白压成单个空格：后面的正则不会被超长空白拖成二次方
        if s:
            out.append(s)
        frames[-1] = []

    while i < n:
        ch = text[i]
        cur = frames[-1]
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            cur.append(" ")
        elif text.startswith("/*", i):
            level, i = 1, i + 2
            while i < n and level:
                if text.startswith("/*", i):
                    level, i = level + 1, i + 2
                elif text.startswith("*/", i):
                    level, i = level - 1, i + 2
                else:
                    i += 1
            cur.append(" ")
        elif ch == "'":
            esc = bool(cur) and cur[-1] in "Ee" and (len(cur) == 1 or not _word(cur[-2]))     # E'..' 里反斜杠是转义
            j = i + 1
            while j < n:
                if esc and text[j] == "\\":
                    j += 2
                elif text.startswith("''", j):
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            i = min(j + 1, n)
            cur.append("'x'")
        elif ch == '"':
            j = i + 1
            while j < n:
                if text.startswith('""', j):
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            i = min(j + 1, n)
            cur.append('"x"')
        elif ch == "$" and not (cur and _word(cur[-1])) and _DOLLAR_TAG.match(text, i):
            tag = _DOLLAR_TAG.match(text, i).group(0)
            end = text.find(tag, i + len(tag))
            body = text[i + len(tag): n if end < 0 else end]
            if _depth < _SQL_DEPTH:
                out.extend(sql_units(body, _depth + 1))
            i = n if end < 0 else end + len(tag)
            cur.append("'x'")
        elif ch == "(":
            cur.append("()")
            frames.append([])
            i += 1
        elif ch == ")":
            if len(frames) > 1:
                flush()
                frames.pop()
            i += 1
        elif ch == ";":
            flush()
            i += 1
        else:
            cur.append(ch)
            i += 1
    while frames:
        flush()
        frames.pop()
    return out


_IDENT = r'(?:"x"|[\w$]+)(?: ?\. ?(?:"x"|[\w$]+))*'            # schema.table / "x"."y"（空白已压成单个空格）
_UPDATE_HEAD = rf"\bUPDATE (?:ONLY )?(?!SET\b){_IDENT} ?\*? (?:(?:AS )?(?!SET\b)(?:\"x\"|\w+) )?SET\b"
_WHERE = re.compile(r"\bWHERE\b", re.I)
_TAUTOLOGY = re.compile(r"\bWHERE (?:TRUE|1 ?= ?1)(?: RETURNING\b.*)?$", re.I)   # WHERE true / WHERE 1=1：形式上有 WHERE，实际是整张表
# 简单类：这个词组出现就算。TRUNCATE 作权限名、触发器事件时不是在清表：GRANT / REVOKE / COMMENT / CREATE TRIGGER 开头的语句不算
_SQL_SIMPLE = {
    "truncate": re.compile(r"^(?!(?:GRANT|REVOKE|COMMENT|ALTER DEFAULT PRIVILEGES|"
                           r"CREATE (?:OR REPLACE )?(?:CONSTRAINT )?TRIGGER)\b).*?\bTRUNCATE\b(?! ?\()", re.I),
    "drop_table": re.compile(r"\bDROP TABLE\b", re.I),
    "drop_schema": re.compile(r"\bDROP SCHEMA\b", re.I),
    "drop_database": re.compile(r"\bDROP DATABASE\b", re.I),
}
# 「没有 WHERE」类：只看这条语句里最后一个 DELETE FROM / UPDATE t SET 之后有没有 WHERE（前面的那些后面的 WHERE 只会更多），线性时间。
# UPDATE t SET …：`ON CONFLICT DO UPDATE SET`、`ON UPDATE CASCADE`、`FOR UPDATE`、`GRANT UPDATE ON` 都不是
_SQL_WRITE = {
    "delete_no_where": re.compile(r"\bDELETE FROM\b", re.I),
    "update_no_where": re.compile(_UPDATE_HEAD, re.I),
}


def _writes_without_where(head: "re.Pattern", unit: str) -> bool:
    last = None
    for last in head.finditer(unit):
        pass
    if last is None:
        return False
    tail = unit[last.end():]
    return not _WHERE.search(tail) or bool(_TAUTOLOGY.search(tail))


def sql_findings(text: str) -> list:
    """这段 SQL 里有哪几类破坏性写法（SQL_CATEGORIES 里的名字，去重，按固定顺序）。只看语句本身，不执行、不连数据库。"""
    if not _SQL_QUICK.search(text):
        return []
    found = set()
    for unit in sql_units(text):
        for cat, rx in _SQL_SIMPLE.items():
            if cat not in found and rx.search(unit):
                found.add(cat)
        for cat, head in _SQL_WRITE.items():
            if cat not in found and _writes_without_where(head, unit):
                found.add(cat)
    return [c for c in SQL_CATEGORIES if c in found]


def _tool_strings(ti: dict, field: str | None = None) -> list:
    """入参里要扫的字符串。给了 field 且它在入参里 → 只扫它；
    没给，或者 field 不在入参里（工具改了字段名）→ 扫全部字符串值，宁可多看也不要悄悄失效。"""
    def walk(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)
    return list(walk(ti[field] if field and field in ti else ti))


def check_tool(doc: dict, tool: str, ti: dict) -> list:
    """forbid_tool_input：工具名命中某条启用规则的 tools（fnmatch，区分大小写），就扫它的入参。
    消息里不回显 SQL 原文：只说是哪一类（入参可能带用户数据）。patterns 命中才带命中片段（最多 60 字）。"""
    lang = doc.get("lang", "en")
    out = []
    for c in active(doc, "tool"):
        if not any(fnmatch.fnmatchcase(tool, p) for p in c.get("tools") or []):
            continue
        deny = set(c.get("sql_deny") or ())
        cats, frags = set(), []
        for text in _tool_strings(ti, c.get("field")):
            if deny:
                cats.update(x for x in sql_findings(text) if x in deny)
            if c.get("patterns"):
                for unit in (sql_units(text) if c.get("normalize") == "sql" else [text]):
                    for rx in _compiled(c):
                        m = rx.search(unit)
                        if m and m.group(0).strip()[:60] not in frags:
                            frags.append(m.group(0).strip()[:60])
        msgs = []
        if cats:
            what = ("；" if lang == "zh" else "; ").join(_t(lang, "sql_" + x) for x in SQL_CATEGORIES if x in cats)
            msgs.append(_t(lang, "sql_found", tool=tool, what=what))
        msgs.extend(_t(lang, "tool_match", tool=tool, m=f) for f in frags)
        if msgs:
            out.extend(_stamp(c, [Violation(c["id"], tool, 0, " / ".join(msgs))]))
    return out


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
        if c["type"] == "forbid_tool_input":
            _validate_tool_rule(p, c)
    return doc


def _validate_tool_rule(p, c: dict) -> None:
    """forbid_tool_input 写错了要在加载时就报出来 —— 否则它会悄悄什么都不拦。"""
    cid = c.get("id")
    tools = c.get("tools")
    if not (isinstance(tools, list) and tools and all(isinstance(x, str) and x for x in tools)):
        raise ValueError(f"{p}: {cid} 的 tools 要是非空的工具名列表 / 'tools' must be a non-empty list of tool names")
    deny, pats = c.get("sql_deny") or [], c.get("patterns") or []
    if not deny and not pats:
        raise ValueError(f"{p}: {cid} 至少要有 patterns 或 sql_deny / needs 'patterns' or 'sql_deny'")
    bad = [x for x in deny if x not in SQL_CATEGORIES]
    if bad:
        raise ValueError(f"{p}: {cid} 的 sql_deny 有不认识的类别 / unknown sql_deny {bad!r}；可用 / allowed: {', '.join(SQL_CATEGORIES)}")
    if c.get("normalize") not in (None, *NORMALIZERS):
        raise ValueError(f"{p}: {cid} 的 normalize 只能是 / must be one of {NORMALIZERS}")
    if c.get("field") is not None and not isinstance(c["field"], str):
        raise ValueError(f"{p}: {cid} 的 field 要是字符串 / 'field' must be a string")
    for rx in pats:
        try:
            re.compile(rx)
        except re.error as e:
            raise ValueError(f"{p}: {cid} 的正则编译不过 / bad regex {rx!r}: {e}") from None


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
        if c["type"] == "load_budget":
            continue                              # 整条链的事，下面单独算
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
    out.extend(check_budget_edit(root, doc, Path(root) / rel, before, after))
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
# 每次必读链：规则的固定成本（load_budget）
#
# Claude Code 每个 session 开工就加载的：根 CLAUDE.md / .claude/CLAUDE.md / CLAUDE.local.md（没有 CLAUDE.md 时是
# AGENTS.md）、它们的 @导入（最多四层）、不带 paths: 的 .claude/rules/**/*.md。官方文档：memory 页。
# 还有一种文档里看不出来的：规则里写着「每个 session 都读 X」—— AI 照做，X 也就每次都读了。这里叫「软导入」，
# 一样算进固定成本。带 paths: 的 rules、子目录的 CLAUDE.md 是碰到相关文件才加载，不算。
# ---------------------------------------------------------------------------
ROOT_CLAUDE = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
ROOT_AGENTS = ("AGENTS.md", ".claude/AGENTS.md")
IMPORT_DEPTH = 4                     # 官方：@导入最多递归四层；软导入沿用同一上限
MEMORY_LINES, MEMORY_BYTES = 200, 25_000   # 官方：MEMORY.md 开工只读前 200 行或 25KB
CJK = re.compile(r"[㐀-鿿豈-﫿　-〿＀-￯]")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)、])\s+")
_LABEL_RE = re.compile(r"^\*\*[^*\n]{1,40}[:：]\s*\*\*")      # 「**按需读：**」这种加粗小标签，自成一段
_IMPORT_RE = re.compile(r"(?<![`\w@])@((?:[^\s`\\]|\\ )+)")
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
# 「每个 session 都读」这类话。标题命中 → 整节都算；正文命中 → 这一段算。
SOFT_HEAD = re.compile(r"每个\s*session|每次\s*(?:session|会话|对话|开工)|新\s*session|session\s*(?:开始|启动|接管)|"
                       r"接管\s*checklist|开工必读|必读(?![链预])|\b(?:every|each|new) session\b|\bsession start\b|"
                       r"\bbefore (?:every|each|any) (?:session|task)\b|\balways read\b|\bmust[- ]read\b", re.I)
SOFT_LINE = re.compile(r"每个\s*session\s*都?\s*(?:要|必须|先)?\s*读(?!的)|每次(?:都|必须|先)\s*读(?!的)|必读(?![链预])|"
                       r"新\s*session\s*(?:都|必须|要)?\s*先\s*(?:读|看)|session\s*(?:开始|启动)(?:时|前|后)?[，,]?\s*(?:先|必须)?\s*读|"
                       r"(?:工作|开工|开始|动手|干活)前[，,]?\s*(?:先|必须)?\s*读|先读这份|"
                       r"\balways read\b|\bmust[- ]read\b|\bat the start of (?:every|each) (?:session|task)\b|"
                       r"\b(?:every|each) session\b[^.\n]{0,40}\bread\b|\bread\b[^.\n]{0,60}\bbefore (?:every|each|any) (?:session|task)\b",
                       re.I)
# 说的是「按需读」「不用每次读」，或者在讲预算本身 —— 不是软导入
SOFT_SKIP = re.compile(r"不再|不用|不必|无需|按需|需要时|用到时|才读|才看|如适用|若适用|拿不准|预算|"
                       r"同步规则|同步到|写进|写入|记进|记到|落盘|更新到|\bsync(?:ed)? to\b|\bwrite (?:it )?(?:to|into)\b|"
                       r"\bappend to\b|"
                       r"\bwhen (?:needed|relevant|you need)\b|\bon demand\b|\bonly (?:when|if)\b|"
                       r"\bif (?:needed|relevant|applicable)\b|\bnot every\b|\bno need\b|\bdon'?t need\b|\boptional\b|"
                       r"\bbudget\b", re.I)
_CLAUSE_RE = re.compile(r"[。；;！!？?]|\s+—+\s+|——|(?<=\S)\.\s+")
_SOFT_PATH = re.compile(r"`([^`\n]+)`|\]\(([^)\s]+)\)|(?<![\w`(/])(~/[^\s`)）]+)")


@dataclass
class Loaded:
    """每次必读链上的一个文件。"""
    path: str           # 项目内是相对路径；项目外显示成 ~/… 或绝对路径
    how: str            # root / import / rules / soft；machine=True 时还有 user / ancestor / memory
    by: str             # 被谁带进来的（软导入、@导入才有）
    tokens: int
    nbytes: int
    outside: bool       # 在项目外（换台电脑可能就没有）


def estimate_tokens(text: str) -> int:
    """粗估：中日韩字符约 1 token / 字，其它约 1 token / 3.5 字符。只用来比大小，不是计费。"""
    cjk = len(CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.5)


def frontmatter(text: str):
    """返回 (frontmatter 行数, paths 列表)。只认 .claude/rules 用到的 paths 字段（列表或逗号分隔都认）。"""
    if not text.startswith("---\n"):
        return 0, ()
    end = text.find("\n---", 4)
    if end < 0:
        return 0, ()
    block = text[4:end]
    paths = []
    if "paths" in block:
        paths = re.findall(r"^\s*-\s*[\"']?([^\"'\n]+?)[\"']?\s*$", block, re.M)
        inline = re.search(r"^\s*paths\s*:\s*(\S.*)$", block, re.M)
        if not paths and inline:
            paths = [x.strip().strip("\"'[] ") for x in inline.group(1).split(",") if x.strip().strip("\"'[] ")]
    return block.count("\n") + 3, tuple(paths)


def imports_of(text: str) -> list:
    """CLAUDE.md 里的 @path 导入（跳过代码块和反引号，和 Claude Code 的规则一致）。"""
    body = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1", "", text)
    body = _CODE_SPAN_RE.sub("", body)
    out = []
    for m in _IMPORT_RE.finditer(body):
        q = m.group(1).replace("\\ ", " ").rstrip(".,;:)）。，")
        if "/" in q or "." in q:
            out.append(q)
    return out


def _units(text: str):
    """把 Markdown 切成「一段话 / 一个列表项 / 一行表格 / 一个标题」：(行号, 文字, 是不是标题, 标题级别)。"""
    out, cur, start, fence = [], [], 0, False

    def flush():
        if cur:
            out.append((start, " ".join(cur), False, 0))
            cur.clear()

    for i, ln in enumerate(text.splitlines(), 1):
        if _FENCE_RE.match(ln):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        h = _HEAD_RE.match(ln)
        body = re.sub(r"^\s*>\s?", "", ln).strip()
        if h:
            flush()
            out.append((i, h.group(2), True, len(h.group(1))))
            continue
        if not body or _ITEM_RE.match(body) or body.startswith("|") or _LABEL_RE.match(body):
            flush()
        if body:
            if not cur:
                start = i
            cur.append(body)
            if body.startswith("|"):
                flush()
    flush()
    return out


_QUOTED = re.compile(r"「[^」]*」|“[^”]*”|\"[^\"\n]*\"|『[^』]*』")


def _says_read(text: str) -> bool:
    """这句话是不是在叫 AI 每次都读（引号里提到这几个字的不算 —— 那是在谈论这句话，不是在下指令）。"""
    return bool(SOFT_LINE.search(_QUOTED.sub("", text)))


def _is_label(body: str) -> bool:
    """一段只有一个小标签（「**新 session 先读：**」「按需读：」）—— 它管下面那几段，不管整节。"""
    plain = body.replace("*", "").strip()
    return len(plain) <= 40 and plain.endswith((":", "："))


def soft_reads(text: str) -> list:
    """规则里写着「每个 session 都读 / 开工前先读」的文件：[(行号, 写的路径)]。
    标题命中 → 整节算，直到同级标题；节里出现「按需读：」这种小标签 → 从那里往下不算；小标签命中 → 从那里往下算。"""
    out, soft_level, head_level = [], 0.0, 0
    for line, body, is_head, level in _units(text):
        if is_head:
            head_level = level
            if soft_level and level <= soft_level:
                soft_level = 0
            if not soft_level and SOFT_HEAD.search(body) and not SOFT_SKIP.search(body):
                soft_level = level
            continue
        if _is_label(body):
            if SOFT_SKIP.search(body):
                soft_level = 0
            elif SOFT_HEAD.search(body) or _says_read(body) or re.search(r"先读|先看|read first", body, re.I):
                soft_level = head_level + 0.5
            continue
        if not (soft_level or _says_read(body)):
            continue
        for clause in _CLAUSE_RE.split(body):           # 「先读 A —— 同步规则：写进 B」：A 算，B 不算
            if SOFT_SKIP.search(clause) or (not soft_level and not _says_read(clause)):
                continue
            for m in _SOFT_PATH.finditer(clause):
                q = (m.group(1) or m.group(2) or m.group(3) or "").split("#")[0].strip()
                if q and not re.search(r"[*?<>{}$|]|^[a-z]+://", q, re.I) and ("/" in q or re.search(r"\.(md|mdc|txt)$", q)):
                    out.append((line, q))
    return out


def _resolve_read(root: Path, from_file: Path, q: str) -> Path | None:
    """软导入 / @导入写的路径 → 磁盘上的文件。先相对写它的文件，再相对项目根；~ 和绝对路径照写。"""
    if q.startswith("~"):
        cands = [Path(q).expanduser()]
    elif q.startswith("/"):
        cands = [Path(q)]
    else:
        cands = [from_file.parent / q, root / q]
    for c in cands:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def shown_path(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + p.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def memory_file(root: Path) -> Path:
    """Claude Code 自动记忆的索引：~/.claude/projects/<项目路径，非字母数字换成 -> /memory/MEMORY.md"""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory" / "MEMORY.md"


def load_chain(root, override: dict | None = None, machine: bool = False) -> list:
    """每次必读链：[Loaded]。override = {绝对路径: 改后的全文}，用来算「这次修改之后」的链。
    machine=True 再加上这台电脑自己的部分（用户级、上级目录、自动记忆）—— 只给体检看，检查不算它：换台电脑就不一样。"""
    root = Path(root).resolve()
    over = {str(Path(k).resolve()): v for k, v in (override or {}).items()}
    seen: dict = {}

    def text_of(p: Path):
        k = str(p)
        if k in over:
            return over[k]
        return read_text(p) if p.is_file() else None

    def add(p: Path, how: str, by: str = "", depth: int = 0, text: str | None = None):
        p = p.resolve()
        if str(p) in seen:
            return
        t = text if text is not None else text_of(p)
        if t is None:
            return
        shown = shown_path(root, p)
        inside = not (shown.startswith("~/") or shown.startswith("/"))
        seen[str(p)] = Loaded(shown, how, by, estimate_tokens(t), len(t.encode("utf-8")), not inside)
        if depth >= IMPORT_DEPTH or how == "memory":
            return
        for q in imports_of(t):
            r = _resolve_read(root, p, q)
            if r is not None:
                add(r, "import", shown, depth + 1)
        for _line, q in soft_reads(t):
            r = _resolve_read(root, p, q)
            if r is not None:
                add(r, "soft", shown, depth + 1)

    if machine:
        home = Path.home() / ".claude"
        add(home / "CLAUDE.md", "user")
        for d in sorted((home / "rules").rglob("*.md")) if (home / "rules").is_dir() else []:
            if not frontmatter(text_of(d) or "")[1]:
                add(d, "user")
        for d in reversed(root.parents):
            for name in ("CLAUDE.md", "CLAUDE.local.md"):
                add(d / name, "ancestor")
    has_claude = any(text_of(root / f) is not None for f in ROOT_CLAUDE)
    for f in ROOT_CLAUDE + (() if has_claude else ROOT_AGENTS):
        add(root / f, "root")
    rules_dir = root / ".claude" / "rules"
    found = set(str(x.resolve()) for x in rules_dir.rglob("*.md")) if rules_dir.is_dir() else set()
    found |= {k for k in over if k.startswith(str(rules_dir.resolve()) + os.sep) and k.endswith(".md")}
    for k in sorted(found):
        t = text_of(Path(k))
        if t is not None and not frontmatter(t)[1]:
            add(Path(k), "rules", text=t)
    if machine:
        mem = memory_file(root)
        t = text_of(mem)
        if t is not None:
            head = "\n".join(t.splitlines()[:MEMORY_LINES]).encode("utf-8")[:MEMORY_BYTES].decode("utf-8", "ignore")
            add(mem, "memory", text=head)
    return list(seen.values())


def chain_tokens(chain: list) -> int:
    return sum(x.tokens for x in chain)


def _budget_parts(chain: list, lang: str) -> str:
    top = sorted(chain, key=lambda x: -x.tokens)[:6]
    parts = [f"{x.path} {x.tokens:,}" + (f"（{_t(lang, 'how_soft')}）" if lang == "zh" and x.how == "soft" else
                                       f" ({_t(lang, 'how_soft')})" if x.how == "soft" else "") for x in top]
    more = len(chain) - len(top)
    return " · ".join(parts) + (f" · … +{more}" if more > 0 else "")


def _entry(root: Path) -> str:
    return next((f for f in ROOT_CLAUDE + ROOT_AGENTS if (root / f).is_file()), "CLAUDE.md")


def check_budgets(root, doc: dict, only=None) -> list:
    """整条每次必读链超没超预算。only 给了（Stop 时这次动过的文件），链上的项目内文件都没动就不算。"""
    root = Path(root).resolve()
    lang = doc.get("lang", "en")
    checks = [c for c in doc.get("checks", []) if c.get("enabled", True) and c["type"] == "load_budget"]
    if not checks:
        return []
    chain = load_chain(root)
    if only is not None:
        touched = set(only)
        if not any(x.path in touched for x in chain if not x.outside) and \
                not any(t.startswith(".claude/rules/") or t in ROOT_CLAUDE for t in touched):
            return []
    total = chain_tokens(chain)
    out = []
    for c in checks:
        lim = int(c.get("max_tokens") or 0)
        if lim and total > lim:
            msg = _t(lang, "budget", n=total, limit=lim, parts=_budget_parts(chain, lang)) + " " + _t(lang, "budget_fix")
            out.extend(_stamp(c, [Violation(c["id"], _entry(root), 0, msg)]))
    return out


def in_chain(root, abs_path) -> bool:
    """这个文件改了会不会改变每次必读链（它在链上，或者它是根规则文件 / .claude/rules 里的文件）。"""
    root = Path(root).resolve()
    p = Path(abs_path).resolve()
    rel = shown_path(root, p)
    if rel in ROOT_CLAUDE + ROOT_AGENTS or (rel.startswith(".claude/rules/") and rel.endswith(".md")):
        return True
    return any(x.path == rel for x in load_chain(root))


def check_budget_edit(root, doc: dict, abs_path, before: str, after: str) -> list:
    """棘轮：链变长、且改完超预算才拦。本来就超的，这次在缩短也放行。"""
    checks = [c for c in active(doc, "edit") if c["type"] == "load_budget"]
    if not checks or not in_chain(root, abs_path):
        return []
    lang = doc.get("lang", "en")
    old = load_chain(root, {str(abs_path): before} if before else None)
    new = load_chain(root, {str(abs_path): after})
    b, n = chain_tokens(old), chain_tokens(new)
    out = []
    for c in checks:
        lim = int(c.get("max_tokens") or 0)
        if lim and n > lim and n > b:
            msg = _t(lang, "budget", n=n, limit=lim, parts=_budget_parts(new, lang))
            if b > lim:
                msg += _t(lang, "budget_grows", before=b, n=n)
            msg += " " + _t(lang, "budget_fix")
            out.extend(_stamp(c, [Violation(c["id"], shown_path(Path(root).resolve(), Path(abs_path).resolve()), 0, msg)]))
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
        if rel is None:                           # 项目外的文件：只有它在每次必读链上（被软导入）才管
            texts = _edit_texts(root, tool, ti)
            vs = check_budget_edit(root, doc, ti["file_path"], *texts) if texts else []
            if vs:
                return 2, "\n".join([_t(lang, "block_edit"), format_violations(vs, lang), _t(lang, "how")]), ""
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

    if event == "PreToolUse" and tool:            # Write / Edit / Bash 之外的工具（MCP）：看入参
        vs = check_tool(doc, tool, ti)
        if vs:
            return 2, "\n".join([_t(lang, "block_tool"), format_violations(vs, lang), _t(lang, "how_tool")]), ""
        return 0, "", ""

    if event == "Stop":
        vs = check_reply(doc, data.get("last_assistant_message") or "")
        changed = changed_files(root)
        real, _held = split_baseline(doc, check_project(root, doc, only=changed) + check_budgets(root, doc, only=changed))
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
    real, held = split_baseline(doc, check_project(root, doc) + check_budgets(root, doc))
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
