# -*- coding: utf-8 -*-
"""找规则文件，把它们切成「可能是规则」的片段。

只做结构切分，不判断是不是规则（那是 classify.py 的事）。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import engine
from .model import Candidate, RuleFile

# Claude Code 每个 session 自动加载的项目级文件（官方文档 memory 页「How CLAUDE.md files load」）
ROOT_CLAUDE = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
ROOT_AGENTS = ("AGENTS.md", ".claude/AGENTS.md")
# 其它 AI 编程工具的规则文件：Claude 不自动读，但同样是「写给 AI 的规则」
OTHER_TOOLS = (".cursorrules", ".windsurfrules", "GEMINI.md", ".github/copilot-instructions.md")
RULE_DOC_NAME = re.compile(r"宪法|规则|规范|守则|约定|rules?\b|guidelines?|conventions?|constitution|polic(?:y|ies)|standards?",
                           re.I)
DISCOVERY_SKIP = (engine.SKIP_DIRS - {".claude"}) | {"worktrees"}

_FENCE = re.compile(r"^\s*(```+|~~~+)")
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)、])\s+(.*)$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_IMPORT = re.compile(r"(?<![`\w@])@((?:[^\s`\\]|\\ )+)")
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def clean(s: str) -> str:
    """去掉粗体标记和多余空白，保留反引号（反引号里的东西是抽检查参数的依据）。"""
    s = s.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", s).strip()


def frontmatter(text: str):
    """返回 (frontmatter 行数, paths 列表)。只认 .claude/rules 用到的 paths 字段。"""
    if not text.startswith("---\n"):
        return 0, ()
    end = text.find("\n---", 4)
    if end < 0:
        return 0, ()
    block = text[4:end]
    paths = re.findall(r"^\s*-\s*[\"']?([^\"'\n]+?)[\"']?\s*$", block, re.M) if "paths" in block else []
    return block.count("\n") + 3, tuple(paths)


def split_candidates(rel: str, text: str) -> list:
    lines = text.splitlines()
    fm_end, _ = frontmatter(text)
    out: list = []
    section: list = []
    cur = None                    # [kind, 起始行, [文字]]
    fence = None
    header = ""                   # 当前表格的表头
    last_heading = -1             # 最近一个标题在 out 里的下标
    detail_open = False           # 标题后面还没遇到列表 / 表格
    for idx, raw in enumerate(lines, 1):
        if idx <= fm_end:
            continue
        f = _FENCE.match(raw)
        if fence:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
                fence = None
            continue
        if f:
            cur = _flush(out, cur, rel, section, last_heading if detail_open else -1)
            fence = f.group(1)
            continue
        q = _QUOTE.match(raw)
        line = q.group(1) if q else raw
        if not line.strip():
            cur = _flush(out, cur, rel, section, last_heading if detail_open else -1)
            continue
        h = None if q else _HEAD.match(line)
        if h:
            cur = _flush(out, cur, rel, section, -1)
            level = len(h.group(1))
            section = section[:level - 1] + [""] * max(0, level - 1 - len(section))
            title = clean(h.group(2))
            out.append(Candidate(rel, idx, "heading", title, tuple(s for s in section if s)))
            section.append(title)
            last_heading, detail_open = len(out) - 1, True
            continue
        if _TABLE.match(line):
            cur = _flush(out, cur, rel, section, -1)
            detail_open = False
            nxt = lines[idx] if idx < len(lines) else ""
            cells = [clean(c) for c in line.strip().strip("|").split("|")]
            if _TABLE_SEP.match(line):
                continue          # 分隔行
            if _TABLE_SEP.match(nxt):
                header = " | ".join(cells)          # 表头不是规则，但它说明了下面每行是什么
                continue
            out.append(Candidate(rel, idx, "table", " | ".join(c for c in cells if c), tuple(section),
                                 detail=header))
            continue
        b = _BULLET.match(line)
        if b:
            cur = _flush(out, cur, rel, section, -1)
            detail_open = False
            cur = ["bullet", idx, [b.group(2)]]
            continue
        if cur is not None:
            cur[2].append(line.strip())
            continue
        cur = ["quote" if q else "para", idx, [line.strip()]]
    _flush(out, cur, rel, section, last_heading if detail_open else -1)
    _attach_details(out)
    return out


def _flush(out: list, cur, rel: str, section: list, under: int):
    if cur:
        kind, start, parts = cur
        text = clean(" ".join(parts))
        if text:
            out.append(Candidate(rel, start, kind, text, tuple(section),
                                 under=under if kind in ("para", "quote") else -1))
    return None


def _attach_details(cands: list):
    for c in cands:
        if c.under >= 0:
            h = cands[c.under]
            h.detail = (h.detail + " " + c.text).strip()[:1200]


# ---------------------------------------------------------------------------
# 找规则文件
# ---------------------------------------------------------------------------
def _read(root: Path, rel: str) -> str | None:
    return engine.read_text(root / rel)


def imports_of(text: str) -> list:
    """CLAUDE.md 里的 @path 导入（跳过代码块和反引号，和 Claude Code 的规则一致）。"""
    body = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1", "", text)
    body = _CODE_SPAN.sub("", body)
    out = []
    for m in _IMPORT.finditer(body):
        p = m.group(1).replace("\\ ", " ").rstrip(".,;:)）。，")
        if "/" in p or "." in p:
            out.append(p)
    return out


def referenced_paths(text: str) -> list:
    """规则文件里提到的路径：markdown 链接、反引号里像路径的东西。"""
    found = []
    for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)\)", text):
        found.append(m.group(1))
    for m in re.finditer(r"`([^`\n]+)`", text):
        found.append(m.group(1).strip())
    out = []
    for p in found:
        p = p.split("#")[0].strip()
        if not p or re.match(r"^[a-z]+://|^mailto:", p, re.I) or " " in p.strip("/"):
            continue
        if re.search(r"[*?<>{}$|]", p):
            continue
        if "/" in p or re.search(r"\.(md|mdc|py|js|ts|tsx|json|ya?ml|toml|sh|command|html|txt)$", p):
            out.append(p)
    return out


def discover(root, include=()) -> list:
    """项目里所有规则文件。顺序：自动加载的在前。"""
    root = Path(root).resolve()
    files = [f for f in engine.list_files(root, skip=DISCOVERY_SKIP) if not f.startswith(".claude/worktrees/")]
    fileset = set(files)
    out: dict = {}

    def add(rel, auto, by="", scope=()):
        if rel in out or rel not in fileset:
            return
        text = _read(root, rel)
        if text is not None:
            out[rel] = RuleFile(rel, text, auto, by, scope)

    has_claude = any(f in fileset for f in ROOT_CLAUDE)
    for f in ROOT_CLAUDE:
        add(f, True)
    for f in ROOT_AGENTS:
        add(f, not has_claude)
    for f in files:
        if f.startswith(".claude/rules/") and f.endswith(".md"):
            text = _read(root, f) or ""
            _, scope = frontmatter(text)
            add(f, not scope, scope=scope)
    _follow_imports(root, out, fileset)
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md") or f in OTHER_TOOLS \
                or (f.startswith(".cursor/rules/") and f.endswith((".md", ".mdc"))):
            add(f, False)
    for rf in list(out.values()):
        for p in referenced_paths(rf.text):
            rel = _resolve(root, rf.path, p)
            if rel and rel.endswith(".md") and RULE_DOC_NAME.search(rel.rsplit("/", 1)[-1]):
                add(rel, False, by=rf.path)
    for p in include:
        rel = _resolve(root, "", p)
        if rel:
            add(rel, False, by="--include")
    return list(out.values())


def _follow_imports(root: Path, out: dict, fileset: set):
    queue = [rf for rf in out.values() if rf.auto_loaded]
    depth = 0
    while queue and depth < 4:           # 官方：最多递归四层
        nxt = []
        for rf in queue:
            for p in imports_of(rf.text):
                rel = _resolve(root, rf.path, p)
                if rel and rel in fileset and rel not in out:
                    text = _read(root, rel)
                    if text is not None:
                        out[rel] = RuleFile(rel, text, True, rf.path)
                        nxt.append(out[rel])
        queue, depth = nxt, depth + 1


def _resolve(root: Path, from_rel: str, p: str) -> str | None:
    """把规则文件里写的路径解析成项目内相对路径；指到项目外返回 None。"""
    if p.startswith("~") or p.startswith("/"):
        return None
    base = (root / from_rel).parent if from_rel else root
    for cand in (base / p, root / p):
        try:
            rel = cand.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if (root / rel).exists():
            return rel
    return None
