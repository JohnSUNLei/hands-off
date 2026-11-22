# -*- coding: utf-8 -*-
"""规则验真：规则文件里点名的路径和命令，在这台电脑上还在不在。

不只看规则写得好不好，还对着磁盘查它点名的东西（思路来自 ctxlint / AgentLint）。
三种结论：在（ok）/ 失效（broken）/ 未能判断（unknown）。拿不准的一律「未能判断」，不算通过。

只读，不跑任何命令：命令只查「程序在不在 PATH 里、脚本文件在不在、package.json / Makefile 里有没有这一项」。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import shlex
import shutil
from pathlib import Path

from . import classify, parse
from .model import BROKEN, OK, UNKNOWN, Ref

NOT_A_PATH = re.compile(r"^(?:text|application|image|audio|video|multipart|font)/[\w.+-]+$")   # MIME 类型
_MACHINE_SPAN = re.compile(r"`([~/][^`\n]+)`")
_MACHINE_LINK = re.compile(r"\]\(([~/][^)\s]+)\)")
_BARE_HOME = re.compile(r"(?<![\w`(/~])(~/[^\s`)）】」，。、；：！？,;]+)")
_TRAILING = "。，、；：！？,;:)）」】"
_PLACEHOLDER = re.compile(r"<[^>\n]+>|\{[^}\n]*\}|\$\{?\w+")
_WILD = re.compile(_PLACEHOLDER.pattern + r"|[*?\[]")
_TEMPLATE_SPAN = re.compile(r"^[\w.\-]+(?:/[^\s/`|]+)+/?$")
_VAGUE_BASE = re.compile(r"`[~/][^`]*`|`[^`]*<[^`>]+>[^`]*`")

SHELL_BUILTINS = {"cd", "echo", "export", "source", ".", "true", "false", "test", "[", "printf", "set", "unset",
                  "alias", "read", "exit", "return", "wait", "pwd", ":", "eval", "type", "ulimit", "umask"}
PREFIX_WORDS = {"sudo", "env", "time", "nohup", "command", "exec", "nice"}
INTERPRETER = re.compile(r"^(?:python(?:\d(?:\.\d+)?)?|node|bash|sh|zsh|ruby|perl|php)$")
VALUE_OPTS = {"-W", "-X", "-r", "--require", "--import", "--loader", "--input-type", "-I"}
PKG_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
PKG_BUILTIN = {"install", "i", "ci", "add", "remove", "rm", "uninstall", "dlx", "exec", "x", "create", "init", "why",
               "list", "ls", "outdated", "update", "up", "upgrade", "link", "publish", "pack", "config", "store",
               "audit", "info", "view", "version", "login", "logout", "cache", "prune", "rebuild", "help", "global",
               "set", "workspace", "workspaces", "dedupe", "env", "import", "patch", "pm"}
SCRIPT_EXT = re.compile(r"\.(?:sh|bash|zsh|command|py|js|mjs|cjs|ts|rb|pl)$")
_RANK = {OK: 0, UNKNOWN: 1, BROKEN: 2}

WHY = {
    "found": ("在", "exists"),
    "found_unique": ("仓库里只有一个 `{a}`，就是它", "the repo has exactly one `{a}`; that is it"),
    "template_ok": ("模板路径，固定部分 `{a}` 在", "template path; the fixed part `{a}` exists"),
    "glob_ok": ("通配符能匹配上文件", "the wildcard matches files"),
    "glob_none": ("通配符一个文件都没匹配上", "the wildcard matches no file"),
    "missing": ("这台电脑上没有", "not on this machine"),
    "fixed_missing": ("连固定部分 `{a}` 都不在", "even the fixed part `{a}` is missing"),
    "all_wild": ("整条都是占位符 / 通配符，没有能查的部分", "all placeholder / wildcard, nothing to check"),
    "repo_missing": ("仓库里找不到", "not found in the repo"),
    "ext_base": ("相对同一段里的 `{a}` 找到了", "found next to `{a}`, named in the same passage"),
    "vague_base": ("同一段里有仓库外路径或占位符，说不清它相对谁；那里和仓库里都没找到",
                   "the same passage names an outside path or a placeholder, so it is unclear what this is relative "
                   "to; not found there or in the repo"),
    "cmd_ok": ("程序在，点名的脚本 / 目录也在", "the program exists, and so does any script / folder it names"),
    "no_exe": ("当前 PATH 里找不到 `{a}`（可能没装，也可能只是 PATH 不同）",
               "`{a}` is not on the current PATH (maybe not installed, maybe just a different PATH)"),
    "script_missing": ("脚本 `{a}` 不在", "script `{a}` does not exist"),
    "script_elsewhere": ("仓库里有 `{a}`，但规则没写在哪个目录下跑", "the repo has `{a}`, but the rule does not say which "
                                                       "folder to run it from"),
    "script_many": ("仓库里有好几个 `{a}`，规则没写在哪个目录下跑", "the repo has several `{a}`; the rule does not say which "
                                                    "folder to run it from"),
    "cd_missing": ("`cd` 去的目录 `{a}` 不在", "the folder `{a}` that it `cd`s into does not exist"),
    "module_ok": ("这台电脑的 Python 里有这个模块", "this machine's Python has the module"),
    "module_unknown": ("`-m {a}`：这台电脑的 Python 里找不到这个模块（可能装在别的环境里）",
                       "`-m {a}`: this machine's Python does not have the module (it may live in another environment)"),
    "pkg_script_missing": ("package.json 里没有 `{a}` 这个 script", "package.json has no `{a}` script"),
    "no_pkg": ("附近没有 package.json，看不出有没有 `{a}`", "no package.json nearby, so cannot tell whether `{a}` exists"),
    "bad_json": ("`{a}` 读不懂（不是合法 JSON）", "`{a}` is not valid JSON"),
    "make_missing": ("Makefile 里没有 `{a}` 这个目标", "the Makefile has no `{a}` target"),
    "make_maybe": ("Makefile 里没直接写 `{a}`（可能来自 include 或模式规则）",
                   "the Makefile does not spell out `{a}` (it may come from an include or a pattern rule)"),
    "no_makefile": ("附近没有 Makefile", "no Makefile nearby"),
    "placeholder": ("带占位符 `{a}`，没法查", "contains the placeholder `{a}`; cannot check"),
    "unparsable": ("引号没配对，看不懂这条命令", "unbalanced quotes; cannot read this command"),
}


def explain(ref: Ref, lang: str) -> str:
    zh, en = WHY.get(ref.why, (ref.why, ref.why))
    return (zh if lang == "zh" else en).format(a=ref.arg)


def counts(refs: list) -> dict:
    out = {OK: 0, BROKEN: 0, UNKNOWN: 0}
    for r in refs:
        out[r.verdict] += 1
    return out


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def collect(root, rfiles: list, files: list) -> list:
    """所有规则文件里点名的路径和命令 → [Ref]。同一个规则文件里同一个东西只算一次，取最好的结论。"""
    root = Path(root)
    fileset = set(files)
    repo = (fileset, {f.rsplit("/", 1)[-1] for f in files},
            {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)})
    found = []
    for rf in rfiles:
        for c in parse.split_candidates(rf.path, rf.text):
            machine, bases = _machine(rf.path, c)
            found += machine
            found += _repo(root, rf.path, c, repo, bases)
            found += _commands(root, rf.path, c, repo)
    return _dedupe(found)


def _dedupe(refs: list) -> list:
    best, order = {}, []
    for r in refs:
        k = (r.file, r.kind, r.text)
        if k not in best:
            best[k] = r
            order.append(k)
        elif (r.verdict == OK) > (best[k].verdict == OK) or \
                (best[k].verdict == UNKNOWN and r.verdict == BROKEN):
            best[k] = r                # 有一处能对上就算在；对不上的里头，判得清的比「未能判断」好
    return [best[k] for k in order]


def exists_in_repo(root: Path, from_rel: str, p: str, fileset: set, names: set, dirs: set) -> bool:
    """仓库里的相对路径在不在：先按规则文件所在目录 / 项目根解析，再看仓库里有没有同名文件或同样结尾的路径。"""
    if parse._resolve(root, from_rel, p):
        return True
    if "/" not in p:
        return p in names or p in dirs
    return p in dirs or any(f.endswith("/" + p) or f == p for f in fileset) or any(d.endswith("/" + p) for d in dirs)


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def _fixed_part(p: str) -> tuple:
    """(第一个占位符 / 通配符之前的部分, 后面还有没有占位符 / 通配符)"""
    segs = p.split("/")
    fixed = []
    for s in segs:
        if _WILD.search(s):
            break
        fixed.append(s)
    return "/".join(fixed), len(fixed) < len(segs)


def _real_abs(p: str) -> bool:
    """`/home/me/...` 是磁盘路径；`/doctor`、`/api/v1` 这种是斜杠命令或接口地址，不算。"""
    first = p.strip("/").split("/", 1)[0].split(" ", 1)[0]
    return bool(first) and "/" in p.strip("/") and os.path.isdir("/" + first)


def judge_machine(p: str) -> tuple:
    """`~/` 或绝对路径 → (结论, 理由, 参数)。"""
    head, wild = _fixed_part(p)
    if wild:
        if head in ("", "~"):
            return UNKNOWN, "all_wild", ""
        if not os.path.exists(os.path.expanduser(head)):
            return BROKEN, "fixed_missing", head
        if _PLACEHOLDER.search(p):
            return OK, "template_ok", head
        return (OK, "glob_ok", "") if glob.glob(os.path.expanduser(p)) else (UNKNOWN, "glob_none", "")
    if os.path.exists(os.path.expanduser(p)):
        return OK, "found", ""
    for m in reversed(list(re.finditer(r"\s+", p))):      # 「路径 + 说明文字」：后面那截不像路径才截
        if "/" in p[m.end():]:
            break
        if os.path.exists(os.path.expanduser(p[:m.start()])):
            return OK, "found", ""
    return BROKEN, "missing", ""


def _machine(rule_file: str, c) -> tuple:
    """这一段里的 `~/` 和绝对路径 → ([Ref], 在的那些所在的目录)。"""
    raw = [m.group(1).strip() for m in _MACHINE_SPAN.finditer(c.text)]
    raw += [m.group(1).split("#")[0] for m in _MACHINE_LINK.finditer(c.text)]
    raw += [m.group(1) for m in _BARE_HOME.finditer(_MACHINE_SPAN.sub(" ", c.text))]
    out, bases = [], []
    for p in dict.fromkeys(x.rstrip(_TRAILING) for x in raw):
        if p in ("~", "~/", "/") or (p.startswith("/") and not _real_abs(p)):
            continue
        verdict, why, arg = judge_machine(p)
        out.append(Ref(rule_file, c.line, "path", p, verdict, why, arg, "machine"))
        if verdict == OK and why == "found":
            full = os.path.expanduser(p)
            bases.append(full if os.path.isdir(full) else os.path.dirname(full))
    return out, bases


def _repo(root: Path, rule_file: str, c, repo: tuple, ext_bases: list) -> list:
    """这一段里的仓库相对路径。同一段里前面提过目录，后面的裸文件名先按那个目录找。"""
    fileset, names, dirs = repo
    text = c.text
    vague = bool(_VAGUE_BASE.search(text))
    out, bases = [], []
    for p in parse.referenced_paths(text):
        if p.startswith(("~", "/")) or NOT_A_PATH.match(p) or re.fullmatch(r"\.\w{1,8}", p):
            continue                      # 「碰到 `.py` 文件时」说的是扩展名，不是文件
        bare = "/" not in p.rstrip("/")
        if not (bare or re.search(r"\.\w{1,8}$", p) or p.endswith("/") or p.split("/", 1)[0] in dirs):
            continue                      # 接口路径这类带斜杠的东西不是文件
        q = p.rstrip("/")
        found = exists_in_repo(root, rule_file, q, fileset, names, dirs) or any(f"{b}/{q}" in fileset for b in bases)
        if not bare:
            bases.append(q.rsplit("/", 1)[0] if re.search(r"\.\w{1,8}$", q) else q)
        ext = None if found else next((b for b in ext_bases if os.path.exists(os.path.join(b, q))), None)
        if found:
            v = (OK, "found", "")
        elif ext:
            v = (OK, "ext_base", _shown(ext))
        elif bare and vague:
            v = (UNKNOWN, "vague_base", "")
        else:
            v = (BROKEN, "repo_missing", "")
        out.append(Ref(rule_file, c.line, "path", p, *v, "repo"))
    for s, _ in classify.spans(text):               # 模板路径：`runs/<作品>/` 查固定部分
        if s.startswith(("~", "/")) or not _PLACEHOLDER.search(s) or not _TEMPLATE_SPAN.match(s):
            continue
        head, _wild = _fixed_part(s)
        if head:
            ok = exists_in_repo(root, rule_file, head, fileset, names, dirs)
            out.append(Ref(rule_file, c.line, "path", s, OK if ok else BROKEN,
                           "template_ok" if ok else "fixed_missing", head, "repo"))
    return out


def _shown(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def segments(cmd: str) -> list:
    """`cd a && python3 b.py | tee x` → [['cd', 'a'], ['python3', 'b.py'], ['tee', 'x']]。重定向和它的目标去掉。

    引号没配对会抛 ValueError。
    """
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    segs, cur, skip = [], [], False
    for t in lex:
        if skip:
            skip = False
            continue
        if t and set(t) <= set(";&|") and t != "&>":
            segs.append(cur)
            cur = []
        elif t and set(t) <= set("<>&"):
            skip = True                    # 重定向：连同后面的目标一起去掉
        else:
            cur.append(t)
    segs.append(cur)
    return [s for s in segs if s]


def _strip_prefix(seg: list) -> list:
    i = 0
    while i < len(seg) and (re.match(r"^\w+=", seg[i]) or seg[i] in PREFIX_WORDS):
        i += 1
    return seg[i:]


def looks_like_command(s: str) -> bool:
    try:
        segs = segments(s)
    except ValueError:
        return classify.is_command(s)
    segs = [seg for seg in map(_strip_prefix, segs) if seg]
    if len(segs) > 1 and segs[0][0] == "cd":
        return True                        # `cd x && 随便什么`：一看就是命令
    return any(classify.is_command(" ".join(seg)) for seg in segs)


def _commands(root: Path, rule_file: str, c, repo: tuple) -> list:
    text = c.text
    neg = {s for s, _ in classify.negative_spans(text)}
    hint = classify.run_cwd(text, with_cd=False)
    out = []
    for s, _ in classify.spans(text):
        if s in neg or not looks_like_command(s):
            continue                       # 「不要跑 `x`」里的 x 本来就不该跑，不查它在不在
        verdict, why, arg = judge_command(root, s, _cwds(root, rule_file, hint), repo[0])
        out.append(Ref(rule_file, c.line, "command", s, verdict, why, arg, "command"))
    return out


def _cwds(root: Path, rule_file: str, hint: str) -> list:
    here = (root / rule_file).parent
    if hint:
        for base in (root, here):
            if (base / hint).is_dir():
                return [base / hint]
        return [root / hint]
    return [here, root] if here != root else [root]


def judge_command(root: Path, cmd: str, cwds: list, fileset: set) -> tuple:
    """一条命令 → (结论, 理由, 参数)。整条里最坏的那一段说了算。"""
    try:
        segs = segments(cmd)
    except ValueError:
        return UNKNOWN, "unparsable", ""
    worst = (OK, "cmd_ok", "")
    for seg in map(_strip_prefix, segs):
        if not seg:
            continue
        v, cwds = _segment(root, seg, cwds, fileset)
        if _RANK[v[0]] > _RANK[worst[0]]:
            worst = v
    return worst


def _find(p: str, cwds: list, want_dir: bool = False):
    if p.startswith(("~", "/")):
        cands = [Path(os.path.expanduser(p))]
    else:
        cands = [d / p for d in cwds]
    for x in cands:
        if (x.is_dir() if want_dir else x.exists()):
            return x
    return None


def _segment(root: Path, seg: list, cwds: list, fileset: set) -> tuple:
    exe, args = seg[0], seg[1:]
    ok = (OK, "cmd_ok", "")
    if _PLACEHOLDER.search(exe):
        return (UNKNOWN, "placeholder", exe), cwds
    if exe == "cd":
        d = args[0] if args else "~"
        if _PLACEHOLDER.search(d):
            return (UNKNOWN, "placeholder", d), cwds
        hit = _find(d, cwds, want_dir=True)
        return (ok, [hit]) if hit else ((BROKEN, "cd_missing", d), cwds)
    if exe in (".", "source"):
        return (_script(args[0], cwds, fileset) if args else ok), cwds
    if exe in SHELL_BUILTINS:
        return ok, cwds
    if "/" in exe or SCRIPT_EXT.search(exe):
        return _script(exe, cwds, fileset), cwds
    if shutil.which(exe) is None:
        return (UNKNOWN, "no_exe", exe), cwds
    if INTERPRETER.match(exe):
        return _interpreter(exe, args, cwds, fileset), cwds
    if exe in PKG_MANAGERS:
        return _package_script(exe, args, cwds, fileset), cwds
    if exe == "make":
        return _make(args, cwds), cwds
    return ok, cwds


def _script(script: str, cwds: list, fileset: set) -> tuple:
    if _PLACEHOLDER.search(script):
        return UNKNOWN, "placeholder", script
    if _find(script, cwds):
        return OK, "cmd_ok", ""
    if script.startswith(("~", "/")):
        return BROKEN, "script_missing", script
    bare = re.sub(r"^\./", "", script)
    others = sorted(f for f in fileset if f == bare or f.endswith("/" + bare))
    if len(others) == 1 and "/" not in bare:
        return OK, "found_unique", others[0]
    if len(others) > 1:
        return UNKNOWN, "script_many", bare
    if others:
        return UNKNOWN, "script_elsewhere", others[0]
    return BROKEN, "script_missing", script


def _interpreter(exe: str, args: list, cwds: list, fileset: set) -> tuple:
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-m" and i + 1 < len(args):
            return _module(exe, args[i + 1], cwds)
        if a.startswith("-m") and len(a) > 2 and exe.startswith("python"):
            return _module(exe, a[2:], cwds)
        if a in ("-c", "-e", "--eval", "-p", "--print", "-"):
            return OK, "cmd_ok", ""
        if a in VALUE_OPTS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return _script(a, cwds, fileset)
    return OK, "cmd_ok", ""


def _module(exe: str, mod: str, cwds: list) -> tuple:
    top = mod.split(".")[0]
    if any((d / f"{top}.py").is_file() or (d / top).is_dir() for d in cwds):
        return OK, "cmd_ok", ""
    if exe.startswith("python"):
        try:
            if importlib.util.find_spec(top) is not None:      # 只找，不导入
                return OK, "module_ok", ""
        except (ImportError, ValueError):
            pass
    return UNKNOWN, "module_unknown", mod


def _package_script(exe: str, args: list, cwds: list, fileset: set) -> tuple:
    if not args:
        return OK, "cmd_ok", ""
    sub, name = args[0], None
    if sub == "run":
        name = args[1] if len(args) > 1 else None
    elif sub in ("test", "start", "t", "tst"):
        name = "test" if sub in ("t", "tst") else sub
    elif exe != "npm" and not sub.startswith("-") and sub not in PKG_BUILTIN:
        if "/" in sub or SCRIPT_EXT.search(sub):
            return _script(sub, cwds, fileset)        # `bun x.ts`
        name = sub                                      # `pnpm build` = `pnpm run build`
    if not name:
        return OK, "cmd_ok", ""
    if _PLACEHOLDER.search(name):
        return UNKNOWN, "placeholder", name
    pkg = next((d / "package.json" for d in cwds if (d / "package.json").is_file()), None)
    if pkg is None:
        return UNKNOWN, "no_pkg", name
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return UNKNOWN, "bad_json", _shown(str(pkg))
    return (OK, "cmd_ok", "") if name in scripts else (BROKEN, "pkg_script_missing", name)


def _make(args: list, cwds: list) -> tuple:
    target, i = None, 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            cwds = [d / args[i + 1] for d in cwds]
            i += 2
            continue
        if not a.startswith("-") and "=" not in a and target is None:
            target = a
        i += 1
    mk = next((d / n for d in cwds for n in ("GNUmakefile", "makefile", "Makefile") if (d / n).is_file()), None)
    if mk is None:
        return UNKNOWN, "no_makefile", ""
    if target is None or _PLACEHOLDER.search(target):
        return OK, "cmd_ok", ""
    text = mk.read_text(encoding="utf-8", errors="replace")
    rx = re.compile(r"^(?:[^\s:#=]+[ \t]+)*" + re.escape(target) + r"(?:[ \t]+[^\s:#=]+)*[ \t]*::?(?!=)", re.M)
    if rx.search(text):
        return OK, "cmd_ok", ""
    if re.search(r"^-?include\s|^[^\s:#=]*%[^\s:#=]*\s*:", text, re.M):
        return UNKNOWN, "make_maybe", target
    return BROKEN, "make_missing", target
