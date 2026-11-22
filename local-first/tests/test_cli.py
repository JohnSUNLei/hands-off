"""CLI: init, check, route, bill, serve, demo, one-file install."""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time

from _helpers import ProxyCase, chat, lf


class Cli(ProxyCase):
    def test_init_writes_valid_config(self):
        r = self.run_cli("init", str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        lf.load_config(self.tmp / lf.CONFIG_FILE)
        self.assertEqual(self.run_cli("init", str(self.tmp)).returncode, 1)          # no silent overwrite
        self.assertEqual(self.run_cli("init", str(self.tmp), "--force").returncode, 0)

    def test_check_probes_live(self):
        self.write_cfg(self.cfg)
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("qwen/qwen3.6-35b-a3b available", r.stdout)
        r = self.run_cli("check", "--deep")
        self.assertIn("answered 'local answer'", r.stdout)
        self.local.stop()
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ lmstudio", r.stdout)
        self.assertIn("does NOT fall back", r.stdout)

    def test_check_flags_empty_answers(self):
        self.local.reply = ""
        self.write_cfg(self.cfg)
        r = self.run_cli("check", "--deep")
        self.assertIn("EMPTY", r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_route_dry_run(self):
        self.write_cfg(self.cfg)
        r = self.run_cli("route", "Translate this into Spanish")
        self.assertIn("task: translate", r.stdout)
        self.assertIn("→ lmstudio (local)", r.stdout)
        r = self.run_cli("route", "my notes", "--tag", "private", "--model", "cloud")
        self.assertEqual(r.returncode, 3)
        self.assertIn("REFUSED", r.stdout)

    def test_bill_cli(self):
        self.write_cfg(self.cfg)
        os.environ["LOCALFIRST_NOW"] = "2026-11-21T10:00:00-05:00"
        self.post(chat("Summarize this"))
        self.post(chat("Prove this", model="cloud"))
        r = self.run_cli("bill", "--ledger", str(self.ledger.path), "--month", "2026-11")
        self.assertIn("50% of 2 requests ran on this machine", r.stdout)
        self.assertIn("bill (estimate)", r.stdout)
        r = self.run_cli("bill", "--ledger", str(self.ledger.path), "--month", "2026-11", "--json")
        self.assertEqual(json.loads(r.stdout)["requests"], 2)
        r = self.run_cli("bill", "--ledger", str(self.ledger.path), "--month", "2026-10")
        self.assertIn("0% of 0 requests", r.stdout)

    def test_serve_subprocess(self):
        cfg = dict(self.cfg, listen="127.0.0.1:0")
        self.write_cfg(cfg)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOCALFIRST_")}
        env.update(LOCALFIRST_HOME=str(self.tmp / "home"), NO_COLOR="1")
        p = subprocess.Popen([sys.executable, str(lf.Path(lf.__file__)), "serve"], cwd=str(self.tmp),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            line = p.stdout.readline()
            self.assertIn("listening on http://127.0.0.1:", line)
            base = line.split("listening on ")[1].split()[0]
            status, h, body = lf.post(base + "/chat/completions", chat("Summarize"))
            self.assertEqual((status, h["X-Local-First-Where"]), (200, "local"))
            self.assertTrue((self.tmp / "home" / "ledger.jsonl").exists())
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(10)

    def test_broken_config_is_loud(self):
        (self.tmp / lf.CONFIG_FILE).write_text('{"routes": [], "typo": 1}', "utf-8")
        r = self.run_cli("bill")
        self.assertEqual(r.returncode, 70)
        self.assertIn("unknown key", r.stderr)

    def test_demo(self):
        r = self.run_cli("demo", "--fast", "--color", "never")
        self.assertEqual(r.returncode, 0, r.stderr)
        for s in ("LOCAL ", "CLOUD ", "KEPT ", "HTTP 503", "cloud requests during the outage: 0",
                  "bill (estimate)", "you paid (est.)", "saved (est.)"):
            self.assertIn(s, r.stdout)
        strip = lambda t: "\n".join(l for l in t.splitlines() if "127.0.0.1" not in l)  # noqa: E731 — ports vary
        self.assertEqual(strip(r.stdout), strip(self.run_cli("demo", "--fast", "--color", "never").stdout))

    def test_single_file_install(self):
        exe = self.tmp / "bin" / "local-first"
        exe.parent.mkdir()
        shutil.copyfile(lf.Path(lf.__file__), exe)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), f"local-first {lf.VERSION}")
