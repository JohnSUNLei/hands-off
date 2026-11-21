"""Shared fixtures: a fake mailbox thread, a temp state dir, a policy. Nothing touches the network."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "sendfuse.py"
sys.path.insert(0, str(ROOT))
import sendfuse as sf  # noqa: E402

ME = "you@example.com"
JORDAN = "jordan@acme.example"

INCIDENT = copy.deepcopy(sf.DEMO_THREAD)
REPLY_WITH_TIMES = sf.DEMO_REPLY
WED = "2026-11-19T09:40:00-05:00"


def scratch() -> Path:
    """A temp dir that is never inside a synced folder (the repo may live in one)."""
    base = Path(tempfile.gettempdir()).resolve()
    return Path(tempfile.mkdtemp(prefix="sf-test-", dir=base))


class FuseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = scratch()
        self.state_dir = self.tmp / "state"
        self.threads = self.tmp / "threads"
        self.threads.mkdir()
        self._env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SENDFUSE_")}

    def tearDown(self):
        for k in [k for k in os.environ if k.startswith("SENDFUSE_")]:
            del os.environ[k]
        os.environ.update(self._env)

    def write_thread(self, thread: dict, name="t.json"):
        (self.threads / name).write_text(json.dumps(thread), "utf-8")

    def policy(self, **over) -> dict:
        p = copy.deepcopy(sf.DEFAULT_POLICY)
        p.update(me=[ME], thread_source={"type": "json", "path": str(self.threads)}, _base=str(self.tmp),
                 quiet_hours={"start": "21:00", "end": "08:00", "tz": "America/Toronto", "weekends": False})
        p.update(over)
        return p

    def fuse(self, **over) -> "sf.Fuse":
        return sf.Fuse(self.policy(**over), sf.State(self.state_dir))

    def reply(self, body=REPLY_WITH_TIMES, in_reply_to="<m1@acme.example>", **kw) -> "sf.Outgoing":
        kw.setdefault("subject", "Re: Project kickoff — Acme website redesign")
        return sf.Outgoing(body=body, in_reply_to=in_reply_to, via="test", **kw)

    def rules(self, verdict) -> list:
        return [r.rule for r in verdict.reasons]

    def run_cli(self, *args, stdin="", env=None, cwd=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith("SENDFUSE_")}
        e.update({"SENDFUSE_STATE_DIR": str(self.state_dir), "NO_COLOR": "1"})
        e.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
                              env=e, cwd=str(cwd or self.tmp), timeout=60)
