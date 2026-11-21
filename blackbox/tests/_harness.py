"""Shared test plumbing: a throwaway project with blackbox installed, and a replay of an overnight agent run."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "blackbox.py"
FIXTURES = HERE / "fixtures"

CHECKOUT = "def total(items):\n    return sum(i['price'] for i in items)\n"
CHECKOUT_NEW = ("def total(items):\n    \"\"\"Sum of item prices, in dollars.\"\"\"\n"
                "    return round(sum(i['price'] * i.get('qty', 1) for i in items), 2)\n")
PRICES = '{\n  "tea": {"price": 4.5},\n  "cake": {"price": 6.25},\n  "coffee": {"price": 3.75}\n}\n'
PRICES_ZERO = '{\n  "tea": {"price": 0},\n  "cake": {"price": 0},\n  "coffee": {"price": 0}\n}\n'
BACKUPS = {"data/backups/2026-11-19.json": '{"orders": 41}\n', "data/backups/2026-11-20.json": '{"orders": 57}\n'}


class Project:
    def __init__(self, init_args=(), settings=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="blackbox-test-")).resolve()
        self.root = self.tmp / "shop"
        self.home = self.tmp / "home"
        self.home.mkdir()
        files = {"src/checkout.py": CHECKOUT, "data/prices.json": PRICES, "README.md": "# shop\n", **BACKUPS}
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.root / "node_modules" / "x").mkdir(parents=True)
        (self.root / "node_modules" / "x" / "index.js").write_text("ignored\n")
        if settings is not None:
            (self.root / ".claude").mkdir()
            (self.root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
        if init_args is not None:
            r = self.cli("init", str(self.root), *init_args, engine=ENGINE)
            assert r.returncode == 0, r.stderr

    @property
    def engine(self):
        return self.root / ".blackbox" / "blackbox.py"

    def env(self, now="2026-11-21T02:14:00-05:00"):
        e = dict(os.environ)
        e["HOME"] = str(self.home)
        e["BLACKBOX_NOW"] = now
        return e

    def cli(self, *args, engine=None, stdin=None):
        return subprocess.run([sys.executable, str(engine or self.engine), *args], cwd=self.root, input=stdin,
                              capture_output=True, text=True, env=self.env(), timeout=60)

    def hook(self, name, now="2026-11-21T02:14:00-05:00", **override):
        raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        raw = raw.replace("{{ROOT}}", str(self.root)).replace("{{HOME}}", str(self.home))
        data = json.loads(raw)
        data.update(override)
        r = subprocess.run([sys.executable, str(self.engine), "hook"], input=json.dumps(data), cwd=self.root,
                           capture_output=True, text=True, env=self.env(now), timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None

    def tool(self, name, effect=None, now="2026-11-21T02:14:00-05:00"):
        """Pre hook → what the tool does to the disk → Post hook, like Claude Code would."""
        pre = self.hook("pre_" + name, now=now)
        if effect:
            effect(self.root)
        self.hook("post_" + name, now=now)
        return pre

    def night_run(self):
        """The overnight session used in the README and the promo."""
        self.hook("session_start", now="2026-11-20T23:02:00-05:00")
        self.hook("prompt", now="2026-11-20T23:02:10-05:00")
        self.tool("read_checkout", now="2026-11-20T23:02:31-05:00")
        self.tool("edit_checkout", lambda r: (r / "src/checkout.py").write_text(CHECKOUT_NEW), now="2026-11-20T23:03:05-05:00")
        self.tool("bash_tests_fail", now="2026-11-20T23:03:40-05:00")
        self.tool("bash_sed_prices", lambda r: (r / "data/prices.json").write_text(PRICES_ZERO), now="2026-11-20T23:41:12-05:00")
        self.tool("bash_rm_backups", lambda r: shutil.rmtree(r / "data/backups"), now="2026-11-21T00:07:55-05:00")
        self.tool("bash_tests_pass", now="2026-11-21T00:08:30-05:00")
        self.tool("webfetch_docs", now="2026-11-21T00:09:02-05:00")
        self.tool("write_notes", lambda r: (r / "NOTES.md").write_text("# Overnight notes\n\n- total() now handles qty\n"),
                  now="2026-11-21T00:10:15-05:00")
        self.hook("stop", now="2026-11-21T00:10:40-05:00")

    def steps(self):
        p = self.root / ".blackbox" / "steps.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def step(self, tool=None, cmd=None, kind=None):
        for s in self.steps():
            if kind and s.get("kind") != kind:
                continue
            if tool and s.get("tool") != tool:
                continue
            if cmd and cmd not in (s.get("input") or {}).get("command", ""):
                continue
            return s
        raise AssertionError(f"no step {tool} {cmd} {kind}")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
