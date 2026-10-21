"""Rebuild all seed data under data/seed from scripts/seed/scenario.py.

Usage (from the repo root):
    python scripts/seed/gen_all.py

Runs the generators in order and stops at the first failure. Every generator is
deterministic, so rerunning produces identical files.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEPS = [
    ["scripts/seed/gen_synthea.py"],
    ["scripts/seed/gen_synthea.py", "--check"],
    ["scripts/seed/gen_documents.py"],
]


def main() -> None:
    for step in STEPS:
        print(f"\n== python {' '.join(step)}", flush=True)
        result = subprocess.run([sys.executable, *step], cwd=ROOT)
        if result.returncode != 0:
            sys.exit(f"Step failed: {' '.join(step)}")
    print("\nSeed data rebuilt in data/seed/")


if __name__ == "__main__":
    main()
