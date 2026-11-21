# -*- coding: utf-8 -*-
"""分类和检查生成。每条都是一句真实世界里会写的规则。

准则：能查的要查得出来；拿不准的宁可「要人判断」，也不生成会乱拦的检查。
"""
import unittest

from _util import PKG_ROOT  # noqa: F401  (设置 sys.path)
from ruleteeth import classify
from ruleteeth.model import CHECKABLE, JUDGMENT, VAGUE, Candidate


def one(text, kind="bullet", section=(), detail="", lang="zh", file_lang=""):
    rules = classify.classify([Candidate("CLAUDE.md", 1, kind, text, section, detail)], lang, file_lang)
    return rules[0] if rules else None


def types(rule):
    return [c["type"] for c in rule.checks] if rule else []


# (规则原文, 期望分类, 期望的检查类型)
CASES = [
    ("单文件不得超过 1500 行", CHECKABLE, ["max_lines"]),
    ("Keep files under 300 lines", CHECKABLE, ["max_lines"]),
    ("API key 不硬编码", CHECKABLE, ["no_secrets"]),
    ("Never commit API keys or tokens", CHECKABLE, ["no_secrets"]),
    ("Do not edit generated `docs/ja` or `docs/zh` pages.", CHECKABLE, ["protected_path"]),
    ("Do NOT mark PRs as ready (`gh pr ready`)", CHECKABLE, ["forbid_command"]),
    ("Use `pnpm` instead of `npm`", CHECKABLE, ["forbid_command"]),
    ("Do not use `datetime.now()` in tests", CHECKABLE, ["forbid_pattern"]),
    ('每个 agent 回应 John 都以 "John —" 开头', CHECKABLE, ["reply_prefix"]),
    ('Do NOT add "Generated with Claude Code" footers to commits', CHECKABLE, ["forbid_command"]),
    ("改完代码随手跑 `python3 tests/run_all.py`", CHECKABLE, ["run_command"]),
    ("Write clean code", VAGUE, []),
    ("代码要优雅", VAGUE, []),
    ("每个重大决策必须由 Turing 跑一次反证", JUDGMENT, []),
    # 下面这些看着像禁令，其实不是 —— 生成了就会乱拦
    ("Bootstrapping does not require `pnpm build-all`.", JUDGMENT, []),
    ("The adopted PR is never retargeted at `canary`.", None, []),
    ("When changes are confined to `test/`, do not start `pnpm dev`.", JUDGMENT, []),
    ("Never run pytest on the host — always use `breeze`.", JUDGMENT, []),
    ("Avoid `main` in names", JUDGMENT, []),
]


class ClassifyTest(unittest.TestCase):
    def test_cases(self):
        for text, cat, want in CASES:
            with self.subTest(text=text):
                r = one(text, lang="en" if text.isascii() else "zh")
                if cat is None:
                    self.assertTrue(r is None or not r.checks, f"不该生成检查: {r and r.checks}")
                    continue
                self.assertIsNotNone(r, "应该被认成规则")
                self.assertEqual(r.category, cat, r.checks)
                self.assertEqual(types(r), want)

    def test_owner_rule_from_heading_and_detail(self):
        r = one("C1 调 LM Studio 只能经 `llm.py`", kind="heading",
                detail="唯一出口是 `llm.LLM`。别的模块一律 `from llm import LLM`，不许自己拼 `chat/completions`。",
                file_lang="python")
        spec = r.checks[0]
        self.assertEqual(r.rule_id, "C1")
        self.assertEqual(spec["type"], "forbid_pattern")
        self.assertEqual(spec["allow"][0], "llm.py")
        self.assertIn("tests/", spec["allow"], "「只能经 X」类规则，测试默认豁免（测试要造假服务）")
        self.assertEqual(spec["files"], ["*.py"])
        self.assertTrue(any("chat/completions" in p for p in spec["patterns"]))
        self.assertFalse(any("from" in p for p in spec["patterns"]), "「一律 X」是要用的，不是禁用的")

    def test_rule_file_size_rule_targets_root(self):
        r = one("单个项目根的体量上限约 10KB / 200 行（总根沿用 400 行上限）")
        spec = r.checks[0]
        self.assertEqual((spec["limit"], spec["max_bytes"], spec["files"]), (200, 10240, ["CLAUDE.md"]))

    def test_run_command_is_off_by_default_and_keeps_cwd(self):
        r = one("改完 Python 代码随手跑 `python3 tests/run_all.py --only L0`（在 `realtime-translator/` 下）")
        spec = r.checks[0]
        self.assertFalse(spec["enabled"], "从文档里抽出来的命令不能默认自动执行")
        self.assertEqual(spec["cwd"], "realtime-translator")

    def test_test_section_scopes_to_test_files(self):
        r = one("Do NOT use `check()` - it is deprecated", section=("Writing Tests",), lang="en")
        self.assertIn("tests/", r.checks[0]["where"])

    def test_index_table_row_is_not_a_rule(self):
        c = Candidate("CLAUDE.md", 1, "table", "B. 实时翻译 | 改 `realtime-translator/` | `README.md`",
                      detail="子项目 | 什么时候进入 | 入口")
        self.assertEqual(classify.classify([c]), [])
        c2 = Candidate("AGENTS.md", 1, "table", "Before changing APIs | Use `strategy` skill",
                       detail="When | Required action")
        self.assertEqual(len(classify.classify([c2], "en")), 1, "表头写着 Required 的表格行是规则")

    def test_rule_section_makes_plain_bullets_rules(self):
        self.assertIsNotNone(one("周报：每周日由 Elon 主笔", section=("沟通约定",)))
        self.assertIsNone(one("海外华人，全职 all-in", section=("创始人",)))

    def test_soft_rule_flag(self):
        r = one("Prefer integration tests over unit tests", lang="en")
        self.assertTrue(r.soft)


if __name__ == "__main__":
    unittest.main()
