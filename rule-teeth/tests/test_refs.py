# -*- coding: utf-8 -*-
"""规则验真：规则文件点名的路径和命令在不在。在 / 失效 / 未能判断 三种都要判对，拿不准的不能算通过。

HOME 换成临时目录（`~/` 开头的路径只看它）；PATH 查找换成假的（结果不随这台电脑装了什么而变）。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _util import Project
from ruleteeth import checkup, classify, refs, report
from ruleteeth.model import BROKEN, OK, UNKNOWN

RULES = """# 规则

## 指针

- 入口见 `docs/real.md`
- 设计见 `docs/missing.md`
- 方法论在 `~/notes/method.md`，旧笔记在 `~/notes/gone.md`
- 本机路径 `{abs_ok}` 和 `{abs_gone}`
- 作品放在 `runs/<作品>/`，草稿放在 `drafts/<名字>/`
- 会话记录在 `~/.claude/projects/<项目>/*.jsonl`
- 别的仓库：`~/other/repo/README.md`、`GUIDE.md`、`NOPE.md`
- `/doctor prompt-audit` 是斜杠命令，接口是 `/api/v1/users`
- 碰到 `.py` 文件时要小心

## 命令

- 改完代码随手跑 `python3 scripts/ok.py`
- 发布前跑 `python3 scripts/gone.py`
- 改完 Python 代码随手跑 `python3 tests/run_all.py`（在 `alpha/` 下）
- 提交前跑 `python3 tests/run_all.py --fast`
- 部署跑 `bash deploy.sh`，打包跑 `./tools/missing.sh`
- 前端跑 `npm run build`，检查跑 `npm run lint`
- 测试跑 `make test`，发版跑 `make release`
- 格式化跑 `python3 -m json.tool data.json`，另一个跑 `python3 -m surely_not_installed_mod_xyz`
- 不要跑 `python3 scripts/forbidden.py`
- 子目录里跑 `cd sub && python3 run.py`，以及 `cd nowhere && ls`
- 基础设施跑 `terraform plan`
- 坏命令 `python3 "unclosed`
"""
FILES = {
    "docs/real.md": "x", "runs/.keep": "", "scripts/ok.py": "", "alpha/tests/run_all.py": "",
    "beta/tests/run_all.py": "", "deploy.sh": "", "package.json": '{"scripts": {"build": "x"}}',
    "Makefile": "test:\n\techo ok\n", "sub/run.py": "",
}


def fake_which(exe):
    return None if exe == "terraform" else "/bin/" + exe


class RefsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        h = Path(cls.home.name).resolve()
        for f in ("notes/method.md", "other/repo/README.md", "other/repo/GUIDE.md", ".claude/projects/k/a.jsonl"):
            (h / f).parent.mkdir(parents=True, exist_ok=True)
            (h / f).write_text("x", encoding="utf-8")
        cls.abs_ok, cls.abs_gone = str(h / "notes" / "method.md"), str(h / "abs" / "gone.md")
        cls.p = Project({**FILES, "CLAUDE.md": RULES.format(abs_ok=cls.abs_ok, abs_gone=cls.abs_gone)})
        with mock.patch.dict(os.environ, {"HOME": str(h)}), mock.patch.object(refs.shutil, "which", fake_which):
            cls.cu = checkup.run(cls.p.root)
            cls.report = report.render(cls.cu, "demo")
        cls.v = {r.text: r for r in cls.cu.refs}

    @classmethod
    def tearDownClass(cls):
        cls.p.__exit__()
        cls.home.cleanup()

    def verdict(self, text):
        self.assertIn(text, self.v, f"没查到 {text!r}")
        return self.v[text].verdict

    def test_repo_paths(self):
        self.assertEqual(self.verdict("docs/real.md"), OK)
        self.assertEqual(self.verdict("docs/missing.md"), BROKEN)
        self.assertNotIn(".py", self.v, "扩展名不是文件")

    def test_home_and_absolute_paths(self):
        self.assertEqual(self.verdict("~/notes/method.md"), OK)
        self.assertEqual(self.verdict("~/notes/gone.md"), BROKEN)
        self.assertEqual(self.verdict(self.abs_ok), OK)
        self.assertEqual(self.verdict(self.abs_gone), BROKEN)
        self.assertNotIn("/doctor prompt-audit", self.v, "斜杠命令不是路径")
        self.assertNotIn("/api/v1/users", self.v, "接口地址不是路径")

    def test_templates(self):
        self.assertEqual(self.verdict("runs/<作品>/"), OK)
        self.assertEqual(self.verdict("drafts/<名字>/"), BROKEN)
        self.assertEqual(self.verdict("~/.claude/projects/<项目>/*.jsonl"), OK)

    def test_names_next_to_an_outside_path(self):
        self.assertEqual(self.verdict("~/other/repo/README.md"), OK)
        self.assertEqual(self.verdict("GUIDE.md"), OK)
        self.assertEqual(self.v["GUIDE.md"].why, "ext_base")
        self.assertEqual(self.verdict("NOPE.md"), UNKNOWN, "说不清相对谁的，标未能判断，不算失效也不算通过")

    def test_scripts(self):
        self.assertEqual(self.verdict("python3 scripts/ok.py"), OK)
        self.assertEqual(self.verdict("python3 scripts/gone.py"), BROKEN)
        self.assertEqual(self.verdict("python3 tests/run_all.py"), OK, "「在 `alpha/` 下」：按那个目录找")
        self.assertEqual(self.verdict("python3 tests/run_all.py --fast"), UNKNOWN, "好几个子项目都有，没说在哪跑")
        self.assertEqual(self.verdict("bash deploy.sh"), OK)
        self.assertEqual(self.verdict("./tools/missing.sh"), BROKEN)
        self.assertNotIn("python3 scripts/forbidden.py", self.v, "「不要跑」的不查")

    def test_package_scripts_make_and_modules(self):
        self.assertEqual(self.verdict("npm run build"), OK)
        self.assertEqual(self.verdict("npm run lint"), BROKEN)
        self.assertEqual(self.verdict("make test"), OK)
        self.assertEqual(self.verdict("make release"), BROKEN)
        self.assertEqual(self.verdict("python3 -m json.tool data.json"), OK)
        self.assertEqual(self.verdict("python3 -m surely_not_installed_mod_xyz"), UNKNOWN)

    def test_cd_missing_program_and_bad_quotes(self):
        self.assertEqual(self.verdict("cd sub && python3 run.py"), OK)
        self.assertEqual(self.verdict("cd nowhere && ls"), BROKEN)
        self.assertEqual(self.verdict("terraform plan"), UNKNOWN, "PATH 里没有：可能只是 PATH 不同，不算失效")
        self.assertEqual(self.verdict('python3 "unclosed'), UNKNOWN)

    def test_checkup_finding_and_report(self):
        f = [x for x in self.cu.findings if x.kind == "broken_ref"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, 3, "自动加载的根文件里有失效引用：先处理")
        n = refs.counts(self.cu.refs)
        self.assertIn(f"{n[BROKEN]} 个已经失效", f[0].title)
        self.assertIn(f"{n[UNKNOWN]} 个未能判断", f[0].title)
        self.assertIn("规则验真", self.report)
        self.assertIn("❓ 未能判断", self.report)
        self.assertIn(("CLAUDE.md", "docs/missing.md", False), self.cu.dead)

    def test_english_report(self):
        self.cu.lang = "en"
        try:
            en = report.render(self.cu, "demo")
        finally:
            self.cu.lang = "zh"
        self.assertIn("Reference check", en)
        self.assertIn("not judged", en)


class HelpersTest(unittest.TestCase):
    def test_segments(self):
        self.assertEqual(refs.segments("cd a && python3 b.py | tee x > y.log"),
                         [["cd", "a"], ["python3", "b.py"], ["tee", "x"]])
        with self.assertRaises(ValueError):
            refs.segments('echo "x')

    def test_run_cwd(self):
        self.assertEqual(classify.run_cwd("跑 `pytest`（在 `alpha/` 下）"), "alpha")
        self.assertEqual(classify.run_cwd("跑 `cd beta && pytest`", with_cd=False), "")
        self.assertEqual(classify.run_cwd("跑 `cd beta && pytest`"), "beta")


if __name__ == "__main__":
    unittest.main()
