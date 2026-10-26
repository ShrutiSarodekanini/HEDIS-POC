"""ValueSets service wrapper.

Delegates to backend value set enrichment flow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_valuesets(args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:cli", "--", "valueSets", *args]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Usage: python services/valuesets/main.py -- --measure-bundle <bundle> --vs-api-key <key>")
        sys.exit(0)

    passthrough = sys.argv[1:]
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    sys.exit(run_valuesets(passthrough))
