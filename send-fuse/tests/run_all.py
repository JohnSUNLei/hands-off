#!/usr/bin/env python3
"""Run every send-fuse test. No network, no real mailbox, nothing is ever sent.

    python3 tests/run_all.py          # all
    python3 tests/run_all.py -k hook  # only tests whose name contains "hook"
"""
import argparse
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only run tests whose id contains this text")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(HERE))
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    if a.k:
        def walk(s):
            for t in s:
                yield from (walk(t) if isinstance(t, unittest.TestSuite) else [t])
        suite = unittest.TestSuite(t for t in walk(suite) if a.k in t.id())
    res = unittest.TextTestRunner(verbosity=2 if a.v else 1).run(suite)
    total = res.testsRun
    bad = len(res.failures) + len(res.errors)
    print(f"\nsend-fuse tests: {total - bad - len(res.skipped)} passed, {bad} failed, {len(res.skipped)} skipped")
    return 0 if res.wasSuccessful() and not res.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
