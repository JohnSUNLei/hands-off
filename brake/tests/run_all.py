#!/usr/bin/env python3
"""Run every brake test. Usage: python3 tests/run_all.py [-v]"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2 if "-v" in sys.argv else 1).run(suite)
    print(f"\n{'ALL PASS' if result.wasSuccessful() else 'FAILED'}: {result.testsRun} tests, "
          f"{len(result.failures)} failures, {len(result.errors)} errors, {len(result.skipped)} skipped")
    sys.exit(0 if result.wasSuccessful() else 1)
