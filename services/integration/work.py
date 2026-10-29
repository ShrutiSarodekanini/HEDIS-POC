"""Read side of the integration API: measure star positions, the ranked work list and one gap in full.

The work list is the demo page's query, plus one rule: gaps a reviewer has confirmed (closed_pending_engine,
excluded_pending_engine) or the engine has closed (closed, excluded) leave the list, so a decision is visible at once.
Their scores stay in gap_priority_score.
"""

import math
from datetime import date

import psycopg
from psycopg.rows import dict_row

from services.gap_priority import scoring
from services.gap_priority.config import load_rules
from services.gap_priority.inputs import read_inputs
from services.star_modeler.main import CMS_RATING_YEAR

from . import parts, stars
from .config import SETTINGS

ROLES = {
    "reviewer": ("abstractor_review",),
    "outreach": ("member_outreach_call", "mailed_fit_kit"),
    "doctor": ("point_of_care_alert", "provider_gap_list", "chart_review"),
}
OFF_LIST = ["closed_pending_engine", "excluded_pending_engine", "closed", "excluded"]


def rounded_rate(numerator: int, denominator: int) -> int:
    """The rate rounded half up to a whole percent, as the gap prioritization score does (US-05)."""
    return (200 * numerator + denominator) // (2 * denominator) if denominator else 0


def measures(conn: psycopg.Connection, as_of: date) -> dict:
    """Per measure: rate and star now, and the rate and star once confirmed closures and exclusions reach the engine,
    all from the star rating functions (stars.py). The value score still comes from the gap prioritization rules."""
    rules = load_rules()
    _, inputs = read_inputs(conn, as_of, rules.star_year)
    with conn.cursor(row_factory=dict_row) as cur:
        rates = cur.execute(
            """SELECT measure_id,
                      count(*) FILTER (WHERE in_denominator AND NOT coalesce(excluded, false)) AS denominator,
                      count(*) FILTER (WHERE in_denominator AND in_numerator AND NOT coalesce(excluded, false))
                          AS numerator
                 FROM v_measure_result_latest WHERE measurement_year = %s GROUP BY measure_id ORDER BY measure_id""",
            (SETTINGS.measurement_year,)).fetchall()
        gaps = {r["measure_id"]: r for r in cur.execute(
            """SELECT g.measure_id,
                      count(*) FILTER (WHERE coalesce(d.review_status, 'open') <> ALL(%s::text[])) AS open_gaps,
                      count(*) FILTER (WHERE d.review_status = 'closed_pending_engine') AS confirmed,
                      count(*) FILTER (WHERE d.review_status = 'excluded_pending_engine') AS excluded
                 FROM care_gap g LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
                WHERE g.status = 'open' GROUP BY g.measure_id""", (OFF_LIST,)).fetchall()}
        tiers = {r["priority_tier"]: r["count"] for r in cur.execute(
            """SELECT s.priority_tier, count(*) FROM v_gap_priority_latest s
                 JOIN care_gap g ON g.gap_id = s.gap_id LEFT JOIN care_gap_detail d ON d.gap_id = s.gap_id
                WHERE g.status = 'open' AND coalesce(d.review_status, 'open') <> ALL(%s::text[]) GROUP BY 1""",
            (OFF_LIST,)).fetchall()}
        last = cur.execute("SELECT max(scored_at) AS last FROM gap_priority_score").fetchone()["last"]
        names = {r["measure_id"]: r["name"] for r in cur.execute(
            "SELECT measure_id, name FROM measure_rule WHERE measurement_year = %s", (SETTINGS.measurement_year,))}
        by_model = cur.execute(
            """SELECT extraction_model_version AS model,
                      count(*) FILTER (WHERE status = 'proposed') AS proposed,
                      count(*) FILTER (WHERE status = 'confirmed') AS confirmed,
                      count(*) FILTER (WHERE status = 'rejected') AS rejected
                 FROM evidence_proposal GROUP BY 1 ORDER BY 1""").fetchall()
        supplemental = cur.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE NOT exported AND ready) AS unexported,
                      count(*) FILTER (WHERE NOT exported AND NOT ready) AS waiting_for_parts
                 FROM (SELECT EXISTS (SELECT 1 FROM export_run_items i WHERE i.supplemental_record_id = s.id)
                                  AS exported,
                              d.review_status IN ('closed_pending_engine', 'excluded_pending_engine') AS ready
                         FROM supplemental_records s
                         JOIN evidence_proposal p ON p.evidence_id = s.evidence_id
                         JOIN care_gap_detail d ON d.gap_id = p.gap_id) r""").fetchone()
        exports = cur.execute(
            """SELECT id, run_at, row_count, left(file_sha256, 12) AS file_sha256
                 FROM export_runs ORDER BY run_at DESC, id DESC LIMIT 5""").fetchall()
    out = []
    year = SETTINGS.measurement_year
    with stars.session() as s:
        for r in rates:
            measure_id = r["measure_id"]
            card = stars.measure_card(s, measure_id, year)
            projected = stars.projection(s, measure_id, year)
            m = inputs.get((measure_id, year))
            value = scoring.measure_value(m, rules)
            out.append({
                "measure": measure_id, "name": names.get(measure_id, measure_id),
                "weight": m.weight if m else None, "numerator": card["numerator"], "denominator": card["denominator"],
                "rate": card["rate"], "rounded": rounded_rate(card["numerator"], card["denominator"]),
                "stars": card["stars"], "status": card["status"], "explanation": card["explanation"],
                "cut_points": card["cut_points"],
                "confirmed_pending_engine": gaps.get(measure_id, {}).get("confirmed", 0),
                "excluded_pending_engine": gaps.get(measure_id, {}).get("excluded", 0),
                "projected_rate": projected["projected_rate"], "projected_stars": projected["projected_stars"],
                "projection_source": stars.SOURCE,
                "value": round(value.value, 4), "value_reason": value.reason,
                "open_gaps": gaps.get(measure_id, {}).get("open_gaps", 0)})
    for m in by_model:
        decided = m["confirmed"] + m["rejected"]
        m["acceptance_rate"] = round(100 * m["confirmed"] / decided, 1) if decided else None
    evidence = {"proposed": sum(m["proposed"] for m in by_model), "confirmed": sum(m["confirmed"] for m in by_model),
                "rejected": sum(m["rejected"] for m in by_model), "by_model": by_model,
                "supplemental_total": supplemental["total"], "supplemental_unexported": supplemental["unexported"],
                "supplemental_waiting_for_parts": supplemental["waiting_for_parts"],
                "exports": exports}
    return {"measures": out, "tiers": tiers, "last_scored_at": last, "rules_version": rules.version,
            "cms_rating_year": CMS_RATING_YEAR, "evidence": evidence}


def work_list(conn: psycopg.Connection, role: str | None, tier: str | None, measure: str | None,
              limit: int = 400) -> list[dict]:
    """The ranked work list: newest score per open gap, best first, filtered by role, tier and measure."""
    channels = list(ROLES[role]) if role in ROLES else None
    rows = conn.execute(
        """SELECT coalesce(d.seed_code, s.gap_id::text), s.gap_id::text, g.measure_id, s.priority_tier,
                  s.composite_score, s.closure_likelihood_score, s.financial_value_score, s.engagement_score,
                  s.recommended_channel, r.reasons, r.action_reason,
                  g.patient_id, mr.name, g.missing_element, coalesce(d.review_status, 'open'),
                  coalesce(d.pending_count, 0),
                  (SELECT min(e.period_start)::date FROM encounter e
                    WHERE e.patient_id = g.patient_id AND e.period_start > now()),
                  EXISTS (SELECT 1 FROM document_reference dr WHERE dr.patient_id = g.patient_id)
             FROM v_gap_priority_latest s
             JOIN care_gap g ON g.gap_id = s.gap_id
             LEFT JOIN care_gap_detail d ON d.gap_id = s.gap_id
             LEFT JOIN gap_priority_reason r ON r.score_pk = s.score_pk
             LEFT JOIN measure_rule mr ON mr.measure_id = g.measure_id AND mr.measurement_year = g.measurement_year
            WHERE g.status = 'open' AND coalesce(d.review_status, 'open') <> ALL(%(off)s::text[])
              AND (%(tier)s::text IS NULL OR s.priority_tier = %(tier)s::text)
              AND (%(measure)s::text IS NULL OR g.measure_id = %(measure)s::text)
              AND (%(channels)s::text[] IS NULL OR s.recommended_channel = ANY(%(channels)s::text[]))
            ORDER BY array_position(ARRAY['high','medium','low'], s.priority_tier), s.composite_score DESC,
                     s.closure_likelihood_score DESC, 1
            LIMIT %(limit)s""",
        {"off": OFF_LIST, "tier": tier, "measure": measure, "channels": channels, "limit": limit}).fetchall()
    keys = ("key", "gap_id", "measure", "tier", "composite", "likelihood", "value", "engagement", "channel",
            "reasons", "action", "patient_id", "measure_name", "missing_element", "review_status", "pending_count",
            "next_visit", "has_documents")
    return [dict(zip(keys, r)) for r in rows]


def gap_detail(conn: psycopg.Connection, key: str, as_of: date) -> dict | None:
    """One gap: its facts, how its scores were built, the patient's documents and the evidence so far, each finding
    with the page text around its quote, its rule check and the AI attempt that found it."""
    with conn.cursor(row_factory=dict_row) as cur:
        gap = cur.execute(
            """SELECT g.gap_id::text, coalesce(d.seed_code, g.gap_id::text) AS key, g.patient_id, g.measure_id,
                      g.measurement_year, g.status, g.missing_element, g.deadline_date,
                      g.last_relevant_event_date, g.last_relevant_event_description,
                      coalesce(d.review_status, 'open') AS review_status, coalesce(d.pending_count, 0) AS pending_count,
                      mr.name AS measure_name, s.priority_tier AS tier, s.composite_score, s.recommended_channel,
                      pr.reasons, pr.action_reason
                 FROM care_gap g LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
                 LEFT JOIN measure_rule mr ON mr.measure_id = g.measure_id AND mr.measurement_year = g.measurement_year
                 LEFT JOIN v_gap_priority_latest s ON s.gap_id = g.gap_id
                 LEFT JOIN gap_priority_reason pr ON pr.score_pk = s.score_pk
                WHERE g.gap_id::text = %(key)s OR d.seed_code = %(key)s""", {"key": key}).fetchone()
        if gap is None:
            return None
        documents = cur.execute(
            """SELECT r.document_reference_id AS document_id, r.type_code AS doc_type,
                      (r.doc_date AT TIME ZONE 'UTC')::date AS document_date, c.conversion_status, c.page_count,
                      c.near_duplicate_of
                 FROM document_reference r
                 LEFT JOIN document_conversion c ON c.document_reference_id = r.document_reference_id
                WHERE r.patient_id = %s ORDER BY r.doc_date DESC NULLS LAST, r.document_reference_id""",
            (gap["patient_id"],)).fetchall()
        evidence = cur.execute(
            """SELECT p.evidence_id::text, p.status, p.passage_citation AS excerpt, p.proposed_finding,
                      p.confidence_score AS confidence, p.extraction_model_version AS model, p.reviewer_id,
                      p.decided_at, x.document_reference_id AS document_id, x.page_no, x.evidence_kind,
                      x.evidence_type, x.service_date_raw, x.service_date_from, x.service_date_to, x.value, x.reason,
                      x.handwritten, x.low_confidence, x.char_start, x.char_end, pg.text AS page_text,
                      dec.reason AS decision_reason,
                      a.id AS attempt_id, r.model_name AS attempt_model, r.started_at AS attempt_started_at,
                      a.duration_ms AS attempt_duration_ms,
                      (SELECT count(*) FROM evidence_proposal_detail k WHERE k.attempt_id = a.id) AS attempt_kept,
                      coalesce(jsonb_array_length(a.dropped), 0) AS attempt_dropped
                 FROM evidence_proposal p
                 LEFT JOIN evidence_proposal_detail x ON x.evidence_id = p.evidence_id
                 LEFT JOIN document_pages pg ON pg.document_reference_id = x.document_reference_id
                                            AND pg.page_no = x.page_no
                 LEFT JOIN review_decisions dec ON dec.evidence_id = p.evidence_id
                 LEFT JOIN extraction_attempts a ON a.id = x.attempt_id
                 LEFT JOIN extraction_runs r ON r.id = a.run_id
                WHERE p.gap_id = %s::uuid ORDER BY x.created_at, p.evidence_id""", (gap["gap_id"],)).fetchall()
        ai_runs = cur.execute(
            """SELECT r.id AS run_id, r.started_at, r.model_name, a.id AS attempt_id, a.status,
                      (SELECT count(*) FROM evidence_proposal_detail x WHERE x.attempt_id = a.id) AS kept,
                      coalesce(jsonb_array_length(a.dropped), 0) AS dropped_count, a.dropped, a.pages_sent,
                      a.raw_response, a.prompt_version,
                      a.prompt_tokens, a.output_tokens, a.duration_ms, split_part(a.error, E'\n', 1) AS error
                 FROM extraction_attempts a JOIN extraction_runs r ON r.id = a.run_id
                WHERE a.gap_id = %s::uuid ORDER BY r.started_at DESC, a.id DESC""", (gap["gap_id"],)).fetchall()
    rule, age, items = parts.load(conn, gap["gap_id"])
    conn.rollback()
    checked = parts.check(rule, age, items)
    for e in evidence:
        _enrich(e, rule, checked)
    with stars.session() as s:
        impact = stars.gap_impact(s, gap["gap_id"])
    return {"gap": gap, "breakdown": breakdown(conn, gap["key"], as_of), "documents": documents,
            "evidence": evidence, "ai_runs": ai_runs, "impact": impact, "parts": checked}


CONTEXT_CHARS = 200


def _enrich(e: dict, rule: dict, checked: dict) -> None:
    """Add context (page text around the quote), rule_check and attempt to one evidence row, in place."""
    e["part"], e["reason"] = parts.split_reason(e["reason"])
    text, start, end = e.pop("page_text") or "", e.pop("char_start"), e.pop("char_end")
    if text and start is not None and end is not None:
        lo, hi = max(0, start - CONTEXT_CHARS), min(len(text), end + CONTEXT_CHARS)
        e["context"] = {"text": text[lo:hi], "quote_start": start - lo, "quote_end": end - lo,
                        "starts_mid_page": lo > 0, "ends_mid_page": hi < len(text)}
    else:
        e["context"] = None
    window = next((w for w in rule.get("windows") or [] if w.get("evidence_type") == e["evidence_type"]), None)
    served_from, served_to = e["service_date_from"], e["service_date_to"]
    inside = None
    if window and served_from and served_to:
        low = date.fromisoformat(window["from"]) if window.get("from") else None
        high = date.fromisoformat(window["to"]) if window.get("to") else None
        inside = (low is None or served_from >= low) and (high is None or served_to <= high)
    group = (checked["numerator"]["parts"] if e["evidence_kind"] == "numerator" else next(
        (x["parts"] for x in checked["exclusions"] if x["evidence_type"] == e["evidence_type"]), []))
    e["rule_check"] = {
        "window_from": window.get("from") if window else None, "window_to": window.get("to") if window else None,
        "window_text": window.get("relative") if window else None,
        "service_date_from": served_from, "service_date_to": served_to, "inside_window": inside,
        "parts": group if len(group) > 1 else [],
        "part_label": parts.part_label(checked, e["evidence_kind"], e["evidence_type"], e["part"]),
        "part_count": len(group)}
    e["attempt"] = None if e["attempt_id"] is None else {
        "attempt_id": e["attempt_id"], "model": e["attempt_model"], "started_at": e["attempt_started_at"],
        "duration_ms": e["attempt_duration_ms"], "kept": e["attempt_kept"], "dropped": e["attempt_dropped"]}
    for k in ("attempt_id", "attempt_model", "attempt_started_at", "attempt_duration_ms", "attempt_kept",
              "attempt_dropped"):
        e.pop(k)


def breakdown(conn: psycopg.Connection, key: str, as_of: date) -> dict | None:
    """Recompute one gap's scores step by step from the same inputs and rules the scoring run uses (demo logic)."""
    rules = load_rules()
    gap_list, inputs = read_inputs(conn, as_of, rules.star_year)
    gap = next((g for g in gap_list if g.key == key), None)
    if gap is None:
        return None
    s = scoring.signals(gap, as_of, rules)
    start = gap.closure_rate if gap.closure_rate is not None else rules.default_closure_rate
    steps = [{"label": "Practice track record" if gap.closure_rate is not None else "No practice record (default)",
              "change": start}]
    for applies, label, change in (
            (gap.evidence_waiting, "Evidence waiting for review", rules.evidence_waiting),
            (s.visit_soon, f"Visit booked in {s.days_to_visit} days", rules.visit_soon),
            (s.recent_contact, f"Contacted {s.days_since_contact} days ago", rules.recent_contact),
            (gap.attributed_org_id is not None, "Has an attributed practice", rules.attributed_practice),
            (s.unreachable, "No contact for over a year" if s.days_since_contact is not None else "No recorded contact",
             rules.no_contact),
            (s.out_of_time, f"{s.days_left} days left and no visit before the deadline", rules.out_of_time)):
        if applies:
            steps.append({"label": label, "change": change})
    likelihood = scoring.likelihood(gap, as_of, rules)
    if s.recent_contact:
        level, level_label = rules.engagement_recent, f"Contacted {s.days_since_contact} days ago (within 90)"
    elif not s.unreachable:
        level, level_label = rules.engagement_within_year, f"Contacted {s.days_since_contact} days ago (within a year)"
    else:
        level, level_label = rules.engagement_other, "No contact for over a year, or never"
    eng_steps = [{"label": level_label, "change": level}]
    if gap.next_visit is not None:
        eng_steps.append({"label": f"Visit booked in {s.days_to_visit} days", "change": rules.engagement_visit_booked})
    m = inputs[(gap.measure_id, gap.measurement_year)]
    value = scoring.measure_value(m, rules)
    rounded = rounded_rate(m.numerator, m.denominator)
    cuts = {star: float(low) for star, low in m.cut_points}
    stars = max((st for st, low in cuts.items() if rounded >= low), default=None) if cuts else None
    next_cut = cuts.get(stars + 1) if stars else None
    target = math.ceil((next_cut - 0.5) * m.denominator / 100) if next_cut else None
    return {
        "key": gap.key, "measure": gap.measure_id, "as_of": as_of,
        "likelihood": {"steps": steps, "raw": round(sum(x["change"] for x in steps), 4), "min": rules.likelihood_min,
                       "max": rules.likelihood_max, "result": round(likelihood, 4)},
        "engagement": {"steps": eng_steps, "cap": rules.engagement_cap,
                       "result": round(scoring.engagement(gap, as_of, rules), 4)},
        "value": {"weight": m.weight, "numerator": m.numerator, "denominator": m.denominator,
                  "rate": round(100 * m.numerator / m.denominator, 2) if m.denominator else None,
                  "rounded": rounded, "stars": stars, "next_cut": next_cut, "target": target,
                  "needed": (target - m.numerator) if target else None, "divisor": rules.value_divisor,
                  "reason": value.reason, "result": round(value.value, 4), "non_star_value": rules.non_star_value},
        "composite": round(likelihood * value.value, 4),
        "channel": scoring.channel(gap, as_of, rules),
    }
