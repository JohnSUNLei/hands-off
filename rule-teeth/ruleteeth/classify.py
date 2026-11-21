# -*- coding: utf-8 -*-
"""判断一个片段是不是规则，是的话分三类，并给能查的规则生成检查。

全是确定性的词表 + 正则，不调模型：同一份规则文件永远得到同一个结论，结论能被测试钉住。
宁可把能查的判成「要人判断」，也不生成一条会乱拦的检查 —— 会喊狼来了的检查很快就没人理。
"""
from __future__ import annotations

import re

from . import engine
from .model import CHECKABLE, JUDGMENT, VAGUE, Candidate, Rule

# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------
_NOT_BIE = "区分特类级告识个性差辨派离鉴告"
NEG_ZH = (r"不得|不许|不准|不要|不能|不可(?![逆能靠信缺思])|禁止|严禁|不允许|绝不|永远不|千万别|千万不要|不再|不用|而不是|而非|"
          rf"(?<![{_NOT_BIE}])别(?![的人处名称])|不(?=[改动碰删写提装用跑发合推建加放读硬])")
NEG_EN = (r"\b(?:never|don'?t|do not|does not|must not|mustn'?t|shall not|should not|shouldn'?t|cannot|can'?t|"
          r"avoid|forbidden|prohibited|disallowed|no longer)\b")
NEG_NEAR_EN = NEG_EN[:-3] + r"|not|no|instead of|rather than|without)\b"
STRONG_ZH = (r"必须|务必|一律|一定要|只能|只许|只在|只经|只放|只用|只做|只读|只准|只有[^，。]{1,12}才|严格|"
             r"每次|每个[^，。]{0,12}都|所有[^，。]{0,12}都|永远|先[^，。；]{1,20}再")
STRONG_EN = (r"\b(?:must|never|always|required|requires|mandatory|only|shall|forbidden|prohibited|"
             r"do not|don'?t|cannot|no exceptions|at most|no more than|max(?:imum)?|limit)\b")
LIMIT_ZH = r"上限|不超过|以内|之内|封顶"
SOFT_ZH_BULLET = r"要|得(?=[用写跑先])"         # 列表项里的「X 要 Y」就是规则；成段的话里「要」太常见，不算
SOFT_ZH = r"应该|应当|应(?=[该当先在用把以])|需要|尽量|优先|避免|建议|最好|记得|随手|注意|要(?=[先把用写跑在给带])|请"
SOFT_EN = r"\b(?:should|prefer|preferably|avoid|try to|ensure|make sure|recommended|keep|consider|please)\b"
IMPERATIVE_EN = re.compile(
    r"^(?:use|run|keep|write|add|avoid|prefer|put|place|call|check|update|ensure|make|follow|include|remove|"
    r"read|create|commit|test|document|name|import|return|wrap|limit|split|treat|verify|validate|stop|ask|"
    r"reply|respond|start|begin|leave|mark|log|record|report|state|tell|be|do|don't|never|always|only|no|"
    r"prefix|format|lint|install|define|store|handle|pass|set|move|delete|rename|open|close|review|link|"
    r"mention|list|describe|explain|group|sort|skip|default|favor|favour|apply|preserve|clean|squash|rebase)\b",
    re.I)
IMPERATIVE_ZH = re.compile(r"^(?:先|再|改|跑|用|写|加|放|读|查|记|按|把|给|每|所有|任何|只|凡|一律|别|不要|禁止|不许|统一)")
VAGUE_WORDS = re.compile(
    r"best practices?|\bclean\b|\bgood\b|\bnice\b|\bproper(?:ly)?\b|appropriate(?:ly)?|careful(?:ly)?|reasonabl[ey]|"
    r"readable|maintainable|elegant|high[- ]quality|robust|\bsimple\b|\bclear(?:ly)?\b|concise|sensible|"
    r"thoughtful(?:ly)?|idiomatic|as needed|when appropriate|if possible|where possible|where appropriate|"
    r"优雅|合理|适当|注意|尽量|简洁|清晰|规范的|良好|高质量|最佳实践|好的|认真|仔细|谨慎|必要时|酌情|灵活|视情况|"
    r"可读|易维护|干净|漂亮|专业",
    re.I)
POINTER = re.compile(r"^(?:见|详见|参见|参考|完整[^：:]{0,12}[：:]|see\b|refer to\b|details?\s+(?:in|at)\b)", re.I)
TALK_WORDS = re.compile(r"回应|回复|回答|汇报|沟通|开头|称呼|语气|reply|respond|answer|greet|address|tone|"
                        r"commit message|PR description|pull request", re.I)
PROCESS_WORDS = re.compile(r"先[^，。]{0,20}再|流程|步骤|顺序|每个 ?session|读[^，。]{0,6}(?:文件|入口)|"
                           r"\bbefore\b|\bafter\b|\bfirst\b|\bthen\b|workflow|\bsteps?\b", re.I)
DECIDE_WORDS = re.compile(r"决策|拍板|判断|取舍|权衡|审批|批准|decid|decision|judg|trade-?off|approv", re.I)

RUN_VERB = re.compile(r"跑|运行|执行|\brun\b|\bexecute\b|\binvoke\b", re.I)
RUN_TIMING = re.compile(r"改完|改动后|修改后|提交前|提交之前|完成前|结束前|交付前|随手|每次|之后都|"
                        r"before (?:you )?(?:commit|committing|push|pushing|finish|finishing|submitting|opening|"
                        r"creating a pr|sending)|after (?:every |each |any )?(?:change|edit|modif)|"
                        r"must pass|should pass|make sure .{0,30}pass|ensure .{0,30}pass", re.I)
EDIT_VERB = re.compile(r"改|动|修改|编辑|删|覆盖|写入|碰|\b(?:edit|modify|change|touch|overwrite|delete|remove|rename|"
                       r"write to|alter|hand-?edit)\b", re.I)
ONLY_ZH = re.compile(r"只能|只许|只在|只经|只放|只准|唯一(?:的)?(?:出口|入口|出处|地方)?")
ONLY_EN = re.compile(r"\bonly (?:in|inside|through|via|from|within|use)\b|\bexclusively\b|\bmust go through\b|"
                     r"\bsingle source\b|\bmust (?:stay|live|remain|be kept) (?:only )?in\b|\bbelongs? (?:only )?in\b",
                     re.I)
ADD_VERB = re.compile(r"\b(?:add|adding|include|including|write|put|append|insert|sign)\b|加|写|附|署名", re.I)
COMMIT_WORDS = re.compile(r"\bcommits?\b|\bPRs?\b|pull requests?|提交|合并请求", re.I)
SECRET_WORDS = re.compile(r"api[ _-]?keys?|secrets?|tokens?|passwords?|credentials?|密钥|口令|密码|凭证|私钥|令牌", re.I)
SECRET_VERBS = re.compile(r"硬编码|写死|明文|提交|进\s*git|hard-?cod|commit|check in|in (?:the )?(?:code|repo)|plain ?text|inline",
                          re.I)
LINE_LIMIT = (
    re.compile(r"(?:不得|不能|不要|不许|别|不)?超过\s*(?:约)?\s*(\d[\d,]*)\s*行"),
    re.compile(r"(\d[\d,]*)\s*行(?:以内|之内|以下|为上限|上限)"),
    re.compile(r"上限\s*(?:约|大约|是)?\s*(?:\d+\s*KB\s*/\s*)?(\d[\d,]*)\s*行"),
    re.compile(r"(?:under|below|less than|fewer than|at most|max(?:imum)?(?: of)?|no more than|<=?|≤|"
               r"limit(?:ed)? to|exceed(?:ing)?|over)\s*~?(\d[\d,]*)\s*(?:lines|loc|lines of code)\b", re.I),
    re.compile(r"(\d[\d,]*)\s*(?:lines|loc)\s*(?:max|maximum|limit|or (?:less|fewer))\b", re.I),
)
KB_LIMIT = re.compile(r"(\d+(?:\.\d+)?)\s*KB", re.I)
FUNC_WORDS = re.compile(r"函数|方法|function|method|class|类(?!型)", re.I)
RULEFILE_WORDS = re.compile(r"CLAUDE\.md|AGENTS\.md|本文件|这份文件|根文件|项目根|宪法.{0,4}根|this file|root file|"
                            r"rule files?|instruction files?", re.I)
SCOPE_ZH = re.compile(r"在[^，。`]{1,14}(?:里|中|内|下)")
SCOPE_EN = re.compile(r"\bin (?:the )?\w+ (?:code|layer|routes?|components?|handlers?|files?|modules?|views?)\b", re.I)
LANGS = {
    "python": re.compile(r"python|\.py\b", re.I),
    "typescript": re.compile(r"typescript|\bTS\b|\.tsx?\b"),
    "javascript": re.compile(r"javascript|\bJS\b|\bnode(?:\.js)?\b|\.jsx?\b"),
    "go": re.compile(r"\bgolang\b|\bGo (?:code|files?)\b"),
    "rust": re.compile(r"\brust\b|\.rs\b", re.I),
    "swift": re.compile(r"\bswift\b", re.I),
    "java": re.compile(r"\bjava\b(?!script)", re.I),
    "kotlin": re.compile(r"\bkotlin\b", re.I),
    "ruby": re.compile(r"\bruby\b", re.I),
}
CMD_WORDS = {
    "git", "npm", "npx", "pnpm", "yarn", "bun", "pip", "pip3", "python", "python3", "uv", "uvx", "poetry",
    "pytest", "cargo", "go", "make", "docker", "kubectl", "rm", "curl", "wget", "sudo", "chmod", "brew", "node",
    "deno", "ruff", "black", "eslint", "prettier", "tsc", "jest", "vitest", "mvn", "gradle", "bash", "sh",
    "zsh", "bazel", "turbo", "nx", "gh", "terraform", "helm", "swift", "xcodebuild", "rails", "bundle",
}
_SPAN = re.compile(r"`([^`\n]+)`")
_QUOTED = re.compile(r"[\"“「『]\s*([^\"”」』]{1,60}?)\s*[\"”」』]")
_PLACEHOLDER = re.compile(r"<[^>]*\b(?:name|path|file|dir|id|key|value|version|url|branch|your|xxx|foo|bar|"
                          r"number|n|x|tag|title|appDir|type)\b[^>]*>|\{[^}]*\}|\.\.\.|…|path/to|xxx|foo|"
                          r"[一-鿿]", re.I)
RULE_SECTION = re.compile(r"约定|规则|规范|铁律|原则|红线|守则|要求|须知|禁忌|纪律|rules?\b|conventions?|guidelines?|"
                          r"polic(?:y|ies)|requirements?|do'?s|don'?ts|must|checklist|standards?", re.I)
RULE_HEADER = re.compile(r"required|rule|must|action|do\b|don'?t|要求|规则|规定|必须|做法|禁止", re.I)
PROHIBIT_EN = (r"\b(?:never|don'?t|do not|must not|mustn'?t|shall not|should not|shouldn'?t|avoid|forbidden|"
               r"prohibited|disallowed|no longer)\b")
ACTION_EN = re.compile(r"\b(?:use|using|call|calling|import|importing|add|adding|write|writing|introduce|put|"
                       r"hard-?code|commit|print|log|create|depend|rely|reference|mix|run|edit|modify|change|touch|"
                       r"delete|remove|assert|mock|catch|raise|throw|set|access|read|push|merge|force|install|"
                       r"bypass|disable|skip|ignore|store|expose|leak|paste|copy|start|invoke|execute|mark|open)\w*\b",
                       re.I)
POSITIVE_RESET = re.compile(r"\balways\b|\binstead\b|\brather\b|\bprefer|—\s*use\b|而是|改用|一律|应该|总是", re.I)
LEAD_SCOPE = re.compile(r"^(?:in|inside|under|within|for)\s+`([\w./-]+)`\s*[,:]|^在\s*`([\w./-]+)`\s*(?:里|中|下|内)", re.I)
NOT_NEED = re.compile(r"\s*(?:need|require|have to|necessarily)\b", re.I)
EXCEPT = re.compile(r"\bexcept\b|\bunless\b|\bother than\b|除了|除非|例外", re.I)
CONDITIONAL = re.compile(r"(?:^|[.:;!?]\s+)(?:when|if|unless|only when|for [^.:]{1,40}? only)\b|"
                         r"如果|若是|假如|当[^，。]{1,20}时|除非|仅当|只有在", re.I)
TEST_SECTION = re.compile(r"\btests?\b|testing|测试|\bspecs?\b", re.I)
TEST_GLOBS = ["*test*", "*spec*", "test/", "tests/", "__tests__/", "e2e/"]
_ID = re.compile(r"^(?:\*\*)?(?:§\s*)?([A-Z]{1,4}-?\d{1,3}(?:\.\d+)?)(?=[\s:：.、　)）]|$)")
_ANCHOR = re.compile(r"`[^`]+`|\d|[\w\-]+\.(?:md|py|js|ts|json|ya?ml|toml|sh)\b|[\"“「]")
MAX_SNIPPET = 160


def _has(rx, text) -> bool:
    return re.search(rx, text, re.I) is not None if isinstance(rx, str) else rx.search(text) is not None


def is_normative(c: Candidate) -> tuple:
    """返回 (是不是规则, 是不是软规则)。"""
    t = c.text
    strong = _has(STRONG_ZH, t) or _has(NEG_ZH, t) or _has(LIMIT_ZH, t) or _has(STRONG_EN, t) or _has(NEG_EN, t)
    soft = _has(SOFT_ZH, t) or _has(SOFT_EN, t) or (c.kind == "bullet" and _has(SOFT_ZH_BULLET, t))
    imperative = False
    if c.kind in ("bullet", "table"):
        cells = t.split(" | ") if c.kind == "table" else [t]
        imperative = any(IMPERATIVE_EN.match(x) or IMPERATIVE_ZH.match(x) for x in cells)
    in_rule_section = any(RULE_SECTION.search(s) for s in c.section[-2:])
    if c.kind == "heading" and not strong:
        return False, False                      # 标题只认强标记，「## 测试」这类不算
    if c.kind == "table" and not (strong or (imperative and RULE_HEADER.search(c.detail))):
        return False, False                      # 表格多半是索引 / 对照：要么有明确的要求词，要么表头写着「要求」
    if c.kind in ("para", "quote") and not strong:
        return False, False                      # 成段的文字多是解释，只认强标记
    if c.kind == "bullet" and in_rule_section and not (strong or soft or imperative):
        return True, False                       # 「## 沟通约定」下面的每一条都是规则
    if POINTER.match(t) and not strong:
        return False, False
    if not (strong or soft or imperative):
        return False, False
    return True, soft and not strong


# ---------------------------------------------------------------------------
# 反引号片段：判断它是命令、路径还是代码写法，以及在句子里是「要」还是「不要」
# ---------------------------------------------------------------------------
def spans(text: str) -> list:
    """[(片段, 起点)]"""
    return [(m.group(1).strip(), m.start()) for m in _SPAN.finditer(text)]


def is_command(s: str) -> bool:
    first = s.split()[0] if s.split() else ""
    return first in CMD_WORDS or first.startswith("./") or bool(re.match(r"^[\w.-]+\.(?:sh|command)$", first))


def is_path(s: str) -> bool:
    if " " in s or is_command(s):
        return False
    return ("/" in s and "//" not in s and not s.startswith("/")) or bool(
        re.match(r"^\.?[\w.\-]+\.(?:md|mdc|py|js|ts|tsx|jsx|json|ya?ml|toml|sh|lock|env|txt|cfg|ini|html|css)$", s)) \
        or s in (".env", ".env.local")


def _clauses(text: str) -> list:
    """按小句切：[(小句, 在原文里的起点)]。反引号里的标点不切。"""
    out, start, in_tick = [], 0, False
    for i, ch in enumerate(text):
        if ch == "`":
            in_tick = not in_tick
        elif not in_tick and ch in "，。；;.!?！？\n" and not (ch == "." and i + 1 < len(text) and text[i + 1].isalnum()):
            out.append((text[start:i + 1], start))
            start = i + 1
    out.append((text[start:], start))
    return [(c, s) for c, s in out if c.strip()]


def _sentences(text: str) -> list:
    out, start, in_tick = [], 0, False
    for i, ch in enumerate(text):
        if ch == "`":
            in_tick = not in_tick
        elif not in_tick and ch in "。；;!?！？\n" or (not in_tick and ch == "." and (i + 1 == len(text) or text[i + 1] == " ")):
            out.append((text[start:i + 1], start))
            start = i + 1
    out.append((text[start:], start))
    return [(c, s) for c, s in out if c.strip()]


def _negation(clause: str, strong: bool):
    """小句里第一个真正的「不许」。「does not require X」「不需要」不算禁止。"""
    rxs = (NEG_ZH, PROHIBIT_EN) if strong else (NEG_ZH, NEG_NEAR_EN)
    for rx in rxs:
        for m in re.finditer(rx, clause, re.I):
            if not NOT_NEED.match(clause[m.end():]):
                return m
    return None


def negative_spans(text: str, strong: bool = False) -> list:
    """出现在否定小句里、且在否定词之后的片段：「不许自己拼 `x`」「never use `B`」。

    strong=False 时还认 not / instead of / rather than（用来抓「用 pnpm，不用 npm」这类命令）；
    「except / 除了」后面的片段是例外，不是禁止。
    """
    out = []
    for clause, _base in _clauses(text):
        neg = _negation(clause, strong)
        if not neg:
            continue
        exc = EXCEPT.search(clause, neg.end())
        stop = exc.start() if exc else len(clause)
        for s, pos in spans(clause):
            if not (neg.start() <= pos < stop):
                continue
            between = clause[neg.end():pos]
            if POSITIVE_RESET.search(between):
                continue                          # 「never X — always use `Y`」里的 Y 是要用的
            if strong and re.match(r"[A-Za-z]", neg.group(0)) and len(between.strip()) > 2 \
                    and not ACTION_EN.search(between):
                continue                          # 「it is never retargeted at `canary`」是描述，不是禁令
            out.append((s, clause))
    return out


def owner_split(text: str):
    """「`X` 只能出现在 `Y`」→ ([X], [Y])；「调某服务只能经 `llm.py`」→ ([], [llm.py])。"""
    subjects, owners = [], []
    for sent, _ in _sentences(text):
        m = ONLY_ZH.search(sent) or ONLY_EN.search(sent)
        if not m:
            continue
        for s, pos in spans(sent):
            if pos < m.start():
                subjects.append(s)
            elif is_path(s) or re.match(r"^[\w.\-/]+$", s):
                owners.append(s)
    return subjects, owners


def usable_literal(s: str) -> bool:
    return len(s) >= 3 and not _PLACEHOLDER.search(s) and not s.isdigit() and not re.fullmatch(r"[\W_]+", s)


def code_literal(s: str) -> bool:
    """能当「禁用写法」的片段。单个普通单词（`main`、`any`、`canary`）在注释和正文里到处都是，拦了全是误报。"""
    if not usable_literal(s):
        return False
    if re.fullmatch(r"[A-Za-z]+", s):
        return bool(re.search(r"[a-z][A-Z]", s))          # camelCase 才算标识符
    return True


def literal_regex(s: str) -> str:
    s = s[:-2] + "(" if s.endswith("()") else s
    s = s[:-1] if s.endswith("/*") else s       # `pkg/*` 指的是 pkg/ 下的任何东西
    body = re.escape(s)
    if re.match(r"\w", s):
        body = r"\b" + body
    if re.search(r"\w$", s):
        body += r"\b"
    return body


def command_regex(s: str) -> str:
    words = [re.escape(w) for w in s.split()]
    return r"(?:^|[\s;&|(`])" + r"\s+".join(words) + r"(?=$|[\s;&|)`])"


# ---------------------------------------------------------------------------
# 抽检查
# ---------------------------------------------------------------------------
def _scope(text: str, file_lang: str) -> list:
    """检查管哪些文件：规则（含所在章节）提到哪种语言就管哪种，没提就管所有代码文件。"""
    exts = []
    for lang, rx in LANGS.items():
        if rx.search(text):
            exts.extend(engine.LANG_EXTS[lang])
    if not exts and file_lang:
        exts = list(engine.LANG_EXTS[file_lang])
    return ["*" + e for e in (exts or engine.CODE_EXTS)]


def _max_lines(c: Candidate, text: str, file_lang: str, root_file: str):
    hits = [m for rx in LINE_LIMIT for m in [rx.search(text)] if m]
    limit = int(min(hits, key=lambda m: m.start()).group(1).replace(",", "")) if hits else None
    if not limit or limit < 20:
        return None
    if FUNC_WORDS.search(text) and not re.search(r"单文件|文件|\bfiles?\b", text, re.I):
        return None                               # 函数长度要按语言做语法分析，第一版不做
    spec = {"type": "max_lines", "limit": limit}
    kb = KB_LIMIT.search(text)
    if kb:
        spec["max_bytes"] = int(float(kb.group(1)) * 1024)
    if RULEFILE_WORDS.search(text):
        spec["files"] = [root_file if re.search(r"根|root|项目根", text, re.I) else c.file]
    else:
        spec["files"] = _scope(text, file_lang)
    return spec


def _secrets(text: str, file_lang: str):
    said_no = any(SECRET_WORDS.search(cl) and SECRET_VERBS.search(cl) and _negation(cl, True)
                  for cl, _ in _clauses(text))
    if said_no:
        return {"type": "no_secrets", "files": ["*" + e for e in engine.CODE_EXTS + engine.CONFIG_EXTS],
                "allow": ["*.example", "*.sample", "*.md"]}
    return None


def _run_command(text: str):
    if not (RUN_VERB.search(text) and RUN_TIMING.search(text)):
        return None
    neg = [s for s, _ in negative_spans(text)]
    for s, _ in spans(text):
        if is_command(s) and s not in neg and not _PLACEHOLDER.search(s):
            spec = {"type": "run_command", "command": s, "timeout": 300, "enabled": False}
            m = re.search(r"在\s*`([^`]+)`\s*(?:目录)?下|(?:\bin|\bfrom|\bunder)\s+`([^`]+/)`|cd\s+([\w./-]+)\s*&&",
                          text)
            if m:
                spec["cwd"] = (m.group(1) or m.group(2) or m.group(3)).strip("/")
            return spec
    return None


def _run_target(s: str, clause: str) -> bool:
    """「Do not run `kind create cluster`」：前面有「跑」字的多词片段也是命令，哪怕不认识它的程序名。"""
    return " " in s and bool(RUN_VERB.search(clause)) and clause.find(s) > RUN_VERB.search(clause).start()


def _forbid_command(text: str):
    if CONDITIONAL.search(text):
        return None                               # 「改测试时不要跑 X」—— 条件机器判断不了，宁可不生成
    pats = [command_regex(s) for s, _ in negative_spans(text) if is_command(s) and usable_literal(s)]
    pats += [command_regex(s) for s, cl in negative_spans(text, strong=True) if _run_target(s, cl) and usable_literal(s)]
    m = re.search(r"\buse\s+`([^`]+)`\s*(?:,\s*)?(?:instead of|not|rather than)\s+`([^`]+)`", text, re.I) or \
        re.search(r"用\s*`([^`]+)`\s*[，,]?\s*(?:不用|不要用|别用|而不是|而非)\s*`([^`]+)`", text)
    if m and is_command(m.group(2)):
        pats.append(command_regex(m.group(2)))
    for clause, _ in _clauses(text):              # 「commit 里不许写 "Generated with …"」→ 拦 git commit / gh pr
        if COMMIT_WORDS.search(clause) and _negation(clause, True) and ADD_VERB.search(clause):
            for q in _QUOTED.findall(clause):
                if len(q) >= 6:
                    pats.append(r"(?:git\s+commit|gh\s+pr\s+(?:create|edit))[\s\S]*" + re.escape(q))
    pats = sorted(set(pats))
    return {"type": "forbid_command", "patterns": pats} if pats else None


def _protected(text: str):
    if CONDITIONAL.search(text):
        return None
    paths = []
    for s, clause in negative_spans(text, strong=True):
        if is_path(s) and EDIT_VERB.search(clause) and usable_literal(s):
            paths.append(s.lstrip("./") if s.startswith("./") else s)
    return {"type": "protected_path", "paths": sorted(set(paths))} if paths else None


def _forbid_pattern(text: str, file_lang: str, section: tuple = ()):
    if CONDITIONAL.search(text):
        return None
    subjects, owners = owner_split(text)
    owners = [o for o in owners if is_path(o)]   # 「只能经 `llm.py`」才算主人；「只能用 `breeze`」不是文件
    pats = []
    for s, clause in negative_spans(text, strong=True):
        if is_command(s) or _run_target(s, clause) or s in owners:
            continue
        if is_path(s) and (EDIT_VERB.search(clause) or re.search(r"\.\w{1,5}$|/$|^\.", s)):
            continue                              # 文件名不是代码写法（「别提交 `.env`」不该去搜 `.env` 字样）
        if SCOPE_ZH.search(clause) or SCOPE_EN.search(clause):
            continue                              # 「不在业务路由里用 X」—— 范围说不清，生成了会乱拦
        if code_literal(s):
            pats.append(literal_regex(s))
    if owners:
        pats.extend(literal_regex(s) for s in subjects if code_literal(s) and not is_path(s) and not is_command(s))
    pats = sorted(set(pats))
    if not pats:
        return None
    spec = {"type": "forbid_pattern", "patterns": pats, "files": _scope(text + " " + " ".join(section), file_lang)}
    lead = LEAD_SCOPE.match(text)
    if lead:
        spec["where"] = [(lead.group(1) or lead.group(2)).strip("/") + "/"]   # 「In `airflow-core`, …」
    elif section and TEST_SECTION.search(" ".join(section[-2:])):
        spec["where"] = list(TEST_GLOBS)          # 「写测试」一节里的禁令只管测试文件
    if owners:
        # 「只能经 llm.py」：测试要造假服务、守卫自己要写出被禁的写法，所以测试目录默认豁免
        spec["allow"] = sorted({o for o in owners if is_path(o)}) + list(TEST_GLOBS)
    return spec


def _reply_prefix(text: str):
    if not re.search(r"回应|回复|回答|reply|respond|answer|messages?", text, re.I):
        return None
    m = re.search(r"以\s*[\"“「『`]+\s*([^\"”」』`]{1,24}?)\s*[\"”」』`]+\s*开头", text) or \
        re.search(r"(?:start|begin)s?\s+with\s+[\"“`']+([^\"”`']{1,24}?)[\"”`']+", text, re.I)
    return {"type": "reply_prefix", "prefix": m.group(1)} if m else None


def extract_checks(c: Candidate, file_lang: str = "", root_file: str = "CLAUDE.md") -> list:
    """一条规则能生成哪些检查。顺序就是优先级，同一类只取一个。"""
    text = (c.text + "\n" + c.detail).strip()
    out = []
    for spec in (_max_lines(c, text, file_lang, root_file), _secrets(text, file_lang), _reply_prefix(text),
                 _run_command(text), _forbid_command(text), _protected(text), _forbid_pattern(text, file_lang, c.section)):
        if spec:
            out.append(spec)
    if any(s["type"] in ("max_lines", "no_secrets", "reply_prefix") for s in out):
        out = [s for s in out if s["type"] not in ("forbid_pattern",)]
    return out


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------
def _hint(c: Candidate, lang: str) -> str:
    t = c.text + " " + c.detail
    zh = lang == "zh"
    _, owners = owner_split(t)
    if owners:
        return ("知道只能在哪个文件，但没写「别处不许出现什么写法」。把那个写法放进反引号，就能自动查。" if zh else
                "It says where something must live, but not what is forbidden elsewhere. Put that code pattern in "
                "backticks and it becomes checkable.")
    if TALK_WORDS.search(t):
        return ("管的是对话 / 沟通方式，文件里看不到。" if zh else
                "It is about how the AI talks or communicates; files cannot show it.")
    if DECIDE_WORDS.search(t):
        return "要人拍板的事，机器不该替你判断。" if zh else "A human decision; a machine should not judge it."
    if PROCESS_WORDS.search(t):
        return ("管的是做事的顺序 / 流程：要看 AI 做了什么，不是文件长什么样。能落到「结束前必须跑过某条命令」的，就能变成 Stop hook。"
                if zh else
                "It is about order of work: what the AI did, not what files look like. If it boils down to \"must have "
                "run command X before finishing\", it can become a Stop hook.")
    return ("要读懂意思才能判断。能拆出一句写明文件名、写法或数字的，那一句就能自动查。" if zh else
            "Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part "
            "can be checked automatically.")


def _vague_hint(word: str, lang: str) -> str:
    if lang == "zh":
        return f"「{word}」没法检查，连人都难说算不算做到。换成具体的：哪个文件、什么写法、多少行、跑什么命令。"
    return (f'"{word}" cannot be checked; even a human cannot say whether it was met. Say it concretely: which '
            f"file, which pattern, how many lines, which command.")


def file_language(text: str) -> str:
    """规则文件开头（标题和第一段）只提到一种语言 → 整个文件默认管这种语言。"""
    head = []
    for ln in text.splitlines()[:6]:
        if re.match(r"\s*(?:[-*+]|\d+[.)])\s|##", ln):
            break                                 # 只看标题和开头那段说明，不看规则本身
        head.append(ln)
    head = "\n".join(head)
    hits = [lang for lang, rx in LANGS.items() if rx.search(head)]
    return hits[0] if len(hits) == 1 else ""


def classify(cands: list, lang: str = "zh", file_lang: str = "", root_file: str = "CLAUDE.md") -> list:
    rules = []
    rule_headings = set()
    for i, c in enumerate(cands):
        if c.under >= 0 and c.under in rule_headings:
            continue                              # 并进标题型规则了，不重复计
        ok, soft = is_normative(c)
        if not ok:
            continue
        if c.kind == "heading":
            rule_headings.add(i)
        m = _ID.match(c.text)
        r = Rule(c, JUDGMENT, soft=soft, rule_id=m.group(1) if m else "")
        r.checks = extract_checks(c, file_lang, root_file)
        if r.checks:
            r.category = CHECKABLE
        else:
            vw = VAGUE_WORDS.search(c.text)
            body = _SPAN.sub("", c.text)
            if vw and len(body) <= 140 and not _ANCHOR.search(c.text + " " + c.detail):
                r.category, r.hint = VAGUE, _vague_hint(vw.group(0), lang)
            elif soft and len(body) < 14 and not _ANCHOR.search(c.text):
                r.category, r.hint = VAGUE, _vague_hint(body.strip(), lang)
            else:
                r.hint = _hint(c, lang)
        rules.append(r)
    return rules


def snippet(text: str, n: int = MAX_SNIPPET) -> str:
    text = text.replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"
