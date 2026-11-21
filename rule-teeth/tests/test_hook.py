# -*- coding: utf-8 -*-
"""端到端：init --hook 之后，像 Claude Code 那样把 JSON 喂给 .rule-teeth/engine.py，看它咬不咬。

hook 协议照官方文档：PreToolUse / Stop 上 exit 2 = 拦下，stderr 是给 Claude 的理由。
"""
import json
import unittest

from _util import Project

RULES = """# 项目规则

## 约定

- 单文件不得超过 20 行
- 不许用 `requests.post`
- Do NOT mark PRs as ready (`gh pr ready`)
- 不要改 `vendor/` 里的文件
- 每个 agent 回应 John 都以 "John —" 开头
"""


def write_event(root, rel, content):
    return {"hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(root / rel), "content": content}}


class HookTest(unittest.TestCase):
    def setUp(self):
        self.p = Project({"CLAUDE.md": RULES, "src/old.py": "a = requests.post(u)\n"})
        r = self.p.cli("init", ".", "--hook")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.p.git("add", "-A")
        self.p.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "teeth")

    def tearDown(self):
        self.p.__exit__()

    def test_settings_merged_once_and_unhook(self):
        settings = self.p.root / ".claude" / "settings.json"
        self.p.cli("init", ".", "--hook")                    # 再跑一次不能重复挂
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 1)
        self.assertEqual(data["hooks"]["PreToolUse"][0]["matcher"], "Write|Edit|Bash")
        self.assertIn("${CLAUDE_PROJECT_DIR}", data["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.p.cli("unhook", ".")
        self.assertNotIn("hooks", json.loads(settings.read_text(encoding="utf-8")))

    def test_write_with_forbidden_pattern_is_blocked(self):
        r = self.p.hook(write_event(self.p.root, "src/new.py", "x = requests.post(u)\n"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("requests", r.stderr)
        self.assertIn("CLAUDE.md:", r.stderr, "要告诉 Claude 是哪条规则、写在哪")

    def test_clean_write_passes(self):
        r = self.p.hook(write_event(self.p.root, "src/new.py", "x = 1\n"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_edit_of_old_violation_file_not_blocked(self):
        e = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(self.p.root / "src/old.py"), "old_string": "a =", "new_string": "b ="}}
        self.assertEqual(self.p.hook(e).returncode, 0, "老文件里早就有的问题，不拦无关修改")

    def test_too_long_file_blocked(self):
        r = self.p.hook(write_event(self.p.root, "src/long.py", "x = 1\n" * 25))
        self.assertEqual(r.returncode, 2)

    def test_bash_command_blocked(self):
        e = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "gh pr ready 7"}}
        self.assertEqual(self.p.hook(e).returncode, 2)
        e["tool_input"]["command"] = "gh pr view 7"
        self.assertEqual(self.p.hook(e).returncode, 0)

    def test_protected_dir_and_self_protection(self):
        self.assertEqual(self.p.hook(write_event(self.p.root, "vendor/x.js", "1")).returncode, 2)
        r = self.p.hook(write_event(self.p.root, ".rule-teeth/rules.json", "{}"))
        self.assertEqual(r.returncode, 2, "AI 不能自己给自己松绑")

    def test_stop_blocks_then_gives_up_loudly(self):
        self.p.write({"src/new.py": "x = requests.post(u)\n"})       # 绕过 hook 写进去的违规（比如用 Bash）
        stop = {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "John — 好了"}
        first = self.p.hook(stop)
        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertIn("src/new.py", first.stderr)
        self.p.hook({**stop, "stop_hook_active": True})
        third = self.p.hook({**stop, "stop_hook_active": True})
        self.assertEqual(third.returncode, 0, "同一批违规拦了两次还在，就放行 —— 不能死循环")
        self.assertIn("systemMessage", third.stdout, "放行也要明说没解决")

    def test_stop_checks_reply_prefix(self):
        r = self.p.hook({"hook_event_name": "Stop", "last_assistant_message": "好了"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("John —", r.stderr)

    def test_outside_project_and_unknown_events_pass(self):
        self.assertEqual(self.p.hook(write_event(self.p.root.parent, "elsewhere.py", "requests.post")).returncode, 0)
        self.assertEqual(self.p.hook({"hook_event_name": "SessionStart"}).returncode, 0)

    def test_broken_rules_file_is_a_visible_non_blocking_error(self):
        (self.p.root / ".rule-teeth" / "rules.json").write_text("{not json", encoding="utf-8")
        r = self.p.hook(write_event(self.p.root, "src/new.py", "x = requests.post(u)\n"))
        self.assertEqual(r.returncode, 1, "坏了要显式报错，但不能拦住人干活（exit 1 = 非阻塞错误）")
        self.assertIn("rule-teeth hook error", r.stderr)

    def test_check_command_reports_and_counts_deferred(self):
        r = self.p.cli("check", ".")
        self.assertEqual(r.returncode, 0, r.stdout)        # 老违规已登记暂缓
        self.assertIn("暂缓", r.stdout)


if __name__ == "__main__":
    unittest.main()
