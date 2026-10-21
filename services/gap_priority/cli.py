"""Command line for gap prioritization.

    python -m services.gap_priority.cli score [--as-of YYYY-MM-DD]
    python -m services.gap_priority.cli show [--tier high] [--measure KED] [--limit 20]

Output shows the gap's seed code (or gap id), never names, MRNs or patient IDs.
"""

import argparse
import sys
from datetime import date

from services.document_intake import db

from .config import TIERS, load_rules
from .runner import latest_scores, score_all


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    return "\n".join([line, "  ".join("-" * w for w in widths)] +
                     ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells])


def cmd_score(as_of: date) -> int:
    rules = load_rules()
    with db.connect() as conn:
        summary = score_all(conn, as_of, rules)
    print(f"Scored {summary.gaps_scored} open gaps as of {summary.as_of} with rules {summary.rules_version} "
          f"(scored_at {summary.scored_at:%Y-%m-%d %H:%M:%S %z}).")
    print("Tiers:    " + ", ".join(f"{tier} {count}" for tier, count in summary.tiers.items()))
    print("Channels: " + ", ".join(f"{name} {count}" for name, count in summary.channels.items()))
    return 0


def cmd_show(tier: str | None, measure: str | None, limit: int) -> int:
    with db.connect() as conn:
        scores = latest_scores(conn, tier, measure, limit)
    if not scores:
        print("No scored open gaps match. Run the score command first.")
        return 0
    lines = [[s["key"], s["measure_id"], s["priority_tier"], _score(s["composite_score"]),
              _score(s["closure_likelihood_score"]), _score(s["financial_value_score"]), _score(s["engagement_score"]),
              s["recommended_channel"], s["first_reason"]] for s in scores]
    print(_table(["gap", "measure", "tier", "composite", "likelihood", "value", "engagement", "channel", "reason"],
                 lines))
    return 0


def _score(number: object) -> str:
    return "" if number is None else f"{float(number):.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gap_priority", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="score every open gap and append the results")
    s.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="run date (default today)")
    w = sub.add_parser("show", help="list the latest priority of open gaps, best first")
    w.add_argument("--tier", choices=TIERS, default=None)
    w.add_argument("--measure", default=None, help="measure code, for example KED")
    w.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "score":
        return cmd_score(args.as_of)
    return cmd_show(args.tier, args.measure, args.limit)


if __name__ == "__main__":
    sys.exit(main())
