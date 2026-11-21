#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule-teeth 全部自带测试：python3 tests/run_all.py

测试自动发现（tests/test_*.py），只用标准库 unittest。要 git（造临时仓库），没有 git 会显式报错停住。
"""
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if shutil.which("git") is None:
        print("需要 git 才能跑测试（造临时仓库）/ git is required to run the tests", file=sys.stderr)
        return 2
    sys.path.insert(0, str(HERE))
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
