"""Command line for document intake.

    python -m services.document_intake.cli process-pending [--limit N]
    python -m services.document_intake.cli ingest <path> [--org ORG] [--member M-xxx]
    python -m services.document_intake.cli queue
"""

import argparse
import sys
from pathlib import Path

from . import db, intake


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    return "\n".join([line, "  ".join("-" * w for w in widths)] +
                     ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells])


def cmd_process_pending(limit: int | None) -> int:
    with db.connect() as conn:
        res = intake.process_pending(conn, limit)
    rows = [[r["source_file_id"], r["filename"], r["processing_status"], r["match_status"], (r["note"] or "")[:60]]
            for r in res["results"]]
    if rows:
        print(_table(["id", "file", "status", "match", "note"], rows))
    print(f"\nselected {res['selected']}: done {res['done']}, failed {res['failed']}, rejected {res['rejected']}, "
          f"skipped {res['skipped']}, needs match {res['needs_match']}")
    return 1 if res["failed"] else 0


def cmd_ingest(path: str, org: str | None, member: str | None) -> int:
    target = Path(path)
    if not target.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    with db.connect() as conn:
        try:
            result = intake.receive_file(conn, target.read_bytes(), target.name, org, member)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    rows = _flatten(result)
    print(_table(["file", "result", "processing", "match", "member", "note"], rows))
    return 0 if result["status"] in ("received", "duplicate") else 1


def _flatten(result: dict, depth: int = 0) -> list[list]:
    pad = "  " * depth
    rows = [[pad + str(result.get("filename") or ""), result["status"], result.get("processing_status"),
             result.get("match_status"), result.get("member_id"),
             (result.get("processing_note") or result.get("message") or "")[:60]]]
    for child in result.get("children", []):
        rows.extend(_flatten(child, depth + 1))
    return rows


def cmd_queue() -> int:
    with db.connect() as conn:
        items = intake.match_queue(conn)
    if not items:
        print("The member-match queue is empty.")
        return 0
    rows = []
    for item in items:
        f = item["file"]
        cands = "; ".join(f"{c['member_id']} {c['name']}" for c in item["candidates"][:3]) or "(none)"
        rows.append([f["id"], f["original_name"], f["processing_status"], cands])
    print(_table(["id", "file", "status", "top candidates"], rows))
    print(f"\n{len(items)} file(s) waiting for a member match")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="document_intake", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process-pending", help="convert every file still waiting")
    p.add_argument("--limit", type=int, default=None, help="stop after N files")
    i = sub.add_parser("ingest", help="receive one file from disk")
    i.add_argument("path")
    i.add_argument("--org", default=None, help="source organization id")
    i.add_argument("--member", default=None, help="member id, when known")
    sub.add_parser("queue", help="show files that need a member match")
    args = parser.parse_args(argv)
    if args.command == "process-pending":
        return cmd_process_pending(args.limit)
    if args.command == "ingest":
        return cmd_ingest(args.path, args.org, args.member)
    return cmd_queue()


if __name__ == "__main__":
    sys.exit(main())
