# -*- coding: utf-8 -*-
"""体检：从没被检查过的、该下沉的、超体量的、死指针、写两遍的，都要点得出来。"""
import unittest

from _util import Project
from ruleteeth import checkup, report

ROOT_RULES = """# 根规则

## 约定

- 单文件不得超过 800 行
- 改完 Python 代码随手跑 `python3 tests/run_all.py`（在 `alpha/` 下）
- 所有重大决策必须对照 `docs/missing.md`
- 部署流程见 `~/somewhere/outside.md`
- 代码要优雅
- Python 文件里不许用 `print(` 调试

## 子项目索引

| 子项目 | 入口 |
|---|---|
| Alpha | `alpha/README.md` |
| Beta | `beta/README.md` |
| Gamma | `gamma/README.md` |
""" + "\n".join(f"- 说明 {i}" for i in range(220))

FILES = {
    "CLAUDE.md": ROOT_RULES,
    "代码宪法.md": "# 代码宪法\n\n### C1 只能经 `llm.py`\n\n不许自己拼 `chat/completions`。\n\n- 单文件不得超过 800 行\n",
    "alpha/README.md": "a", "alpha/app.py": "x = 1\n", "alpha/tests/rules_guard.py": 'R = ["C1"]  # 代码宪法 chat/completions\n',
    "beta/README.md": "b", "beta/app.ts": "x", "gamma/README.md": "g", "gamma/app.go": "x",
}


class CheckupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(FILES)
        cls.p.write({"CLAUDE.md": ROOT_RULES + "\n规则细节见 `代码宪法.md`\n"})
        cls.cu = checkup.run(cls.p.root)

    @classmethod
    def tearDownClass(cls):
        cls.p.__exit__()

    def kinds(self):
        return {f.kind for f in self.cu.findings}

    def test_size_over_limit(self):
        self.assertIn("size", self.kinds())
        self.assertEqual(self.cu.findings[0].kind, "size", "超体量的排第一")

    def test_subprojects_from_index(self):
        self.assertEqual(self.cu.subprojects, ["alpha", "beta", "gamma"])

    def test_sink_python_only_rule(self):
        sunk = [r.text for r, _why, _dest in self.cu.sink]
        self.assertTrue(any("print(" in t for t in sunk), sunk)
        self.assertTrue(any("run_all" in t for t in sunk), "只提到 alpha 的也要点出来")

    def test_dead_and_external_pointers(self):
        self.assertIn(("CLAUDE.md", "docs/missing.md", False), self.cu.dead)
        self.assertTrue(any(p == "~/somewhere/outside.md" for _f, p, _ok in self.cu.external))
        self.assertFalse(any(p == "alpha/README.md" for _f, p, _d in self.cu.dead))

    def test_existing_check_detected_by_id(self):
        c1 = [r for r in self.cu.rules if r.rule_id == "C1"][0]
        self.assertEqual(c1.checked_by, ["alpha/tests/rules_guard.py"])

    def test_vague_and_duplicate(self):
        self.assertIn("vague", self.kinds())
        self.assertTrue(any("800" in a.text and "800" in b.text for a, b, _s in self.cu.duplicates))

    def test_report_renders_both_languages_and_hides_text(self):
        zh = report.render(self.cu, "demo")
        self.assertIn("从来没被机器检查过", zh)
        self.cu.lang = "en"
        en = report.render(self.cu, "demo", quote=False)
        self.cu.lang = "zh"
        self.assertIn("never been checked", en)
        self.assertNotIn("代码要优雅", en, "--no-quote 不能转载原文")

    def test_single_file_mode(self):
        path = self.p.root / "代码宪法.md"
        cu = checkup.run_file(path, shown_as="their/AGENTS.md")
        self.assertEqual(cu.files[0].path, "their/AGENTS.md")
        self.assertGreaterEqual(len(cu.rules), 2)


if __name__ == "__main__":
    unittest.main()
