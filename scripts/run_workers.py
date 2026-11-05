"""Run the pipeline workers in one terminal, each output line prefixed with the worker's name.

    python scripts/run_workers.py                       all ten workers
    python scripts/run_workers.py measures evidence     only these (names without the _worker suffix)

Ctrl+C stops them all. A worker that exits with an error is restarted with backoff; one that keeps failing
is left stopped and the others keep running.
"""

import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKERS = ("conversion", "load", "quality", "valuesets", "measures", "gap_identifier", "gaps", "documents",
           "evidence", "stars")
FIRST_RESTART_DELAY_S = 5
MAX_RESTART_DELAY_S = 60
HEALTHY_RUN_S = 5 * 60
MAX_FAILURES = 5
FAILURE_WINDOW_S = 10 * 60


def _start(name: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-u", "-m", f"services.workers.{name}_worker"], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")


def _supervise(name: str, procs: dict[str, subprocess.Popen], stopping: threading.Event) -> None:
    """Run one worker, print its output, and restart it after a non-zero exit until it fails too often."""
    delay = FIRST_RESTART_DELAY_S
    failures: deque[float] = deque()
    while not stopping.is_set():
        started = time.monotonic()
        proc = _start(name)
        procs[name] = proc
        for line in proc.stdout:
            print(f"[{name}] {line}", end="", flush=True)
        code = proc.wait()
        if stopping.is_set() or code == 0:
            print(f"[{name}] exited with code {code}", flush=True)
            return
        now = time.monotonic()
        if now - started >= HEALTHY_RUN_S:
            delay = FIRST_RESTART_DELAY_S
        failures.append(now)
        while failures and now - failures[0] > FAILURE_WINDOW_S:
            failures.popleft()
        if len(failures) >= MAX_FAILURES:
            print(f"[{name}] keeps failing, not restarting. Check the error above.", flush=True)
            return
        print(f"[{name}] exited with code {code}, restarting in {delay}s", flush=True)
        if stopping.wait(delay):
            return
        delay = min(delay * 2, MAX_RESTART_DELAY_S)


def main(names: list[str]) -> None:
    unknown = set(names) - set(WORKERS)
    if unknown:
        raise SystemExit(f"Unknown worker(s): {', '.join(sorted(unknown))}. Choose from: {', '.join(WORKERS)}")
    stopping = threading.Event()
    procs: dict[str, subprocess.Popen] = {}
    supervisors = [threading.Thread(target=_supervise, args=(name, procs, stopping), daemon=True)
                   for name in names or WORKERS]
    for supervisor in supervisors:
        supervisor.start()
    try:
        for supervisor in supervisors:
            # A timeout keeps the main thread responsive to Ctrl+C on Windows.
            while supervisor.is_alive():
                supervisor.join(timeout=1)
    except KeyboardInterrupt:
        stopping.set()
        for proc in list(procs.values()):
            proc.terminate()


if __name__ == "__main__":
    main(sys.argv[1:])
