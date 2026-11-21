"""Shared test plumbing: a throwaway project with brake installed, and a transcript that grows as 'time' passes."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "brake.py"
FIXTURES = HERE / "fixtures"
START = dt.datetime(2026, 11, 21, 3, 0, 0, tzinfo=dt.timezone.utc)
PER_CALL = (5 * 8 + 2500 * 40 + 30000 * 10 + 450000 * 0.2) / 1_000_000      # Fable 5.2 list prices → $0.49004
NEW_TOK = 5 + 30000 + 2500


def lines_by_call():
    """The fixture transcript, grouped: [lines for model call 0, lines for call 1, ...]."""
    raw = (FIXTURES / "runaway.jsonl").read_text().splitlines(keepends=True)
    groups, cur = [], []
    for ln in raw:
        d = json.loads(ln)
        cur.append(ln)
        if d.get("type") == "user" and d.get("uuid", "").startswith("r"):
            groups.append(cur)
            cur = []
    return groups, cur


class Project:
    def __init__(self, init_args=("--usd", "5"), settings=None, with_subagent=False):
        self.tmp = Path(tempfile.mkdtemp(prefix="brake-test-")).resolve()
        self.root = self.tmp / "app"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.js").write_text("a\n")
        self.transcript = self.tmp / "projects" / "runaway-1.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("")
        if with_subagent:
            shutil.copytree(FIXTURES / "runaway", self.transcript.with_suffix(""))
        if settings is not None:
            (self.root / ".claude").mkdir()
            (self.root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
        self.groups, _ = lines_by_call()
        self.written = 0
        if init_args is not None:
            r = self.cli("init", str(self.root), *init_args, engine=ENGINE)
            assert r.returncode == 0, r.stderr

    @property
    def engine(self):
        return self.root / ".brake" / "brake.py"

    def now(self, calls):
        return (START + dt.timedelta(seconds=20 * calls + 5)).isoformat()

    def env(self, now=None):
        e = dict(os.environ)
        e["BRAKE_NOW"] = now or self.now(self.written)
        return e

    def cli(self, *args, engine=None):
        return subprocess.run([sys.executable, str(engine or self.engine), *args], cwd=self.root,
                              capture_output=True, text=True, env=self.env(), timeout=30)

    def grow(self, n):
        """Append the next n model calls to the transcript."""
        with open(self.transcript, "a") as f:
            for g in self.groups[self.written:self.written + n]:
                f.writelines(g)
        self.written += n

    def hook(self, name, now=None, **override):
        raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        raw = raw.replace("{{ROOT}}", str(self.root)).replace("{{TRANSCRIPT}}", str(self.transcript))
        data = json.loads(raw)
        data.update(override)
        r = subprocess.run([sys.executable, str(self.engine), "hook"], input=json.dumps(data), cwd=self.root,
                           capture_output=True, text=True, env=self.env(now), timeout=30)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None

    def start(self):
        self.hook("session_start", now=START.isoformat())
        self.hook("prompt", now=START.isoformat())

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def stopped(out):
    return bool(out) and out.get("continue") is False and \
        out["hookSpecificOutput"]["permissionDecision"] == "deny"
