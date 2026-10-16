"""Data ingestion service wrapper.

This delegates to the working TypeScript backend CLI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_backend_cli(args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:cli", "--", *args]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(
            "Usage: python services/data_ingestion/main.py -- detailed --measure-bundle <bundle> --patients-directory <dir>"
        )
        sys.exit(0)

    sys.exit(run_backend_cli(sys.argv[1:]))
