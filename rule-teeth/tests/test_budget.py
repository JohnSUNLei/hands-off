# -*- coding: utf-8 -*-
"""每次必读链（load_budget）：算得准、软导入认得出也不乱认、只拦变多、hook 当场咬。"""
import tempfile
import unittest
from pathlib import Path

from _util import Project
from ruleteeth import checkup, classify, engine
from ruleteeth.model import Candidate

BIG = "规则正文。" * 400            # 约 2,000 token
BUDGET_RULE = "- 每个 session 固定要读的规则合计不超过 3,000 token（每次必读预算）\n"


def chain_paths(root):
    return {x.path: x.how for x in engine.load_chain(root)}


class SoftReadTest(unittest.TestCase):
    def test_heading_scope_and_label_off(self):
        text = ("## 每个 session 都读\n\n1. 方法论 → `docs/a.md`\n2. 业务 `docs/b.md`\n\n**按需读：**\n\n"
                "| 用途 | 文件 |\n|---|---|\n| 历史 | `docs/c.md` |\n\n## 别的\n\n- 见 `docs/d.md`\n")
        self.assertEqual([q for _, q in engine.soft_reads(text)], ["docs/a.md", "docs/b.md"])

    def test_line_trigger_negation_and_write_targets(self):
        text = ("开工前先读 `x.md`。\n\n（`p.md` 只属于子项目 A，不再每次必读。）\n\n"
                "Read `y.md` at the start of every session.\n\n- Read `z.md` only when needed, at the start of every session.\n\n"
                "- 新 session 先读 `a.md` —— 同步规则：新事实写进 `timeline.md`\n\n```\n每个 session 都读 `code.md`\n```\n")
        self.assertEqual([q for _, q in engine.soft_reads(text)], ["x.md", "y.md", "a.md"])

    def test_paragraph_spanning_lines(self):
        text = "> 每个项目工作前，先读这份（方法论），再读\n> 该项目根目录的 `PROJECT.md`（业务上下文）。\n"
        self.assertEqual([q for _, q in engine.soft_reads(text)], ["PROJECT.md"])


class ChainTest(unittest.TestCase):
    def test_what_counts(self):
        outside = tempfile.TemporaryDirectory()
        ext = Path(outside.name).resolve() / "method.md"
        ext.write_text("方法论正文\n", encoding="utf-8")
        files = {
            "CLAUDE.md": f"# 根\n\n@docs/core.md\n\n## 每个 session 都读\n\n- `{ext}`\n- `docs/b.md`\n",
            "docs/core.md": "核心\n\n@deep.md\n",
            "docs/deep.md": "更深一层\n",
            "docs/b.md": "业务\n",
            ".claude/rules/always.md": "# 没有 paths，开工就加载\n",
            ".claude/rules/py.md": "---\npaths:\n  - \"**/*.py\"\n---\n# 碰到 .py 才加载\n",
            "sub/CLAUDE.md": "# 子目录，进去才加载\n",
            "docs/later.md": "按需\n",
        }
        with Project(files) as p:
            got = chain_paths(p.root)
            self.assertEqual(got["CLAUDE.md"], "root")
            self.assertEqual(got["docs/core.md"], "import")
            self.assertEqual(got["docs/deep.md"], "import")
            self.assertEqual(got["docs/b.md"], "soft")
            self.assertEqual(got[".claude/rules/always.md"], "rules")
            self.assertEqual(got[engine.shown_path(p.root, ext)], "soft")
            for not_loaded in (".claude/rules/py.md", "sub/CLAUDE.md", "docs/later.md"):
                self.assertNotIn(not_loaded, got)
            outside_items = [x for x in engine.load_chain(p.root) if x.outside]
            self.assertEqual(len(outside_items), 1)
        outside.cleanup()

    def test_agents_md_only_without_claude_md(self):
        with Project({"AGENTS.md": "规则\n"}) as p:
            self.assertEqual(chain_paths(p.root), {"AGENTS.md": "root"})
        with Project({"AGENTS.md": "规则\n", "CLAUDE.md": "根\n"}) as p:
            self.assertEqual(chain_paths(p.root), {"CLAUDE.md": "root"})

    def test_cycle_does_not_loop(self):
        with Project({"CLAUDE.md": "@a.md\n", "a.md": "@CLAUDE.md\n每个 session 都读 `CLAUDE.md`\n"}) as p:
            self.assertEqual(set(chain_paths(p.root)), {"CLAUDE.md", "a.md"})


class BudgetCheckTest(unittest.TestCase):
    CHECK = {"id": "RT1", "type": "load_budget", "max_tokens": 3000, "rule": "预算", "source": "CLAUDE.md:2"}
    DOC = {"lang": "zh", "checks": [CHECK]}

    def test_over_and_under(self):
        files = {"CLAUDE.md": "# 根\n\n## 每个 session 都读\n\n- `big.md`\n", "big.md": BIG * 2}
        with Project(files) as p:
            vs = engine.check_budgets(p.root, self.DOC)
            self.assertEqual([(v.check, v.file) for v in vs], [("RT1", "CLAUDE.md")])
            self.assertIn("big.md", vs[0].msg)
            self.assertIn("规则里写着每次都读", vs[0].msg)
            self.assertEqual(engine.check_budgets(p.root, self.DOC, only=["src/app.py"]), [], "链上文件没动，Stop 不算")
            p.write({"CLAUDE.md": "# 根\n"})
            self.assertEqual(engine.check_budgets(p.root, self.DOC), [])

    def test_edit_ratchet(self):
        files = {"CLAUDE.md": "# 根\n", "big.md": BIG * 2, ".claude/rules/py.md": "---\npaths: \"**/*.py\"\n---\n"}
        with Project(files) as p:
            root_md = p.root / "CLAUDE.md"
            before = root_md.read_text(encoding="utf-8")
            grow = before + "\n## 每个 session 都读\n\n- `big.md`\n"
            self.assertEqual([v.check for v in engine.check_edit(p.root, self.DOC, "CLAUDE.md", before, grow)], ["RT1"])
            # 已经超了：缩短放行，再变长拦下并说明「本来就超了」
            p.write({"CLAUDE.md": grow})
            shrink = grow.replace("- `big.md`\n", "- 做发布时读 `big.md`（按需）\n")
            self.assertEqual(engine.check_edit(p.root, self.DOC, "CLAUDE.md", grow, shrink), [])
            more = grow + BIG
            vs = engine.check_edit(p.root, self.DOC, "CLAUDE.md", grow, more)
            self.assertTrue(vs and "本来就超了" in vs[0].msg)
            # 带 paths: 的规则文件不在链上，写多长都不归这条管
            scoped = "---\npaths: \"**/*.py\"\n---\n" + BIG * 3
            self.assertEqual(engine.check_edit(p.root, self.DOC, ".claude/rules/py.md", "", scoped), [])
            # 新建一个不带 paths 的规则文件 = 加进链
            self.assertEqual([v.check for v in engine.check_edit(p.root, self.DOC, ".claude/rules/new.md", "", BIG)],
                             ["RT1"])


class ClassifyBudgetTest(unittest.TestCase):
    def spec(self, text, lang="zh"):
        rules = classify.classify([Candidate("CLAUDE.md", 1, "bullet", text)], lang)
        return [c for r in rules for c in r.checks if c["type"] == "load_budget"]

    def test_budget_rules(self):
        self.assertEqual(self.spec(BUDGET_RULE.strip("- \n"))[0]["max_tokens"], 3000)
        self.assertEqual(self.spec("Keep the always-on context loaded every session under 3k tokens.", "en")[0]["max_tokens"], 3000)
        self.assertEqual(self.spec("每次必读链上限 4千 token"), [{"type": "load_budget", "max_tokens": 4000}])

    def test_not_budget_rules(self):
        self.assertEqual(self.spec("省 token 不得降低产出标准——达不到就升级到 Claude"), [])
        self.assertEqual(self.spec("API token 不得硬编码，每个 session 都从环境变量读"), [])


class CheckupChainTest(unittest.TestCase):
    def test_finding_and_budget_from_rule(self):
        files = {"CLAUDE.md": "# 根\n\n## 约定\n\n" + BUDGET_RULE + "\n## 每个 session 都读\n\n- `big.md`\n",
                 "big.md": BIG * 2}
        with Project(files) as p:
            cu = checkup.run(p.root)
            self.assertEqual(cu.budget, 3000)
            top = cu.findings[0]
            self.assertEqual(top.kind, "load_chain")
            self.assertEqual(top.severity, 3)
            self.assertEqual(top.items[0].path, "big.md")


class BudgetHookTest(unittest.TestCase):
    def test_hook_blocks_growing_chain(self):
        files = {"CLAUDE.md": "# 根\n\n## 约定\n\n" + BUDGET_RULE, "big.md": BIG * 2}
        with Project(files) as p:
            r = p.cli("init", ".", "--hook")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("每次必读链", r.stdout)
            grow = (p.root / "CLAUDE.md").read_text(encoding="utf-8") + "\n## 每个 session 都读\n\n- `big.md`\n"
            res = p.hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                          "tool_input": {"file_path": str(p.root / "CLAUDE.md"), "content": grow}})
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("超过预算 3,000", res.stderr)
            self.assertIn("CLAUDE.md:", res.stderr)
            ok = (p.root / "CLAUDE.md").read_text(encoding="utf-8") + "\n- 做发布时读 `big.md`\n"
            res = p.hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                          "tool_input": {"file_path": str(p.root / "CLAUDE.md"), "content": ok}})
            self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
