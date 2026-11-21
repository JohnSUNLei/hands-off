"""Shared test plumbing: a throwaway project with tripwire installed, and a way to feed it hook inputs."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "tripwire.py"
FIXTURES = HERE / "fixtures"
FAKE_KEY = "sk-demo-0000-FAKE-KEY-1234"


class Project:
    def __init__(self, init_args=(), settings=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="tripwire-test-")).resolve()
        self.root = self.tmp / "proj"
        self.home = self.tmp / "home"
        (self.home / "Downloads").mkdir(parents=True)
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("print('hello')\n")
        (self.root / ".env").write_text(f"ACME_API_KEY={FAKE_KEY}\n")
        (self.root / ".env.example").write_text("ACME_API_KEY=\n")
        if settings is not None:
            (self.root / ".claude").mkdir()
            (self.root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
        if init_args is not None:
            r = self.cli("init", str(self.root), *init_args, engine=ENGINE)
            assert r.returncode == 0, r.stderr

    @property
    def engine(self):
        return self.root / ".tripwire" / "tripwire.py"

    def env(self):
        e = dict(os.environ)
        e["HOME"] = str(self.home)
        e["TRIPWIRE_NOW"] = "2026-11-21T14:02:11-05:00"
        return e

    def cli(self, *args, engine=None):
        return subprocess.run([sys.executable, str(engine or self.engine), *args], cwd=self.root,
                              capture_output=True, text=True, env=self.env(), timeout=30)

    def fixture(self, name, **override):
        raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        raw = raw.replace("{{ROOT}}", str(self.root)).replace("{{HOME}}", str(self.home))
        data = json.loads(raw)
        data.update(override)
        return data

    def hook(self, name, **override):
        """Run the installed engine exactly the way Claude Code does. Returns (code, json-or-None, stderr)."""
        data = self.fixture(name, **override)
        r = subprocess.run([sys.executable, str(self.engine), "hook"], input=json.dumps(data), cwd=self.root,
                           capture_output=True, text=True, env=self.env(), timeout=30)
        out = json.loads(r.stdout) if r.stdout.strip() else None
        return r.returncode, out, r.stderr

    def decision(self, name, **override):
        code, out, err = self.hook(name, **override)
        assert code == 0, err
        if out is None:
            return None, ""
        h = out.get("hookSpecificOutput", {})
        return h.get("permissionDecision"), h.get("permissionDecisionReason", "")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
