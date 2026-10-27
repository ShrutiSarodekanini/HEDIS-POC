"""Measure calculation service wrapper.

Runs the backend calculation CLI from the aligned structure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_measure_calc(args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:cli", "--", *args]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(
            "Usage: python services/measure_calc/main.py -- detailed --measure-bundle <bundle> --patients-directory <dir>"
        )
        sys.exit(0)

    sys.exit(run_measure_calc(sys.argv[1:]))
