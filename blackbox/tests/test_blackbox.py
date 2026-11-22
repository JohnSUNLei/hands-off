"""End-to-end: replay an overnight agent run through the installed engine, then read the record and undo."""
import json
import unittest
import zlib

from _harness import BACKUPS, CHECKOUT, CHECKOUT_NEW, ENGINE, PRICES, PRICES_ZERO, Project


class Recording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.night_run()

    @classmethod
    def tearDownClass(cls):
        cls.p.cleanup()

    def test_prompt_and_final_words_recorded(self):
        self.assertIn("get the tests passing", self.p.step(kind="prompt")["text"])
        self.assertIn("Tests pass", self.p.step(kind="said")["text"])

    def test_edit_records_before_and_after(self):
        s = self.p.step(tool="Edit")
        self.assertEqual(len(s["files"]), 1)
        f = s["files"][0]
        self.assertEqual((f["path"], f["change"]), ("src/checkout.py", "modified"))
        self.assertEqual((f["plus"], f["minus"]), (2, 1))

    def test_shell_changes_are_caught_without_knowing_the_command(self):
        sed = self.p.step(cmd="sed -i")
        self.assertEqual([(f["path"], f["change"]) for f in sed["files"]], [("data/prices.json", "modified")])
        rm = self.p.step(cmd="rm -rf data/backups")
        self.assertEqual(sorted((f["path"], f["change"]) for f in rm["files"]),
                         sorted((p, "deleted") for p in BACKUPS))

    def test_reads_and_pages_and_failures(self):
        self.assertEqual(self.p.step(tool="WebFetch")["input"]["url"], "https://docs.python.org/3/library/decimal.html")
        fail = self.p.step(cmd="pytest")
        self.assertEqual(fail["status"], "error")
        self.assertIn("1 failed", fail["error"])
        ok = [s for s in self.p.steps() if "pytest" in (s.get("input") or {}).get("command", "")][1]
        self.assertIn("4 passed", ok["output"])

    def test_ignored_dirs_not_snapshotted(self):
        st = json.loads((self.p.root / ".blackbox" / "state.json").read_text())
        self.assertNotIn("node_modules/x/index.js", st["files"])
        self.assertIn("data/prices.json", st["files"])

    def test_list_is_plain_language(self):
        out = self.p.cli("list").stdout
        self.assertIn("You asked: “Tidy up the checkout code", out)
        self.assertIn("Edited src/checkout.py", out)
        self.assertIn("Normalize price fields — `sed -i", out)
        self.assertIn("Remove stale backups — `rm -rf data/backups`", out)
        self.assertIn("Read the web page https://docs.python.org", out)
        self.assertIn("Created NOTES.md", out)
        self.assertIn("Run the test suite — `python3 -m pytest -q` — failed", out)
        self.assertRegex(out, r"\d+ \d\d:\d\d ! Remove stale backups")      # flagged

    def test_timeline_page(self):
        r = self.p.cli("report")
        self.assertEqual(r.returncode, 0)
        page = (self.p.root / ".blackbox" / "timeline.html").read_text()
        self.assertIn("What your AI did", page)
        self.assertIn("Tidy up the checkout code", page)
        self.assertIn("python3 .blackbox/blackbox.py undo", page)
        self.assertIn('class="f deleted"', page)
        self.assertIn("risky", page)
        self.assertNotIn("<script src", page)                 # self-contained
        self.assertIn("ran for", page)

    def test_show_prints_the_diff(self):
        n = self.p.step(cmd="sed -i")["n"]
        out = self.p.cli("show", str(n)).stdout
        self.assertIn('-  "tea": {"price": 4.5},', out)
        self.assertIn('+  "tea": {"price": 0},', out)


class Undo(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.night_run()

    def tearDown(self):
        self.p.cleanup()

    def test_undo_one_bad_step_keeps_the_rest(self):
        n = self.p.step(cmd="sed -i")["n"]
        r = self.p.cli("undo", str(n), "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"Step {n} is undone", r.stdout)
        self.assertEqual((self.p.root / "data/prices.json").read_text(), PRICES)
        self.assertEqual((self.p.root / "src/checkout.py").read_text(), CHECKOUT_NEW)      # good edit kept
        self.assertTrue((self.p.root / "NOTES.md").exists())

    def test_undo_brings_deleted_files_back(self):
        n = self.p.step(cmd="rm -rf")["n"]
        self.assertEqual(self.p.cli("undo", str(n), "--yes").returncode, 0)
        for rel, text in BACKUPS.items():
            self.assertEqual((self.p.root / rel).read_text(), text)

    def test_undo_of_created_file_deletes_it(self):
        n = self.p.step(tool="Write")["n"]
        self.assertEqual(self.p.cli("undo", str(n), "--yes").returncode, 0)
        self.assertFalse((self.p.root / "NOTES.md").exists())

    def test_undo_is_itself_undoable(self):
        n = self.p.step(cmd="sed -i")["n"]
        r = self.p.cli("undo", str(n), "--yes")
        m = self.p.step(kind="undo")["n"]
        self.assertEqual(self.p.cli("undo", str(m), "--yes").returncode, 0)
        self.assertEqual((self.p.root / "data/prices.json").read_text(), PRICES_ZERO)
        # and the original can be undone again
        self.assertEqual(self.p.cli("undo", str(n), "--yes").returncode, 0)
        self.assertEqual((self.p.root / "data/prices.json").read_text(), PRICES)

    def test_refuses_when_a_later_step_changed_the_file(self):
        self.p.tool("edit_checkout", lambda r: (r / "src/checkout.py").write_text(CHECKOUT_NEW + "# later\n"))
        n = self.p.step(tool="Edit")["n"]
        r = self.p.cli("undo", str(n), "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed again after step", r.stdout)
        self.assertIn(CHECKOUT_NEW + "# later\n", (self.p.root / "src/checkout.py").read_text())
        r = self.p.cli("undo", str(n), "--yes", "--force")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.p.root / "src/checkout.py").read_text(), CHECKOUT)

    def test_interactive_confirmation_can_cancel(self):
        n = self.p.step(cmd="sed -i")["n"]
        import _harness, sys, subprocess
        # not a tty → assume yes; so test the function directly with a fake prompt
        sys.path.insert(0, str(self.p.root / ".blackbox"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("bb_installed", self.p.engine)
        bb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bb)
        lines = []
        code = bb.undo(bb.Box(self.p.root / ".blackbox"), n, out=lines.append, ask=lambda q: "n")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", lines[-1])
        self.assertEqual((self.p.root / "data/prices.json").read_text(), PRICES_ZERO)

    def test_already_undone_and_unknown_and_no_files(self):
        n = self.p.step(cmd="sed -i")["n"]
        self.p.cli("undo", str(n), "--yes")
        self.assertIn("already undone", self.p.cli("undo", str(n), "--yes").stdout)
        self.assertEqual(self.p.cli("undo", "999", "--yes").returncode, 2)
        web = self.p.step(tool="WebFetch")["n"]
        self.assertIn("changed no files", self.p.cli("undo", str(web), "--yes").stdout)

    def test_timeline_marks_undone(self):
        n = self.p.step(cmd="sed -i")["n"]
        self.p.cli("undo", str(n), "--yes")
        self.p.cli("report")
        page = (self.p.root / ".blackbox" / "timeline.html").read_text()
        self.assertIn("step undone", page)
        self.assertIn(f"Undid step {n}", page)


class OutsideAndSafety(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.cleanup()

    def test_changes_between_steps_are_attributed_to_outside(self):
        self.p.hook("session_start")
        (self.p.root / "README.md").write_text("# shop\nedited by hand\n")
        self.p.tool("bash_tests_pass")
        out = self.p.step(kind="outside")
        self.assertEqual([f["path"] for f in out["files"]], ["README.md"])
        self.assertEqual(self.p.step(cmd="pytest")["files"], [])        # not blamed on the command

    def test_write_outside_project_is_flagged(self):
        self.p.tool("write_outside")
        s = self.p.step(tool="Write")
        self.assertTrue(s.get("outside_path"))
        self.assertIn("!", self.p.cli("list").stdout.splitlines()[0])

    def test_helper_agent_steps_are_labelled(self):
        self.p.hook("pre_read_checkout", agent_id="a1", agent_type="Explore")
        self.p.hook("post_read_checkout", agent_id="a1", agent_type="Explore")
        self.assertEqual(self.p.step(tool="Read")["agent"], "Explore")

    def test_record_cannot_be_tampered_with(self):
        for name in ("pre_edit_self", "pre_bash_self_rm"):
            out = self.p.hook(name)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", name)
        self.assertIsNone(self.p.hook("pre_bash_self_undo"))

    def test_chinese(self):
        p = Project(init_args=("--lang", "zh"))
        try:
            p.night_run()
            out = p.cli("list").stdout
            self.assertIn("你说：「Tidy up", out)
            self.assertIn("修改了 src/checkout.py", out)
            p.cli("report")
            self.assertIn("你的 AI 都干了什么", (p.root / ".blackbox" / "timeline.html").read_text())
        finally:
            p.cleanup()

    def test_same_input_same_record(self):
        q = Project()
        try:
            self.p.night_run()
            q.night_run()
            strip = lambda steps: [{k: v for k, v in s.items() if k != "session"} for s in steps]
            self.assertEqual(strip(self.p.steps()), strip(q.steps()))
        finally:
            q.cleanup()


class Install(unittest.TestCase):
    OTHER = {"permissions": {"allow": ["Bash(npm test)"]},
             "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./mine.sh"}]}]}}

    def test_install_and_uninstall_touch_only_ours(self):
        p = Project(settings=self.OTHER)
        try:
            s = json.loads((p.root / ".claude" / "settings.json").read_text())
            self.assertEqual(s["permissions"], self.OTHER["permissions"])
            self.assertEqual(s["hooks"]["PreToolUse"][0], self.OTHER["hooks"]["PreToolUse"][0])
            for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"):
                self.assertTrue(any(".blackbox/blackbox.py" in h["command"] for g in s["hooks"][ev] for h in g["hooks"]), ev)
            self.assertIn("already", p.cli("init", str(p.root)).stdout)
            self.assertEqual(json.loads((p.root / ".claude" / "settings.json").read_text()), s)
            gi = (p.root / ".blackbox" / ".gitignore").read_text()
            self.assertIn("!blackbox.py", gi)
            r = p.cli("uninstall", str(p.root))
            self.assertIn("6 hook", r.stdout)
            self.assertEqual(json.loads((p.root / ".claude" / "settings.json").read_text()), self.OTHER)
            self.assertFalse((p.root / ".blackbox").exists())
        finally:
            p.cleanup()

    def test_keep_data(self):
        p = Project()
        try:
            p.night_run()
            p.cli("uninstall", str(p.root), "--keep-data")
            self.assertTrue((p.root / ".blackbox" / "steps.jsonl").exists())
        finally:
            p.cleanup()

    def test_initial_snapshot_makes_first_step_undoable(self):
        p = Project()
        try:
            p.tool("bash_sed_prices", lambda r: (r / "data/prices.json").write_text(PRICES_ZERO))
            n = p.step(cmd="sed")["n"]
            self.assertEqual(p.cli("undo", str(n), "--yes").returncode, 0)
            self.assertEqual((p.root / "data/prices.json").read_text(), PRICES)
        finally:
            p.cleanup()

    def test_skip_files_and_skip_dirs_keep_secrets_out_of_the_record(self):
        p = Project(init_args=None)
        try:
            secret = "OPENAI_API_KEY=sk-test-do-not-keep-0123456789"
            (p.root / ".env").write_text(secret + "\n")
            (p.root / "vendor").mkdir()
            (p.root / "vendor" / "lib.js").write_text("v1\n")
            (p.root / ".blackbox").mkdir()
            (p.root / "secret-santa").mkdir()                       # a name-only pattern must not match folder names
            (p.root / "secret-santa" / "list.json").write_text('{"gift": "tea"}\n')
            (p.root / ".blackbox" / "config.json").write_text(json.dumps({"skip_files": [".env", "*.pem", "*secret*.json"],
                                                                          "skip_dirs": ["vendor"]}))
            r = p.cli("init", str(p.root), engine=ENGINE)
            self.assertEqual(r.returncode, 0, r.stderr)
            root = str(p.root)
            # an Edit to .env and to a file in a skipped folder, then a shell command that rewrites .env
            p.hook("pre_write_notes", tool_input={"file_path": root + "/.env", "content": "x"})
            (p.root / ".env").write_text(secret + "\nNEW_KEY=sk-test-new-also-not-kept\n")
            p.hook("post_write_notes", tool_input={"file_path": root + "/.env", "content": "x"})
            p.hook("pre_edit_checkout", tool_input={"file_path": root + "/vendor/lib.js", "old_string": "v1", "new_string": "v2"})
            (p.root / "vendor" / "lib.js").write_text("v2\n")
            p.hook("post_edit_checkout", tool_input={"file_path": root + "/vendor/lib.js", "old_string": "v1", "new_string": "v2"})
            p.tool("bash_sed_prices", lambda r: (r / ".env").write_text("ROTATED=sk-test-rotated-not-kept\n"))
            p.hook("stop")
            blobs = [zlib.decompress(f.read_bytes()) for f in (p.root / ".blackbox" / "objects").rglob("*") if f.is_file()]
            self.assertFalse([b for b in blobs if b"sk-test" in b], "a secret file's contents entered the record")
            self.assertNotIn("sk-test", (p.root / ".blackbox" / "timeline.html").read_text())
            write = p.step(tool="Write")
            self.assertEqual([(f["path"], f["change"]) for f in write["files"]], [(".env", "modified")])   # the change is still noted
            self.assertEqual(p.step(tool="Edit")["files"], [])
            self.assertEqual([f["path"] for f in p.step(cmd="sed -i")["files"]], [".env"])
            manifest = json.loads((p.root / ".blackbox" / "state.json").read_text())["files"]
            self.assertNotIn("vendor/lib.js", manifest)
            self.assertFalse(manifest["secret-santa/list.json"][2].startswith("skip:"))
            out = p.cli("undo", str(p.step(cmd="sed -i")["n"]), "--yes").stdout       # nothing to put back, and nothing deleted
            self.assertIn("skip_files", out)
            self.assertIn("ROTATED", (p.root / ".env").read_text())
        finally:
            p.cleanup()

    def test_broken_input_never_wedges(self):
        p = Project()
        try:
            import subprocess, sys
            r = subprocess.run([sys.executable, str(p.engine), "hook"], input="{oops", capture_output=True, text=True,
                               cwd=p.root, env=p.env())
            self.assertEqual(r.returncode, 1)
            self.assertIn("blackbox hook error", r.stderr)
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
