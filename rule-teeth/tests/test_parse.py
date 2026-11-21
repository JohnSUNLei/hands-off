# -*- coding: utf-8 -*-
"""切分与发现规则文件。"""
import unittest

from _util import Project
from ruleteeth import parse

DOC = """# 标题

> 引用里的一句：必须这样。

## 规则

- 第一条，
  续行也算第一条
- 第二条

```python
- 代码块里的列表不算
```

| 名字 | 说明 |
|---|---|
| a | 不许做 b |

### C1 只能经 `llm.py`

唯一出口是 `llm.LLM`，不许自己拼 `chat/completions`。
"""


class ParseTest(unittest.TestCase):
    def test_split_kinds_and_continuation(self):
        cs = parse.split_candidates("CLAUDE.md", DOC)
        kinds = [(c.kind, c.text) for c in cs]
        self.assertIn(("bullet", "第一条， 续行也算第一条"), kinds)
        self.assertIn(("bullet", "第二条"), kinds)
        self.assertIn(("quote", "引用里的一句：必须这样。"), kinds)
        self.assertFalse(any("代码块" in t for _, t in kinds), "代码块里的东西不该进候选")
        self.assertFalse(any(t.startswith("名字") for _, t in kinds), "表头不是规则")
        table = [c for c in cs if c.kind == "table"][0]
        self.assertEqual(table.detail, "名字 | 说明")

    def test_heading_detail_absorbs_paragraph(self):
        cs = parse.split_candidates("CLAUDE.md", DOC)
        head = [c for c in cs if c.kind == "heading" and c.text.startswith("C1")][0]
        self.assertIn("chat/completions", head.detail)
        para = [c for c in cs if c.kind == "para" and "唯一出口" in c.text][0]
        self.assertEqual(cs[para.under], head)
        self.assertEqual(head.section, ("标题", "规则"))

    def test_frontmatter_paths(self):
        n, paths = parse.frontmatter('---\npaths:\n  - "src/**/*.ts"\n---\n- 规则\n')
        self.assertEqual(paths, ("src/**/*.ts",))
        self.assertEqual(n, 4)

    def test_imports_skip_code_spans(self):
        text = "看 @docs/rules.md 和 `@not/this.md`\n```\n@nor/this.md\n```\n"
        self.assertEqual(parse.imports_of(text), ["docs/rules.md"])

    def test_discover_respects_agents_md_precedence(self):
        files = {"CLAUDE.md": "- 必须 A\n@docs/extra.md\n", "AGENTS.md": "- must B\n",
                 "docs/extra.md": "- 必须 C\n", "sub/CLAUDE.md": "- 必须 D\n",
                 "README.md": "见 `代码宪法.md`\n", "代码宪法.md": "- 不得 E\n"}
        with Project(files) as p:
            got = {rf.path: rf for rf in parse.discover(p.root)}
        self.assertTrue(got["CLAUDE.md"].auto_loaded)
        self.assertFalse(got["AGENTS.md"].auto_loaded, "有 CLAUDE.md 时 Claude Code 默认不读 AGENTS.md")
        self.assertTrue(got["docs/extra.md"].auto_loaded, "@import 进来的跟着自动加载")
        self.assertFalse(got["sub/CLAUDE.md"].auto_loaded, "子目录的按需加载")
        self.assertNotIn("代码宪法.md", got, "只跟踪规则文件里提到的规则文档，README 里提到的不算")

    def test_discover_follows_rule_doc_reference(self):
        with Project({"CLAUDE.md": "规则细节见 `代码宪法.md`\n", "代码宪法.md": "- 不得 E\n"}) as p:
            got = {rf.path: rf for rf in parse.discover(p.root)}
        self.assertEqual(got["代码宪法.md"].imported_by, "CLAUDE.md")


if __name__ == "__main__":
    unittest.main()
