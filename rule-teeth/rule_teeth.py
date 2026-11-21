#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule-teeth —— 只写在文档里的规则等于没有规则。

用法见 README.md；`python3 rule_teeth.py -h` 看全部命令。只用 Python 标准库（3.9+）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ruleteeth.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
