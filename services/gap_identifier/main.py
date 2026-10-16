"""Gap identifier service wrapper.

Delegates to backend gap calculation mode.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_gap_identifier(args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:gaps", "--", *args]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(
            "Usage: python services/gap_identifier/main.py -- --measure-bundle <bundle> --patients-directory <dir>"
        )
        sys.exit(0)

    passthrough = sys.argv[1:]
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    sys.exit(run_gap_identifier(passthrough))
