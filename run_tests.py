#!/usr/bin/env python3
"""Run every tool's test suite (or the ones named on the command line).

    python3 run_tests.py              # all six
    python3 run_tests.py tripwire     # just one
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ["rule-teeth", "send-fuse", "tripwire", "blackbox", "brake", "local-first"]


def main(argv: list[str]) -> int:
    wanted = argv or TOOLS
    unknown = [t for t in wanted if t not in TOOLS]
    if unknown:
        print(f"unknown tool(s): {', '.join(unknown)} — choose from {', '.join(TOOLS)}")
        return 2
    failed = []
    for tool in wanted:
        print(f"\n=== {tool} ===", flush=True)
        code = subprocess.call([sys.executable, "tests/run_all.py"], cwd=ROOT / tool)
        if code != 0:
            failed.append(tool)
    print("\n" + ("ALL PASS: " + ", ".join(wanted) if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
