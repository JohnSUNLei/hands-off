"""End-to-end: the installed engine, fed the same JSON Claude Code would send, with a growing transcript."""
import json
import unittest

import brake as bk
from _harness import NEW_TOK, PER_CALL, START, Project, stopped


class Money(unittest.TestCase):
    def setUp(self):
        self.p = Project(init_args=("--usd", "5", "--repeat", "0"))
        self.p.start()

    def tearDown(self):
        self.p.cleanup()

    def test_quiet_under_budget(self):
        self.p.grow(3)
        self.assertIsNone(self.p.hook("pre_read_src"))

    def test_warns_once_at_80_percent_and_tells_the_agent_to_wrap_up(self):
        self.p.grow(9)                                    # 9 × $0.49 = $4.41 → 88%
        out = self.p.hook("pre_read_src")
        self.assertIn("88% of this task's $5.00 budget used ($4.41)", out["systemMessage"])
        self.assertIn("Wrap up", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.p.hook("pre_read_src"))   # only once

    def test_stops_at_the_limit_with_the_reason(self):
        self.p.grow(11)                                   # $5.39
        out = self.p.hook("pre_edit_src")
        self.assertTrue(stopped(out))
        self.assertIn("estimated cost $5.39 reached the $5.00 task limit", out["stopReason"])
        self.assertIn("send a new message", out["stopReason"])
        self.assertIn("Do not try to get around this", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("since “Make the test suite pass.", out["stopReason"])

    def test_stays_stopped_until_the_next_message_then_fresh_budget(self):
        self.p.grow(11)
        self.assertTrue(stopped(self.p.hook("pre_edit_src")))
        self.assertTrue(stopped(self.p.hook("pre_read_src")))
        self.p.hook("prompt")                             # the human says "go on"
        self.assertIsNone(self.p.hook("pre_read_src"))
        self.p.grow(12)                                   # the call in flight at the prompt belongs to the old task
        self.assertTrue(stopped(self.p.hook("pre_read_src")))

    def test_each_response_counted_once(self):
        self.p.grow(10)
        st = json.loads(next((self.p.root / ".brake" / "state").glob("*.json")).read_text())
        reader = bk.UsageReader(st, {k: list(v) for k, v in bk.PRICES.items()})
        tok, usd = reader.totals(None)
        self.assertEqual(tok, 0)                          # nothing read yet: hooks have not run
        self.p.hook("pre_read_src")
        st = json.loads(next((self.p.root / ".brake" / "state").glob("*.json")).read_text())
        reader = bk.UsageReader(st, {k: list(v) for k, v in bk.PRICES.items()})
        tok, usd = reader.totals(None)
        self.assertEqual(tok, 10 * NEW_TOK)
        self.assertAlmostEqual(usd, 10 * PER_CALL, places=6)

    def test_status(self):
        self.p.grow(11)
        self.p.hook("pre_edit_src")
        out = self.p.cli("status").stdout
        self.assertIn("task: $5.00", out)
        self.assertIn("⛔", out)


class OtherLimits(unittest.TestCase):
    def test_loop(self):
        p = Project(init_args=("--usd", "100", "--repeat", "4"))
        try:
            p.start()
            for i in range(3):
                p.grow(1)
                self.assertIsNone(p.hook("pre_bash_test" if i % 2 == 0 else "pre_bash_test_again"), i)
            p.hook("pre_read_src")
            p.grow(1)
            out = p.hook("pre_bash_test")                 # 4th `npm test` (descriptions differ: still the same call)
            self.assertTrue(stopped(out))
            self.assertIn("the same call was made 4 times in the last 5 calls: Bash(npm test)", out["stopReason"])
        finally:
            p.cleanup()

    def test_minutes(self):
        p = Project(init_args=("--usd", "100", "--minutes", "30", "--repeat", "0"))
        try:
            p.start()
            self.assertIsNone(p.hook("pre_read_src", now="2026-11-21T03:20:00+00:00"))
            out = p.hook("pre_read_src", now="2026-11-21T03:31:00+00:00")
            self.assertTrue(stopped(out))
            self.assertIn("it has run 31 min, past the 30-minute task limit", out["stopReason"])
        finally:
            p.cleanup()

    def test_tokens(self):
        p = Project(init_args=("--usd", "100", "--tokens", "100000", "--repeat", "0"))
        try:
            p.start()
            p.grow(3)                                      # 97,515 new tokens
            self.assertIn("98%", p.hook("pre_read_src")["systemMessage"])
            p.grow(1)
            out = p.hook("pre_read_src")
            self.assertIn("130,020 new tokens reached the 100,000 task limit", out["stopReason"])
        finally:
            p.cleanup()

    def test_session_limit_blocks_new_prompts(self):
        p = Project(init_args=("--usd", "100", "--repeat", "0"))
        try:
            cfg = p.root / ".brake" / "config.json"
            doc = json.loads(cfg.read_text())
            doc["session"]["usd"] = 3
            cfg.write_text(json.dumps(doc))
            p.start()
            p.grow(7)                                      # $3.43
            out = p.hook("pre_read_src")
            self.assertIn("session limit", out["stopReason"])
            out = p.hook("prompt")
            self.assertEqual(out["decision"], "block")
            self.assertIn("this session reached its estimated cost $3.43", out["reason"])
        finally:
            p.cleanup()

    def test_subagent_spend_counts(self):
        p = Project(init_args=("--usd", "5", "--repeat", "0"), with_subagent=True)
        try:
            p.start()
            p.grow(10)                                     # $4.90 + Haiku 5.5 helper ($0.0025)
            st_out = p.hook("pre_read_src")
            st = json.loads(next((p.root / ".brake" / "state").glob("*.json")).read_text())
            r = bk.UsageReader(st, {k: list(v) for k, v in bk.PRICES.items()})
            tok, usd = r.totals(None)
            self.assertAlmostEqual(usd, 10 * PER_CALL + (20000 * 0.10 + 1000 * 0.50) / 1e6, places=6)
        finally:
            p.cleanup()

    def test_missing_transcript_is_said_out_loud(self):
        p = Project()
        try:
            p.start()
            out = p.hook("pre_read_src", transcript_path=str(p.tmp / "nope.jsonl"))
            self.assertIn("Dollar and token limits are OFF", out["systemMessage"])
            self.assertIsNone(p.hook("pre_read_src", transcript_path=str(p.tmp / "nope.jsonl")))   # said once
        finally:
            p.cleanup()

    def test_chinese(self):
        p = Project(init_args=("--usd", "5", "--lang", "zh"))
        try:
            p.start()
            p.grow(11)
            out = p.hook("pre_edit_src")
            self.assertIn("已叫停这个任务", out["stopReason"])
            self.assertIn("到了单个任务上限 $5.00", out["stopReason"])
        finally:
            p.cleanup()


class Safety(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.start()

    def tearDown(self):
        self.p.cleanup()

    def test_limits_cannot_be_loosened_by_the_ai(self):
        for name in ("pre_edit_self", "pre_bash_self_rm"):
            out = self.p.hook(name)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", name)
        self.assertIsNone(self.p.hook("pre_bash_self_status"))

    def test_broken_input_never_wedges(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, str(self.p.engine), "hook"], input="[", capture_output=True, text=True,
                           cwd=self.p.root, env=self.p.env())
        self.assertEqual(r.returncode, 1)
        self.assertIn("brake hook error", r.stderr)

    def test_same_input_same_output(self):
        self.p.grow(11)
        a = self.p.hook("pre_edit_src")
        b = self.p.hook("pre_edit_src")
        self.assertEqual(a["stopReason"].splitlines()[0], b["stopReason"].splitlines()[0])


class Prices(unittest.TestCase):
    P = {k: list(v) for k, v in bk.PRICES.items()}

    def test_cache_and_fast(self):
        u = {"input_tokens": 1000, "output_tokens": 1000, "cache_creation_input_tokens": 3000,
             "cache_read_input_tokens": 10000, "cache_creation": {"ephemeral_1h_input_tokens": 1000}}
        tok, usd = bk.message_cost(u, "claude-opus-5-5", self.P)
        self.assertEqual(tok, 5000)
        self.assertAlmostEqual(usd, (1000 * 4 + 1000 * 20 + 2000 * 5 + 1000 * 8 + 10000 * 0.2) / 1e6)
        _, fast = bk.message_cost({**u, "speed": "fast"}, "claude-opus-5-5", self.P)
        self.assertAlmostEqual(fast, 2 * usd)

    def test_unknown_and_dated_models(self):
        u = {"input_tokens": 1_000_000}
        self.assertEqual(bk.message_cost(u, "claude-sonnet-4-6-20260101", self.P)[1], 3.0)
        self.assertEqual(bk.message_cost(u, "some-new-model", self.P)[1], 10.0)     # priced high on purpose


class Install(unittest.TestCase):
    OTHER = {"env": {"X": "1"}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./s.sh"}]}]}}

    def test_install_uninstall(self):
        p = Project(settings=self.OTHER)
        try:
            s = json.loads((p.root / ".claude" / "settings.json").read_text())
            self.assertEqual(s["env"], {"X": "1"})
            self.assertEqual(s["hooks"]["Stop"], self.OTHER["hooks"]["Stop"])
            for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
                self.assertIn(".brake/brake.py", s["hooks"][ev][0]["hooks"][0]["command"])
            cfg = json.loads((p.root / ".brake" / "config.json").read_text())
            self.assertEqual(cfg["task"]["usd"], 5.0)
            self.assertIn("already", p.cli("init", str(p.root)).stdout)
            r = p.cli("uninstall", str(p.root))
            self.assertIn("3 hook", r.stdout)
            self.assertEqual(json.loads((p.root / ".claude" / "settings.json").read_text()), self.OTHER)
            self.assertFalse((p.root / ".brake").exists())
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
