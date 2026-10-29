"""Virus scan of uploaded bytes with the Windows Defender command-line scanner.

Result values: clean | infected | not_scanned | scan_failed.
Scanning is OFF by default for the POC (result not_scanned). Set INTAKE_VIRUS_SCAN=1 to turn it on.
INTAKE_SKIP_SCAN=1 always skips it (used by tests).
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .config import SETTINGS

log = logging.getLogger(__name__)

CLEAN, INFECTED, NOT_SCANNED, SCAN_FAILED = "clean", "infected", "not_scanned", "scan_failed"
SCAN_TIMEOUT_SECONDS = 180

# MpCmdRun exit codes: 0 = no threat found, 2 = threat found, anything else = the scan did not complete.
_EXIT_CLEAN, _EXIT_THREAT = 0, 2


def scan_bytes(data: bytes, filename: str) -> str:
    """Scan file bytes and return clean, infected, not_scanned or scan_failed.

    The bytes are written to a temporary file that is always deleted afterwards. The filename is not used for
    the temporary file name, so an odd name cannot influence the scanner.
    """
    if (os.environ.get("INTAKE_VIRUS_SCAN") != "1" or os.environ.get("INTAKE_SKIP_SCAN") == "1"
            or not SETTINGS.defender_cmd):
        return NOT_SCANNED

    path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix="intake_scan_", suffix=".bin")
        path = Path(name)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if not path.exists():
            return INFECTED  # real-time protection removed it as soon as it was written
        return _run_scanner(path)
    except OSError as exc:
        log.warning("virus scan could not run: %s", type(exc).__name__)
        return SCAN_FAILED
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not delete scan temp file %s: %s", path, exc)


def _run_scanner(path: Path) -> str:
    try:
        proc = subprocess.run(
            [SETTINGS.defender_cmd, "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
            capture_output=True, timeout=SCAN_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        log.warning("virus scan timed out for %s", path)
        return SCAN_FAILED
    if proc.returncode == _EXIT_CLEAN:
        return CLEAN
    if proc.returncode == _EXIT_THREAT:
        # MpCmdRun also exits with 2 when it cannot run at all (for example Defender is turned off); that prints
        # "Failed with hr = ..." and no scan result, so it must not be reported as a threat.
        output = (proc.stdout or b"") + (proc.stderr or b"")
        return SCAN_FAILED if b"Failed with hr" in output else INFECTED
    return SCAN_FAILED
