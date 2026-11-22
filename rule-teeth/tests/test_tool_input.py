# -*- coding: utf-8 -*-
"""forbid_tool_input：看 MCP 工具的入参。

三层测：①SQL 拆句和分类（sql_units / sql_findings）；②规则怎么挑工具、挑字段、报什么话（check_tool + 加载校验）；
③端到端 —— 像 Claude Code 那样把 JSON 喂给装好的 hook，看退出码。
"""
import json
import unittest

from _util import Project
from ruleteeth import engine

EXEC = "mcp__supabase__execute_sql"
MIGR = "mcp__supabase__apply_migration"
ALL = ("truncate", "drop_table", "drop_schema", "drop_database", "delete_no_where", "update_no_where")
NO_DROP_TABLE = tuple(c for c in ALL if c != "drop_table")

RT13 = {"id": "RT13", "type": "forbid_tool_input", "tools": [EXEC], "field": "query", "sql_deny": list(ALL),
        "rule": "execute_sql 不许清表、删表、删库，也不许不带 WHERE 的 DELETE / UPDATE", "source": "test"}
RT14 = {"id": "RT14", "type": "forbid_tool_input", "tools": [MIGR], "field": "query", "sql_deny": list(NO_DROP_TABLE),
        "rule": "迁移里删表删列常见，只拦最凶的", "source": "test"}
DOC = {"lang": "zh", "checks": [RT13, RT14]}

# (SQL, 应该认出的类别)。SQL 全是自己编的。
POSITIVE = [
    ("TRUNCATE users", {"truncate"}),
    ("truncate table public.notes restart identity cascade", {"truncate"}),
    ("DROP TABLE x", {"drop_table"}),
    ("drop table if exists public.tmp_a, public.tmp_b cascade", {"drop_table"}),
    ("Drop Table Foo", {"drop_table"}),
    ("DROP SCHEMA staging CASCADE", {"drop_schema"}),
    ("drop database foo", {"drop_database"}),
    ("DELETE FROM sessions", {"delete_no_where"}),
    ("delete from public.sessions returning *", {"delete_no_where"}),
    ("DELETE FROM ONLY t", {"delete_no_where"}),
    ("DELETE\nFROM\n  t", {"delete_no_where"}),
    ("DELETE/**/FROM t", {"delete_no_where"}),
    ("DELETE FROM t USING u", {"delete_no_where"}),
    ("UPDATE users SET plan='x'", {"update_no_where"}),
    ("update public.users u set plan = 'x' from y", {"update_no_where"}),
    ("UPDATE public.t AS x SET a = 1", {"update_no_where"}),
    ('UPDATE "My Table" SET a = 1', {"update_no_where"}),
    ("UPDATE ONLY t SET a=1", {"update_no_where"}),
    # 多语句
    ("SELECT 1; DELETE FROM t;", {"delete_no_where"}),
    ("BEGIN; UPDATE t SET a=1; COMMIT;", {"update_no_where"}),
    ("INSERT INTO log VALUES (1); TRUNCATE log", {"truncate"}),
    ("delete from t where id in (select id from u); drop table x", {"drop_table"}),
    ("UPDATE t SET a=1 WHERE id=1; DELETE FROM u", {"delete_no_where"}),
    # 注释 / 字符串里的 WHERE 不算数
    ("-- cleanup\nDELETE FROM t -- WHERE id=1", {"delete_no_where"}),
    ("/* WHERE */ DELETE /* x */ FROM t", {"delete_no_where"}),
    ("UPDATE t SET a = 1 /* WHERE id = 1 */", {"update_no_where"}),
    ("UPDATE t SET note = 'where it is'", {"update_no_where"}),
    # 看上去有 WHERE，其实是整张表
    ("DELETE FROM t WHERE true", {"delete_no_where"}),
    ("DELETE FROM t WHERE 1 = 1 RETURNING *", {"delete_no_where"}),
    ("UPDATE t SET a = 1 WHERE 1=1", {"update_no_where"}),
    # 子查询里的 WHERE 不是外层语句的 WHERE
    ("UPDATE t SET a = (SELECT b FROM u WHERE u.id = 1)", {"update_no_where"}),
    ("UPDATE t SET a=1 FROM (SELECT 1) u", {"update_no_where"}),
    # 藏在 CTE / EXPLAIN / DO 块 / 函数体里
    ("WITH d AS (DELETE FROM t RETURNING *) SELECT count(*) FROM d", {"delete_no_where"}),
    ("EXPLAIN ANALYZE DELETE FROM t", {"delete_no_where"}),
    ("DO $$ BEGIN TRUNCATE t; END $$", {"truncate"}),
    ("DO $f$ BEGIN DROP TABLE x; END $f$", {"drop_table"}),
    ("DO $a$ BEGIN EXECUTE $b$ DELETE FROM t $b$; END $a$", {"delete_no_where"}),
    ("CREATE FUNCTION f() RETURNS void AS $$ BEGIN DELETE FROM t; END $$ LANGUAGE plpgsql", {"delete_no_where"}),
    # 引号的坑：标准字符串里反斜杠不是转义，E 字符串里才是
    ("select '\\' ; delete from t", {"delete_no_where"}),
    ("select E'\\'' ; delete from t", {"delete_no_where"}),
    # 没写完的注释 / 括号：数据库会报错，但宁可拦
    ("DELETE FROM t /* unterminated WHERE", {"delete_no_where"}),
    ("DO $a$ BEGIN DELETE FROM t", {"delete_no_where"}),
    # 一条里多类
    ("TRUNCATE a; DROP SCHEMA s; DELETE FROM b", {"truncate", "drop_schema", "delete_no_where"}),
]

NEGATIVE = [
    "SELECT * FROM users WHERE id = 1",
    "SELECT 'DROP TABLE users'",
    "SELECT E'it\\'s DROP TABLE x'",
    "SELECT 'it''s DELETE FROM t'",
    "-- DROP TABLE x\nSELECT 1",
    "select 1 -- ; delete from t",
    "/* TRUNCATE t */ SELECT 1",
    "SELECT * FROM \"drop table\"",
    "DELETE FROM t WHERE id = 1",
    "DELETE FROM t\nWHERE id = 1",
    "delete from t where id in (select id from u)",
    "DELETE FROM t WHERE ctid IN (SELECT ctid FROM t LIMIT 1)",
    "DELETE FROM t WHERE EXISTS (SELECT 1 FROM u)",
    "UPDATE t SET a=1 WHERE id=1",
    "UPDATE t SET a = 1 WHERE (id = 1)",
    "UPDATE t SET a=1 WHERE id IN (SELECT id FROM u WHERE x)",
    "UPDATE t SET a=1 FROM u WHERE u.id=t.id",
    "UPDATE t SET a = $1 WHERE id = $2",
    "INSERT INTO t (a) VALUES ('DELETE FROM t')",
    "INSERT INTO t(a) VALUES (1) ON CONFLICT (a) DO UPDATE SET b = 2",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t WHERE a = 1 FOR UPDATE SKIP LOCKED",
    "CREATE TABLE t (a int REFERENCES u(id) ON DELETE CASCADE ON UPDATE SET NULL)",
    "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES u(id) ON UPDATE CASCADE ON DELETE SET NULL",
    "GRANT UPDATE, DELETE, TRUNCATE ON t TO r",
    "REVOKE TRUNCATE ON t FROM anon",
    "CREATE TRIGGER x AFTER DELETE OR UPDATE ON t FOR EACH ROW EXECUTE FUNCTION f()",
    "CREATE TRIGGER x AFTER TRUNCATE ON t FOR EACH STATEMENT EXECUTE FUNCTION f()",
    "CREATE POLICY p ON t FOR UPDATE USING (true)",
    "CREATE POLICY p ON t FOR DELETE USING (a = 1)",
    "SELECT date_trunc('day', now())",
    "select truncate_count, can_delete, last_update from stats",
    "COMMENT ON TABLE t IS 'truncate me'",
    "COMMENT ON COLUMN t.a IS 'update t set'",
    "ALTER TABLE t ADD COLUMN a int",
    "ALTER TABLE t DROP COLUMN a",
    "DROP INDEX i",
    "DROP VIEW v",
    "DROP FUNCTION f",
    "CREATE TABLE tmp AS SELECT * FROM t",
    "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$",
    "SELECT $1, $2",
]


def tool_event(tool, **ti):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti}


def cats(sql, deny=ALL):
    vs = engine.check_tool({"lang": "zh", "checks": [{**RT13, "sql_deny": list(deny)}]}, EXEC, {"query": sql})
    return vs


class SqlUnitsTest(unittest.TestCase):
    def test_strips_comments_strings_and_splits(self):
        self.assertEqual(engine.sql_units("SELECT 'a;b' -- x;y\n; /* ; */ DELETE FROM t"), ["SELECT 'x'", "DELETE FROM t"])

    def test_parens_become_their_own_statement(self):
        self.assertEqual(sorted(engine.sql_units("UPDATE t SET a = (SELECT b FROM u WHERE x)")),
                         sorted(["UPDATE t SET a = ()", "SELECT b FROM u WHERE x"]))

    def test_dollar_body_is_scanned_as_code(self):
        units = engine.sql_units("DO $x$ BEGIN\nTRUNCATE t; END $x$")
        self.assertTrue(any(u.endswith("TRUNCATE t") for u in units), units)

    def test_param_is_not_a_dollar_quote(self):
        self.assertEqual(engine.sql_units("SELECT $1, $2"), ["SELECT $1, $2"])

    def test_nested_block_comment(self):
        self.assertEqual(engine.sql_units("SELECT /* a /* b */ DELETE FROM t */ 1"), ["SELECT 1"])

    def test_whitespace_is_collapsed(self):
        self.assertEqual(engine.sql_units("DELETE\n\t  FROM   t"), ["DELETE FROM t"])

    def test_pathological_inputs_stay_fast(self):
        import time
        bombs = ["UPDATE t" + " " * 300_000 + "x", "DELETE FROM x " * 50_000 + "WHERE a = 1", "UPDATE t SET a = 1 " * 30_000,
                 "UPDATE a" + ".b" * 100_000 + " x", "(" * 50_000 + "DELETE FROM t" + ")" * 50_000, "$$" * 50_000]
        t0 = time.time()
        for b in bombs:
            engine.sql_findings(b)
        self.assertLess(time.time() - t0, 10, "超长 / 重复 / 嵌套的输入不能把 hook 拖慢到超时（超时 = 放行）")

    def test_empty_and_no_keyword_are_cheap(self):
        self.assertEqual(engine.sql_findings(""), [])
        self.assertEqual(engine.sql_findings("SELECT * FROM t"), [])


class JudgmentTableTest(unittest.TestCase):
    """RT13（execute_sql，六类全拦）和 RT14（apply_migration，不拦 DROP TABLE）的判断表。"""

    def test_findings_are_the_right_categories(self):
        for sql, want in POSITIVE:
            self.assertEqual(set(engine.sql_findings(sql)), want, sql)

    def test_negatives_have_no_findings(self):
        for sql in NEGATIVE:
            self.assertEqual(engine.sql_findings(sql), [], sql)

    def test_table_is_big_enough(self):
        self.assertGreaterEqual(len(POSITIVE), 15)
        self.assertGreaterEqual(len(NEGATIVE), 15)

    def test_rt13_blocks_every_positive_and_no_negative(self):
        for sql, _want in POSITIVE:
            self.assertTrue(engine.check_tool(DOC, EXEC, {"query": sql, "project_id": "p"}), sql)
        for sql in NEGATIVE:
            self.assertFalse(engine.check_tool(DOC, EXEC, {"query": sql, "project_id": "p"}), sql)

    def test_rt14_lets_drop_table_through_but_nothing_else_changes(self):
        for sql, want in POSITIVE:
            blocked = bool(engine.check_tool(DOC, MIGR, {"query": sql, "name": "m", "project_id": "p"}))
            self.assertEqual(blocked, bool(want - {"drop_table"}), sql)
        for sql in NEGATIVE:
            self.assertFalse(engine.check_tool(DOC, MIGR, {"query": sql}), sql)

    def test_tightening_rt14_is_one_line(self):
        tight = {"lang": "zh", "checks": [{**RT14, "sql_deny": list(ALL)}]}
        self.assertTrue(engine.check_tool(tight, MIGR, {"query": "DROP TABLE x"}))
        loose = {"lang": "zh", "checks": [{**RT14, "sql_deny": list(NO_DROP_TABLE),
                                           "patterns": [r"\bDROP\s+COLUMN\b"], "normalize": "sql"}]}
        self.assertTrue(engine.check_tool(loose, MIGR, {"query": "ALTER TABLE t DROP COLUMN a"}))
        self.assertFalse(engine.check_tool(loose, MIGR, {"query": "ALTER TABLE t ADD COLUMN a int"}))
        self.assertFalse(engine.check_tool(loose, MIGR, {"query": "SELECT 'DROP COLUMN'"}), "normalize: sql 让字符串里的不算")


class CheckToolTest(unittest.TestCase):
    def test_only_named_tools_are_looked_at(self):
        self.assertFalse(engine.check_tool(DOC, "mcp__supabase__list_tables", {"query": "TRUNCATE t"}))
        self.assertFalse(engine.check_tool(DOC, "Bash", {"query": "TRUNCATE t"}))
        self.assertFalse(engine.check_tool(DOC, EXEC.upper(), {"query": "TRUNCATE t"}), "工具名区分大小写")

    def test_tools_use_fnmatch(self):
        doc = {"lang": "en", "checks": [{**RT13, "tools": ["mcp__supabase__*"]}]}
        self.assertTrue(engine.check_tool(doc, "mcp__supabase__anything", {"query": "TRUNCATE t"}))
        self.assertFalse(engine.check_tool(doc, "mcp__github__push", {"query": "TRUNCATE t"}))

    def test_disabled_rule_is_off(self):
        doc = {"lang": "zh", "checks": [{**RT13, "enabled": False}]}
        self.assertFalse(engine.check_tool(doc, EXEC, {"query": "TRUNCATE t"}))

    def test_field_only_scans_that_field_but_falls_back_when_missing(self):
        self.assertFalse(engine.check_tool(DOC, EXEC, {"query": "SELECT 1", "name": "TRUNCATE t"}), "只看 query")
        self.assertTrue(engine.check_tool(DOC, EXEC, {"sql": "TRUNCATE t"}), "query 这个字段没了 → 扫全部字符串，不能悄悄失效")
        self.assertTrue(engine.check_tool(DOC, EXEC, {"query": ["SELECT 1", "TRUNCATE t"]}), "列表里的字符串也扫")

    def test_no_field_scans_every_string(self):
        doc = {"lang": "zh", "checks": [{k: v for k, v in RT13.items() if k != "field"}]}
        self.assertTrue(engine.check_tool(doc, EXEC, {"a": {"b": ["x", "TRUNCATE t"]}}))

    def test_non_string_inputs_are_ignored_not_crashing(self):
        self.assertFalse(engine.check_tool(DOC, EXEC, {"query": 5}))
        self.assertFalse(engine.check_tool(DOC, EXEC, {}))

    def test_patterns_without_sql_normalizer_scan_raw_text(self):
        c = {"id": "RT20", "type": "forbid_tool_input", "tools": ["mcp__x__*"], "patterns": [r"(?i)secret\w*"]}
        doc = {"lang": "en", "checks": [c]}
        vs = engine.check_tool(doc, "mcp__x__run", {"cmd": "cat SECRET_file"})
        self.assertEqual([v.check for v in vs], ["RT20"])
        self.assertIn("SECRET_file", vs[0].msg)
        self.assertFalse(engine.check_tool(doc, "mcp__x__run", {"cmd": "ls"}))

    def test_message_names_the_category_but_never_echoes_the_sql(self):
        marker = "kid_marker_7731"
        vs = engine.check_tool(DOC, EXEC, {"query": f"DELETE FROM notes -- {marker}\n; UPDATE {marker} SET a = '{marker}'"})
        text = engine.format_violations(vs, "zh")
        self.assertIn("DELETE 没有 WHERE", text)
        self.assertIn("UPDATE 没有 WHERE", text)
        self.assertNotIn(marker, text, "SQL 里可能有用户数据，报错信息里只说类别")
        en = engine.format_violations(engine.check_tool({**DOC, "lang": "en"}, EXEC, {"query": "TRUNCATE t"}), "en")
        self.assertIn("TRUNCATE", en)
        self.assertIn(EXEC, en)

    def test_violation_carries_rule_and_source(self):
        v = engine.check_tool(DOC, EXEC, {"query": "TRUNCATE t"})[0]
        self.assertEqual((v.check, v.source), ("RT13", "test"))
        self.assertIn("RT13", str(v))


class LoadValidationTest(unittest.TestCase):
    def _load(self, check):
        with Project({"CLAUDE.md": "# x\n"}, git=False) as p:
            d = p.root / ".rule-teeth"
            d.mkdir()
            (d / "rules.json").write_text(json.dumps({"checks": [check]}), encoding="utf-8")
            return engine.load_rules(p.root)

    def test_good_rule_loads(self):
        self.assertEqual(self._load(RT13)["checks"][0]["id"], "RT13")

    def test_bad_rules_fail_loudly_at_load_time(self):
        bads = [{**RT13, "tools": []}, {**RT13, "tools": "mcp__x"}, {k: v for k, v in RT13.items() if k != "sql_deny"},
                {**RT13, "sql_deny": ["drop_tabel"]}, {**RT13, "normalize": "xml"}, {**RT13, "field": 3},
                {"id": "RT9", "type": "forbid_tool_input", "tools": ["a"], "patterns": ["("]}]
        for c in bads:
            with self.assertRaises(ValueError, msg=str(c)):
                self._load(c)

    def test_event_table_knows_the_type(self):
        self.assertIn("forbid_tool_input", engine.CHECK_TYPES)
        self.assertEqual(engine.EVENTS["forbid_tool_input"], ("tool",))
        self.assertEqual(engine.SQL_CATEGORIES, ALL)


class HookEndToEndTest(unittest.TestCase):
    """像 Claude Code 那样：装好的 hook 从 stdin 读 JSON，违规 exit 2、stderr 是理由。"""

    def setUp(self):
        self.p = Project({"CLAUDE.md": "# 规则\n\n- 不许用 `requests.post`\n"})
        self.assertEqual(self.p.cli("init", ".", "--hook").returncode, 0)
        rules = self.p.root / ".rule-teeth" / "rules.json"
        doc = json.loads(rules.read_text(encoding="utf-8"))
        doc["lang"] = "zh"
        doc["checks"] += [RT13, RT14]
        rules.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.p.__exit__()

    def test_destructive_sql_is_blocked_with_exit_2(self):
        r = self.p.hook(tool_event(EXEC, project_id="p", query="DELETE FROM sessions"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("RT13", r.stderr)
        self.assertIn("DELETE 没有 WHERE", r.stderr)
        self.assertIn("不要换个写法去绕", r.stderr)

    def test_harmless_sql_and_other_tools_pass(self):
        for e in (tool_event(EXEC, project_id="p", query="SELECT count(*) FROM sessions WHERE a = 1"),
                  tool_event(EXEC, project_id="p", query="UPDATE t SET a = 1 WHERE id = 3"),
                  tool_event("mcp__supabase__list_tables", project_id="p"),
                  tool_event("mcp__github__push_files", files=["TRUNCATE x"]),
                  tool_event(EXEC)):
            r = self.p.hook(e)
            self.assertEqual((r.returncode, r.stderr), (0, ""), e)

    def test_migration_rule_is_looser(self):
        ok = self.p.hook(tool_event(MIGR, project_id="p", name="drop_old", query="DROP TABLE old_things"))
        self.assertEqual((ok.returncode, ok.stderr), (0, ""))
        bad = self.p.hook(tool_event(MIGR, project_id="p", name="wipe", query="TRUNCATE old_things"))
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assertIn("RT14", bad.stderr)

    def test_old_kinds_still_work_next_to_the_new_one(self):
        r = self.p.hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                         "tool_input": {"file_path": str(self.p.root / "a.py"), "content": "requests.post(u)\n"}})
        self.assertEqual(r.returncode, 2, r.stderr)
        r = self.p.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(r.returncode, 0)

    def test_only_pre_tool_use_looks_at_inputs(self):
        e = {**tool_event(EXEC, query="TRUNCATE t"), "hook_event_name": "PostToolUse"}
        self.assertEqual(self.p.hook(e).returncode, 0)

    def test_broken_new_rule_is_a_visible_non_blocking_error(self):
        rules = self.p.root / ".rule-teeth" / "rules.json"
        doc = json.loads(rules.read_text(encoding="utf-8"))
        doc["checks"].append({"id": "RT99", "type": "forbid_tool_input", "tools": [], "sql_deny": ["truncate"]})
        rules.write_text(json.dumps(doc), encoding="utf-8")
        r = self.p.hook(tool_event(EXEC, query="SELECT 1"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("rule-teeth hook error", r.stderr)


if __name__ == "__main__":
    unittest.main()
