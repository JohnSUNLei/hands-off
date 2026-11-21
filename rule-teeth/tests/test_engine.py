# -*- coding: utf-8 -*-
"""运行时：每种检查抓得到、不误伤；棘轮只拦新增；暂缓是欠条不是豁免。"""
import datetime as dt
import unittest

from _util import fake_key
from ruleteeth import engine

FORBID = {"id": "RT1", "type": "forbid_pattern", "patterns": [r"\brequests\.post\b"], "files": ["*.py"],
          "allow": ["llm.py"], "rule": "只能经 llm.py", "source": "CLAUDE.md:3"}
LINES = {"id": "RT2", "type": "max_lines", "limit": 3, "files": ["*.py"]}
SECRETS = {"id": "RT3", "type": "no_secrets", "files": ["*.py", "*.json"]}
CMD = {"id": "RT4", "type": "forbid_command", "patterns": [r"(?:^|[\s;&|(`])gh\s+pr\s+ready(?=$|[\s;&|)`])"]}
PROTECT = {"id": "RT5", "type": "protected_path", "paths": ["docs/ja", "CLAUDE.md"]}
PREFIX = {"id": "RT6", "type": "reply_prefix", "prefix": "John —"}
DOC = {"lang": "zh", "checks": [FORBID, LINES, SECRETS, CMD, PROTECT, PREFIX]}


def ids(vs):
    return sorted({v.check for v in vs})


class GlobTest(unittest.TestCase):
    def test_match_glob(self):
        yes = [("a/b/llm.py", "llm.py"), ("src/x.py", "*.py"), ("docs/ja/a.md", "docs/ja"), ("docs/ja/a.md", "docs/"),
               ("pkg/tests/t.py", "tests/"), ("a/b.test.ts", "*test*"), (".env", ".env")]
        no = [("src/x.ts", "*.py"), ("docs/jab/a.md", "docs/ja/"), ("a/llm.py.bak", "llm.py")]
        for rel, pat in yes:
            self.assertTrue(engine.match_glob(rel, pat), (rel, pat))
        for rel, pat in no:
            self.assertFalse(engine.match_glob(rel, pat), (rel, pat))

    def test_where_and_allow(self):
        c = {**FORBID, "where": ["tests/"]}
        self.assertTrue(engine.applies(c, "tests/a.py"))
        self.assertFalse(engine.applies(c, "src/a.py"))
        self.assertFalse(engine.applies(FORBID, "core/llm.py"))


class ScanTest(unittest.TestCase):
    def test_forbid_pattern_skips_comments(self):
        vs = engine.scan_text(FORBID, "a.py", "# requests.post 在注释里\nr = requests.post(u)\n", "zh")
        self.assertEqual([v.line for v in vs], [2])
        self.assertEqual(vs[0].source, "CLAUDE.md:3")

    def test_max_lines(self):
        self.assertEqual(ids(engine.scan_text(LINES, "a.py", "1\n2\n3\n4\n")), ["RT2"])
        self.assertEqual(engine.scan_text(LINES, "a.py", "1\n2\n3\n"), [])

    def test_secrets_real_vs_placeholder(self):
        self.assertEqual(ids(engine.scan_text(SECRETS, "a.py", f'KEY = "{fake_key()}"\n')), ["RT3"])
        self.assertEqual(engine.scan_text(SECRETS, "a.py", 'api_key = "your_api_key_here_xxx"\n'), [])
        self.assertEqual(engine.scan_text(SECRETS, "a.py", 'api_key = os.environ["API_KEY"]\n'), [])

    def test_command_and_reply(self):
        cases = [(engine.check_bash, "git push && gh pr ready 12", ["RT4"]), (engine.check_bash, "gh pr view 12", []),
                 (engine.check_reply, "好的，做完了", ["RT6"]), (engine.check_reply, "John — 做完了", [])]
        for fn, text, want in cases:
            self.assertEqual(ids(fn(DOC, text)), want, text)


class RatchetTest(unittest.TestCase):
    def test_only_new_violations_block(self):
        old = "a = requests.post(u)\n"
        self.assertEqual(engine.check_edit(".", DOC, "x.py", old, old + "b = 1\n"), [],
                         "文件里早就有的老问题，不拦这次无关的修改")
        new = engine.check_edit(".", DOC, "x.py", old, old + "b = requests.post(v)\n")
        self.assertEqual(ids(new), ["RT1"])

    def test_max_lines_growth_vs_shrink(self):
        long = "1\n2\n3\n4\n5\n"
        self.assertEqual(ids(engine.check_edit(".", DOC, "x.py", long, long + "6\n")), ["RT2"])
        self.assertEqual(engine.check_edit(".", DOC, "x.py", long, "1\n2\n3\n4\n"), [], "变短是在改好，不拦")

    def test_protected_path(self):
        self.assertEqual(ids(engine.check_edit(".", DOC, "docs/ja/index.md", "", "x")), ["RT5"])
        self.assertEqual(engine.check_edit(".", DOC, "docs/en/index.md", "", "x"), [])

    def test_disabled_check_is_silent(self):
        doc = {"checks": [{**FORBID, "enabled": False}]}
        self.assertEqual(engine.check_edit(".", doc, "x.py", "", "requests.post(u)\n"), [])


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.v = [engine.Violation("RT1", "a.py", i, "m") for i in (1, 2)]
        self.today = dt.date(2026, 11, 21)

    def doc(self, count, until):
        return {"baseline": [{"check": "RT1", "file": "a.py", "count": count, "until": until}]}

    def test_iou_not_waiver(self):
        # (登记的条数, 到期日, 期望 (还算违规, 暂缓)) —— 到期或比登记时多了，都重新算违规
        for count, until, want in [(2, "2026-12-01", (0, 2)), (2, "2026-11-20", (2, 0)), (1, "2026-12-01", (2, 0))]:
            real, held = engine.split_baseline(self.doc(count, until), self.v, self.today)
            self.assertEqual((len(real), len(held)), want, (count, until))

    def test_make_baseline(self):
        b = engine.make_baseline(self.v, 30, self.today)
        self.assertEqual(b, [{"check": "RT1", "file": "a.py", "count": 2, "until": "2026-12-21"}])


if __name__ == "__main__":
    unittest.main()
