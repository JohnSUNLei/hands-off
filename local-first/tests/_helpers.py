"""Shared fixtures: fake OpenAI-compatible backends on localhost, a proxy on a random port.
No test ever talks to a real cloud API."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "localfirst.py"
sys.path.insert(0, str(ROOT))
import localfirst as lf  # noqa: E402


def chat(text, **kw):
    return {"model": kw.pop("model", "auto"), "messages": [{"role": "user", "content": text}], **kw}


class ProxyCase(unittest.TestCase):
    local_models = ["qwen/qwen3.6-35b-a3b", "text-embedding-nomic"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lf-test-"))
        self._env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("LOCALFIRST_")}
        lf.Upstream._models_cache.clear()
        self.local = lf.FakeBackend(list(self.local_models), "local answer")
        self.cloud = lf.FakeBackend(["big-cloud-model"], "cloud answer")
        self.cfg = self.make_cfg()
        self.ledger = lf.Ledger(self.tmp / "ledger.jsonl")
        self.start()

    def make_cfg(self, **over):
        cfg = lf.demo_config(self.local.url, self.cloud.url)
        cfg.update(over)
        return cfg

    def start(self):
        self.srv = lf.serve(self.cfg, self.ledger, "127.0.0.1", 0, log=lambda s: None)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        self.url = self.base + "/chat/completions"

    def restart(self, cfg):
        self.srv.shutdown()
        self.srv.server_close()
        self.cfg = cfg
        self.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for f in (self.local, self.cloud):
            try:
                f.stop()
            except Exception:  # noqa: BLE001 — some tests stop the fake on purpose
                pass
        for k in [k for k in os.environ if k.startswith("LOCALFIRST_")]:
            del os.environ[k]
        os.environ.update(self._env)

    def post(self, body, headers=None):
        return lf.post(self.url, body, headers)

    def run_cli(self, *args, env=None, cwd=None, stdin=""):
        e = {k: v for k, v in os.environ.items() if not k.startswith("LOCALFIRST_")}
        e.update({"LOCALFIRST_HOME": str(self.tmp / "home"), "NO_COLOR": "1"})
        e.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e,
                              cwd=str(cwd or self.tmp), timeout=60, input=stdin)

    def write_cfg(self, cfg, where=None):
        p = (where or self.tmp) / lf.CONFIG_FILE
        p.write_text(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}), "utf-8")
        return p
