"""bar-lock end to end: real throwaway git repos, hooks driven the way Claude Code drives them, waivers, self-protection."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bar_lock as bl  # noqa: E402

GOOD_TEST = textwrap.dedent('''\
    import math
    from decimal import Decimal
    from shop import compute

    def test_total():
        assert compute(3, 6.66) == Decimal("19.98")
        assert math.isclose(compute(1, 9.99), 9.99, rel_tol=1e-9)
''')
GOOD_GATE = "MIN_PASS_SCORE = 90\n"


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="barlock_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.waivers = self.tmp / "waivers_home"
        env = mock.patch.dict(os.environ, {"BAR_LOCK_HOME": str(self.waivers), "GIT_CONFIG_GLOBAL": os.devnull,
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        self.git("init", "-q")
        self.write("tests/test_total.py", GOOD_TEST)
        self.write("gate.py", GOOD_GATE)
        self.write("shop.py", "def compute(n, p):\n    return n * p\n")
        bl.install(self.root)
        self.commit("start")

    # -- helpers
    def git(self, *args):
        p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                            "-C", str(self.root), *args], capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(p.stderr)
        return p.stdout

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def weaken_assertion(self):
        self.write("tests/test_total.py", GOOD_TEST.replace('assert compute(3, 6.66) == Decimal("19.98")', "assert compute(3, 6.66)"))

    def lower_the_bar(self):
        self.write("gate.py", "MIN_PASS_SCORE = 40\n")

    def ev(self, event, **kw):
        return {"hook_event_name": event, "session_id": "s1", "cwd": str(self.root), **kw}

    def bash(self, cmd):
        return self.ev("PreToolUse", tool_name="Bash", tool_input={"command": cmd})

    def run_hook(self, data):
        code, out, err = bl.hook(data)
        self.assertEqual(code, 0)
        return json.loads(out) if out else {}

    def start_session(self):
        self.run_hook(self.ev("SessionStart", source="startup"))

    def denied(self, out):
        h = out.get("hookSpecificOutput") or {}
        return h.get("permissionDecision") == "deny"

    def reason(self, out):
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def ids(self, text):
        return [w for w in text.replace(")", " ").split() if len(w) == 24 and all(c in "0123456789abcdef" for c in w)]

    def cli(self, *argv):
        os.chdir(self.root)
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = bl.main(list(argv))
        return rc, buf.getvalue(), err.getvalue()


class CommitGate(Project):
    def test_weakened_assertion_blocks_the_commit(self):
        self.start_session()
        self.weaken_assertion()
        out = self.run_hook(self.bash('git commit -am "fix tests"'))
        self.assertTrue(self.denied(out))
        why = self.reason(out)
        self.assertIn("K1", why)
        self.assertIn("waive", why)                   # tells the agent to ask the user, not to retry
        self.assertIn("blocked this commit", out["systemMessage"])

    def test_clean_commit_goes_through(self):
        self.start_session()
        self.write("tests/test_total.py", GOOD_TEST + "\ndef test_more():\n    assert compute(2, 2) == 4\n")
        self.assertEqual(self.run_hook(self.bash('git commit -am "more tests"')), {})

    def test_commands_that_are_not_commits_cost_nothing(self):
        self.start_session()
        with mock.patch.object(bl.Repo, "discover", side_effect=AssertionError("must not touch git")):
            for cmd in ("ls -la", "pytest -q", "git status", "git diff", 'echo "git commit is a word"'.replace('"', "'") + " | cat"):
                code, out, _ = bl.hook(self.bash(cmd))
                self.assertEqual((code, out) if "git commit" not in cmd else (0, ""), (0, ""), cmd)

    def test_compound_command_and_git_options_are_seen(self):
        self.start_session()
        self.weaken_assertion()
        for cmd in ('git add -A && git commit -m x', 'git -C . commit -m x', 'cd . && git commit --amend --no-edit',
                    'git commit -m x --no-verify'):
            self.assertTrue(self.denied(self.run_hook(self.bash(cmd))), cmd)

    def test_lowered_pass_line_blocks(self):
        self.start_session()
        self.lower_the_bar()
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertTrue(self.denied(out))
        self.assertIn("K5", self.reason(out))

    def test_deleting_the_test_file_blocks(self):
        self.start_session()
        (self.root / "tests/test_total.py").unlink()
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertIn("K1", self.reason(out))

    def test_findings_that_were_already_dirty_at_session_start_are_not_blamed(self):
        self.weaken_assertion()                       # the user did this before the agent started
        self.start_session()
        self.assertEqual(self.run_hook(self.bash("git commit -am x")), {})
        self.lower_the_bar()                          # the agent does this one
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertTrue(self.denied(out))
        self.assertIn("K5", self.reason(out))
        self.assertNotIn("K1", self.reason(out))

    def test_a_baseline_made_at_commit_time_excuses_nothing(self):
        self.weaken_assertion()                       # no SessionStart was ever seen
        self.assertTrue(self.denied(self.run_hook(self.bash("git commit -am x"))))

    def test_notes_do_not_block_and_are_shown_once(self):
        self.start_session()
        self.write("tests/test_total.py", GOOD_TEST.replace("19.98", "20.5"))
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertFalse(self.denied(out))
        self.assertIn("K1", out["systemMessage"])
        self.assertEqual(self.run_hook(self.bash("git commit -am y")), {})

    def test_a_repo_with_no_commit_yet_does_not_crash(self):
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        subprocess.run(["git", "-C", str(fresh), "init", "-q"], check=True)
        bl.install(fresh)
        (fresh / "tests").mkdir()
        (fresh / "tests" / "test_a.py").write_text("def test_a():\n    assert 1 == 1\n", encoding="utf-8")
        data = {"hook_event_name": "PreToolUse", "session_id": "f", "cwd": str(fresh), "tool_name": "Bash",
                "tool_input": {"command": "git commit -am x"}}
        self.assertEqual(bl.hook(data), (0, "", ""))
        self.assertEqual(bl.hook({**data, "hook_event_name": "Stop", "tool_name": ""})[0], 0)

    def test_outside_a_git_repo_nothing_happens(self):
        loose = self.tmp / "loose"
        (loose / ".bar-lock").mkdir(parents=True)
        shutil.copy2(self.root / ".bar-lock" / bl.ENGINE_FILE, loose / ".bar-lock" / bl.ENGINE_FILE)
        data = {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(loose), "tool_name": "Bash",
                "tool_input": {"command": "git commit -am x"}}
        self.assertEqual(bl.hook(data), (0, "", ""))


class Waivers(Project):
    def test_the_user_can_waive_and_then_the_commit_goes_through(self):
        self.start_session()
        self.weaken_assertion()
        why = self.reason(self.run_hook(self.bash("git commit -am x")))
        ids = self.ids(why)
        self.assertTrue(ids)
        rc, out, _ = self.cli("waive", ids[0], "--reason", "the old check was wrong")
        self.assertEqual(rc, 0)
        self.assertEqual(self.run_hook(self.bash("git commit -am x")), {})
        rc, out, _ = self.cli("waivers")
        self.assertIn("the old check was wrong", out)

    def test_waive_needs_a_reason(self):
        rc, _, err = self.cli("waive", "a" * 24)
        self.assertEqual(rc, 2)
        self.assertIn("reason", err)

    def test_waive_current_covers_whatever_blocks_right_now(self):
        self.start_session()
        self.weaken_assertion()
        self.lower_the_bar()
        self.assertTrue(self.denied(self.run_hook(self.bash("git commit -am x"))))
        rc, out, _ = self.cli("waive", "--current", "--reason", "reviewed")
        self.assertEqual(rc, 0)
        self.assertEqual(self.run_hook(self.bash("git commit -am x")), {})

    def test_unwaive(self):
        self.start_session()
        self.weaken_assertion()
        fid = self.ids(self.reason(self.run_hook(self.bash("git commit -am x"))))[0]
        self.cli("waive", fid, "--reason", "ok")
        self.cli("unwaive", fid)
        self.assertTrue(self.denied(self.run_hook(self.bash("git commit -am x"))))

    def test_a_waiver_comment_written_in_the_diff_counts_for_nothing(self):
        self.start_session()
        self.write("tests/test_total.py", GOOD_TEST.replace('assert compute(3, 6.66) == Decimal("19.98")',
                   "assert compute(3, 6.66)  # tampercheck: allow  hallucinot-disable-line  bar-lock: allow"))
        self.assertTrue(self.denied(self.run_hook(self.bash("git commit -am x"))))

    def test_a_waiver_only_covers_that_exact_change(self):
        self.start_session()
        self.weaken_assertion()
        fid = self.ids(self.reason(self.run_hook(self.bash("git commit -am x"))))[0]
        self.cli("waive", fid, "--reason", "ok")
        self.lower_the_bar()                           # a different weakening
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertTrue(self.denied(out))
        self.assertIn("K5", self.reason(out))

    def test_unreadable_waiver_file_is_loud_not_silent(self):
        self.waivers.mkdir(parents=True)
        (self.waivers / bl.WAIVER_FILE).write_text("{not json", encoding="utf-8")
        self.start_session = lambda: None
        with self.assertRaises(bl.GitError):
            bl.hook(self.bash("git commit -am x"))


class StopAndPush(Project):
    def test_stop_is_refused_while_the_bar_is_lowered(self):
        self.start_session()
        self.weaken_assertion()
        out = self.run_hook(self.ev("Stop", stop_hook_active=False))
        self.assertEqual(out["decision"], "block")
        self.assertIn("waive", out["reason"])
        self.assertIn("not done yet", out["systemMessage"])

    def test_stop_gives_up_loudly_after_two_reminders(self):
        self.start_session()
        self.weaken_assertion()
        first = self.run_hook(self.ev("Stop", stop_hook_active=False))
        second = self.run_hook(self.ev("Stop", stop_hook_active=True))     # Claude Code sets this when it is already continuing
        third = self.run_hook(self.ev("Stop", stop_hook_active=True))
        self.assertEqual((first["decision"], second["decision"]), ("block", "block"))
        self.assertNotIn("decision", third)
        self.assertIn("NOT blocking", third["systemMessage"])
        self.assertIn("assertion weakened", third["systemMessage"])      # and it says what is still wrong

    def test_a_clean_stop_resets_the_count(self):
        self.start_session()
        self.weaken_assertion()
        self.run_hook(self.ev("Stop"))
        self.write("tests/test_total.py", GOOD_TEST)                     # the agent fixed it
        self.assertEqual(self.run_hook(self.ev("Stop")), {})
        self.weaken_assertion()
        self.assertEqual(self.run_hook(self.ev("Stop"))["decision"], "block")

    def test_stop_is_quiet_when_nothing_got_easier(self):
        self.start_session()
        self.write("shop.py", "def compute(n, p):\n    return round(n * p, 2)\n")
        self.assertEqual(self.run_hook(self.ev("Stop")), {})

    def test_stop_with_no_baseline_starts_one_instead_of_guessing(self):
        self.weaken_assertion()
        out = self.run_hook(self.ev("Stop"))
        self.assertNotIn("decision", out)
        self.assertIn("baseline", out["systemMessage"])

    def test_stop_sees_changes_committed_during_the_session(self):
        self.start_session()
        self.weaken_assertion()
        self.commit("sneaky")                         # committed without going through the hook
        self.assertEqual(self.run_hook(self.ev("Stop"))["decision"], "block")

    def test_push_checks_everything_committed_this_session(self):
        self.start_session()
        self.weaken_assertion()
        self.commit("sneaky")                         # e.g. made by a script, or earlier by an amend
        self.assertTrue(self.denied(self.run_hook(self.bash("git push origin main"))))
        out = self.run_hook(self.bash("gh pr create --fill"))
        self.assertTrue(self.denied(out))
        self.assertIn("blocked this push", out["systemMessage"])

    def test_push_with_nothing_new_is_fine(self):
        self.start_session()
        self.assertEqual(self.run_hook(self.bash("git push")), {})


class SelfProtection(Project):
    def test_the_agent_cannot_waive_uninstall_or_edit_its_own_files(self):
        self.start_session()
        engine = ".bar-lock/bar_lock.py"
        for cmd in (f"python3 {engine} waive {'a' * 24} --reason x", f"python3 {engine} uninstall", f"python3 {engine} init",
                    "rm -rf .bar-lock", "sed -i s/90/10/ .bar-lock/config.json", "echo '{}' > .bar-lock/config.json",
                    f"echo {{}} > {self.waivers}/waivers.json", "BAR_LOCK_HOME=/tmp/x python3 .bar-lock/bar_lock.py waive y --reason z"):
            out = self.run_hook(self.bash(cmd))
            self.assertTrue(self.denied(out), cmd)
            self.assertIn("own terminal", self.reason(out))

    def test_the_agent_cannot_write_to_waivers_or_config(self):
        for path in (self.root / ".bar-lock" / "config.json", self.root / ".bar-lock" / "bar_lock.py", self.waivers / "waivers.json"):
            for tool in ("Write", "Edit", "MultiEdit"):
                out = self.run_hook(self.ev("PreToolUse", tool_name=tool, tool_input={"file_path": str(path), "content": "x"}))
                self.assertTrue(self.denied(out), f"{tool} {path}")

    def test_the_agent_can_still_read_and_run_check(self):
        self.start_session()
        for cmd in ("cat .bar-lock/config.json", "ls .bar-lock", "python3 .bar-lock/bar_lock.py check",
                    "python3 .bar-lock/bar_lock.py waivers", "grep min_score .bar-lock/config.json"):
            self.assertEqual(self.run_hook(self.bash(cmd)), {}, cmd)

    def test_the_agent_cannot_unhook_it(self):
        settings = self.root / ".claude" / "settings.json"
        out = self.run_hook(self.ev("PreToolUse", tool_name="Edit", tool_input={
            "file_path": str(settings), "old_string": '"command": "python3 \\"$CLAUDE_PROJECT_DIR/.bar-lock/bar_lock.py\\" hook"',
            "new_string": ""}))
        self.assertTrue(self.denied(out))
        out = self.run_hook(self.ev("PreToolUse", tool_name="Write", tool_input={"file_path": str(settings), "content": "{}"}))
        self.assertTrue(self.denied(out))
        doc = json.loads(settings.read_text(encoding="utf-8"))
        doc["permissions"] = {"allow": ["Bash(ls)"]}
        out = self.run_hook(self.ev("PreToolUse", tool_name="Write", tool_input={"file_path": str(settings), "content": json.dumps(doc)}))
        self.assertFalse(self.denied(out))             # unrelated settings edits that keep our hooks are fine
        out = self.run_hook(self.bash(f"sed -i '' /bar_lock/d {settings}"))
        self.assertTrue(self.denied(out))

    def test_ordinary_edits_are_untouched(self):
        out = self.run_hook(self.ev("PreToolUse", tool_name="Edit", tool_input={"file_path": str(self.root / "shop.py"),
                                                                                  "old_string": "a", "new_string": "b"}))
        self.assertEqual(out, {})


class ClaudeCodeStyle(Project):
    """Feed the installed copy exactly the way Claude Code does: JSON on stdin, a process per event."""

    def fire(self, data, raw=None):
        p = subprocess.run([sys.executable, str(self.root / ".bar-lock" / bl.ENGINE_FILE), "hook"], input=raw if raw is not None else json.dumps(data),
                           capture_output=True, text=True, cwd=str(self.root), env=os.environ.copy())
        return p

    def test_whole_session(self):
        self.assertEqual(self.fire(self.ev("SessionStart", source="startup")).returncode, 0)
        self.weaken_assertion()
        p = self.fire(self.bash("git commit -am x"))
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        stop = json.loads(self.fire(self.ev("Stop", stop_hook_active=False)).stdout)
        self.assertEqual(stop["decision"], "block")

    def test_a_crash_is_loud_and_lets_the_action_through(self):
        (self.root / ".bar-lock" / bl.CONFIG_FILE).write_text("{broken", encoding="utf-8")
        p = self.fire(self.bash("git commit -am x"))
        self.assertEqual(p.returncode, 1)               # a hook error Claude Code shows; the commit is not blocked by us
        self.assertIn("bar-lock", p.stderr)
        self.assertIn("nothing was verified", p.stderr)
        self.assertEqual(p.stdout.strip(), "")

    def test_garbage_input_is_loud_too(self):
        p = self.fire(None, raw="not json at all")
        self.assertEqual(p.returncode, 1)
        self.assertIn("bar-lock", p.stderr)

    def test_chinese_messages(self):
        shutil.rmtree(self.root / ".bar-lock")
        self.git("rm", "-r", "-q", "--cached", ".bar-lock")
        (self.root / ".claude" / "settings.json").unlink()
        os.chdir(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            bl.main(["init", "--lang", "zh"])
        self.commit("zh")
        self.fire(self.ev("SessionStart", source="startup"))
        self.weaken_assertion()
        out = json.loads(self.fire(self.bash("git commit -am x")).stdout)
        self.assertIn("拦下了这次提交", out["systemMessage"])
        self.assertIn("断言变弱", out["systemMessage"])


class CheckCommand(Project):
    def test_exit_codes_and_modes(self):
        self.assertEqual(self.cli("check")[0], 0)
        self.weaken_assertion()
        rc, out, _ = self.cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("K1", out)
        self.assertEqual(self.cli("check", "--staged")[0], 0)           # nothing staged yet
        self.git("add", "-A")
        self.assertEqual(self.cli("check", "--staged")[0], 1)
        self.commit("weak")
        self.assertEqual(self.cli("check")[0], 0)
        self.assertEqual(self.cli("check", "--base", "HEAD~1")[0], 1)
        self.assertEqual(self.cli("check", "--base", "HEAD~1", "--head", "HEAD")[0], 1)

    def test_json_output(self):
        self.weaken_assertion()
        self.lower_the_bar()
        rc, out, _ = self.cli("check", "--json")
        doc = json.loads(out)
        self.assertEqual(doc["blocking"], 2)
        self.assertEqual({f["kind"] for f in doc["findings"]}, {"K1", "K5"})
        self.assertTrue(all(len(f["id"]) == 24 for f in doc["findings"]))

    def test_waived_findings_are_hidden_unless_asked(self):
        self.weaken_assertion()
        self.cli("waive", "--current", "--reason", "ok")
        self.assertEqual(self.cli("check")[0], 0)
        self.assertEqual(self.cli("check", "--all")[0], 1)

    def test_not_a_repo(self):
        os.chdir(self.tmp)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bl.main(["check"]), 2)

    def test_new_untracked_files_are_ignored(self):
        self.write("tests/test_new.py", "import pytest\n@pytest.mark.skip\ndef test_x():\n    assert 1 == 1\n")
        self.assertEqual(self.cli("check")[0], 0)

    def test_files_that_are_too_big_are_reported_not_skipped_silently(self):
        self.write("tests/test_big.py", "x = 1\n" * 1)
        self.commit("small")
        self.write("tests/test_big.py", "# " + "x" * (bl.MAX_FILE_BYTES + 10) + "\n")
        rc, out, _ = self.cli("check")
        self.assertIn("K0", out)
        self.assertEqual(rc, 0)


class Install(Project):
    def test_install_is_idempotent_and_keeps_other_hooks(self):
        settings = self.root / ".claude" / "settings.json"
        doc = json.loads(settings.read_text(encoding="utf-8"))
        doc["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": "echo other"}]})
        settings.write_text(json.dumps(doc), encoding="utf-8")
        os.chdir(self.root)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            bl.main(["init"])
        self.assertIn("already", buf.getvalue())
        doc = json.loads(settings.read_text(encoding="utf-8"))
        ours = [h for ev in doc["hooks"].values() for g in ev for h in g["hooks"] if bl.HOOK_MARK in h["command"]]
        self.assertEqual(len(ours), 3)
        self.assertTrue(any("echo other" in h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]))
        self.assertEqual(doc["hooks"]["PreToolUse"][0]["matcher"], bl.PRE_MATCHER)

    def test_pin_is_written_to_config(self):
        os.chdir(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            bl.main(["init", "--pin", "ops/thresholds.json", "--pin", "gate.py"])
        cfg = json.loads((self.root / ".bar-lock" / bl.CONFIG_FILE).read_text(encoding="utf-8"))
        self.assertEqual(cfg["pinned"], ["gate.py", "ops/thresholds.json"])
        self.start_session()
        self.write("gate.py", "MIN_PASS_SCORE = 95\n")                # even raising a pinned number needs the user
        out = self.run_hook(self.bash("git commit -am x"))
        self.assertIn("K5", self.reason(out))

    def test_uninstall_removes_only_ours(self):
        settings = self.root / ".claude" / "settings.json"
        doc = json.loads(settings.read_text(encoding="utf-8"))
        doc["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": "echo other"}]})
        settings.write_text(json.dumps(doc), encoding="utf-8")
        os.chdir(self.root)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            bl.main(["uninstall"])
        self.assertIn("3 hook", buf.getvalue())
        doc = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(list(doc["hooks"]), ["Stop"])
        self.assertFalse((self.root / ".bar-lock").exists())

    def test_invalid_settings_is_never_overwritten(self):
        bad = self.tmp / "bad"
        (bad / ".claude").mkdir(parents=True)
        (bad / ".claude" / "settings.json").write_text("{nope", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            bl.install(bad)
        self.assertIn("not valid JSON", str(cm.exception))
        self.assertEqual((bad / ".claude" / "settings.json").read_text(encoding="utf-8"), "{nope")

    def test_state_directory_is_git_ignored(self):
        self.start_session()
        self.assertIn("state/", (self.root / ".bar-lock" / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
