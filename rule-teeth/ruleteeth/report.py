# -*- coding: utf-8 -*-
"""把体检结果写成给人看的 Markdown（中文或英文）。"""
from __future__ import annotations

import datetime as _dt

from . import classify, engine, refs
from .checkup import Checkup, rule_status
from .model import BROKEN, CHECKABLE, JUDGMENT, OK, UNKNOWN, VAGUE, Ref

L = {
    "zh": {
        "title": "规则体检报告 · {name}",
        "meta": "rule-teeth {v} · {d} · 只读体检，没有改动任何文件",
        "oneline": "**一句话**：{total} 条规则，**{unchecked} 条从来没被机器检查过**（{pct}%），只靠 AI 自觉；{quick} 条现在就能装上牙。",
        "oneline_single": "**一句话**：{total} 条规则，{quick} 条现在就能变成 Claude Code hook 里的检查，其余要人判断或写得太模糊。（只看了这一份文件，没扫它的仓库。）",
        "overview": "总览", "files": "规则文件", "files_v": "{n} 个（每个 session 自动加载 {a} 个）",
        "rules": "规则", "rules_v": "{n} 条（另有 {i} 段是说明 / 索引 / 例子，不算规则）",
        "checkable": "能自动检查", "judgment": "要人判断", "vague": "太模糊",
        "checked": "已有机器检查", "unchecked": "从没被检查过",
        "top": "前 {n} 条发现", "more": "另有 {n} 条，见下文",
        "sizes": "每个 session 自动加载的规则文件", "size_cols": "| 文件 | 字节 | 行 | ≈token | 状态 |",
        "chain": "每次必读链：每个 session 开工前固定要读的东西", "chain_cols": "| 文件 | 怎么进来的 | 被谁带进来 | ≈token |",
        "chain_total": "项目合计", "chain_budget": "预算 {b:,} token：{state}",
        "chain_machine": "这台电脑另加（用户级 / 上级目录 / 自动记忆，不算进项目预算）",
        "chain_note": ("「写着每次都读」= 规则里有「每个 session 都读 X」「开工前先读 X」这类话 —— AI 照做，X 就成了固定成本，"
                       "但它不在 Claude Code 的加载清单里，所以官方的 /context 和 200 行建议都看不到它。"
                       "最便宜的省法通常就在这里：改成「做 Y 时读 X」。分层方法见 rule-teeth 的 LAYERS.zh.md。"),
        "ok": "✅ 没超", "near": "⚠️ 贴线", "over": "❌ 超了",
        "teeth": "能自动查的规则 —— 可以一键装上牙", "teeth_cols": "| 位置 | 规则 | 生成的检查 | 现状 |",
        "teeth_note": "装牙：`python3 rule_teeth.py init {path} --hook`。命令类检查（Stop 时跑测试）默认关着，要你在 `.rule-teeth/rules.json` 里亲手打开 —— 工具不会自作主张替你跑命令。",
        "sink": "该下沉的规则（根文件里只服务一个或一部分子项目）", "sink_cols": "| 位置 | 规则 | 为什么 | 建议放到 |",
        "sink_note": "Claude Code 官方的下沉办法：子项目自己的 `CLAUDE.md`（进那个目录干活才加载），或 `.claude/rules/xxx.md` 加 `paths:`（碰到匹配的文件才加载）。",
        "refs": "规则验真：点名的路径和命令还在不在", "refs_cols": "| 位置 | 类型 | 写的是 | 结论 | 怎么判的 |",
        "refs_sum": "共 {n} 个引用：✅ 在 {ok} 个 · ❌ 失效 {broken} 个 · ❓ 未能判断 {unknown} 个（未能判断 ≠ 通过）。下表只列失效和未能判断的。",
        "refs_note": ("只查不跑：路径看磁盘上在不在（仓库里的相对路径也认 git 里同名的文件）；命令只看程序在不在 PATH 里、"
                      "点名的脚本 / 目录在不在、package.json / Makefile 里有没有这一项。「不要跑 `x`」里的 x 不查。"
                      "查的是这台电脑：换台电脑、给别人用，`~/` 开头的那些多半会断。"),
        "refs_row": "引用的路径 / 命令", "refs_row_v": "{n} 个（失效 {broken}，未能判断 {unknown}）",
        "kind_path": "路径", "kind_command": "命令",
        "v": {OK: "✅ 在", BROKEN: "❌ 失效", UNKNOWN: "❓ 未能判断"},
        "ext": "指到仓库外的路径", "ext_cols": "| 规则文件 | 路径 | 这台电脑上有没有 |",
        "vague_t": "太模糊的规则，以及怎么改写", "vague_cols": "| 位置 | 规则 | 怎么改 |",
        "now": "现在就不符合规则的地方（按规则原文扫）", "now_cols": "| 位置 | 问题 | 规则 |",
        "now_note": "这是按规则字面意思扫的。已经有守卫的子项目，那边可能另有白名单（比如允许某个桥接文件），以那边为准；没有守卫的子项目，这里就是第一次有人查。",
        "dup": "几乎写了两遍的规则", "dup_cols": "| 位置 A | 位置 B | 相似度 | 规则 A |",
        "all": "全部规则", "all_cols": "| # | 位置 | 规则 | 分类 | 状态 |",
        "how": "怎么分的类",
        "how_body": ("- **能自动检查**：规则里写出了机器认得的东西 —— 反引号里的写法 / 命令 / 文件、明确的行数、密钥、回复开头 —— rule-teeth 已经替它生成了检查。\n"
                     "- **要人判断**：说得清楚，但要读懂意思才能判断（「重大决策要反证」）。表里写了原因，能拆出可查部分的也写了怎么拆。\n"
                     "- **太模糊**：连人都没法判断算不算做到了（「代码要优雅」）。\n"
                     "- 分类全靠词表和正则，不调任何模型：同一份文件永远得到同一个结论。拿不准时宁可判成「要人判断」，也不生成一条会乱拦的检查。\n"
                     "- 「已有检查」= 测试 / CI / hook 文件里出现了这条规则的编号或它独有的写法，是线索不是证明，值得点开看一眼。"),
        "hidden": "（公开项目只给位置和分类，不转载原文）",
        "cat": {CHECKABLE: "能自动查", JUDGMENT: "要人判断", VAGUE: "太模糊"},
        "soft": "（软）",
    },
    "en": {
        "title": "Rule checkup · {name}",
        "meta": "rule-teeth {v} · {d} · read-only checkup, no files were changed",
        "oneline": "**In one line**: {total} rules, **{unchecked} have never been checked by a machine** ({pct}%) — they rely on the AI remembering; {quick} can get teeth right now.",
        "oneline_single": "**In one line**: {total} rules; {quick} can become Claude Code hook checks right now, the rest need human judgment or are too vague. (Only this one file was read; its repo was not scanned.)",
        "overview": "Overview", "files": "Rule files", "files_v": "{n} ({a} auto-loaded into every session)",
        "rules": "Rules", "rules_v": "{n} (plus {i} passages that are explanations / indexes / examples, not rules)",
        "checkable": "Checkable automatically", "judgment": "Needs human judgment", "vague": "Too vague",
        "checked": "Already machine-checked", "unchecked": "Never checked",
        "top": "Top {n} findings", "more": "{n} more below",
        "sizes": "Rule files auto-loaded into every session", "size_cols": "| File | Bytes | Lines | ≈tokens | Status |",
        "chain": "The always-read chain: what every session reads before doing anything", "chain_cols": "| File | How it gets in | Brought in by | ≈tokens |",
        "chain_total": "Project total", "chain_budget": "budget {b:,} tokens: {state}",
        "chain_machine": "Added on this machine (user-level / parent folders / auto memory; not counted in the project budget)",
        "chain_note": ("\"Told to read every session\" = the rules say \"read X at the start of every session\" or similar. The AI "
                       "complies, so X becomes a fixed cost — yet it is not on Claude Code's load list, so /context and the "
                       "200-line advice never see it. This is usually the cheapest place to cut: make it \"read X when doing Y\". "
                       "The layering method is in rule-teeth's LAYERS.md."),
        "ok": "✅ within limit", "near": "⚠️ at the limit", "over": "❌ over",
        "teeth": "Checkable rules — teeth you can install with one command", "teeth_cols": "| Where | Rule | Generated check | Status |",
        "teeth_note": "Install: `python3 rule_teeth.py init {path} --hook`. Command checks (run tests at Stop) start switched off; you turn them on by hand in `.rule-teeth/rules.json` — the tool never decides on its own to run commands for you.",
        "sink": "Rules that should move down (root file, but serve one or some subprojects)", "sink_cols": "| Where | Rule | Why | Move to |",
        "sink_note": "Claude Code's own ways to move a rule down: the subproject's own `CLAUDE.md` (loads only when working there), or `.claude/rules/xxx.md` with `paths:` (loads only when matching files are touched).",
        "refs": "Reference check: do the paths and commands the rules name still exist?", "refs_cols": "| Where | Kind | As written | Verdict | How it was judged |",
        "refs_sum": "{n} references: ✅ {ok} exist · ❌ {broken} broken · ❓ {unknown} could not be judged (not judged ≠ passed). The table lists only broken and not-judged ones.",
        "refs_note": ("Checked, never run: paths are looked up on disk (repo-relative ones also match a git file of the same name); "
                      "for commands only the program on PATH, the script / folder they name, and the package.json / Makefile entry "
                      "are checked. The x in \"don't run `x`\" is skipped. This is this machine: on another machine or for a "
                      "teammate, the `~/` ones will likely break."),
        "refs_row": "Paths / commands named", "refs_row_v": "{n} ({broken} broken, {unknown} not judged)",
        "kind_path": "path", "kind_command": "command",
        "v": {OK: "✅ exists", BROKEN: "❌ broken", UNKNOWN: "❓ not judged"},
        "ext": "Pointers outside the repo", "ext_cols": "| Rule file | Path | Exists on this machine |",
        "vague_t": "Vague rules, and how to rewrite them", "vague_cols": "| Where | Rule | How to fix |",
        "now": "Where the rules are broken today (rules as written)", "now_cols": "| Where | Problem | Rule |",
        "now_note": "This scan follows the rules literally. Where a subproject already has its own guard, that guard may have an allow-list (e.g. a bridge file) and wins; where none exists, this is the first time anyone checked.",
        "dup": "Rules written twice", "dup_cols": "| Where A | Where B | Similarity | Rule A |",
        "all": "All rules", "all_cols": "| # | Where | Rule | Category | Status |",
        "how": "How rules were classified",
        "how_body": ("- **Checkable**: the rule names something a machine can recognise — a code pattern / command / file in backticks, a line count, secrets, how replies start — and rule-teeth generated a check for it.\n"
                     "- **Needs judgment**: clear, but you have to understand it to judge it (\"every major decision needs a counter-argument\"). The table says why, and how to split off a checkable part when there is one.\n"
                     "- **Too vague**: nobody can tell whether it was followed (\"write clean code\").\n"
                     "- Classification is word lists and regular expressions, no model calls: the same file always gets the same verdict. When unsure it says \"needs judgment\" rather than generate a check that blocks the wrong things.\n"
                     "- \"Checked by\" means a test / CI / hook file contains the rule's ID or a pattern unique to it. A lead, not proof — worth a look."),
        "hidden": "(public project: locations and categories only, rule text not reproduced)",
        "cat": {CHECKABLE: "checkable", JUDGMENT: "judgment", VAGUE: "vague"},
        "soft": " (soft)",
    },
}


def describe_check(spec: dict, lang: str) -> str:
    zh = lang == "zh"
    t = spec["type"]
    if t == "max_lines":
        globs = spec.get("files") or []
        if set(globs) == {"*" + e for e in engine.CODE_EXTS}:
            files = "所有代码文件" if zh else "all code files"
        else:
            files = ", ".join(globs)[:60]
        s = (f"≤ {spec['limit']} 行" if zh else f"≤ {spec['limit']} lines")
        if spec.get("max_bytes"):
            s += f" / ≤ {spec['max_bytes'] // 1024}KB"
        return f"{s}（{files}）" if zh else f"{s} ({files})"
    if t == "forbid_pattern":
        pats = ", ".join(f"`{_unregex(p)}`" for p in spec["patterns"][:4])
        allow = spec.get("allow")
        where = spec.get("where")
        s = (f"禁用写法 {pats}" if zh else f"forbid {pats}")
        if allow:
            s += (f"（{', '.join(allow)} 除外）" if zh else f" (except in {', '.join(allow)})")
        if where:
            s += (f"，只管 {', '.join(where[:3])}" if zh else f", only in {', '.join(where[:3])}")
        return s
    if t == "no_secrets":
        return "代码 / 配置里不许出现密钥（内置 8 种密钥格式）" if zh else "no secrets in code / config (8 built-in key formats)"
    if t == "protected_path":
        return (f"不许改 {', '.join(spec['paths'])}" if zh else f"block edits to {', '.join(spec['paths'])}")
    if t == "forbid_command":
        pats = ", ".join(f"`{_unregex(p)}`" for p in spec["patterns"][:3])
        return (f"拦命令 {pats}" if zh else f"block commands {pats}")
    if t == "run_command":
        cwd = f"（在 {spec['cwd']}/）" if zh and spec.get("cwd") else (f" (in {spec['cwd']}/)" if spec.get("cwd") else "")
        return (f"结束前跑 `{spec['command']}`{cwd}，默认关" if zh else f"run `{spec['command']}`{cwd} before stopping, off by default")
    if t == "reply_prefix":
        return (f"回复必须以「{spec['prefix']}」开头" if zh else f"replies must start with \"{spec['prefix']}\"")
    if t == "load_budget":
        return (f"每次必读链 ≤ {spec['max_tokens']:,} token（根 + @导入 + 不带 paths 的 rules + 写着每次都读的文件）" if zh else
                f"always-read chain ≤ {spec['max_tokens']:,} tokens (root + @imports + unscoped rules + files the rules say to read every session)")
    return t


def _unregex(p: str) -> str:
    """把生成的正则还原成人能看懂的样子（只为显示）。"""
    p = p.replace(r"(?:^|[\s;&|(`])", "").replace(r"(?=$|[\s;&|)`])", "")
    p = p.replace(r"(?:git\s+commit|gh\s+pr\s+(?:create|edit))[\s\S]*", "commit/PR … ")
    p = p.replace(r"\s+", " ").replace(r"\b", "")
    return p.replace("\\", "")


def _cell(text: str, quote: bool, n: int = 90) -> str:
    return classify.snippet(text, n).replace("`", "ˋ") if quote else "—"


def _row(*cells) -> str:
    return "| " + " | ".join(str(c).replace("\n", " ") for c in cells) + " |"


def render(cu: Checkup, name: str = "", quote: bool = True, top: int = 5) -> str:
    lang = cu.lang
    T = L[lang]
    total = len(cu.rules)
    unchecked = len(cu.unchecked)
    quick = len([r for r in cu.by(CHECKABLE) if not r.checked_by])
    out = [f"# {T['title'].format(name=name or cu.root.name)}", "",
           f"> {T['meta'].format(v=engine.VERSION, d=_dt.date.today().isoformat())}", ""]
    if not quote:
        out += [f"> {T['hidden']}", ""]
    pct = round(100 * unchecked / total) if total else 0
    out += [T["oneline_single" if cu.single else "oneline"].format(total=total, unchecked=unchecked, pct=pct,
                                                                     quick=quick), ""]

    out += [f"## {T['overview']}", "", "| | |", "|---|---|",
            _row(T["files"], T["files_v"].format(n=len(cu.files), a=sum(1 for f in cu.files if f.auto_loaded))),
            _row(T["rules"], T["rules_v"].format(n=total, i=sum(cu.info.values()))),
            _row(T["checkable"], len(cu.by(CHECKABLE))),
            _row(T["judgment"], len(cu.by(JUDGMENT))),
            _row(T["vague"], len(cu.by(VAGUE))),
            _row(T["checked"], total - unchecked),
            _row(T["unchecked"], f"**{unchecked}** ({pct}%)")]
    if not cu.single:
        n = refs.counts(cu.refs)
        out.append(_row(T["refs_row"], T["refs_row_v"].format(n=len(cu.refs), broken=n[BROKEN], unknown=n[UNKNOWN])))
    out.append("")
    for rf in cu.files:
        tag = "auto" if rf.auto_loaded else ("← " + rf.imported_by if rf.imported_by else "")
        out.append(f"- `{rf.path}` {tag}")
    out.append("")

    out += [f"## {T['top'].format(n=min(top, len(cu.findings)))}", ""]
    for i, f in enumerate(cu.findings[:top], 1):
        out.append(f"{i}. **{f.title}**")
        for line in _finding_examples(f, lang, quote):
            out.append(f"   - {line}")
    if len(cu.findings) > top:
        out += ["", f"_{T['more'].format(n=len(cu.findings) - top)}_"]
    out.append("")

    out += _sizes(cu, T)
    out += _chain(cu, T)
    out += _teeth(cu, T, quote)
    out += _sink(cu, T, quote)
    out += _refs(cu, T)
    out += _simple_table(T["ext"], T["ext_cols"], [(f"`{a}`", f"`{b}`", T["v"][v]) for a, b, v in cu.external])
    now = _simple_table(T["now"], T["now_cols"],
                        [(f"`{v.file}:{v.line}`", v.msg, f"{v.source}") for v in cu.current[:40]])
    out += now + [T["now_note"], ""] if now else []
    out += _simple_table(T["vague_t"], T["vague_cols"],
                         [(r.cand.where, _cell(r.text, quote, 70), r.hint) for r in cu.by(VAGUE)])
    out += _simple_table(T["dup"], T["dup_cols"],
                         [(a.cand.where, b.cand.where, s, _cell(a.text, quote, 60)) for a, b, s in cu.duplicates])
    rows = [(i, r.cand.where, _cell(r.text, quote) + (T["soft"] if r.soft else ""), T["cat"][r.category],
             rule_status(r, lang) if r.category != JUDGMENT or r.checked_by else r.hint)
            for i, r in enumerate(cu.rules, 1)]
    out += _simple_table(T["all"], T["all_cols"], rows)
    out += [f"## {T['how']}", "", T["how_body"], ""]
    return "\n".join(out)


def _finding_examples(f, lang: str, quote: bool) -> list:
    items = f.items[:3]
    lines = []
    for it in items:
        if isinstance(it, Ref):
            lines.append(f"{it.where} `{it.text}` → {L[lang]['v'][it.verdict]}：{refs.explain(it, lang)}" if lang == "zh"
                         else f"{it.where} `{it.text}` → {L[lang]['v'][it.verdict]}: {refs.explain(it, lang)}")
        elif isinstance(it, engine.Loaded):
            lines.append(f"`{it.path}` ≈{it.tokens:,} token · {engine._t(lang, 'how_' + it.how)}")
        elif hasattr(it, "cand"):
            extra = "; ".join(describe_check(c, lang) for c in it.checks) if it.checks else (it.hint or "")
            lines.append(f"{it.cand.where} {_cell(it.text, quote, 50)}" + (f" → {extra}" if extra else ""))
        elif isinstance(it, tuple) and it and hasattr(it[0], "cand"):
            r = it[0]
            lines.append(f"{r.cand.where} {_cell(r.text, quote, 50)} → {it[1]}")
        elif isinstance(it, tuple):
            lines.append(" · ".join(f"`{x}`" if isinstance(x, str) and "`" not in x else str(x) for x in it[:2]))
    if len(f.items) > 3:
        lines.append(f"… +{len(f.items) - 3}")
    return lines


def _sizes(cu: Checkup, T: dict) -> list:
    out = [f"## {T['sizes']}", "", T["size_cols"], "|---|---:|---:|---:|---|"]
    lim_l, lim_b = cu.limits["lines"], cu.limits["bytes"]
    for rf, nbytes, nlines, _c, toks in cu.sizes:
        ratio = max(nlines / lim_l, nbytes / lim_b)
        state = T["over"] if ratio > 1 else (T["near"] if ratio >= 0.9 else T["ok"])
        out.append(_row(f"`{rf.path}`", f"{nbytes:,}", nlines, f"{toks:,}", f"{state} ({round(ratio * 100)}%)"))
    if len(cu.sizes) > 1:
        out.append(_row("**Σ**", f"{sum(s[1] for s in cu.sizes):,}", sum(s[2] for s in cu.sizes),
                        f"{sum(s[4] for s in cu.sizes):,}", ""))
    return out + [""]


def _chain(cu: Checkup, T: dict) -> list:
    if not cu.chain:
        return []
    lang = cu.lang
    out = [f"## {T['chain']}", "", T["chain_cols"], "|---|---|---|---:|"]
    for x in cu.chain:
        out.append(_row(f"`{x.path}`", engine._t(lang, "how_" + x.how), f"`{x.by}`" if x.by else "", f"{x.tokens:,}"))
    total = engine.chain_tokens(cu.chain)
    state = T["over"] if total > cu.budget else (T["near"] if total >= 0.9 * cu.budget else T["ok"])
    out.append(_row(f"**{T['chain_total']}**", T["chain_budget"].format(b=cu.budget, state=state), "", f"**{total:,}**"))
    if cu.machine:
        out.append(_row(f"_{T['chain_machine']}_", "", "", ""))
        for x in cu.machine:
            out.append(_row(f"`{x.path}`", engine._t(lang, "how_" + x.how), "", f"{x.tokens:,}"))
    return out + ["", T["chain_note"], ""]


def _teeth(cu: Checkup, T: dict, quote: bool) -> list:
    rows = [(r.cand.where, _cell(r.text, quote, 60), "; ".join(describe_check(c, cu.lang) for c in r.checks),
             rule_status(r, cu.lang)) for r in cu.by(CHECKABLE)]
    if not rows:
        return []
    return _simple_table(T["teeth"], T["teeth_cols"], rows) + [T["teeth_note"].format(path="."), ""]


def _refs(cu: Checkup, T: dict, limit: int = 80) -> list:
    if cu.single or not cu.refs:
        return []
    n = refs.counts(cu.refs)
    bad = sorted((r for r in cu.refs if r.verdict != OK), key=lambda r: (r.verdict != BROKEN, r.file, r.line))
    rows = [(f"`{r.where}`", T["kind_" + r.kind], f"`{r.text.replace('`', 'ˋ').replace('|', '/')}`", T["v"][r.verdict],
             refs.explain(r, cu.lang).replace("|", "/")) for r in bad[:limit]]
    out = [f"## {T['refs']}", "", T["refs_sum"].format(n=len(cu.refs), ok=n[OK], broken=n[BROKEN], unknown=n[UNKNOWN]), ""]
    if rows:
        out += _simple_table("", T["refs_cols"], rows)[2:]
        if len(bad) > limit:
            out += [f"_… +{len(bad) - limit}_", ""]
    return out + [T["refs_note"], ""]


def _sink(cu: Checkup, T: dict, quote: bool) -> list:
    rows = [(r.cand.where, _cell(r.text, quote, 60), why, f"`{dest}`") for r, why, dest in cu.sink]
    if not rows:
        return []
    return _simple_table(T["sink"], T["sink_cols"], rows) + [T["sink_note"], ""]


def _simple_table(title: str, cols: str, rows: list) -> list:
    if not rows:
        return []
    sep = "|" + "---|" * cols.count(" | ") + "---|"
    return [f"## {title}", "", cols, sep] + [_row(*r) for r in rows] + [""]
