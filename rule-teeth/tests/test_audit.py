# -*- coding: utf-8 -*-
"""实测榜：拿假会话记录（tests/fixtures/sessions/）重放，违反几次、拦了几次、从没触发、没装牙，都要数得对。

夹具里的路径写成 __ROOT__，测试时换成临时项目的路径。
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _util import HERE, Project
from ruleteeth import audit

RULES = """# 项目规则

## 约定

- 不许用 `requests.post`
- 单文件不得超过 20 行
- Do NOT mark PRs as ready (`gh pr ready`)
- 不要改 `vendor/` 里的文件
- 每个 agent 回应 John 都以 "John —" 开头
- Python 文件里不许用 `eval(`
- 代码要优雅
"""
FIXTURES = HERE / "fixtures" / "sessions"


def lay_out(root: Path, base: Path) -> list:
    """照 Claude Code 的样子摆好会话目录：主会话、它的子 agent、一个 worktree 的会话。返回写出的文件。"""
    key = audit.project_key(root)
    spots = {"main.jsonl": base / key / "s-main.jsonl",
             "agent.jsonl": base / key / "s-main" / "subagents" / "agent-1.jsonl",
             "worktree.jsonl": base / (key + "--claude-worktrees-wt1") / "s-tree.jsonl"}
    for name, dest in spots.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((FIXTURES / name).read_text(encoding="utf-8").replace("__ROOT__", str(root)), encoding="utf-8")
    (base / "-some-other-project").mkdir()
    return list(spots.values())


def digest(paths: list) -> list:
    return [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths]


class AuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project({"CLAUDE.md": RULES, "src/c.py": "r = requests.post(u)\n"})
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.logs = lay_out(cls.p.root, cls.base)
        cls.before = digest(cls.logs)
        cls.au = audit.run(cls.p.root, base=cls.base)
        cls.t = {t.rule.cand.where: t for t in cls.au.tallies}

    @classmethod
    def tearDownClass(cls):
        cls.p.__exit__()
        cls.tmp.cleanup()

    def test_session_logs_untouched(self):
        self.assertEqual(digest(self.logs), self.before, "会话记录只能读，不能改")

    def test_finds_own_and_worktree_folders_only(self):
        names = [d.name for d in self.au.dirs]
        self.assertEqual(len(names), 2)
        self.assertTrue(names[1].endswith("--claude-worktrees-wt1"))
        self.assertEqual((self.au.files, self.au.subagents), (3, 1))

    def test_project_key(self):
        self.assertEqual(audit.project_key("/home/me/AI网站"), "-home-me-AI--")
        self.assertEqual(audit.project_key("/a/b.c_d"), "-a-b-c-d")

    def test_forbidden_pattern_counts_write_edit_multiedit_and_subagent(self):
        t = self.t["CLAUDE.md:5"]
        self.assertEqual(t.violations, 4, "Write、Edit、MultiEdit、子 agent 各一次；改之前就有的不算；抄过来的同一次调用只算一次")
        self.assertEqual(t.blocks, 1)
        self.assertNotIn("src/c.py", t.where, "Edit 前后都有 requests.post：棘轮，不算新违反")

    def test_line_limit_only_from_whole_file_writes(self):
        t = self.t["CLAUDE.md:6"]
        self.assertEqual((t.violations, dict(t.where)), (1, {"src/big.py": 1}))

    def test_command_protected_path_and_worktree(self):
        self.assertEqual(self.t["CLAUDE.md:7"].violations, 1)
        prot = self.t["CLAUDE.md:8"]
        self.assertEqual(dict(prot.where), {"vendor/lib.js": 1, "vendor/y.js": 1}, "worktree 里的文件按它在项目里的位置算")

    def test_replies(self):
        t = self.t["CLAUDE.md:9"]
        self.assertEqual(self.au.replies, 3, "被打断的、客户端自己补的、子 agent 的话都不算回复")
        self.assertEqual((t.violations, t.blocks), (1, 1))
        self.assertEqual(t.where[audit.REPLY], 1)

    def test_counts_and_noise(self):
        self.assertEqual(self.au.calls, 13)
        self.assertEqual(self.au.outside, 1)
        self.assertEqual(self.au.bad_lines, 1, "坏行要数出来，不能悄悄吞掉")
        self.assertEqual(self.au.self_blocks, 1)
        self.assertEqual(dict(self.au.orphans), {("RT9", "CLAUDE.md:99"): 1})
        self.assertEqual(sum(t.blocks for t in self.au.tallies), 2, "普通命令输出里出现拦截字样不算拦截")

    def test_rankings(self):
        self.assertEqual([t.rule.cand.where for t in self.au.ranked()][:2], ["CLAUDE.md:5", "CLAUDE.md:8"])
        self.assertEqual([t.rule.cand.where for t in self.au.never()], ["CLAUDE.md:10"])
        self.assertEqual(len(self.au.untoothed()), 6, "还没 init：每条能查的都没装牙")
        self.assertEqual(len(self.au.tallies), 6, "太模糊的规则不上榜")

    def test_report_both_languages(self):
        zh = audit.render(self.au, "demo")
        for s in ("最常被违反的", "从没触发的", "还没装牙", "1 行不是合法 JSON", "对不上现在的规则"):
            self.assertIn(s, zh)
        self.au.lang = "en"
        try:
            en = audit.render(self.au, "demo")
        finally:
            self.au.lang = "zh"
        self.assertIn("Broken most often", en)
        self.assertIn("Never fired", en)

    def test_since(self):
        au = audit.run(self.p.root, base=self.base, since="2026-11-02")
        self.assertEqual(self.t["CLAUDE.md:5"].violations, 4)
        t = {x.rule.cand.where: x for x in au.tallies}
        self.assertEqual(t["CLAUDE.md:5"].violations, 2, "只剩子 agent 那两次（主会话那份被滤掉了，抄过来的那次不再算重复）")
        self.assertEqual(au.first[:10], "2026-11-02")


class AuditTeethTest(unittest.TestCase):
    def test_installed_teeth_and_switched_off_check(self):
        with Project({"CLAUDE.md": RULES}) as p, tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(p.cli("init", ".").returncode, 0)
            rules = p.root / ".rule-teeth" / "rules.json"
            doc = json.loads(rules.read_text(encoding="utf-8"))
            for c in doc["checks"]:
                if c["source"] == "CLAUDE.md:10":
                    c["enabled"] = False
            rules.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            lay_out(p.root, Path(tmp))
            au = audit.run(p.root, base=Path(tmp))
            self.assertEqual([t.rule.cand.where for t in au.untoothed()], ["CLAUDE.md:10"], "关掉的那条算没装牙")
            t5 = [t for t in au.tallies if t.rule.cand.where == "CLAUDE.md:5"][0]
            self.assertTrue(t5.teeth.startswith("RT"))


class AuditCliTest(unittest.TestCase):
    def test_cli_ok_and_loud_errors(self):
        with Project({"CLAUDE.md": RULES}) as p, tempfile.TemporaryDirectory() as tmp:
            lay_out(p.root, Path(tmp))
            own = Path(tmp) / audit.project_key(p.root)
            r = p.cli("audit", ".", "--sessions", str(own))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("规则实测榜", r.stdout)
            self.assertIn("1 行不是合法 JSON", r.stderr, "坏行要在命令行上也说出来")
            out = p.root / "audit.md"
            r = p.cli("audit", ".", "--sessions", str(own), "--out", str(out), "--lang", "en")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("1. CLAUDE.md:5", r.stdout, "命令行摘要列出最常被违反的")
            self.assertIn("Rule audit", out.read_text(encoding="utf-8"))
            r = p.cli("audit", ".", "--sessions", str(Path(tmp) / "nope"))
            self.assertEqual(r.returncode, 2)
            self.assertIn("找不到会话记录目录", r.stderr)
            empty = Path(tmp) / "empty"
            empty.mkdir()
            r = p.cli("audit", ".", "--sessions", str(empty))
            self.assertEqual(r.returncode, 2)
            self.assertIn(".jsonl", r.stderr)
            r = p.cli("audit", ".", "--sessions", str(own), "--since", "11/02")
            self.assertEqual(r.returncode, 2)

    def test_no_logs_for_project_is_an_error(self):
        with Project({"CLAUDE.md": RULES}) as p, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(audit.AuditError):
                audit.run(p.root, base=Path(tmp))


if __name__ == "__main__":
    unittest.main()
