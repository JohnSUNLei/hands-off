# -*- coding: utf-8 -*-
"""测试共用的小工具：造临时项目、跑子进程。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
sys.path.insert(0, str(PKG_ROOT))

from ruleteeth import engine  # noqa: E402

CLI = PKG_ROOT / "rule_teeth.py"


class Project:
    """一个临时项目目录。用 with 包起来，出来自动删。"""

    def __init__(self, files: dict, git: bool = True):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.write(files)
        if git:
            self.git("init", "-q")
            self.git("add", "-A")
            self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", "--allow-empty")
        engine._FILES_CACHE.clear()

    def write(self, files: dict):
        for name, text in files.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        engine._FILES_CACHE.clear()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *args], cwd=self.root, capture_output=True, text=True)

    def hook(self, payload: dict):
        """像 Claude Code 那样调用已装好的 hook：JSON 走 stdin。"""
        payload = {"cwd": str(self.root), "session_id": "t", **payload}
        return subprocess.run([sys.executable, str(self.root / ".rule-teeth" / "engine.py"), "hook"],
                              input=json.dumps(payload), cwd=self.root, capture_output=True, text=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()


def fake_key() -> str:
    """拼出来的假密钥 —— 源码里不出现完整的密钥样子，免得被任何扫描器当成真泄露。"""
    return "sk-" + "Q7" * 15
