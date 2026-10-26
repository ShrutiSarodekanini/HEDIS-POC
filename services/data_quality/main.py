"""Data quality service wrapper.

Runs backend unit/integration checks from the aligned root layout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_quality(include_integration: bool) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    commands = [[NPM_BIN, "run", "backend:test"]]
    if include_integration:
        commands.append([NPM_BIN, "run", "backend:test:integration"])

    for cmd in commands:
        completed = subprocess.run(cmd, cwd=repo_root)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    with_integration = "--integration" in sys.argv[1:]
    sys.exit(run_quality(with_integration))
