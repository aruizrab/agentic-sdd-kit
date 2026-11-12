#!/usr/bin/env python3
"""Launcher for the SDD kit tools. Standard library only; Python 3.8+.

    python3 -I tools/sdd/sdd.py <command> [...]

or, after `install.sh` put `bin/sdd` on your PATH, simply `sdd <command>`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sddkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
