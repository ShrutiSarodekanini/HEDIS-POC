"""Flag likely patient details, secrets and forbidden files before they are committed.

Usage (from the repo root):
    python .claude/skills/hedis-team-standards/scripts/phi_check.py --staged      what you are about to commit
    python .claude/skills/hedis-team-standards/scripts/phi_check.py PATH [PATH]   whole files or folders

It reports suspects, not certainties. A line you keep on purpose gets a comment saying why:  # phi-ok: <reason>
Exit code 1 when anything is reported, so it can run as a pre-commit hook.
"""

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Variable, attribute and keyword names that usually hold patient details or document content.
RISKY_NAMES = {
    "first_name", "last_name", "full_name", "patient_name", "member_name", "given", "family",
    "birth_date", "dob", "date_of_birth", "birthdate", "ssn", "mrn",
    "address", "street", "zip", "zip_code", "postal_code", "phone", "fax", "email",
    "original_name", "filename", "file_name",
    "text", "page_text", "excerpt", "quote", "content", "body", "data", "raw", "raw_response", "prompt", "user_text",
}

#: Calls whose arguments end up in logs, error messages, HTTP responses or the audit trail.
SINK_FUNCS = {"print", "audit", "HTTPException", "warn"}
SINK_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
LOGGER_NAMES = {"log", "logger", "logging", "LOG", "LOGGER"}

SECRET_PATTERNS = [
    ("an OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("a Groq key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("an API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("a database password in a URL", re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:(?!<)[^@\s]+@")),
    ("a secret assigned in code", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"'](?!<|\{|sk-or-test)[^\"'\s]{12,}[\"']")),
]

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)\.env$"), ".env holds secrets; only .env.example may be committed"),
    (re.compile(r"^data/intake_store/"), "the intake store holds uploaded documents and page images"),
    (re.compile(r"\.(dump|bak|sql\.gz|log)$"), "dumps and logs can contain patient data"),
]

SUPPRESS = "phi-ok:"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.message}"


def _is_sink(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in SINK_FUNCS
    if isinstance(func, ast.Attribute):
        owner = func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
        return (func.attr in SINK_METHODS and owner_name in LOGGER_NAMES) or func.attr in SINK_FUNCS
    return False


#: Wrapping a value in one of these turns it into a size, a type or a flag, which is safe to log.
SAFE_WRAPPERS = {"len", "type", "bool", "isinstance", "hash", "id", "sorted_keys"}


def _risky_names_in(node: ast.AST) -> set[str]:
    """Risky names used inside the call's arguments, including inside f-strings, except inside len() and the like."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SAFE_WRAPPERS:
        return set()
    found: set[str] = set()
    if isinstance(node, ast.Name) and node.id in RISKY_NAMES:
        found.add(node.id)
    elif isinstance(node, ast.Attribute) and node.attr in RISKY_NAMES:
        found.add(node.attr)
    elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in RISKY_NAMES:
        found.add(str(node.slice.value))
    for child in ast.iter_child_nodes(node):
        found |= _risky_names_in(child)
    return found


def _call_args(call: ast.Call) -> list[ast.AST]:
    """Positional arguments and keyword values. Keyword names are not checked: raw=... on an exception is an
    attribute kept for protected storage, not part of the message."""
    return [*call.args, *(k.value for k in call.keywords)]


def check_python(path: str, source: str, lines: set[int] | None) -> list[Finding]:
    """PHI-looking values passed to logs, prints, errors, HTTP errors and audit calls."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 1, "parse", "could not read the file as Python; check it by hand")]
    text_lines = source.splitlines()
    findings = []
    for node in ast.walk(tree):
        calls: list[ast.Call] = []
        if isinstance(node, ast.Call) and _is_sink(node):
            calls.append(node)
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            calls.append(node.exc)
        for call in calls:
            span = set(range(call.lineno, (call.end_lineno or call.lineno) + 1))
            if lines is not None and not span & lines:
                continue
            if any(SUPPRESS in text_lines[n - 1] for n in span if n <= len(text_lines)):
                continue
            names = set().union(set(), *(_risky_names_in(a) for a in _call_args(call)))
            if names:
                findings.append(Finding(path, call.lineno, "patient details?",
                                        f"{', '.join(sorted(names))} passed to {ast.unparse(call.func)}(); log or "
                                        "raise IDs, not values (see .github/instructions/python.instructions.md)"))
    return findings


def check_secrets(path: str, source: str, lines: set[int] | None) -> list[Finding]:
    findings = []
    for number, line in enumerate(source.splitlines(), 1):
        if lines is not None and number not in lines:
            continue
        if SUPPRESS in line:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path, number, "secret?", f"looks like {label}; keep it in .env only"))
    return findings


def check_path(path: str) -> list[Finding]:
    return [Finding(path, 1, "forbidden file", why) for pattern, why in FORBIDDEN_PATHS if pattern.search(path)]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=True).stdout


def staged_changes() -> dict[str, set[int]]:
    """Staged files with the line numbers that were added or changed."""
    changed: dict[str, set[int]] = {}
    current = None
    for line in _git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current:
                changed.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, count = int(match[1]), int(match[2] or 1)
            changed[current].update(range(start, start + count))
    return changed


def scan_file(path: str, source: str, lines: set[int] | None) -> list[Finding]:
    findings = check_path(path)
    if path.endswith(".py"):
        findings += check_python(path, source, lines)
    findings += check_secrets(path, source, lines)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="files or folders to scan in full")
    parser.add_argument("--staged", action="store_true", help="scan only the lines staged for commit")
    args = parser.parse_args(argv)
    if not args.staged and not args.paths:
        parser.error("pass --staged or one or more paths")

    findings: list[Finding] = []
    if args.staged:
        for path, lines in staged_changes().items():
            source = _git("show", f":{path}") if not check_path(path) else ""
            findings += scan_file(path, source, lines)
    for root in args.paths:
        root_path = Path(root)
        files = sorted(root_path.rglob("*.py")) if root_path.is_dir() else [root_path]
        for file in files:
            if "__pycache__" in file.parts:
                continue
            findings += scan_file(file.as_posix(), file.read_text(encoding="utf-8", errors="replace"), None)

    for finding in findings:
        print(finding)
    print(f"{len(findings)} finding(s)." if findings else "No findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
