"""Gap priority service wrapper.

Current implementation reuses generated gaps output and reports.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_priority_flow() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:test", "--", "GapsReportBuilder"]
    completed = subprocess.run(cmd, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    print("Running gap-priority validation via gaps test suite...")
    sys.exit(run_priority_flow())
