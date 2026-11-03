"""Run the pipeline workers in one terminal, each output line prefixed with the worker's name.

    python scripts/run_workers.py                       all ten workers
    python scripts/run_workers.py measures evidence     only these (names without the _worker suffix)

Ctrl+C stops them all. A worker that exits is reported and the others keep running.
"""

import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKERS = ("conversion", "load", "quality", "valuesets", "measures", "gap_identifier", "gaps", "documents",
           "evidence", "stars")


def _pump(name: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        print(f"[{name}] {line}", end="", flush=True)
    print(f"[{name}] exited with code {proc.wait()}", flush=True)


def main(names: list[str]) -> None:
    unknown = set(names) - set(WORKERS)
    if unknown:
        raise SystemExit(f"Unknown worker(s): {', '.join(sorted(unknown))}. Choose from: {', '.join(WORKERS)}")
    procs = []
    for name in names or WORKERS:
        proc = subprocess.Popen([sys.executable, "-u", "-m", f"services.workers.{name}_worker"], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        procs.append(proc)
        threading.Thread(target=_pump, args=(name, proc), daemon=True).start()
    try:
        for proc in procs:
            proc.wait()
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()


if __name__ == "__main__":
    main(sys.argv[1:])
