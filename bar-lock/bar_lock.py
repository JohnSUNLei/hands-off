#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bar-lock — your AI can't quietly lower the bar to get a green check.

When a test fails, an agent has two ways out: fix the code, or make the check easier. Delete the assertion,
add a skip, widen the tolerance, swallow the error, lower the pass line. A linter can't see it — it reads the
file as it is now; the evidence is in what *changed*. bar-lock reads the change.

One file, Python standard library only, no model calls. It looks at five kinds of "easier", and only at what
THIS change made easier (old habits are never reported, tightening never flagged):

  K1 assertions removed or weakened     K2 a test newly disabled                K3 a tolerance widened
  K4 an error newly swallowed           K5 a pass line lowered, or any number in a file you "pin"

It checks when the agent runs `git commit` / `git push` and when it says "done" (Stop) — as Claude Code hooks,
not a pre-commit script it can skip with `--no-verify`. A waiver is granted by YOU, from your own terminal, into
a file the agent may not touch; a waiver comment the agent writes inside the diff counts for nothing.
`block` stops the action, `note` is only shown. If bar-lock itself breaks it says so and lets the action through.
"""
from __future__ import annotations

import ast
import datetime as _dt
import difflib
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"
HOME_DIR = ".bar-lock"
ENGINE_FILE = "bar_lock.py"
CONFIG_FILE = "config.json"
STATE_DIR = "state"
WAIVER_FILE = "waivers.json"
HOOK_MARK = HOME_DIR + "/" + ENGINE_FILE
HOOK_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.bar-lock/bar_lock.py" hook'
HOOK_TIMEOUT = 30
PRE_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"
HOOK_EVENTS = ("SessionStart", "PreToolUse", "Stop")
MAX_FILE_BYTES = 200_000
MAX_FILES = 300
MAX_SHOWN = 12
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

DEFAULT_TEST_GLOBS = ["**/test_*.py", "**/*_test.py", "**/conftest.py", "**/tests/**", "**/test/**",
                      "**/__tests__/**", "**/*.test.*", "**/*.spec.*"]
DEFAULT_IGNORE_GLOBS = ["**/node_modules/**", "**/.git/**", "**/dist/**", "**/build/**", "**/.venv/**",
                        "**/venv/**", "**/__pycache__/**", "**/*.lock", "**/package-lock.json", "**/*.min.*",
                        "**/*.svg", "**/*.csv", "**/*.ipynb", "**/.bar-lock/**"]
DEFAULT_CONFIG = {
    "lang": "en",
    "test_globs": None,        # None = built-in list; set a list to replace it
    "extra_test_globs": [],    # added to the built-in list
    "gate_globs": [],          # non-test files where a newly swallowed error also counts (K4)
    "pinned": [],              # files whose numbers you own: ANY changed number blocks (K5)
    "ignore": [],              # files bar-lock never looks at
    "max_stop_blocks": 2,      # how many times "done" is refused per session before it only warns loudly
}

# --- Words. Every user-visible sentence lives here, once. ---
TEXT = {
    "en": {
        "K1.removed": "{n} assertion(s) removed and none added back: {ev}",
        "K1.weakened": "assertion weakened: {ev}",
        "K1.trivial": "always-true assertion added: {ev}",
        "K1.file_deleted": "test file deleted ({n} tests), none of them reappears anywhere else",
        "K1.file_deleted_part": "test file deleted ({n} tests); some reappear elsewhere",
        "K1.file_deleted_with_code": "test file deleted together with the code beside it ({n} tests) — looks like a feature removed",
        "K1.func_deleted": "test removed: {ev}",
        "K1.renamed_off": "test renamed so it no longer runs: {ev}",
        "K1.edited": "expected value changed: {ev}",
        "K2.skip": "test disabled: {ev}",
        "K2.skip_new": "a new test is already disabled: {ev}",
        "K3.tol": "tolerance loosened: {ev}",
        "K4.swallow": "{n} new swallowed error(s): {ev}",
        "K5.lowered": "pass line lowered: {ev}",
        "K5.raised": "limit raised: {ev}",
        "K5.deleted": "pass line deleted: {ev}",
        "K5.pinned": "pinned number changed: {ev}",
        "K0.parse": "could not parse {ev} — assertion, skip and swallow checks did not run on this file",
        "K0.size": "{ev} is too large or binary — not checked",
        "K0.many": "more than {n} files changed — only the first {n} were checked",
        "blocked_commit": "bar-lock blocked this commit — {n} change(s) made the checks easier instead of making the code right:",
        "blocked_push": "bar-lock blocked this push — {n} change(s) in this session made the checks easier instead of making the code right:",
        "blocked_stop": "bar-lock: not done yet — {n} change(s) made the checks easier instead of making the code right:",
        "stop_giving_up": "bar-lock: still unresolved after {k} reminder(s) — NOT blocking again. Please review these before you trust the result:",
        "notes": "bar-lock — worth a look (not blocking):",
        "to_ai": ("Do not retry the same thing and do not look for another way around this. Fix the code so the ORIGINAL check passes, "
                  "or undo the change. If you believe a change here is really intended, stop and tell the user; only the user can approve it, "
                  "by running in their own terminal:\n  python3 .bar-lock/bar_lock.py waive {ids} --reason \"why\"\n"
                  "A waiver or an `allow` comment written by you does not count."),
        "self": "bar-lock: the AI may not change its own waivers, limits or hooks. Ask the user to do it in their own terminal.",
        "no_check": "bar-lock: this check did not run ({err}). The action was allowed — nothing was verified.",
        "baseline": "bar-lock: no session baseline yet (installed mid-session?). Starting one now; earlier changes are not judged.",
        "installed": "bar-lock installed in {root}\n  engine:  {engine}\n  hooks:   {settings} ({events})\n  config:  {config}\n"
                     "Start a new Claude Code session in this project to load the hooks.\nGrant a waiver (from your own terminal, never the AI's): python3 {engine} waive <id> --reason \"…\"",
        "already": "bar-lock hooks were already in {settings}; engine refreshed.",
        "removed": "bar-lock removed from {root}: {n} hook entr(ies) deleted{data}.",
        "removed_data": ", {dir} deleted", "kept_data": ", {dir} kept",
        "bad_settings": "{path} is not valid JSON ({err}). Fix it first; bar-lock will not overwrite it.",
        "clean": "bar-lock: nothing made easier.",
        "waived_n": "({n} waived)",
        "waive_done": "waived {n} finding(s): {ids}",
        "waive_none": "nothing to waive — give an id from a bar-lock message, or --current",
        "waive_reason": "a waiver needs --reason \"why\"",
        "waive_list": "{fp}  {when}  {code}  {path}  — {reason}",
        "waive_empty": "no waivers.",
        "not_installed": "bar-lock is not installed here (no .bar-lock/ found). Run: python3 bar_lock.py init",
        "not_git": "not inside a git repository",
    },
    "zh": {
        "K1.removed": "少了 {n} 条断言，也没补回来：{ev}",
        "K1.weakened": "断言变弱：{ev}",
        "K1.trivial": "新增了恒真断言：{ev}",
        "K1.file_deleted": "测试文件被删（{n} 个测试），哪里都没再出现",
        "K1.file_deleted_part": "测试文件被删（{n} 个测试），其中一部分在别处还在",
        "K1.file_deleted_with_code": "测试文件和它旁边的代码一起被删了（{n} 个测试）——像是整个功能被拿掉",
        "K1.func_deleted": "测试被删：{ev}",
        "K1.renamed_off": "测试被改名，不再被运行：{ev}",
        "K1.edited": "期望值被改了：{ev}",
        "K2.skip": "测试被停用：{ev}",
        "K2.skip_new": "新写的测试一出生就是停用的：{ev}",
        "K3.tol": "容差放宽：{ev}",
        "K4.swallow": "新增 {n} 处吞掉错误：{ev}",
        "K5.lowered": "及格线数字被调低：{ev}",
        "K5.raised": "上限被调高：{ev}",
        "K5.deleted": "及格线数字被删：{ev}",
        "K5.pinned": "被你钉住的数字变了：{ev}",
        "K0.parse": "解析不了 {ev}——这个文件的断言 / 停用 / 吞错检查没跑",
        "K0.size": "{ev} 太大或是二进制——没检查",
        "K0.many": "改动超过 {n} 个文件——只检查了前 {n} 个",
        "blocked_commit": "bar-lock 拦下了这次提交——有 {n} 处改动是把检查变容易，而不是把代码改对：",
        "blocked_push": "bar-lock 拦下了这次推送——本会话有 {n} 处改动是把检查变容易，而不是把代码改对：",
        "blocked_stop": "bar-lock：还不能算做完——有 {n} 处改动是把检查变容易，而不是把代码改对：",
        "stop_giving_up": "bar-lock：提醒了 {k} 次仍未处理——不再拦了。信结果之前请先看这几条：",
        "notes": "bar-lock——值得看一眼（不拦）：",
        "to_ai": ("不要重试同样的事，也不要找别的办法绕过去。把代码改对，让「原来的」检查通过；或者撤销这处改动。如果你认为某处改动确实是有意的，"
                  "停下来告诉用户；只有用户能放行，方法是在他自己的终端里运行：\n  python3 .bar-lock/bar_lock.py waive {ids} --reason \"原因\"\n"
                  "你自己写的豁免或 `allow` 注释不算数。"),
        "self": "bar-lock：AI 不能改自己的豁免、设置和 hook。请让用户在他自己的终端里操作。",
        "no_check": "bar-lock：这次检查没跑成（{err}）。动作已放行——没有验证任何东西。",
        "baseline": "bar-lock：还没有本会话的基线（中途才装的？）。现在开始记；之前的改动不判。",
        "installed": "bar-lock 已装进 {root}\n  引擎：{engine}\n  hooks：{settings}（{events}）\n  配置：{config}\n"
                     "在这个项目里新开一个 Claude Code 会话，hooks 就生效了。\n放行一处改动（在你自己的终端里，不是 AI 的）：python3 {engine} waive <编号> --reason \"…\"",
        "already": "{settings} 里已经有 bar-lock 的 hooks；引擎已刷新。",
        "removed": "已从 {root} 卸载 bar-lock：删掉 {n} 条 hook{data}。",
        "removed_data": "，已删除 {dir}", "kept_data": "，保留了 {dir}",
        "bad_settings": "{path} 不是合法的 JSON（{err}）。请先修好它；bar-lock 不会覆盖它。",
        "clean": "bar-lock：没有把检查变容易的改动。",
        "waived_n": "（另有 {n} 条已豁免）",
        "waive_done": "已豁免 {n} 条：{ids}",
        "waive_none": "没有可豁免的——请给 bar-lock 提示里的编号，或用 --current",
        "waive_reason": "豁免需要写原因：--reason \"原因\"",
        "waive_list": "{fp}  {when}  {code}  {path}  ——{reason}",
        "waive_empty": "没有豁免记录。",
        "not_installed": "这里没装 bar-lock（找不到 .bar-lock/）。请运行：python3 bar_lock.py init",
        "not_git": "不在 git 仓库里",
    },
}


def tx(lang: str, key: str, **kw) -> str:
    return TEXT.get(lang, TEXT["en"])[key].format(**kw)


# --- Findings ---
@dataclass
class Finding:
    code: str               # "K1.weakened"
    level: str              # "block" | "note"
    path: str
    line: int = 0
    where: str = ""         # qualified test name, when there is one
    ev: str = ""            # short evidence, e.g. "assert a == 1 → assert a"
    n: int = 1
    key: str = ""           # stable identity (not the line number) — feeds the id

    @property
    def kind(self) -> str:
        return self.code.split(".")[0]

    @property
    def fp(self) -> str:
        raw = f"{self.code}|{self.path}|{self.where}|{self.key or self.ev}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:24]

    def location(self) -> str:
        loc = self.path + (f":{self.line}" if self.line else "")
        return loc + (f" ({self.where})" if self.where else "")

    def text(self, lang: str) -> str:
        return tx(lang, self.code, n=self.n, ev=self.ev)

    def as_dict(self, lang: str = "en") -> dict:
        return {"id": self.fp, "code": self.code, "kind": self.kind, "level": self.level, "path": self.path,
                "line": self.line, "where": self.where, "evidence": self.ev, "message": self.text(lang)}


def _short(s, n: int = 90) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Aux:
    """What one file can't decide alone: moves between files, and tests that vanish or reappear."""
    removed: list = field(default_factory=list)         # K1.removed candidates
    pool: int = 0                                       # real assertions added that nothing in-place used up
    deleted_funcs: list = field(default_factory=list)   # (path, qualname, short name, n_asserts)
    deleted_files: list = field(default_factory=list)   # (path, set of test names, set of imported module paths)
    deleted_other: list = field(default_factory=list)   # paths of deleted non-test files
    new_names: set = field(default_factory=set)         # names of functions / test titles that are new


# --- Globs ---
@functools.lru_cache(maxsize=512)
def _glob_re(g: str):
    out, i = [], 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def match_any(path: str, globs) -> bool:
    return any(_glob_re(g).match(path) for g in globs or ())


# --- Python: assertions, disabled tests, swallowed errors — read from the syntax tree, before and after ---
def _unp(node) -> str:
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:  # noqa: BLE001 — never let a weird node break a check
        return "<expr>"


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


@dataclass
class Assertion:
    norm: str
    cls: str                # specific | call | bare | trivial | other
    subj: tuple = ()
    key: tuple = ()
    line: int = 0


def _truthy(n) -> bool:
    return isinstance(n, ast.Constant) and bool(n.value)


def _falsy(n) -> bool:
    return isinstance(n, ast.Constant) and not n.value


def _classify_expr(t) -> tuple:
    """(cls, subjects, key) of the expression inside a plain `assert`."""
    if _truthy(t):
        return "trivial", (), ()
    if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not):
        return ("trivial", (), ()) if _falsy(t.operand) else _classify_expr(t.operand)
    if isinstance(t, ast.Compare) and len(t.ops) == 1:
        op, right, left = t.ops[0], t.comparators[0], t.left
        if isinstance(op, (ast.IsNot, ast.NotEq)) and isinstance(right, ast.Constant) and right.value is None:
            return "bare", (_unp(left),), ()
        if _unp(left) == _unp(right):
            return "trivial", (), ()
        return "specific", (_unp(left), _unp(right)), (_unp(left), type(op).__name__)
    if isinstance(t, ast.Call) and _dotted(t.func) in ("isinstance", "issubclass") and t.args:
        return "specific", (_unp(t.args[0]),), ("isinstance", _unp(t.args[0]))
    if isinstance(t, ast.BoolOp):
        parts = [_classify_expr(v) for v in t.values]
        weak = ("bare", "call")
        if all(p[0] in weak for p in parts):
            return ("bare" if all(p[0] == "bare" for p in parts) else "call"), tuple(s for p in parts for s in p[1]), ()
        return "specific", tuple(s for p in parts for s in p[1]), tuple(k for p in parts for k in p[2])
    if isinstance(t, ast.Call):
        return "call", (_unp(t),), ()
    return "bare", (_unp(t),), ()


_UNITTEST_BARE = {"assertTrue": _truthy, "assertFalse": _falsy}


def _call_assertion(call: ast.Call):
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    if not (name.startswith("assert")):
        return None
    norm = _unp(call)
    args = call.args
    if name in _UNITTEST_BARE and args:
        weak = "call" if isinstance(args[0], ast.Call) else "bare"
        return Assertion(norm, "trivial" if _UNITTEST_BARE[name](args[0]) else weak, (_unp(args[0]),), (), call.lineno)
    if name == "assertIsNotNone" and args:
        return Assertion(norm, "bare", (_unp(args[0]),), (), call.lineno)
    if len(args) >= 2 and name in ("assertEqual", "assertEquals", "assertIs", "assertNotEqual", "assertIn",
                                   "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
                                   "assertAlmostEqual", "assertCountEqual", "assertDictEqual", "assertListEqual",
                                   "assertSequenceEqual", "assertSetEqual", "assertTupleEqual", "assertIsInstance"):
        if name in ("assertEqual", "assertEquals", "assertIs") and _unp(args[0]) == _unp(args[1]):
            return Assertion(norm, "trivial", (), (), call.lineno)
        return Assertion(norm, "specific", (_unp(args[0]), _unp(args[1])), (name, _unp(args[0])), call.lineno)
    if name == "assertIsNone" and args:
        return Assertion(norm, "specific", (_unp(args[0]),), (name, _unp(args[0])), call.lineno)
    return Assertion(norm, "other", (), (name, _unp(f)), call.lineno)


_BROAD = {"Exception", "BaseException", "AssertionError"}


def _broad(t) -> bool:
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in _BROAD
    if isinstance(t, ast.Attribute):
        return t.attr in _BROAD
    if isinstance(t, ast.Tuple):
        return any(_broad(e) for e in t.elts)
    return False


def _inert(body) -> bool:
    for s in body:
        if isinstance(s, (ast.Pass, ast.Continue)):
            continue
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
            continue
        if isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant)):
            continue
        return False
    return True


def _deco_skip(d) -> str | None:
    """Shown text if this decorator disables a test unconditionally; None for env guards like skipif(sys.platform...)."""
    call = d if isinstance(d, ast.Call) else None
    name = _dotted(call.func if call else d)
    last = name.rsplit(".", 1)[-1]
    args = call.args if call else []
    kw = {k.arg: k.value for k in (call.keywords if call else []) if k.arg}
    if last in ("skip", "expectedFailure"):
        return "@" + _unp(d)
    if last == "xfail":
        cond = kw.get("condition") if "condition" in kw else (args[0] if args else None)
        return "@" + _unp(d) if cond is None or _truthy(cond) else None
    if last in ("skipif", "skipIf"):
        cond = args[0] if args else kw.get("condition")
        return "@" + _unp(d) if cond is not None and _truthy(cond) else None
    if last == "skipUnless":
        cond = args[0] if args else kw.get("condition")
        return "@" + _unp(d) if cond is not None and _falsy(cond) else None
    return None


_SKIP_CALLS = {"pytest.skip", "pytest.xfail"}


def _stmt_skip(s) -> str | None:
    """A skip written as a plain statement of the function body (not inside an `if`): unconditional."""
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
        name = _dotted(s.value.func)
        if name in _SKIP_CALLS or name.rsplit(".", 1)[-1] == "skipTest":
            return _unp(s.value)
    if isinstance(s, ast.Raise) and s.exc is not None:
        e = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
        if _dotted(e).rsplit(".", 1)[-1] == "SkipTest":
            return "raise " + _unp(s.exc)
    return None


@dataclass
class Fn:
    line: int = 0
    is_class: bool = False
    asserts: list = field(default_factory=list)
    skips: list = field(default_factory=list)       # shown texts
    swallows: list = field(default_factory=list)    # shown texts
    param: int = 0
    loop_asserts: int = 0


class _Collect(ast.NodeVisitor):
    def __init__(self):
        self.fns = {"<module>": Fn()}
        self.names: list = []
        self.cur: list = ["<module>"]
        self.loop = 0

    def _scope(self, node, is_class: bool):
        qn = ".".join(self.names + [node.name])
        fn = Fn(line=node.lineno, is_class=is_class)
        for d in node.decorator_list:
            s = _deco_skip(d)
            if s:
                fn.skips.append(s)
            if _dotted(d.func if isinstance(d, ast.Call) else d).endswith("parametrize"):
                fn.param += 1
        if not is_class:
            for st in node.body:
                s = _stmt_skip(st)
                if s:
                    fn.skips.append(s)
        self.fns[qn] = fn
        self.names.append(node.name)
        self.cur.append(qn)
        saved, self.loop = self.loop, 0
        for st in node.body:
            self.visit(st)
        self.loop = saved
        self.cur.pop()
        self.names.pop()

    def visit_FunctionDef(self, node):
        self._scope(node, False)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._scope(node, True)

    def _loop(self, node):
        self.loop += 1
        self.generic_visit(node)
        self.loop -= 1

    visit_For = visit_While = visit_AsyncFor = _loop

    def _add(self, a: Assertion):
        fn = self.fns[self.cur[-1]]
        fn.asserts.append(a)
        if self.loop:
            fn.loop_asserts += 1

    def visit_Assert(self, node):
        cls, subj, key = _classify_expr(node.test)
        self._add(Assertion("assert " + _unp(node.test), cls, subj, key, node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node):
        a = _call_assertion(node)
        if a:
            self._add(a)
        elif _dotted(node.func).rsplit(".", 1)[-1] in ("raises", "warns") and len(node.args) >= 2:
            self._add(Assertion(_unp(node), "other", (), ("raises", _unp(node.func)), node.lineno))
        self.generic_visit(node)

    def visit_With(self, node):
        for it in node.items:
            ce = it.context_expr
            if not isinstance(ce, ast.Call):
                continue
            last = _dotted(ce.func).rsplit(".", 1)[-1]
            if last in ("raises", "warns", "assertRaises", "assertWarns", "assertRaisesRegex", "deprecated_call"):
                self._add(Assertion(_unp(ce), "other", (), ("raises", _unp(ce.func)), node.lineno))
            elif last == "suppress" and any(_broad(a) for a in ce.args):
                self.fns[self.cur[-1]].swallows.append("with " + _unp(ce))
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if _broad(node.type) and _inert(node.body):
            head = "except" + (" " + _unp(node.type) if node.type is not None else "")
            self.fns[self.cur[-1]].swallows.append(head + ": " + (_unp(node.body[0]) if node.body else "pass"))
        self.generic_visit(node)

    def visit_Assign(self, node):
        if self.cur == ["<module>"] and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            vals = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            for v in vals:
                s = _deco_skip(v)
                if s:
                    self.fns["<module>"].skips.append(s)
        self.generic_visit(node)


def py_collect(src: str):
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, RecursionError):
        return None
    c = _Collect()
    for st in tree.body:
        s = _stmt_skip(st)
        if s:
            c.fns["<module>"].skips.append(s)
        c.visit(st)
    return c.fns


def _extra(items: list, other: list, key=lambda x: x) -> list:
    """What `items` has beyond `other` (multiset difference, order kept)."""
    left = Counter(key(x) for x in other)
    out = []
    for x in items:
        k = key(x)
        if left.get(k, 0) > 0:
            left[k] -= 1
        else:
            out.append(x)
    return out


def _assert_delta(before: list, after: list):
    """Compare one function's assertions before/after.
    Returns (events, removed, add_left): events are (code, level, old, new) for weakened / trivial / edited;
    `removed` is what was lost for good; `add_left` counts real assertions added that nothing here used up."""
    rem = _extra(before, after, lambda a: a.norm)
    add = _extra(after, before, lambda a: a.norm)
    events = [("K1.trivial", "block", None, a) for a in add if a.cls == "trivial"]
    add = [a for a in add if a.cls != "trivial"]
    rem = [r for r in rem if r.cls != "trivial"]
    for r in list(rem):
        if r.cls != "specific":
            continue
        hit = next((x for x in add if x.cls in ("bare", "call") and x.subj and x.subj[0] in r.subj), None)
        if hit is not None:
            events.append(("K1.weakened", "block", r, hit))
            rem.remove(r)
            add.remove(hit)
    for r in list(rem):
        if r.cls != "specific":
            continue
        hit = next((x for x in add if x.cls == "specific" and x.key == r.key), None)
        if hit is not None:
            events.append(("K1.edited", "note", r, hit))
            rem.remove(r)
            add.remove(hit)
    strong = [x for x in add if x.cls in ("specific", "other", "call")]
    bare = [x for x in add if x.cls == "bare"]
    lost = []
    for r in rem:           # an edited assertion is not a lost one: pair each with a replacement as strong as itself
        pool = bare if r.cls == "bare" and bare else strong
        if pool:
            pool.pop()
        else:
            lost.append(r)
    return events, lost, len(strong)


def analyze_python(path: str, before: str | None, after: str, is_test: bool, is_gate: bool, aux: Aux) -> list:
    out = []
    ta = py_collect(after)
    tb = py_collect(before) if before is not None else {}
    if ta is None or tb is None:
        return [Finding("K0.parse", "note", path, ev=path, key="parse")]
    if is_test:
        for qn, fa in ta.items():
            if qn not in tb:
                aux.new_names.add(qn.rsplit(".", 1)[-1])
                aux.pool += sum(1 for a in fa.asserts if a.cls in ("specific", "other", "call"))
        for qn, fb in tb.items():
            if qn not in ta and qn.rsplit(".", 1)[-1].startswith("test"):
                aux.deleted_funcs.append((path, qn, qn.rsplit(".", 1)[-1], sum(1 for a in fb.asserts if a.cls != "trivial")))
    for qn, fa in ta.items():
        fb = tb.get(qn)
        loc = qn if qn != "<module>" else ""
        if is_test and fa.skips and (fb is None or len(fa.skips) > len(fb.skips)):
            shown = (_extra(fa.skips, fb.skips if fb else []) or fa.skips)[0]
            if fb is not None or (qn == "<module>" and before is not None):
                out.append(Finding("K2.skip", "block", path, fa.line, loc, _short(shown), key=shown))
            else:
                out.append(Finding("K2.skip_new", "note", path, fa.line, loc, _short(shown), key=shown))
        if fb is None:
            continue
        in_test = is_test and qn.rsplit(".", 1)[-1].startswith("test")
        if (in_test or is_gate) and len(fa.swallows) > len(fb.swallows):
            k = len(fa.swallows) - len(fb.swallows)
            shown = next((s for s in fa.swallows if s not in fb.swallows), fa.swallows[-1])
            out.append(Finding("K4.swallow", "block", path, fa.line, loc, _short(shown), n=k, key=f"{shown}|{len(fa.swallows)}"))
        if not is_test or fa.is_class:
            continue
        events, removed, add_left = _assert_delta(fb.asserts, fa.asserts)
        for code, level, old, new in events:
            ev = _short(f"{old.norm} → {new.norm}") if old else _short(new.norm)
            out.append(Finding(code, level, path, new.line, loc, ev, key=ev))
        consolidated = fa.param > fb.param or fa.loop_asserts > fb.loop_asserts   # parametrize / loop: same checks, fewer lines
        if removed and not consolidated:
            aux.removed.append(Finding("K1.removed", "block", path, fa.line, loc, _short("; ".join(a.norm for a in removed[:2])),
                                       n=len(removed), key=loc + "|" + "|".join(a.norm for a in removed)))
        else:
            add_left += len(removed)
        aux.pool += add_left
    return out


# --- JavaScript / TypeScript: same five questions, by regex on the changed lines ---
JS_ASSERT = re.compile(r"\bexpect\s*\(|\bassert(?:\.\w+)?\s*\(|\.should\b|\bt\.(?:is|true|false|deepEqual|equal|truthy|falsy|throws)\s*\(")
JS_TRIVIAL = re.compile(r"""expect\s*\(\s*(true|1|"[^"]*"|'[^']*')\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"""
                        r"""|expect\s*\(\s*true\s*\)\s*\.toBeTruthy\(\)|\bassert(?:\.ok)?\s*\(\s*true\s*[,)]""")
JS_STRICT = re.compile(r"expect\s*\((?P<arg>.+?)\)\s*\.(?:toBe|toEqual|toStrictEqual|toHaveLength|toContain|toMatch)\w*\(")
JS_LOOSE = re.compile(r"expect\s*\((?P<arg>.+?)\)\s*\.(?:toBeTruthy|toBeDefined|not\.toBeNull|not\.toBeUndefined|toBeFalsy)\(")
JS_SKIP = re.compile(r"\b(?:it|test|describe|context)\.(?:skip|todo)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\(|\.only\s*\(")
JS_EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*(?:(?:/\*.*?\*/|//[^\n]*)\s*)*\}|\.catch\(\s*\(\s*\w*\s*\)\s*=>\s*\{\s*\}\s*\)", re.S)
JS_TITLE = re.compile(r"""\b(?:it|test)(?:\.\w+)?\s*\(\s*(['"`])(.+?)\1""")


def _js_code(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith(("//", "*", "/*"))


def analyze_js(path: str, before: str | None, after: str, aux: Aux) -> list:
    out = []
    bl = before.splitlines() if before is not None else []
    al = after.splitlines()
    rem, add = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, bl, al, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        rem += [(i + 1, bl[i]) for i in range(i1, i2) if _js_code(bl[i])]
        add += [(j + 1, al[j]) for j in range(j1, j2) if _js_code(al[j])]
    used_r, used_a = set(), set()
    for ri, (rl, rt) in enumerate(rem):
        m = JS_STRICT.search(rt)
        if not m:
            continue
        for ai, (al_, at) in enumerate(add):
            n = JS_LOOSE.search(at)
            if ai not in used_a and n and n.group("arg").strip() == m.group("arg").strip():
                ev = _short(f"{rt.strip()} → {at.strip()}")
                out.append(Finding("K1.weakened", "block", path, al_, "", ev, key=ev))
                used_r.add(ri)
                used_a.add(ai)
                break
    for ai, (al_, at) in enumerate(add):
        if JS_ASSERT.search(at) and JS_TRIVIAL.search(at):
            out.append(Finding("K1.trivial", "block", path, al_, "", _short(at.strip()), key=at.strip()))
            used_a.add(ai)
    gone = [t for i, (_, t) in enumerate(rem) if i not in used_r and JS_ASSERT.search(t) and not JS_TRIVIAL.search(t)]
    came = [t for i, (_, t) in enumerate(add) if i not in used_a and JS_ASSERT.search(t)]
    if len(gone) > len(came):
        aux.removed.append(Finding("K1.removed", "block", path, 0, "", _short("; ".join(g.strip() for g in gone[:2])),
                                   n=len(gone) - len(came), key="|".join(g.strip() for g in gone)))
    aux.pool += max(0, len(came) - len(gone))
    new_skips = [(ln, t) for ln, t in add if JS_SKIP.search(t)]
    if len(new_skips) > sum(1 for _, t in rem if JS_SKIP.search(t)):
        ln, t = new_skips[0]
        out.append(Finding("K2.skip", "block", path, ln, "", _short(t.strip()), key=t.strip()))
    nb = len(JS_EMPTY_CATCH.findall(before or ""))
    na = len(JS_EMPTY_CATCH.findall(after))
    if na > nb:
        out.append(Finding("K4.swallow", "note", path, 0, "", "empty catch", n=na - nb, key=f"catch|{na}"))
    tb = {m.group(2) for m in JS_TITLE.finditer(before or "")}
    ta = {m.group(2) for m in JS_TITLE.finditer(after)}
    aux.new_names |= ta - tb
    for title in tb - ta:
        aux.deleted_funcs.append((path, title, title, 0))
    return out


# --- Numbers: a tolerance widened (K3), a pass line lowered or a limit raised (K5), pinned numbers (K5) ---
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_ASSIGN = re.compile(r"""(?P<name>[A-Za-z_][\w.\-]*)["']?\s*(?:>=|<=|==|=|:)\s*["']?(?P<val>""" + _NUM + r""")(?![\w.])""")
_FLAG = re.compile(r"(?<![\w-])(?P<name>--[A-Za-z][\w\-]*)(?:=|\s+)(?P<val>" + _NUM + r")(?![\w.])")
_CLOSE = re.compile(r"toBeCloseTo\s*\([^()]*,\s*(?P<val>\d+)\s*\)")
_ANNOT = re.compile(r"\b([A-Za-z_]\w*)\s*:\s*[A-Za-z_][\w\[\], .|]*\s*=(?!=)")
STRONG_MIN = re.compile(r"(?:^|_)(?:fail_?under|cov(?:erage)?_?(?:min|threshold|target|fail\w*)|min_?(?:cov(?:erage)?|score|accuracy|acc|pass|pass_?rate"
                        r"|precision|recall|f1|quality|tests?|assertions?)|pass_?(?:score|mark|line|rate|ratio|threshold|percent\w*)"
                        r"|passing_?(?:score|grade|mark|rate)|(?:target|required)_?(?:score|accuracy|coverage|pass\w*))(?:_|$)")
GATE_NAME = re.compile(r"(?:^|_)(?:fail_?under|cov(?:erage)?_?(?:min|threshold|target|fail\w*)|max_?fail\w*|allowed_?fail\w*)(?:_|$)")
STRONG_MAX = re.compile(r"(?:^|_)(?:max_?(?:fail\w*|errors?|warnings?|violations?|issues?|regress\w*|complexity|line_?length|args|branches|statements|locals)"
                        r"|allowed_?(?:fail\w*|errors?|warnings?)|error_?budget)(?:_|$)")
WEAK_MIN = re.compile(r"(?:^|_)(?:threshold|cut_?off)(?:_|$)")
TOL = re.compile(r"^(?:[ar]tol|abs_?tol|rel_?tol|tol|tolerance)$")
TOL_IN_TESTS = re.compile(r"^(?:[ar]tol|abs_?tol|rel_?tol|tol|tolerance|delta|epsilon|eps|rel|abs)$")
DIGITS = re.compile(r"^(?:places|decimal)$")
JEST_COV = {"lines", "branches", "functions", "statements"}


def _norm_name(raw: str) -> str:
    n = raw.lstrip("-").rsplit(".", 1)[-1].replace("-", "_")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", n).lower()


def _nums_of(lines: list, offset: int) -> dict:
    """name -> [(value, line number, raw name, line text)] in source order."""
    found: dict = {}
    for i, raw_line in enumerate(lines):
        line = _ANNOT.sub(r"\1 =", raw_line)
        hits = []
        for rx in (_ASSIGN, _FLAG):
            hits += [(m.start(), m.group("name"), m.group("val")) for m in rx.finditer(line)]
        hits += [(m.start(), "places", m.group("val")) for m in _CLOSE.finditer(line)]
        for _pos, name, val in sorted(hits):
            try:
                v = float(val)
            except ValueError:
                continue
            found.setdefault(_norm_name(name), []).append((v, offset + i + 1, name, raw_line.strip()))
    return found


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) and abs(v) < 1e15 else f"{v:g}"


def _classify(name: str, ctx: str, is_test: bool):
    if STRONG_MIN.search(name):
        return "min", True
    if STRONG_MAX.search(name):
        return "max", True
    if name in JEST_COV and "coveragethreshold" in ctx.replace("_", "").replace("-", ""):
        return "min", True
    if is_test and TOL_IN_TESTS.match(name):
        return "tol", True
    if TOL.match(name):
        return "tol", False
    if DIGITS.match(name):
        return ("digits", True) if is_test else None
    if WEAK_MIN.search(name):
        return "min", False
    return None


def _judge(path: str, name: str, old: float, new: float, line: int, ctx: str, is_test: bool, pinned: bool):
    ev = f"{name}: {_fmt(old)} → {_fmt(new)}"
    key = f"{_norm_name(name)}|{_fmt(old)}|{_fmt(new)}"
    if pinned:
        return Finding("K5.pinned", "block", path, line, "", ev, key=key)
    cls = _classify(_norm_name(name), ctx, is_test)
    if cls is None:
        return None
    kind, strong = cls
    if kind in ("min", "digits"):
        worse = new < old
    else:
        worse = new > old
    if not worse:
        return None
    if kind == "min":
        return Finding("K5.lowered", "block" if strong else "note", path, line, "", ev, key=key)
    if kind == "max":
        return Finding("K5.raised", "block", path, line, "", ev, key=key)
    rel = (1.0 if old - new >= 1 else 0.0) if kind == "digits" else (abs(new - old) / abs(old) if old else 1.0)
    return Finding("K3.tol", "block" if strong and rel >= 0.1 else "note", path, line, "", ev, key=key)


def analyze_numbers(path: str, before: str, after: str, is_test: bool, pinned: bool) -> list:
    out = []
    bl, al = before.splitlines(), after.splitlines()
    after_low = after.lower()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, bl, al, autojunk=False).get_opcodes():
        if tag in ("equal", "insert"):
            continue
        ctx = " ".join(al[max(0, j1 - 3): j2 + 3] + bl[max(0, i1 - 3): i2 + 3]).lower()
        old_nums = _nums_of(bl[i1:i2], i1)
        new_nums = _nums_of(al[j1:j2], j1) if tag == "replace" else {}
        for name, olds in old_nums.items():
            news = new_nums.get(name, [])
            for k, (ov, _ol, raw, _txt) in enumerate(olds):
                if k < len(news):
                    nv, nline = news[k][0], news[k][1]
                    f = _judge(path, news[k][2], ov, nv, nline, ctx, is_test, pinned) if nv != ov else None
                elif raw.lower() not in after_low and (pinned or _classify(name, ctx, is_test) in (("min", True), ("max", True))):
                    f = Finding("K5.pinned" if pinned else "K5.deleted", "block" if pinned or GATE_NAME.search(name) else "note",
                                path, i1 + 1, "", f"{raw}: {_fmt(ov)} → (gone)", key=f"{name}|{_fmt(ov)}|gone")
                else:
                    f = None
                if f:
                    out.append(f)
    return out


# --- One file, then the whole change ---
@dataclass
class FileChange:
    path: str
    before: str | None
    after: str | None
    skip: str = ""          # "size" when the file is binary or too large


def compile_cfg(cfg: dict) -> dict:
    c = dict(cfg)
    c["_test"] = list(cfg.get("test_globs") or DEFAULT_TEST_GLOBS) + list(cfg.get("extra_test_globs") or [])
    c["_ignore"] = DEFAULT_IGNORE_GLOBS + list(cfg.get("ignore") or [])
    return c


def _imported_paths(src: str) -> set:
    """`from a.b import c` -> {a/b, a/b/c}: where the code a test imports would live."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.replace(".", "/") for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            base = node.module.replace(".", "/")
            out |= {base} | {base + "/" + a.name for a in node.names}
    return out


def analyze_file(fc: FileChange, cfg: dict, aux: Aux) -> list:
    p = fc.path
    if fc.skip:
        return [Finding("K0.size", "note", p, ev=p, key="size")]
    is_test = match_any(p, cfg["_test"])
    is_gate = match_any(p, cfg.get("gate_globs"))
    pinned = match_any(p, cfg.get("pinned"))
    ext = Path(p).suffix.lower()
    out = []
    if fc.after is None:
        if pinned and fc.before is not None:
            out.append(Finding("K5.pinned", "block", p, 0, "", "file deleted", key="deleted"))
        if not is_test:
            aux.deleted_other.append(p)
        if is_test and fc.before is not None:
            names, stems = set(), set()
            if ext == ".py":
                tb = py_collect(fc.before) or {}
                names = {q.rsplit(".", 1)[-1] for q in tb if q.rsplit(".", 1)[-1].startswith("test")}
                stems = _imported_paths(fc.before)
            elif ext in JS_EXT:
                names = {m.group(2) for m in JS_TITLE.finditer(fc.before)}
            if names:
                aux.deleted_files.append((p, names, stems))
        return out
    if ext == ".py" and (is_test or is_gate):
        out += analyze_python(p, fc.before, fc.after, is_test, is_gate, aux)
    elif ext in JS_EXT and is_test:
        out += analyze_js(p, fc.before, fc.after, aux)
    if fc.before is not None:
        out += analyze_numbers(p, fc.before, fc.after, is_test, pinned)
    return out


def _renamed_off(name: str) -> set:
    bare = name[len("test"):].lstrip("_") if name.startswith("test") else name
    return {"_" + name, bare, name + "_disabled", "disabled_" + name, "_disabled_" + name, "skip_" + name, "x" + name,
            "_" + bare, "old_" + name, name + "_old", name + "_skip", name + "_off"}


def finalize(aux: Aux) -> list:
    out = []
    if aux.removed and aux.pool < sum(f.n for f in aux.removed):    # not moved elsewhere in the same change
        out += aux.removed
    for path, qn, short, n in aux.deleted_funcs:
        if short in aux.new_names:
            continue                                                   # moved or renamed, still there
        hit = next((c for c in _renamed_off(short) if c in aux.new_names), None)
        if hit:
            out.append(Finding("K1.renamed_off", "block", path, 0, qn, _short(f"{short} → {hit}"), key=f"{short}|{hit}"))
        else:
            out.append(Finding("K1.func_deleted", "note", path, 0, "", _short(qn), key=qn))
    for path, names, stems in aux.deleted_files:
        back = names & aux.new_names
        root = path.rsplit("/", 1)[0] if "/" in path else ""
        if root.rsplit("/", 1)[-1] in ("tests", "test", "__tests__") and "/" in root:
            root = root.rsplit("/", 1)[0]
        code_gone = (root and any(o.startswith(root + "/") for o in aux.deleted_other)) or any(
            o == st + ".py" or o.endswith("/" + st + ".py") or o.endswith("/" + st + "/__init__.py")
            for st in stems for o in aux.deleted_other)
        if not back and code_gone:
            out.append(Finding("K1.file_deleted_with_code", "note", path, 0, "", path, n=len(names), key=f"code{len(names)}"))
        elif not back:
            out.append(Finding("K1.file_deleted", "block", path, 0, "", path, n=len(names), key=f"{len(names)}"))
        elif back != names:
            out.append(Finding("K1.file_deleted_part", "note", path, 0, "", path, n=len(names), key=f"part{len(names)}"))
    return out


def analyze(changes: list, cfg: dict) -> list:
    cfg = compile_cfg(cfg)
    aux, out = Aux(), []
    for fc in changes:
        out += analyze_file(fc, cfg, aux)
    out += finalize(aux)
    seen, uniq = set(), []
    for f in out:
        if f.fp not in seen:
            seen.add(f.fp)
            uniq.append(f)
    uniq.sort(key=lambda f: (f.level != "block", f.path, f.line))
    return uniq


# --- Git: only reads. Never runs the repository's own code. ---
class GitError(Exception):
    pass


class Repo:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def discover(start):
        try:
            p = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode != 0:
            return None
        return Repo(Path(p.stdout.decode("utf-8", "surrogateescape").strip()))

    def git(self, *args, ok=(0,)) -> bytes:
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        try:
            p = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(self.root), *args], capture_output=True,
                               timeout=60, env=env)
        except (OSError, subprocess.SubprocessError) as e:
            raise GitError(f"git {args[0]}: {e}") from e
        if p.returncode not in ok:
            raise GitError(f"git {' '.join(args[:2])}: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return p.stdout

    def head(self):
        out = self.git("rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).decode().strip()
        return out or None

    def name_status(self, *args) -> list:
        raw = self.git("diff", "--name-status", "-z", "--no-renames", *args).split(b"\0")
        return [(a.decode("ascii", "replace"), b.decode("utf-8", "surrogateescape")) for a, b in zip(raw[0::2], raw[1::2])]

    def blob(self, spec: str):
        try:
            return self.git("show", spec)
        except GitError:
            return None

    def read(self, rel: str):
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return None


def _decode(data):
    """(text, skip) — None data means the file doesn't exist on that side."""
    if data is None:
        return None, ""
    if len(data) > MAX_FILE_BYTES or b"\0" in data[:8000]:
        return None, "size"
    return data.decode("utf-8", "replace"), ""


def load_changes(repo: Repo, spec: tuple, cfg: dict) -> tuple:
    """spec: ("worktree", base|None) | ("staged",) | ("range", a, b).  Returns (changes, notes)."""
    c = compile_cfg(cfg)
    head = repo.head()
    if spec[0] == "worktree":
        base = spec[1] or head
        if base is None:
            return [], []                       # no commit yet: nothing to have weakened
        names = repo.name_status(base)
        before, after = (lambda p: repo.blob(f"{base}:{p}")), repo.read
    elif spec[0] == "staged":
        base = head
        if base is None:
            return [], []
        names = repo.name_status("--cached", base)
        before, after = (lambda p: repo.blob(f"{base}:{p}")), (lambda p: repo.blob(f":{p}"))
    else:
        names = repo.name_status(spec[1], spec[2])
        before, after = (lambda p: repo.blob(f"{spec[1]}:{p}")), (lambda p: repo.blob(f"{spec[2]}:{p}"))
    notes, changes = [], []
    names = [(s, p) for s, p in names if not match_any(p, c["_ignore"])]
    if len(names) > MAX_FILES:
        notes.append(Finding("K0.many", "note", "", n=MAX_FILES, key="many"))
        names = names[:MAX_FILES]
    for status, p in names:
        b, bs = _decode(None if status == "A" else before(p))
        a, as_ = _decode(None if status == "D" else after(p))
        changes.append(FileChange(p, b, a, bs or as_))
    return changes, notes


def evaluate(repo: Repo, spec: tuple, cfg: dict, waived=(), pre=()) -> tuple:
    """Findings for this change, minus waivers and minus what was already there. -> (findings, n_waived)"""
    changes, notes = load_changes(repo, spec, cfg)
    found = analyze(changes, cfg) + notes
    skip, ok = set(pre), set(waived)
    live = [f for f in found if f.fp not in skip]
    kept = [f for f in live if f.fp not in ok]
    return kept, len(live) - len(kept)


# --- Waivers: granted by the user, kept where the agent may not write ---
def waiver_dir() -> Path:
    return Path(os.environ.get("BAR_LOCK_HOME") or (Path.home() / HOME_DIR))


def load_waivers() -> dict:
    p = waiver_dir() / WAIVER_FILE
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return dict(doc.get("waivers") or {})
    except (OSError, ValueError, AttributeError):
        raise GitError(f"{p} is unreadable — fix or delete it")


def save_waivers(w: dict) -> None:
    d = waiver_dir()
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / WAIVER_FILE, {"version": 1, "waivers": w})


# --- Config + per-session state ---
def find_home(start, engine_path: Path | None = None):
    if engine_path is not None and engine_path.parent.name == HOME_DIR:
        return engine_path.parent
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / HOME_DIR / ENGINE_FILE).is_file():
            return d / HOME_DIR
    return None


def load_config(home: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if home is not None and (home / CONFIG_FILE).is_file():
        cfg.update(json.loads((home / CONFIG_FILE).read_text(encoding="utf-8")))
    if cfg.get("lang") not in TEXT:
        cfg["lang"] = "en"
    return cfg


def _state_path(home: Path, sid) -> Path:
    return home / STATE_DIR / (re.sub(r"[^\w.-]", "_", str(sid or "nosession"))[:80] + ".json")


def load_state(home: Path, sid) -> dict:
    p = _state_path(home, sid)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"repos": {}, "stop_blocks": 0, "seen": []}
    except (OSError, ValueError):
        return {"repos": {}, "stop_blocks": 0, "seen": []}


def save_state(home: Path, sid, st: dict) -> None:
    p = _state_path(home, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p, st)


# --- Hook ---
CMD_COMMIT = re.compile(r"(?:^|[;&|\n(]|\s)git\s+(?:(?:-C|-c)\s+\S+\s+|--[\w-]+(?:=\S+)?\s+)*commit\b")
CMD_PUSH = re.compile(r"(?:^|[;&|\n(]|\s)git\s+(?:(?:-C|-c)\s+\S+\s+|--[\w-]+(?:=\S+)?\s+)*push\b|(?:^|[;&|\n(]|\s)gh\s+pr\s+(?:create|merge)\b")
READ_ONLY = re.compile(r"^\s*(?:python3?\s+\S*bar_lock\.py\s+(?:check|waivers|version)\b[^;&|<>`$()]*"
                       r"|(?:cat|ls|head|tail|wc|stat|grep|rg)\s+[^;&|<>`$()]*)\s*$")


def touches_self(tool: str, ti: dict, home: Path) -> bool:
    """The agent may read bar-lock's files and run `check`; it may not change them, grant waivers, or unhook it."""
    protected = [os.path.normpath(str(home)), os.path.normpath(str(waiver_dir()))]
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = os.path.normpath(os.path.expanduser(str(ti.get("file_path") or ti.get("notebook_path") or "")))
        if p and any((p + "/").startswith(x + "/") for x in protected):
            return True
        if re.search(r"(?:^|/)\.claude/settings[^/]*\.json$", p):
            texts = [str(ti.get("old_string") or ""), *(str(e.get("old_string") or "") for e in (ti.get("edits") or []) if isinstance(e, dict))]
            if any("bar-lock" in t or "bar_lock" in t for t in texts):
                return True
            if tool == "Write":
                try:
                    old = Path(p).read_text(encoding="utf-8")
                except OSError:
                    old = ""
                return HOOK_MARK in old and HOOK_MARK not in str(ti.get("content") or "")
        return False
    if tool in ("Bash", "PowerShell"):
        cmd = str(ti.get("command") or "")
        named = any(w in cmd for w in (HOME_DIR, "bar_lock", "bar-lock", "BAR_LOCK_HOME", str(waiver_dir())))
        return named and not READ_ONLY.match(cmd)
    return False


def _deny(user: str, ai: str) -> str:
    return json.dumps({"systemMessage": user, "hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ai}}, ensure_ascii=False)


def _lines(findings: list, lang: str) -> str:
    rows = [f"  [{f.kind}] {f.location()} — {f.text(lang)}   (id {f.fp})" for f in findings[:MAX_SHOWN]]
    if len(findings) > MAX_SHOWN:
        rows.append(f"  … +{len(findings) - MAX_SHOWN}")
    return "\n".join(rows)


def _split(findings: list) -> tuple:
    return [f for f in findings if f.level == "block"], [f for f in findings if f.level != "block"]


def _ensure_baseline(repo: Repo, cfg: dict, st: dict, record_pre: bool) -> tuple:
    """(entry, fresh) — the HEAD at the start of this session and the findings that were already there.
    A baseline first made in the middle of a commit (no SessionStart seen) excuses nothing: record_pre=False."""
    key = str(repo.root)
    entry = st["repos"].get(key)
    if entry is not None:
        return entry, False
    head = repo.head()
    already = evaluate(repo, ("worktree", None), cfg)[0] if record_pre else []
    entry = {"base": head, "pre": [f.fp for f in already], "started": _dt.datetime.now().isoformat(timespec="seconds")}
    st["repos"][key] = entry
    return entry, True


def _unseen_notes(notes: list, st: dict) -> list:
    seen = set(st.get("seen") or [])
    fresh = [f for f in notes if f.fp not in seen]
    st["seen"] = list(seen | {f.fp for f in fresh})[-400:]
    return fresh


def hook(data: dict, engine_path: Path | None = None) -> tuple:
    home = find_home(data.get("cwd"), engine_path)
    if home is None:
        return 0, "", ""
    cfg = load_config(home)
    lang = cfg["lang"]
    event = data.get("hook_event_name", "")
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    sid = data.get("session_id")

    if event == "PreToolUse" and touches_self(tool, ti, home):
        return 0, _deny(tx(lang, "self"), tx(lang, "self")), ""
    if event not in ("SessionStart", "PreToolUse", "Stop"):
        return 0, "", ""
    cmd = str(ti.get("command") or "") if tool in ("Bash", "PowerShell") else ""
    if event == "PreToolUse" and not (CMD_COMMIT.search(cmd) or CMD_PUSH.search(cmd)):
        return 0, "", ""        # nothing to judge on this call (the common case): do no git work at all

    repo = Repo.discover(data.get("cwd") or os.getcwd())
    if repo is None:
        return 0, "", ""
    st = load_state(home, sid)
    entry, fresh = _ensure_baseline(repo, cfg, st, record_pre=event != "PreToolUse")
    waived = set(load_waivers())
    pre = set(entry.get("pre") or [])
    out = ""

    if event == "SessionStart":
        pass
    elif event == "PreToolUse":
        is_push = bool(CMD_PUSH.search(cmd)) and not CMD_COMMIT.search(cmd)
        if is_push:
            spec = ("range", entry["base"], repo.head() or entry["base"]) if entry.get("base") else ("worktree", None)
        else:
            spec = ("worktree", None)
        found, nw = evaluate(repo, spec, cfg, waived, pre)
        blocks, notes = _split(found)
        notes = _unseen_notes(notes, st)
        if blocks:
            head = tx(lang, "blocked_push" if is_push else "blocked_commit", n=len(blocks))
            user = head + "\n" + _lines(blocks, lang) + (("\n" + tx(lang, "waived_n", n=nw)) if nw else "")
            ai = head + "\n" + _lines(blocks, lang) + "\n" + tx(lang, "to_ai", ids=" ".join(f.fp for f in blocks[:3]))
            save_state(home, sid, st)
            return 0, _deny(user, ai), ""
        if notes:
            msg = tx(lang, "notes") + "\n" + _lines(notes, lang)
            out = json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}},
                             ensure_ascii=False)
    else:   # Stop
        if fresh:
            save_state(home, sid, st)
            return 0, json.dumps({"systemMessage": tx(lang, "baseline")}, ensure_ascii=False), ""
        found, nw = evaluate(repo, ("worktree", entry.get("base")), cfg, waived, pre)
        blocks, notes = _split(found)
        notes = _unseen_notes(notes, st)
        # `stop_hook_active` is ignored on purpose: the count of refusals in a row is what ends the loop,
        # and it only resets when a Stop comes back clean — so "giving up" stays given up until the agent fixes it.
        if not blocks:
            st["stop_blocks"] = 0
            if notes:
                out = json.dumps({"systemMessage": tx(lang, "notes") + "\n" + _lines(notes, lang)}, ensure_ascii=False)
        elif st.get("stop_blocks", 0) >= int(cfg.get("max_stop_blocks", 2)):
            msg = tx(lang, "stop_giving_up", k=st["stop_blocks"]) + "\n" + _lines(blocks, lang)
            out = json.dumps({"systemMessage": msg}, ensure_ascii=False)
        else:
            st["stop_blocks"] = st.get("stop_blocks", 0) + 1
            head = tx(lang, "blocked_stop", n=len(blocks))
            ai = head + "\n" + _lines(blocks, lang) + "\n" + tx(lang, "to_ai", ids=" ".join(f.fp for f in blocks[:3]))
            out = json.dumps({"decision": "block", "reason": ai, "systemMessage": head + "\n" + _lines(blocks, lang)},
                             ensure_ascii=False)
    save_state(home, sid, st)
    return 0, out, ""


def _main_hook() -> int:
    lang = "en"
    try:
        data = json.loads(sys.stdin.read() or "{}")
        home = find_home(data.get("cwd"), Path(__file__).resolve())
        lang = load_config(home)["lang"] if home else "en"
        code, out, err = hook(data, Path(__file__).resolve())
    except Exception as e:  # noqa: BLE001 — say it loudly, let the action through, never wedge the session
        print(tx(lang, "no_check", err=f"{type(e).__name__}: {_short(e, 160)}"), file=sys.stderr)
        return 1
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


# --- Install / uninstall: touch only our own entries in .claude/settings*.json ---
def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
            entry = {"matcher": PRE_MATCHER, **entry}
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


def install(root: Path, lang: str = "en", local: bool = False, pins: list | None = None) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    home.mkdir(parents=True, exist_ok=True)
    src, dst = Path(__file__).resolve(), home / ENGINE_FILE
    if src != dst.resolve():
        shutil.copy2(src, dst)
    cfg_path = home / CONFIG_FILE
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {k: v for k, v in DEFAULT_CONFIG.items() if k != "test_globs"}
    cfg["lang"] = lang if not cfg_path.is_file() else cfg.get("lang", lang)
    if pins:
        cfg["pinned"] = sorted(set(cfg.get("pinned") or []) | set(pins))
    _write_json(cfg_path, cfg)
    (home / ".gitignore").write_text(STATE_DIR + "/\n", encoding="utf-8")
    settings = root / ".claude" / ("settings.local.json" if local else "settings.json")
    try:
        doc = _load_settings(settings)
        changed = add_hooks(doc)
    except ValueError as e:
        raise SystemExit(tx(lang, "bad_settings", path=settings, err=e))
    if changed:
        _write_json(settings, doc)
        return tx(cfg["lang"], "installed", root=root, engine=dst, settings=settings, events=", ".join(HOOK_EVENTS), config=cfg_path)
    return tx(cfg["lang"], "already", settings=settings)


def uninstall(root: Path, keep_data: bool = False) -> str:
    root = root.resolve()
    home = root / HOME_DIR
    try:
        lang = load_config(home)["lang"]
    except (OSError, ValueError):
        lang = "en"
    n = 0
    for name in ("settings.json", "settings.local.json"):
        p = root / ".claude" / name
        try:
            doc = _load_settings(p)
        except ValueError as e:
            raise SystemExit(tx(lang, "bad_settings", path=p, err=e))
        k = remove_hooks(doc)
        if k:
            _write_json(p, doc)
            n += k
    data = ""
    if home.is_dir():
        if keep_data:
            data = tx(lang, "kept_data", dir=home)
        else:
            shutil.rmtree(home)
            data = tx(lang, "removed_data", dir=home)
    return tx(lang, "removed", root=root, n=n, data=data)


# --- CLI ---
USAGE = """usage: bar_lock.py <command>
  check [--staged | --base REV [--head REV]] [--all] [--json] [--lang en|zh]
                                      list what this change made easier (default: working tree vs HEAD)
                                      exit 0 = nothing blocking, 1 = something blocking, 2 = could not check
  init [DIR] [--lang en|zh] [--local] [--pin GLOB ...]
                                      install into a project (hooks + .bar-lock/)
  uninstall [DIR] [--keep-data]       remove our hooks (and .bar-lock/)
  waive ID... --reason "why"          YOU approve findings (the AI is not allowed to run this)
  waive --current --reason "why"      approve everything blocking in the working tree right now
  unwaive ID...                       take a waiver back
  waivers                             list waivers
  hook                                (called by Claude Code)
  version"""


def _opt(rest: list, name: str, conv=str, many: bool = False):
    vals = []
    while name in rest:
        i = rest.index(name)
        if i + 1 >= len(rest):
            raise SystemExit(USAGE)
        vals.append(conv(rest[i + 1]))
        del rest[i:i + 2]
    return vals if many else (vals[-1] if vals else None)


def cmd_check(rest: list) -> int:
    lang_opt = _opt(rest, "--lang")
    base, head = _opt(rest, "--base"), _opt(rest, "--head")
    as_json, show_all, staged = "--json" in rest, "--all" in rest, "--staged" in rest
    repo = Repo.discover(os.getcwd())
    home = find_home(os.getcwd(), Path(__file__).resolve())
    cfg = load_config(home)
    lang = lang_opt if lang_opt in TEXT else cfg["lang"]
    if repo is None:
        print(tx(lang, "not_git"), file=sys.stderr)
        return 2
    try:
        spec = ("range", base, head or "HEAD") if base else (("staged",) if staged else ("worktree", None))
        waived = set() if show_all else set(load_waivers())
        found, nw = evaluate(repo, spec, cfg, waived)
    except (GitError, OSError, ValueError) as e:
        print(tx(lang, "no_check", err=_short(e, 160)), file=sys.stderr)
        return 2
    blocks = [f for f in found if f.level == "block"]
    if as_json:
        print(json.dumps({"blocking": len(blocks), "waived": nw, "findings": [f.as_dict(lang) for f in found]}, ensure_ascii=False, indent=2))
    elif not found:
        print(tx(lang, "clean") + (" " + tx(lang, "waived_n", n=nw) if nw else ""))
    else:
        print(_lines(found, lang) + (("\n" + tx(lang, "waived_n", n=nw)) if nw else ""))
    return 1 if blocks else 0


def cmd_waive(rest: list) -> int:
    reason = _opt(rest, "--reason")
    current = "--current" in rest
    ids = [a for a in rest if a != "--current"]
    repo = Repo.discover(os.getcwd())
    cfg = load_config(find_home(os.getcwd(), Path(__file__).resolve()))
    lang = cfg["lang"]
    if not reason:
        print(tx(lang, "waive_reason"), file=sys.stderr)
        return 2
    meta = {}
    if current:
        if repo is None:
            print(tx(lang, "not_git"), file=sys.stderr)
            return 2
        found, _ = evaluate(repo, ("worktree", None), cfg)
        for f in found:
            if f.level == "block":
                ids.append(f.fp)
                meta[f.fp] = f
    if not ids:
        print(tx(lang, "waive_none"), file=sys.stderr)
        return 2
    w = load_waivers()
    now = _dt.datetime.now().isoformat(timespec="seconds")
    for i in ids:
        f = meta.get(i)
        w[i] = {"reason": reason, "when": now, "code": f.code if f else "", "path": f.path if f else "",
                "repo": str(repo.root) if repo else ""}
    save_waivers(w)
    print(tx(lang, "waive_done", n=len(ids), ids=" ".join(ids)))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    rest = argv[1:]
    if cmd == "hook":
        return _main_hook()
    if cmd in ("version", "--version", "-V"):
        print(VERSION)
        return 0
    if cmd == "check":
        return cmd_check(rest)
    if cmd == "waive":
        return cmd_waive(rest)
    if cmd == "unwaive":
        w = load_waivers()
        for i in rest:
            w.pop(i, None)
        save_waivers(w)
        return 0
    if cmd == "waivers":
        lang = load_config(find_home(os.getcwd(), Path(__file__).resolve()))["lang"]
        w = load_waivers()
        if not w:
            print(tx(lang, "waive_empty"))
        for fp, m in w.items():
            print(tx(lang, "waive_list", fp=fp, when=m.get("when", ""), code=m.get("code", ""), path=m.get("path", ""), reason=m.get("reason", "")))
        return 0
    if cmd == "init":
        lang = _opt(rest, "--lang") or "en"
        pins = _opt(rest, "--pin", many=True)
        local = "--local" in rest
        rest = [a for a in rest if a != "--local"]
        if lang not in TEXT:
            print(USAGE, file=sys.stderr)
            return 2
        print(install(Path(rest[0] if rest else "."), lang=lang, local=local, pins=pins))
        return 0
    if cmd == "uninstall":
        keep = "--keep-data" in rest
        rest = [a for a in rest if a != "--keep-data"]
        print(uninstall(Path(rest[0] if rest else "."), keep_data=keep))
        return 0
    print(USAGE, file=sys.stderr if cmd != "help" else sys.stdout)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
