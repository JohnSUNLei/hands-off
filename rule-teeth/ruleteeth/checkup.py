# -*- coding: utf-8 -*-
"""规则体检：哪些规则从来没被机器检查过、哪些该下沉、根文件超没超、指针断没断、有没有写了两遍。

只读，不改任何文件。所有结论都是确定性的，能被测试钉住。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import classify, engine, parse
from .model import CHECKABLE, JUDGMENT, VAGUE, Finding, Rule

DEFAULT_MAX_LINES = 200          # Claude Code 官方建议：每个 CLAUDE.md 200 行以内
DEFAULT_MAX_KB = 10              # 本仓库「宪法分层规则」：项目根约 10KB
DEFAULT_BUDGET = 4000            # 每次必读链没写预算时，按「200 行」折算的默认参考线（中文约 4,000 token）
CHECK_FILE = re.compile(
    r"(^|/)(tests?|spec|__tests__|e2e)/|(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|"
    r"^\.github/workflows/|^\.husky/|(^|/)\.pre-commit-config\.ya?ml$|(^|/)lefthook\.ya?ml$|"
    r"^\.claude/settings(\.local)?\.json$|(^|/)\.?eslintrc|(^|/)eslint\.config\.|(^|/)ruff\.toml$")
HOOK_FILE = re.compile(r"^\.github/workflows/|^\.husky/|\.pre-commit-config|lefthook|^\.claude/settings")
GENERIC_DIRS = {"docs", "doc", "agents", "scripts", "script", "tests", "test", "assets", "public", "static",
                "src", "lib", "examples", "example", "tools", "bin", ".github", ".claude", "config", "fixtures"}
MONOREPO_DIRS = ("packages", "apps", "crates", "libs", "services", "plugins", "modules")
MANIFESTS = ("package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml", "README.md", "CLAUDE.md",
             "AGENTS.md")
CJK = engine.CJK


@dataclass
class Checkup:
    root: Path
    lang: str
    files: list                          # RuleFile
    rules: list                          # Rule
    info: dict                           # 规则文件 → 不是规则的片段数
    sizes: list                          # [(RuleFile, bytes, lines, chars, tokens)]
    limits: dict
    subprojects: list
    findings: list = field(default_factory=list)
    sink: list = field(default_factory=list)          # [(Rule, 说明, 建议去处)]
    dead: list = field(default_factory=list)          # [(规则文件, 路径)]
    external: list = field(default_factory=list)      # [(规则文件, 路径, 本机在不在)]
    duplicates: list = field(default_factory=list)    # [(Rule, Rule, 相似度)]
    coverage: list = field(default_factory=list)      # [(规则文件, 条数, 有检查的子项目, 没检查的子项目, 语言)]
    current: list = field(default_factory=list)       # 按规则原文扫一遍，现在就不符合的地方（Violation）
    teeth: dict = field(default_factory=dict)         # 已装的 rule-teeth：source → 检查编号
    single: bool = False                              # 只看了一份规则文件，没扫它的仓库
    chain: list = field(default_factory=list)         # 每次必读链（engine.Loaded），项目自己能管的部分
    machine: list = field(default_factory=list)       # 这台电脑另加的：用户级、上级目录、自动记忆
    budget: int = DEFAULT_BUDGET

    def by(self, category: str) -> list:
        return [r for r in self.rules if r.category == category]

    @property
    def unchecked(self) -> list:
        return [r for r in self.rules if not r.checked_by]


estimate_tokens = engine.estimate_tokens


def detect_lang(texts: list) -> str:
    joined = "".join(texts)[:20000]
    return "zh" if joined and len(CJK.findall(joined)) / max(1, len(joined)) > 0.08 else "en"


# ---------------------------------------------------------------------------
# 已有的机器检查
# ---------------------------------------------------------------------------
def _check_texts(root: Path) -> dict:
    out = {}
    for rel in engine.list_files(root, skip=parse.DISCOVERY_SKIP):
        if CHECK_FILE.search(rel) and not rel.startswith(".claude/worktrees/"):
            t = engine.read_text(root / rel)
            if t:
                out[rel] = t[:400_000]
        if len(out) > 2000:
            break
    return out


def _installed_teeth(root: Path) -> dict:
    p = engine.rules_path(root)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {c.get("source", ""): c["id"] for c in doc.get("checks", []) if c.get("enabled", True)}


def _installed_budget(root: Path) -> list:
    """已装的 load_budget 预算（装了就以它为准，体检和 hook 说同一个数）。"""
    try:
        doc = json.loads(engine.rules_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [c["max_tokens"] for c in doc.get("checks", [])
            if c.get("type") == "load_budget" and c.get("enabled", True) and c.get("max_tokens")]


def build_checks(rules: list, lang: str) -> list:
    """每条能查的规则 → engine 认识的检查（编号 RT1、RT2……）。已有别的机器检查的，生成但默认关，免得查两遍。"""
    checks = []
    for r in rules:
        for spec in r.checks:
            c = {"id": f"RT{len(checks) + 1}", **spec, "rule": classify.snippet(r.text, 120), "source": r.cand.where}
            c.setdefault("enabled", True)
            if r.checked_by and c["enabled"]:
                c["enabled"] = False
                c["note"] = ("已有检查：" if lang == "zh" else "already checked by: ") + ", ".join(r.checked_by)
            checks.append(c)
    return checks


def coverage_gaps(rules: list, rfiles: list, check_texts: dict, subs: list, files: list) -> list:
    """规则写着管全仓库的某种代码，检查却只在几个子项目里跑。

    检查器所在的子项目算「有人查」；别的测试文件引用了检查器（复用同一个守卫）也算。
    """
    out = []
    texts = {rf.path: rf.text for rf in rfiles}
    by_file: dict = {}
    for r in rules:
        if r.checked_by:
            by_file.setdefault(r.cand.file, []).append(r)
    for path, rs in by_file.items():
        lang = classify.file_language(texts.get(path, ""))
        if not lang or len(subs) < 2:
            continue
        exts = engine.LANG_EXTS[lang]
        with_lang = [s for s in subs if any(f.startswith(s + "/") and f.endswith(exts) for f in files)]
        checkers = {f for r in rs for f in r.checked_by if not f.startswith(".rule-teeth")}
        stems = {Path(f).stem for f in checkers if len(Path(f).stem) >= 5}
        users = {k for k, v in check_texts.items() if any(st in v for st in stems)}
        covered = sorted({s for s in with_lang for f in checkers | users if f.startswith(s + "/")})
        missing = [s for s in with_lang if s not in covered]
        if covered and missing:
            out.append((path, len(rs), covered, missing, lang))
    return out


def mark_checked(rules: list, check_texts: dict, teeth: dict):
    """一条规则算「有机器检查」：rule-teeth 装了它；或测试 / CI / hook 里出现了它的编号或它独有的写法。"""
    for r in rules:
        if r.cand.where in teeth:
            r.checked_by.append(f".rule-teeth ({teeth[r.cand.where]})")
        runs = any(c["type"] == "run_command" for c in r.checks)
        pool = {k: v for k, v in check_texts.items() if HOOK_FILE.search(k)} if runs else check_texts
        spans = [s for s, _ in classify.spans(r.text + " " + r.cand.detail)]
        marks = [s for s in spans if (len(s) >= 8 or (len(s) >= 5 and re.search(r"\W", s)))
                 and not classify.is_path(s) and not classify.is_command(s)]
        hits = []
        if r.rule_id:
            # 编号容易撞（别的子项目也有自己的 C1）：编号 + 规则文件名或规则里的某个写法，两样都对上才算
            rx = re.compile(r"[\"'\[(]" + re.escape(r.rule_id) + r"[\"'\])]")
            stem = r.cand.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            also = [stem] + [s for s in spans if len(s) >= 4]
            hits = [k for k, v in pool.items() if rx.search(v) and any(a in v for a in also)]
        if not hits and marks:
            hits = [k for k, v in pool.items() if any(m in v for m in marks)]
        r.checked_by.extend(sorted(hits)[:3])


# ---------------------------------------------------------------------------
# 子项目与下沉
# ---------------------------------------------------------------------------
def find_subprojects(root: Path, root_rf, files: list) -> list:
    top = {f.split("/", 1)[0] for f in files if "/" in f}
    named = []
    if root_rf is not None:
        for c in parse.split_candidates(root_rf.path, root_rf.text):
            if c.kind != "table":
                continue
            for p in parse.referenced_paths(c.text):
                head = p.strip("/").split("/", 1)[0]
                if head in top and head not in GENERIC_DIRS and head not in named:
                    named.append(head)
    if len(named) >= 3:
        return named
    subs = []
    for d in MONOREPO_DIRS:
        subs += sorted({"/".join(f.split("/")[:2]) for f in files if f.startswith(d + "/") and f.count("/") >= 2})
    if subs:
        return subs
    return sorted(d for d in top if d not in GENERIC_DIRS and not d.startswith(".")
                  and any(f"{d}/{m}" in set(files) for m in MANIFESTS))


def _mentions(text: str, sub: str) -> bool:
    name = sub.rsplit("/", 1)[-1]
    return len(name) >= 3 and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text, re.I) is not None


def sink_candidates(rules: list, subs: list, files: list, root_paths: set, lang: str) -> list:
    """根文件里只服务一个（或一部分）子项目的规则 → 该下沉。"""
    out = []
    if len(subs) < 2:
        return out
    has_lang = {}
    for lg, exts in engine.LANG_EXTS.items():
        has_lang[lg] = [s for s in subs if any(f.startswith(s + "/") and f.endswith(exts) for f in files)]
    zh = lang == "zh"
    for r in rules:
        if r.cand.file not in root_paths or r.cand.kind == "table":
            continue
        text = r.text + " " + r.cand.detail
        hit = [s for s in subs if _mentions(text, s)]
        only = (("只提到了 `{}`" if zh else "mentions only `{}`").format(hit[0])) if len(hit) == 1 else ""
        langs = [lg for lg, rx in classify.LANGS.items()
                 if rx.search(text) and re.search(r"(?:" + rx.pattern + r")\s*(?:代码|文件|项目|code|files?|projects?)|"
                                                  + "|".join(re.escape(e) + r"\b" for e in engine.LANG_EXTS[lg]),
                                                  text, re.I)]
        if len(langs) == 1 and 0 < len(has_lang.get(langs[0], [])) < len(subs):
            n, total = len(has_lang[langs[0]]), len(subs)
            ext = engine.LANG_EXTS[langs[0]][0]
            why = (f"只管 {langs[0].title()} 代码，而只有 {n}/{total} 个子项目有 {ext} 文件" if zh else
                   f"only about {langs[0].title()} code, and only {n}/{total} subprojects have {ext} files")
            out.append((r, why + ("；" if zh else "; ") + only if only else why,
                        f".claude/rules/{langs[0]}.md  (paths: \"**/*{ext}\")"))
        elif only:
            out.append((r, only, f"{hit[0]}/CLAUDE.md"))
    return out


def sink_bytes(sink: list) -> int:
    return sum(len((r.text + " " + r.cand.detail).encode("utf-8")) for r, _w, _d in sink)


# ---------------------------------------------------------------------------
# 指针、重复
# ---------------------------------------------------------------------------
NOT_A_PATH = re.compile(r"^(?:text|application|image|audio|video|multipart|font)/[\w.+-]+$")


def pointers(root: Path, rfiles: list, files: list):
    """规则文件里提到、但仓库里找不到的路径；以及指到仓库外面的路径。

    按规则逐段看：同一段里前面提过目录，后面的裸文件名先按那个目录找（「`a/README.md`、`GLOSSARY.md`」）；
    同一段里有仓库外路径或占位符（`<公司>`）的，后面的相对名字说不清是相对谁，不报。
    """
    fileset = set(files)
    names = {f.rsplit("/", 1)[-1] for f in files}
    dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    dead, external, seen = [], [], set()
    for rf in rfiles:
        for c in parse.split_candidates(rf.path, rf.text):
            text = c.text
            vague_base = bool(re.search(r"`[~/][^`]*`|`[^`]*<[^`>]+>[^`]*`", text))
            for m in re.finditer(r"`([~/][^`\n]+)`", text):
                p = m.group(1).strip()
                if (rf.path, p) not in seen and not re.match(r"^/[\w-]+$", p):
                    seen.add((rf.path, p))
                    external.append((rf.path, p, os.path.exists(os.path.expanduser(p))))
            bases = []
            for p in parse.referenced_paths(text):
                if p.startswith(("~", "/")) or NOT_A_PATH.match(p):
                    continue
                bare = "/" not in p.rstrip("/")
                if (rf.path, p) in seen or (bare and vague_base):
                    continue
                if not (bare or re.search(r"\.\w{1,8}$", p) or p.endswith("/") or p.split("/", 1)[0] in dirs):
                    continue                      # 接口路径、MIME 类型这类带斜杠的东西不是文件
                seen.add((rf.path, p))
                found = _exists(root, rf.path, p.rstrip("/"), fileset, names, dirs) or \
                    any(f"{b}/{p}" in fileset for b in bases)
                if not bare:
                    bases.append(p.rstrip("/").rsplit("/", 1)[0] if re.search(r"\.\w{1,8}$", p) else p.rstrip("/"))
                if not found:
                    dead.append((rf.path, p, (root / p).exists()))
    return dead, external


def _exists(root: Path, from_rel: str, p: str, fileset: set, names: set, dirs: set) -> bool:
    if parse._resolve(root, from_rel, p):
        return True
    if "/" not in p:
        return p in names or p in dirs
    return p in dirs or any(f.endswith("/" + p) or f == p for f in fileset) or any(d.endswith("/" + p) for d in dirs)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _grams(s: str) -> set:
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))}


def duplicates(rules: list, threshold: float = 0.55) -> list:
    """两条规则说的几乎是同一句话 —— 改一处必漏另一处。"""
    prepared = [(r, _grams(_norm(r.text))) for r in rules if len(_norm(r.text)) >= 8]
    out = []
    for i in range(len(prepared)):
        a, ga = prepared[i]
        for b, gb in prepared[i + 1:]:
            sim = len(ga & gb) / len(ga | gb) if ga and gb else 0.0
            if sim >= threshold and a.cand.where != b.cand.where:
                out.append((a, b, round(sim, 2)))
    return sorted(out, key=lambda x: -x[2])[:12]


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def run(root, include=(), lang: str = "", max_lines: int = DEFAULT_MAX_LINES, max_kb: int = DEFAULT_MAX_KB) -> Checkup:
    root = Path(root).resolve()
    rfiles = parse.discover(root, include)
    files = [f for f in engine.list_files(root, skip=parse.DISCOVERY_SKIP) if not f.startswith(".claude/worktrees/")]
    lang = lang or detect_lang([rf.text for rf in rfiles])
    root_rf = next((rf for rf in rfiles if rf.auto_loaded and rf.imported_by == ""), None)
    root_name = root_rf.path if root_rf else "CLAUDE.md"
    rules, info = [], {}
    for rf in rfiles:
        cands = parse.split_candidates(rf.path, rf.text)
        rs = classify.classify(cands, lang, classify.file_language(rf.text), root_name)
        rules.extend(rs)
        info[rf.path] = len(cands) - len(rs)
    teeth = _installed_teeth(root)
    check_texts = _check_texts(root)
    mark_checked(rules, check_texts, teeth)
    sizes = []
    for rf in rfiles:
        if rf.auto_loaded:
            raw = rf.text.encode("utf-8")
            sizes.append((rf, len(raw), len(rf.text.splitlines()), len(rf.text), estimate_tokens(rf.text)))
    subs = find_subprojects(root, root_rf, files)
    cu = Checkup(root, lang, rfiles, rules, info, sizes, {"lines": max_lines, "bytes": max_kb * 1024}, subs,
                 teeth=teeth)
    root_paths = {rf.path for rf in rfiles if rf.auto_loaded}
    cu.sink = sink_candidates(rules, subs, files, root_paths, lang)
    cu.dead, cu.external = pointers(root, rfiles, files)
    cu.duplicates = duplicates(rules)
    cu.coverage = coverage_gaps(rules, rfiles, check_texts, subs, files)
    every = [{**c, "enabled": True} for c in build_checks(rules, lang)]
    cu.current = engine.check_project(root, {"lang": lang, "checks": every})
    cu.chain = engine.load_chain(root)
    project = {x.path for x in cu.chain}
    cu.machine = [x for x in engine.load_chain(root, machine=True) if x.path not in project]
    written = [c["max_tokens"] for c in every if c["type"] == "load_budget"]
    cu.budget = int((_installed_budget(root) or written or [DEFAULT_BUDGET])[0])
    cu.findings = rank_findings(cu)
    return cu


def run_file(path, lang: str = "", shown_as: str = "", max_lines: int = DEFAULT_MAX_LINES,
             max_kb: int = DEFAULT_MAX_KB) -> Checkup:
    """只有一份规则文件、没有它的仓库（比如别人项目的 AGENTS.md）：只做规则分类和体量，不查指针和已有检查。"""
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8")
    rf = parse.RuleFile(shown_as or path.name, text, True)
    lang = lang or detect_lang([text])
    cands = parse.split_candidates(rf.path, text)
    rules = classify.classify(cands, lang, classify.file_language(text), rf.path)
    sizes = [(rf, len(text.encode("utf-8")), len(text.splitlines()), len(text), estimate_tokens(text))]
    cu = Checkup(path.parent, lang, [rf], rules, {rf.path: len(cands) - len(rules)}, sizes,
                 {"lines": max_lines, "bytes": max_kb * 1024}, [], single=True)
    cu.duplicates = duplicates(rules)
    cu.findings = rank_findings(cu)
    return cu


def _pl(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _pct(n: int, d: int) -> int:
    return round(100 * n / d) if d else 0


def rank_findings(cu: Checkup) -> list:
    zh = cu.lang == "zh"
    out = []
    total = len(cu.rules)
    unchecked = cu.unchecked
    lim_l, lim_b = cu.limits["lines"], cu.limits["bytes"]
    for rf, nbytes, nlines, _chars, toks in cu.sizes:
        over = nlines > lim_l or nbytes > lim_b
        near = max(nlines / lim_l, nbytes / lim_b) >= 0.9
        if over or near:
            word = ("超了" if over else "贴线") if zh else ("over the limit" if over else "at the limit")
            out.append(Finding("size", 3 if over else 2,
                               (f"`{rf.path}` 体量{word}：{nbytes:,} 字节 / {nlines} 行（上限 {lim_b // 1024}KB / {lim_l} 行），"
                                f"每个 session 固定花约 {toks:,} token" if zh else
                                f"`{rf.path}` is {word}: {nbytes:,} bytes / {nlines} lines (limit {lim_b // 1024}KB / "
                                f"{lim_l} lines); every session pays ~{toks:,} tokens for it"),
                               where=rf.path))
    if cu.chain:
        total_t = engine.chain_tokens(cu.chain)
        root_t = sum(x.tokens for x in cu.chain if x.how == "root")
        soft = [x for x in cu.chain if x.how == "soft"]
        soft_t = sum(x.tokens for x in soft)
        over_b = total_t > cu.budget
        if over_b or soft or total_t >= 0.9 * cu.budget:
            title = (f"每个 session 开工前固定要读约 {total_t:,} token 的规则（预算 {cu.budget:,}）；根文件只占 {root_t:,}"
                     + (f"，{len(soft)} 个文件是规则里写着「每次都读」带进来的（{soft_t:,}）" if soft else "")
                     if zh else
                     f"Every session reads ~{total_t:,} tokens of rules before doing anything (budget {cu.budget:,}); the "
                     f"root file is only {root_t:,}" + (f", and {len(soft)} files ({soft_t:,}) come in because the rules "
                                                        f"say to read them every session" if soft else ""))
            out.append(Finding("load_chain", 3 if over_b else 2, title,
                               items=sorted(cu.chain, key=lambda x: -x.tokens)))
    if total and cu.single:
        out.append(Finding("unchecked", 2,
                           (f"只看规则文件本身：{total} 条规则里，没有一条写明由哪个检查兜底（没扫他们的仓库，那边也许另有检查）"
                            if zh else
                            f"Reading the rule file alone: none of the {total} rules says which check enforces it "
                            f"(their repo was not scanned; checks may exist elsewhere)"),
                           items=unchecked))
    elif total:
        out.append(Finding("unchecked", 3 if _pct(len(unchecked), total) >= 60 else 2,
                           (f"{total} 条规则里有 {len(unchecked)} 条（{_pct(len(unchecked), total)}%）从来没被机器检查过 —— "
                            f"它们只靠 AI 自觉" if zh else
                            f"{len(unchecked)} of {total} rules ({_pct(len(unchecked), total)}%) have never been checked "
                            f"by a machine — they rely on the AI remembering"),
                           items=unchecked))
    for path, n, covered, missing, lg in cu.coverage:
        out.append(Finding("coverage", 3,
                           (f"`{path}` 的 {n} 条规则有机器检查，但检查只在 {len(covered)} 个子项目里跑"
                            f"（{', '.join(covered)}）；另外 {len(missing)} 个有 {lg.title()} 代码的子项目没人查" if zh else
                            f"{n} rules in `{path}` have machine checks, but the checks run in only {len(covered)} "
                            f"subprojects ({', '.join(covered)}); {len(missing)} other subprojects with {lg.title()} "
                            f"code are never checked"),
                           where=path, items=[(path, ", ".join(missing))]))
    if cu.current:
        files_hit = sorted({v.file for v in cu.current})
        out.append(Finding("current", 2,
                           (f"按规则原文扫一遍，现在就有 {len(cu.current)} 处不符合（{len(files_hit)} 个文件）" if zh else
                            f"Scanning with the rules as written: {len(cu.current)} places break them today "
                            f"({len(files_hit)} files)"),
                           items=[(v.file + ":" + str(v.line), v.msg) for v in cu.current]))
    quick = [r for r in cu.by(CHECKABLE) if not r.checked_by]
    if quick:
        kinds = sorted({c["type"] for r in quick for c in r.checks})
        out.append(Finding("quick_win", 3,
                           (f"{len(quick)} 条规则现在就能装上牙（自动生成了检查：{', '.join(kinds)}），一条命令挂进 Claude Code hook"
                            if zh else
                            f"{len(quick)} {_pl(len(quick), 'rule', 'rules')} can get teeth right now (checks generated: {', '.join(kinds)}); one "
                            f"command wires them into Claude Code hooks"),
                           items=quick))
    if cu.sink:
        out.append(Finding("sink", 2,
                           (f"根文件里有 {len(cu.sink)} 条规则只服务一个或一部分子项目，应该下沉（约 {sink_bytes(cu.sink):,} 字节）"
                            f"—— 现在每个 session 都在为它们付 token" if zh else
                            f"{len(cu.sink)} {_pl(len(cu.sink), 'rule', 'rules')} in the root file serve only one or some subprojects and should move "
                            f"down (~{sink_bytes(cu.sink):,} bytes) — today every session pays tokens for them"),
                           items=cu.sink))
    auto_dead = [d for d in cu.dead if d[0] in {rf.path for rf in cu.files if rf.auto_loaded}]
    if cu.dead:
        out.append(Finding("dead_link", 3 if auto_dead else 2,
                           (f"规则文件里有 {len(cu.dead)} 个路径在仓库里找不到 —— AI 照着指针去找，扑空或者自己编"
                            if zh else
                            f"{len(cu.dead)} paths named in rule files do not exist in the repo — the AI follows the "
                            f"pointer and finds nothing, or makes something up"),
                           items=cu.dead))
    vague = cu.by(VAGUE)
    if vague:
        out.append(Finding("vague", 2,
                           (f"{len(vague)} 条规则写得太模糊，连人都没法判断算不算做到了" if zh else
                            f"{len(vague)} {_pl(len(vague), 'rule is', 'rules are')} too vague for anyone, human or "
                            f"machine, to tell whether {_pl(len(vague), 'it was', 'they were')} followed"),
                           items=vague))
    if cu.external:
        out.append(Finding("external", 1,
                           (f"{len(cu.external)} 个指针指到仓库外面（换台电脑、给别人用、开源之后就断）" if zh else
                            f"{len(cu.external)} pointers lead outside the repo (they break on another machine, for a "
                            f"teammate, or once open-sourced)"),
                           items=cu.external))
    if cu.duplicates:
        out.append(Finding("duplicate", 1,
                           (f"{len(cu.duplicates)} 对规则几乎是同一句话写了两遍 —— 改一处必漏另一处" if zh else
                            f"{len(cu.duplicates)} pairs of rules say almost the same thing twice — change one and the "
                            f"other goes stale"),
                           items=cu.duplicates))
    has_claude = any(rf.path in parse.ROOT_CLAUDE for rf in cu.files)
    ignored = [rf.path for rf in cu.files if rf.path in parse.ROOT_AGENTS and has_claude]
    if ignored:
        out.append(Finding("agents_ignored", 2,
                           ("根目录同时有 CLAUDE.md 和 AGENTS.md：Claude Code 默认只读 CLAUDE.md，AGENTS.md 里的规则它看不到"
                            "（除非 CLAUDE.md 里 @AGENTS.md 导入）" if zh else
                            "Both CLAUDE.md and AGENTS.md exist at the root: by default Claude Code reads only CLAUDE.md, "
                            "so the rules in AGENTS.md never reach it (unless CLAUDE.md imports @AGENTS.md)"),
                           where=", ".join(ignored)))
    order = ["load_chain", "size", "unchecked", "coverage", "quick_win", "dead_link", "current", "sink", "agents_ignored", "vague", "external", "duplicate"]
    return sorted(out, key=lambda f: (-f.severity, order.index(f.kind)))


def rule_status(r: Rule, lang: str) -> str:
    zh = lang == "zh"
    if r.checked_by:
        return ("已有检查：" if zh else "checked by: ") + ", ".join(r.checked_by)
    if r.category == CHECKABLE:
        return "能自动查，还没装" if zh else "checkable, not installed yet"
    if r.category == JUDGMENT:
        return "要人判断" if zh else "needs human judgment"
    return "太模糊" if zh else "too vague"
