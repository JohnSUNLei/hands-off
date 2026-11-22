# -*- coding: utf-8 -*-
"""数据类型 —— 每个概念只在这里定义一次。

检查（check）和违规（Violation）的定义在 engine.py：那个文件要能单独复制走，不能依赖这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field

CHECKABLE = "checkable"     # 能自动检查：已经生成了可运行的检查
JUDGMENT = "judgment"       # 要人判断：说得清楚，但机器看不懂它的意思
VAGUE = "vague"             # 太模糊：连人都没法判断算不算做到了
CATEGORIES = (CHECKABLE, JUDGMENT, VAGUE)


@dataclass
class Candidate:
    """规则文件里的一个「可能是规则」的片段：一条列表项、一个标题、一段话或一行表格。"""
    file: str                   # 规则文件，相对项目根
    line: int                   # 起始行号（从 1 开始）
    kind: str                   # bullet / heading / para / table / quote
    text: str                   # 这条的正文（多行已合并）
    section: tuple = ()         # 所在的标题路径
    detail: str = ""            # 标题型规则下面跟着的说明段落（抽检查参数时用）
    under: int = -1             # 紧跟在第几个候选（标题）下面的说明段；标题是规则时它并入标题，不单算

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class Rule:
    """确认是规则之后的分类结果。"""
    cand: Candidate
    category: str
    checks: list = field(default_factory=list)   # 生成的检查（engine 认识的 dict）
    hint: str = ""                                 # 为什么不能自动查 / 怎么改写才能查
    soft: bool = False                             # 「尽量 / should」这类软规则
    rule_id: str = ""                              # 规则自带的编号，如 C1
    checked_by: list = field(default_factory=list)  # 已有的机器检查（文件路径）

    @property
    def text(self) -> str:
        return self.cand.text


@dataclass
class RuleFile:
    path: str                   # 相对项目根
    text: str
    auto_loaded: bool           # Claude Code 每个 session 自动加载（根 CLAUDE.md 及其 @import）
    imported_by: str = ""       # 通过谁的 @import 进来的
    scope: tuple = ()           # .claude/rules/*.md 的 paths: 范围


OK = "ok"                   # 规则验真：在
BROKEN = "broken"           # 找不到（失效）
UNKNOWN = "unknown"         # 未能判断 —— 不算通过
VERDICTS = (BROKEN, UNKNOWN, OK)


@dataclass
class Ref:
    """规则文件里引用的一个路径或命令，以及它在这台电脑上还在不在。"""
    file: str                   # 规则文件，相对项目根
    line: int
    kind: str                   # path / command
    text: str                   # 规则里写的原样
    verdict: str                # OK / BROKEN / UNKNOWN
    why: str = ""               # 怎么判的：理由代号（refs.WHY 里有中英文说法）
    arg: str = ""               # 理由里要填的东西（比如找不到的那个脚本）
    scope: str = "repo"         # repo = 仓库里的相对路径；machine = ~/ 或绝对路径；command

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class Finding:
    """体检里的一条发现。"""
    kind: str                   # size / unchecked / sink / broken_ref / external / duplicate / quick_win / vague …
    severity: int               # 3 = 先处理，2 = 该处理，1 = 知道一下
    title: str
    detail: str = ""
    where: str = ""
    items: list = field(default_factory=list)
