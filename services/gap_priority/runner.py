"""One scoring run: read every open gap, score it and append its priority, all in the caller's transaction.

gap_priority_score is append-only; screens read the newest row per gap through v_gap_priority_latest. Every row of a
run shares one scored_at, and the run writes one audit event with counts only (no patient data).
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
from psycopg.rows import dict_row

from services.document_intake.db import audit

from .config import SETTINGS, TIERS, Rules
from .inputs import read_inputs
from .scoring import (CHANNELS, Ranked, action_reason, channel, engagement, likelihood, measure_value, reasons,
                      tiers)

log = logging.getLogger(__name__)

PLACES = 4                        # stored scores keep 4 decimals; ranking uses the stored values

_INSERT_SCORES = """
INSERT INTO gap_priority_score (gap_id, closure_likelihood_score, financial_value_score, engagement_score,
                                composite_score, priority_tier, recommended_channel, scored_at)
SELECT s.gap_id, s.likelihood, s.value, s.engagement, s.composite, s.tier, s.channel, %s
  FROM unnest(%s::uuid[], %s::numeric[], %s::numeric[], %s::numeric[], %s::numeric[], %s::text[], %s::text[])
       AS s(gap_id, likelihood, value, engagement, composite, tier, channel)
RETURNING score_pk, gap_id
"""

_INSERT_REASONS = """
INSERT INTO gap_priority_reason (score_pk, reasons, action_reason)
SELECT r.score_pk, r.reasons::jsonb, r.action_reason
  FROM unnest(%s::bigint[], %s::text[], %s::text[]) AS r(score_pk, reasons, action_reason)
"""

_LATEST_SQL = """
SELECT coalesce(d.seed_code, s.gap_id::text) AS key, g.measure_id, s.priority_tier, s.composite_score,
       s.closure_likelihood_score, s.financial_value_score, s.engagement_score, s.recommended_channel,
       r.reasons ->> 0 AS first_reason
  FROM v_gap_priority_latest s
  JOIN care_gap g ON g.gap_id = s.gap_id
  LEFT JOIN care_gap_detail d ON d.gap_id = s.gap_id
  LEFT JOIN gap_priority_reason r ON r.score_pk = s.score_pk
 WHERE g.status = 'open'
   AND (%(tier)s::text IS NULL OR s.priority_tier = %(tier)s::text)
   AND (%(measure)s::text IS NULL OR g.measure_id = %(measure)s::text)
 ORDER BY array_position(ARRAY['high', 'medium', 'low'], s.priority_tier), s.composite_score DESC,
          s.closure_likelihood_score DESC, key
 LIMIT %(limit)s
"""


@dataclass(frozen=True)
class RunSummary:
    as_of: date
    scored_at: datetime
    rules_version: str
    gaps_scored: int
    tiers: dict[str, int]
    channels: dict[str, int]


def score_all(conn: psycopg.Connection, as_of: date, rules: Rules) -> RunSummary:
    """Score every open gap once and append the results. The caller commits, so a failure leaves nothing behind."""
    gaps, measures = read_inputs(conn, as_of, rules.star_year)
    values = {key: measure_value(measure, rules) for key, measure in measures.items()}
    rows = []
    for gap in gaps:
        value = values[(gap.measure_id, gap.measurement_year)]
        chosen = channel(gap, as_of, rules)
        row = {
            "gap": gap,
            "likelihood": round(likelihood(gap, as_of, rules), PLACES),
            "value": round(value.value, PLACES),
            "engagement": round(engagement(gap, as_of, rules), PLACES),
            "channel": chosen,
            "reasons": reasons(gap, value, as_of, rules),
            "action": action_reason(gap, chosen, as_of, rules),
        }
        row["composite"] = round(row["likelihood"] * row["value"], PLACES)
        rows.append(row)
    ranked = [Ranked(r["gap"].key, r["composite"], r["likelihood"], r["gap"].evidence_waiting) for r in rows]
    for row, tier in zip(rows, tiers(ranked, rules)):
        row["tier"] = tier

    scored_at = conn.execute("SELECT now()").fetchone()[0]
    if rows:
        _insert(conn, rows, scored_at)
    summary = RunSummary(
        as_of=as_of, scored_at=scored_at, rules_version=rules.version, gaps_scored=len(rows),
        tiers={tier: sum(r["tier"] == tier for r in rows) for tier in TIERS},
        channels={name: count for name, count in Counter(r["channel"] for r in rows).most_common()},
    )
    audit(conn, "gap_priority_run", scored_at.isoformat(), "priority.scored",
          {"run_date": as_of.isoformat(), "scored_at": scored_at.isoformat(), "rules_version": rules.version,
           "gaps_scored": summary.gaps_scored, "tiers": summary.tiers,
           "channels": {name: summary.channels.get(name, 0) for name in CHANNELS}},
          actor=SETTINGS.actor)
    log.info("scored %d open gaps as of %s with rules %s", summary.gaps_scored, as_of, rules.version)
    return summary


def _insert(conn: psycopg.Connection, rows: list[dict], scored_at: datetime) -> None:
    """Two set-based inserts: the scores, then their reasons matched back by gap."""
    cur = conn.execute(_INSERT_SCORES, (
        scored_at, [r["gap"].gap_id for r in rows], [r["likelihood"] for r in rows], [r["value"] for r in rows],
        [r["engagement"] for r in rows], [r["composite"] for r in rows], [r["tier"] for r in rows],
        [r["channel"] for r in rows]))
    score_pk = {gap_id: pk for pk, gap_id in cur.fetchall()}
    if len(score_pk) != len(rows):
        raise RuntimeError(f"expected {len(rows)} priority rows, inserted {len(score_pk)}; nothing was saved")
    conn.execute(_INSERT_REASONS, (
        [score_pk[r["gap"].gap_id] for r in rows], [json.dumps(r["reasons"]) for r in rows],
        [r["action"] for r in rows]))


def latest_scores(conn: psycopg.Connection, tier: str | None = None, measure: str | None = None,
                  limit: int = 20) -> list[dict]:
    """The newest priority of each open gap, best first, for the work list."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LATEST_SQL, {"tier": tier, "measure": measure, "limit": limit})
        return cur.fetchall()
