"""Claude Code hook payloads, the CLI, init, the demo and the one-file install."""
import json
import os
import shutil
import stat
import sys

from _helpers import INCIDENT, JORDAN, ME, REPLY_WITH_TIMES, WED, FuseCase, sf


def pre(tool, tool_input, cwd, tid="toolu_01"):
    return {"session_id": "s1", "transcript_path": "/dev/null", "cwd": str(cwd), "permission_mode": "default",
            "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "tool_use_id": tid}


def decision(stdout: str):
    if not stdout.strip():
        return None, ""
    o = json.loads(stdout)["hookSpecificOutput"]
    assert o["hookEventName"] == "PreToolUse"
    return o["permissionDecision"], o["permissionDecisionReason"]


class ToolClassification(FuseCase):
    def test_send_tools(self):
        pol = sf.DEFAULT_POLICY
        sends = ["mcp__gmail__send_message", "mcp__gmail__reply", "mcp__slack__chat_postMessage", "mcp__x__create_post",
                 "mcp__mail__forward", "mcp__gh__add_issue_comment", "mcp__sms__message_user", "mcp__a__send_draft",
                 "mcp__bsky__publish", "mcp__t__notify_user", "SendEmail"]
        reads = ["mcp__gmail__get_message", "mcp__gmail__search_threads", "mcp__gmail__create_draft",
                 "mcp__gmail__list_drafts", "mcp__slack__get_post", "mcp__x__list_comments", "Read", "Bash",
                 "mcp__mail__email_lookup", "mcp__gmail__update_draft", "WebFetch"]
        for t in sends:
            self.assertTrue(sf.is_send_tool(t, pol), t)
        for t in reads:
            self.assertFalse(sf.is_send_tool(t, pol), t)
        self.assertTrue(sf.is_send_tool("mcp__crm__log_touch", dict(pol, guard_tools=["log_touch$"])))
        self.assertFalse(sf.is_send_tool("mcp__ci__post_status", dict(pol, ignore_tools=["ci__"])))

    def test_matcher_is_a_regex_that_catches_them(self):
        import re
        self.assertTrue(any(c in sf.HOOK_MATCHER for c in "[]^$"))      # literal lists are NOT regexes
        for t in ["mcp__gmail__send_message", "mcp__slack__chat_postMessage", "mcp__gmail__reply", "Bash"]:
            self.assertTrue(re.search(sf.HOOK_MATCHER, t), t)
        self.assertFalse(re.search(sf.HOOK_MATCHER, "Read"))

    def test_field_mapping(self):
        o = sf.outgoing_from_tool("mcp__gmail__send_message", {
            "to": ["Jordan <J@x.example>"], "cc": "b@x.example", "subject": "Re: Hi", "body": "Hello",
            "threadId": "t-1", "inReplyTo": "<m1@x>"})
        self.assertEqual((o.to, o.subject, o.body, o.thread_id, o.in_reply_to),
                         (["j@x.example", "b@x.example"], "Re: Hi", "Hello", "t-1", "<m1@x>"))
        o = sf.outgoing_from_tool("mcp__slack__chat_postMessage", {"channel": "C123", "text": "hi", "thread_ts": "17.2"})
        self.assertEqual((o.to, o.body, o.thread_id), (["c123"], "hi", "17.2"))
        o = sf.outgoing_from_tool("x", {"recipients": [{"email": "a@x.example"}, {"email": "b@x.example"}]})
        self.assertEqual(o.to, ["a@x.example", "b@x.example"])


class HookFunction(FuseCase):
    def setUp(self):
        super().setUp()
        self.write_thread(INCIDENT)
        os.environ["SENDFUSE_NOW"] = WED

    def call(self, payload, **pol):
        return sf.hook(payload, self.policy(**pol), sf.State(self.state_dir))

    def test_incident_denied_with_reason_for_claude(self):
        code, out, err = self.call(pre("mcp__gmail__reply", {"message_id": "<m1@acme.example>",
                                                             "body": REPLY_WITH_TIMES}, self.tmp))
        kind, reason = decision(out)
        self.assertEqual((code, kind), (0, "deny"))
        self.assertIn("already_settled", reason)
        self.assertIn("Do not retry", reason)

    def test_non_send_tool_untouched(self):
        self.assertEqual(self.call(pre("mcp__gmail__get_thread", {"thread_id": "x"}, self.tmp)), (0, "", ""))
        self.assertEqual(self.call(pre("Bash", {"command": "ls -la"}, self.tmp)), (0, "", ""))

    def test_allowed_send_is_reserved_then_settled(self):
        p = pre("mcp__gmail__send_message", {"to": "new@x.example", "subject": "Hello", "body": "Hi"}, self.tmp, "toolu_9")
        self.assertEqual(self.call(p), (0, "", ""))
        st = sf.State(self.state_dir)
        self.assertEqual(st.ledger()[-1]["status"], "reserved")
        post = dict(p, hook_event_name="PostToolUse", tool_response={"ok": True})
        self.assertEqual(self.call(post), (0, "", ""))
        self.assertEqual(st.ledger()[-1]["status"], "sent")
        kind, reason = decision(self.call(dict(p, tool_use_id="toolu_10"))[1])
        self.assertEqual(kind, "deny")
        self.assertIn("same_thread", reason)

    def test_review_asks_the_user_with_preview(self):
        kind, reason = decision(self.call(pre("mcp__gmail__send_message",
                                              {"to": "new@x.example", "subject": "Hello", "body": "Hi there"},
                                              self.tmp), review="always")[1])
        self.assertEqual(kind, "ask")
        self.assertIn("To: new@x.example", reason)
        self.assertIn("Hi there", reason)

    def test_quiet_hours_held(self):
        os.environ["SENDFUSE_NOW"] = "2026-11-19T23:40:00-05:00"
        kind, reason = decision(self.call(pre("mcp__slack__chat_postMessage", {"channel": "#team", "text": "done"},
                                              self.tmp))[1])
        self.assertEqual(kind, "deny")
        self.assertIn("held, not lost", reason)
        self.assertEqual(sf.State(self.state_dir).queue()[-1]["status"], "held")

    def test_bash_send_commands(self):
        for cmd in ("echo hi | mail -s 'Hi' a@x.example", "sendmail -t < msg.txt",
                    "curl -X POST https://slack.com/api/chat.postMessage -d text=hi",
                    "curl https://api.sendgrid.com/v3/mail/send -d @m.json", "gh issue comment 12 -b 'done'"):
            kind, reason = decision(self.call(pre("Bash", {"command": cmd}, self.tmp))[1])
            self.assertEqual(kind, "ask", cmd)
            self.assertIn(cmd[:20], reason)
        kind, _ = decision(self.call(pre("Bash", {"command": "sendmail -t < m"}, self.tmp), bash="block")[1])
        self.assertEqual(kind, "deny")
        self.assertEqual(self.call(pre("Bash", {"command": "sendmail -t < m"}, self.tmp), bash="off"), (0, "", ""))

    def test_internal_error_fails_closed(self):
        (self.state_dir).mkdir(parents=True, exist_ok=True)
        (self.state_dir / "ledger.jsonl").write_text("garbage\n", "utf-8")
        kind, reason = decision(self.call(pre("mcp__gmail__send_message", {"to": "a@x.example", "body": "x"},
                                              self.tmp))[1])
        self.assertEqual(kind, "deny")
        self.assertIn("Failing closed", reason)

    def test_broken_policy_blocks_sends_only(self):
        proj = self.tmp / "proj"
        (proj / ".send-fuse").mkdir(parents=True)
        (proj / ".send-fuse" / "policy.json").write_text("{broken", "utf-8")
        st = sf.State(self.state_dir)
        kind, _ = decision(sf.hook(pre("mcp__gmail__send_message", {"to": "a@x.example"}, proj), state=st)[1])
        self.assertEqual(kind, "deny")
        self.assertEqual(sf.hook(pre("mcp__gmail__get_message", {"id": "1"}, proj), state=st), (0, "", ""))


class Cli(FuseCase):
    def project(self, **pol):
        proj = self.tmp / "proj"
        (proj / ".send-fuse").mkdir(parents=True, exist_ok=True)
        base = {"me": [ME], "thread_source": {"type": "json", "path": "threads"},
                "quiet_hours": {"start": "21:00", "end": "08:00", "tz": "America/Toronto"}}
        base.update(pol)
        (proj / ".send-fuse" / "policy.json").write_text(json.dumps(base), "utf-8")
        shutil.copytree(self.threads, proj / "threads", dirs_exist_ok=True)
        return proj

    def test_hook_subcommand_end_to_end(self):
        self.write_thread(INCIDENT)
        proj = self.project()
        payload = pre("mcp__gmail__reply", {"message_id": "<m1@acme.example>", "body": REPLY_WITH_TIMES}, proj)
        r = self.run_cli("hook", stdin=json.dumps(payload), env={"SENDFUSE_NOW": WED}, cwd=proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision(r.stdout)[0], "deny")
        r = self.run_cli("hook", stdin="{not json", cwd=proj)
        self.assertEqual(decision(r.stdout)[0], "deny")

    def test_check_exit_codes(self):
        self.write_thread(INCIDENT)
        proj = self.project()
        env = {"SENDFUSE_NOW": WED}
        r = self.run_cli("check", "--subject", "Re: Project kickoff — Acme website redesign", "--body", REPLY_WITH_TIMES,
                         "--in-reply-to", "<m1@acme.example>", env=env, cwd=proj)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout)
        r = self.run_cli("check", "--to", "a@x.example", "--subject", "Hi", "--body", "Hello", "--json", env=env, cwd=proj)
        self.assertEqual((r.returncode, json.loads(r.stdout)["decision"]), (0, "allow"))
        r = self.run_cli("check", "--to", "a@x.example", "--subject", "Hi", "--body", "Hello",
                         env={"SENDFUSE_NOW": "2026-11-19T23:00:00-05:00"}, cwd=proj)
        self.assertEqual(r.returncode, 3)

    def test_run_executes_only_when_allowed(self):
        proj = self.project()
        marker = self.tmp / "sent.txt"
        send = [sys.executable, "-c", f"open({str(marker)!r}, 'a').write('x')"]
        env = {"SENDFUSE_NOW": WED}
        args = ["run", "--to", "a@x.example", "--subject", "Hi", "--body", "Hello", "--"]
        r = self.run_cli(*args, *send, env=env, cwd=proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli(*args, *send, env=env, cwd=proj)          # same thread again → blocked, not run
        self.assertEqual(r.returncode, 2)
        self.assertEqual(marker.read_text(), "x")
        fail = [sys.executable, "-c", "import sys; sys.exit(5)"]
        r = self.run_cli("run", "--to", "b@x.example", "--subject", "Yo", "--body", "x", "--", *fail, env=env, cwd=proj)
        self.assertEqual(r.returncode, 5)
        self.assertIn("released", r.stderr)

    def test_queue_approve_drop(self):
        self.write_thread(INCIDENT)
        proj = self.project()
        env = {"SENDFUSE_NOW": WED}
        args = ["check", "--subject", "Re: Kickoff", "--body", REPLY_WITH_TIMES,
                "--in-reply-to", "<m1@acme.example>"]
        self.assertEqual(self.run_cli(*args, env=env, cwd=proj).returncode, 2)
        q = self.run_cli("queue", cwd=proj)
        self.assertIn("#1", q.stdout)
        self.assertIn("already_settled", q.stdout)
        self.assertIn(JORDAN, q.stdout)
        self.assertEqual(self.run_cli("approve", "1", env=env, cwd=proj).returncode, 0)
        self.assertEqual(self.run_cli(*args, env=env, cwd=proj).returncode, 0)    # approved once
        self.assertEqual(self.run_cli(*args, env=env, cwd=proj).returncode, 2)    # …only once
        self.assertEqual(self.run_cli("drop", "2", cwd=proj).returncode, 0)
        self.assertEqual(self.run_cli("drop", "99", cwd=proj).returncode, 70)
        self.assertEqual(self.run_cli("log", cwd=proj).returncode, 0)

    def test_init_merges_settings(self):
        proj = self.tmp / "app"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "settings.json").write_text(json.dumps(
            {"model": "x", "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "lint"}]}]}}),
            "utf-8")
        for _ in range(2):                                      # idempotent
            r = self.run_cli("init", str(proj), "--claude", "--me", ME, cwd=proj)
            self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads((proj / ".claude" / "settings.json").read_text())
        self.assertEqual(doc["model"], "x")
        self.assertEqual(len(doc["hooks"]["PreToolUse"]), 2)
        self.assertEqual(len(doc["hooks"]["PostToolUse"]), 1)
        cmd = doc["hooks"]["PreToolUse"][1]["hooks"][0]["command"]
        self.assertIn("$CLAUDE_PROJECT_DIR", cmd)
        self.assertTrue((proj / ".send-fuse" / "sendfuse.py").is_file())
        self.assertEqual(json.loads((proj / ".send-fuse" / "policy.json").read_text())["me"], [ME])

    def test_demo(self):
        r = self.run_cli("demo", "--fast", "--color", "never")
        self.assertEqual(r.returncode, 0, r.stderr)
        for s in ("BLOCKED", "already_settled", "not_latest", "agent B → BLOCKED", "HELD", "quiet hours"):
            self.assertIn(s, r.stdout)
        self.assertNotIn("\033[", r.stdout)
        self.assertEqual(r.stdout, self.run_cli("demo", "--fast", "--color", "never").stdout)   # deterministic

    def test_single_file_install(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "send-fuse"
        shutil.copyfile(sf.Path(sf.__file__), exe)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        import subprocess
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, cwd=str(self.tmp))
        self.assertEqual(r.stdout.strip(), f"send-fuse {sf.VERSION}")
        r = subprocess.run([str(exe), "demo", "--fast"], capture_output=True, text=True, cwd=str(self.tmp),
                           env={**os.environ, "NO_COLOR": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
