"""Write side of the integration API: AI evidence for a gap, a reviewer's decision, and a re-score after each.

Extraction runs the same code path as the demo page's "Find evidence with AI" (prompt builder, OpenRouter client,
validator) and stores every kept finding as proposed evidence the way its "Save" does. Only synthetic data is sent
to OpenRouter. The model only proposes: a person confirms or rejects, and the database triggers enforce that a
proposal changes status once, with a matching review decision.

Tracking: each "Review with AI" is one extraction_runs row with one extraction_attempts row per model call, and every
saved finding points to the attempt that produced it. A confirmation adds a supplemental_records row (the database
accepts it only with a confirming decision) and every decision adds a star_scenario snapshot of the measure. Run and
attempt rows store only metadata and protected diagnostics; API responses never include raw prompt or completion text.
"""

import logging
import time
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime

import psycopg
from psycopg.types.json import Jsonb

from services.document_intake.db import audit
from services.evidence_extraction import prompt
from services.evidence_extraction.config import SETTINGS as EXTRACTION_SETTINGS
from services.evidence_extraction.config import provider_policy_error
from services.evidence_extraction.contracts import GapContext, ModelError, ModelResult, Prompt, UnusableAnswer
from services.evidence_extraction.model_client import OpenRouterClient
from services.evidence_extraction.validator import parse_findings, validate
from services.gap_priority.config import load_rules
from services.gap_priority.runner import score_all

from . import metrics, parts, stars
from .config import SETTINGS

log = logging.getLogger(__name__)

DECISIONS = {"confirm": "confirmed", "reject": "rejected"}
_DECISION_METRIC = {"confirmed": "approved", "rejected": "rejected"}


class Conflict(ValueError):
    """The request is valid but the record is in a state that does not allow it."""


class AiEvidenceOff(Conflict):
    """The configured provider policy does not allow model calls for the configured data class."""


def find_evidence(conn: psycopg.Connection, key: str) -> dict:
    """Run the extraction for one gap, save kept findings as proposed, then re-score. Raises LookupError.

    The run and its attempts are committed as they happen, so failed calls are kept too. Raises AiEvidenceOff, before
    any run row or model call, unless EXTRACTION_DATA_CLASS is "synthetic"."""
    policy_error = provider_policy_error(EXTRACTION_SETTINGS)
    if policy_error is not None:
        raise AiEvidenceOff(policy_error)
    gap = prompt.load_gap_context(conn, key)
    measure = prompt.load_measure(conn, gap.measure_code, gap.measurement_year)
    pages = prompt.select_pages(conn, gap)
    conn.rollback()
    correlation_id = f"evd-{uuid.uuid4().hex}"
    run_id = conn.execute(
        """INSERT INTO extraction_runs (status, provider, model_name, scope, correlation_id)
           VALUES ('running', 'openrouter', %s, %s, %s) RETURNING id""",
        (EXTRACTION_SETTINGS.model, Jsonb({"gap_id": gap.gap_id, "measure": gap.measure_code,
                                           "trigger": "review_with_ai", "pages": len(pages)}), correlation_id),
    ).fetchone()[0]
    conn.commit()
    if not pages:
        _finish_run(conn, run_id, gap.gap_id, correlation_id=correlation_id, kept=0, dropped=0, failed=0, calls=0)
        return {"run_id": run_id, "correlation_id": correlation_id, "status": "completed", "attempts": [], "saved": 0,
                "skipped_already_saved": 0,
                "failed_attempts": 0,
                "note": "No converted documents for this patient yet: upload one first. No model call was made."}
    attempts, kept, dropped = [], [], []
    try:
        client = OpenRouterClient()
        for p in prompt.build_prompts(gap, measure, pages):
            attempt, attempt_kept, attempt_dropped = _attempt(
                conn, client, run_id, gap, measure, p, correlation_id=correlation_id
            )
            attempts.append(attempt)
            kept += attempt_kept
            dropped += attempt_dropped
        saved, skipped = _save_proposed(conn, uuid.UUID(gap.gap_id), kept, correlation_id=correlation_id)
        skipped += sum(1 for d in dropped if d.get("already_proposed"))
        conn.commit()
    except Exception:
        conn.rollback()
        failed = sum(1 for a in attempts if a["status"] == "failed")
        _finish_run(conn, run_id, gap.gap_id, correlation_id=correlation_id, kept=0, dropped=len(dropped), failed=failed + 1,
                    calls=len(attempts) + 1)
        raise
    failed = sum(1 for a in attempts if a["status"] == "failed")
    status = _finish_run(conn, run_id, gap.gap_id, correlation_id=correlation_id, kept=len(kept), dropped=len(dropped), failed=failed,
                         calls=len(attempts))
    tiers = score_all(conn, date.today(), load_rules()).tiers if saved else None
    conn.commit()
    log.info("gap %s run %s: %d calls, %d kept, %d dropped, %d saved", gap.gap_id, run_id, len(attempts), len(kept),
             len(dropped), saved)
    return {"run_id": run_id, "correlation_id": correlation_id, "status": status, "attempts": attempts, "saved": saved,
            "skipped_already_saved": skipped, "failed_attempts": failed, "tiers": tiers, "note": None}


def _attempt(conn: psycopg.Connection, client: OpenRouterClient, run_id: int,
             gap: GapContext, measure: dict, p: Prompt, *, correlation_id: str) -> tuple[dict, list, list]:
    """One model call, recorded as one extraction_attempts row (committed) before any of its findings is saved."""
    claimed_at, started = datetime.now(UTC), time.perf_counter()
    pages_sent = [{"document_id": pg.document_id, "page_no": pg.page_no, "method": pg.method,
                   "handwritten": pg.handwritten} for pg in p.pages]
    result: ModelResult | None = None
    failure_type: str | None = None
    kept: list[dict] = []
    dropped: list[dict] = []
    try:
        result = _complete(client, p)
        findings = parse_findings(result.text)
    except (ModelError, UnusableAnswer) as exc:
        failure_type = type(exc).__name__
        raw = _diagnostic_raw(exc, result)
        status, error = "failed", _error_code(exc)
    else:
        checked = validate(gap, measure, list(p.pages), findings)
        dropped = [{"evidence_type": d.finding.evidence_type, "document_id": d.finding.document_id,
                    "reason": d.reason} for d in checked.dropped]
        for c in checked.kept:
            earlier = _already_proposed(conn, gap.gap_id, c.document_id, c.page_no, c.char_start, c.char_end)
            if earlier:
                dropped.append({"evidence_type": c.evidence_type, "document_id": c.document_id,
                                "reason": "already proposed in an earlier run",
                                "evidence_id": earlier, "already_proposed": True})
            else:
                kept.append({**asdict(c), "model": result.model, "prompt_version": p.prompt_version})
        raw, status, error = _diagnostic_raw(None, result), ("succeeded" if checked.kept else "no_evidence"), None
    duration = result.duration_ms if result else round(1000 * (time.perf_counter() - started))
    attempt_id = conn.execute(
        """INSERT INTO extraction_attempts (run_id, gap_id, status, error, prompt_version, pages_sent, dropped,
               duration_ms, prompt_tokens, output_tokens, raw_response, claimed_at, correlation_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (run_id, gap.gap_id, status, error, p.prompt_version, Jsonb(pages_sent), Jsonb(dropped), duration,
         result.prompt_tokens if result else None, result.output_tokens if result else None, raw, claimed_at,
         correlation_id)).fetchone()[0]
    conn.commit()
    if failure_type:
        # The reason can quote the provider's answer, so it stays in extraction_attempts.error, not in the log.
        log.error("gap %s run %s attempt %s failed (%s)", gap.gap_id, run_id, attempt_id, failure_type)
    return {
        "attempt_id": attempt_id,
        "status": status,
        "model": result.model if result else EXTRACTION_SETTINGS.model,
        "duration_ms": duration,
        "prompt_tokens": result.prompt_tokens if result else None,
        "output_tokens": result.output_tokens if result else None,
    }, [{**k, "attempt_id": attempt_id} for k in kept], dropped


def _complete(client: OpenRouterClient, p: Prompt) -> ModelResult:
    """client.complete with its time, tokens and outcome recorded; any exception is re-raised unchanged."""
    model = EXTRACTION_SETTINGS.model
    started = time.perf_counter()
    try:
        result = client.complete(p)
    except Exception as exc:
        metrics.AI_CALLS.labels(model, metrics.ai_outcome(exc)).inc()
        raise
    finally:
        metrics.AI_CALL_SECONDS.labels(model).observe(time.perf_counter() - started)
    metrics.AI_CALLS.labels(model, "ok").inc()
    if result.prompt_tokens is not None:
        metrics.AI_TOKENS.labels(model, "prompt").inc(result.prompt_tokens)
    if result.output_tokens is not None:
        metrics.AI_TOKENS.labels(model, "output").inc(result.output_tokens)
    return result


def _already_proposed(conn: psycopg.Connection, gap_id: str, document_id: str, page_no: int, start: int,
                      end: int) -> str | None:
    """The evidence_id of an earlier proposal with exactly this quote on this gap, if there is one."""
    row = conn.execute(
        """SELECT evidence_id::text FROM evidence_proposal_detail WHERE gap_id = %s::uuid
             AND document_reference_id = %s AND page_no = %s AND char_start = %s AND char_end = %s""",
        (gap_id, document_id, page_no, start, end)).fetchone()
    return row[0] if row else None


def _diagnostic_raw(exc: Exception | None, result: ModelResult | None) -> str | None:
    """Raw provider/model content is optional and stays in protected DB diagnostics only."""
    if not EXTRACTION_SETTINGS.store_raw_attempt_response:
        return None
    if exc is not None:
        return getattr(exc, "raw", None) or (result.text if result else None)
    return result.text if result else None


def _error_code(exc: ModelError | UnusableAnswer) -> str:
    """A safe error code for API-visible call records; never include provider/body text here."""
    if isinstance(exc, ModelError):
        prefix = "model_transient" if exc.transient else "model_permanent"
        return f"{prefix}:{exc.code}"
    return "answer:unusable"


def _finish_run(conn: psycopg.Connection, run_id: int, gap_id: str, *, correlation_id: str, kept: int, dropped: int,
                failed: int,
                calls: int) -> str:
    """Close the run: failed when every call failed, otherwise completed; one audit event."""
    status = "failed" if calls and failed == calls else "completed"
    conn.execute("UPDATE extraction_runs SET status = %s, finished_at = now() WHERE id = %s", (status, run_id))
    audit(conn, "extraction_run", str(run_id), f"extraction.{status}",
          {"run_id": run_id, "gap_id": gap_id, "correlation_id": correlation_id, "kept": kept, "dropped": dropped,
           "failed": failed},
          actor=SETTINGS.actor)
    conn.commit()
    return status


def _save_proposed(conn: psycopg.Connection, gap_id: uuid.UUID, kept: list[dict], *, correlation_id: str) -> tuple[int, int]:
    """Store kept findings as proposed evidence (the demo page's save), skipping quotes already proposed."""
    saved = skipped = 0
    for c in kept:
        exists = conn.execute(
            """SELECT 1 FROM evidence_proposal_detail WHERE gap_id = %s AND document_reference_id = %s
                 AND page_no = %s AND char_start = %s AND char_end = %s""",
            (gap_id, c["document_id"], c["page_no"], c["char_start"], c["char_end"])).fetchone()
        if exists:
            skipped += 1
            continue
        evidence_id = uuid.uuid4()
        conn.execute(
            """INSERT INTO evidence_proposal (evidence_id, gap_id, source_document_reference_id, passage_citation,
                   proposed_finding, confidence_score, extraction_model_version, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed')""",
            (evidence_id, gap_id, c["document_id"], c["excerpt"],
             f"{c['evidence_kind']} {c['evidence_type']} {c['service_date_raw']} {c['value']}".strip(),
             c["confidence"], c["model"]))
        conn.execute(
            """INSERT INTO evidence_proposal_detail (evidence_id, gap_id, document_reference_id, attempt_id, page_no,
                   char_start, char_end, evidence_kind, evidence_type, service_date_raw, service_date_from,
                   service_date_to, value, reason, prompt_version, handwritten, low_confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (evidence_id, gap_id, c["document_id"], c.get("attempt_id"), c["page_no"], c["char_start"], c["char_end"],
             c["evidence_kind"], c["evidence_type"], c["service_date_raw"], c["service_date_from"],
             c["service_date_to"], c["value"], parts.tag_reason(c["reason"], c.get("part", "")), c["prompt_version"],
             c["handwritten"], c["low_confidence"]))
        audit(conn, "evidence", str(evidence_id), "evidence.proposed",
              {"gap_id": str(gap_id), "correlation_id": correlation_id}, actor=SETTINGS.actor)
        saved += 1
    _refresh_gap(conn, gap_id)
    return saved, skipped


def decide(conn: psycopg.Connection, evidence_id: str, decision: str, reason: str | None, reviewer: str) -> dict:
    """Record a confirm or reject, update the proposal and its gap, then re-score.

    reviewer is the signed-in user's email, never a value from the request body. Raises LookupError for an unknown
    proposal, ValueError for a bad decision, a reject without a reason or an unusable reviewer, and Conflict when the
    proposal was already decided."""
    status = DECISIONS.get(decision)
    if status is None:
        raise ValueError("decision must be 'confirm' or 'reject'")
    reason = (reason or "").strip() or None
    if status == "rejected" and not reason:
        raise ValueError("a reject needs a reason")
    try:
        evidence_uuid = uuid.UUID(evidence_id)
    except ValueError as exc:
        raise LookupError(f"evidence {evidence_id} not found") from exc
    row = conn.execute("SELECT gap_id, status FROM evidence_proposal WHERE evidence_id = %s FOR UPDATE",
                       (evidence_uuid,)).fetchone()
    if row is None:
        raise LookupError(f"evidence {evidence_id} not found")
    gap_id, current = row
    if current != "proposed":
        raise Conflict(f"evidence {evidence_id} is already {current}")
    if not (reviewer or "").strip():
        raise ValueError("a decision needs the signed-in reviewer")
    reviewer = clean_reviewer(reviewer)
    correlation_id = _evidence_correlation_id(conn, evidence_uuid)
    decision_id = conn.execute(
        """INSERT INTO review_decisions (evidence_id, reviewer, decision, reject_reason_code, reason)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (evidence_uuid, reviewer, status, "other" if status == "rejected" else None, reason)).fetchone()[0]
    conn.execute("UPDATE evidence_proposal SET status = %s, reviewer_id = %s, decided_at = now() WHERE evidence_id = %s",
                 (status, reviewer, evidence_uuid))
    supplemental_id = _record_supplemental(conn, evidence_uuid, decision_id, reviewer) if status == "confirmed" else None
    review_status = _refresh_gap(conn, gap_id)
    audit(conn, "evidence", evidence_id, f"evidence.{status}",
          {"gap_id": str(gap_id), "review_status": review_status, "correlation_id": correlation_id}, actor=reviewer)
    conn.commit()
    metrics.AI_REVIEW_DECISIONS.labels(_DECISION_METRIC[status]).inc()
    summary = score_all(conn, date.today(), load_rules())
    conn.commit()
    scenario_id = _star_scenario(conn, gap_id, reviewer)
    return {"evidence_id": evidence_id, "status": status, "gap_id": str(gap_id), "review_status": review_status,
            "tiers": summary.tiers, "decision_id": decision_id, "supplemental_record_id": supplemental_id,
            "star_scenario_id": scenario_id, "correlation_id": correlation_id}


# Review states that mean every required part is confirmed, and the final state each one closes as.
_CLOSE_AS = {"closed_pending_engine": "closed", "excluded_pending_engine": "excluded"}


def close_gap(conn: psycopg.Connection, gap_id: str, actor_user_id: int, actor: str) -> dict:
    """Close a gap whose required evidence is confirmed, without waiting for the next engine run, then re-score.

    actor is the signed-in user. Raises LookupError for an unknown gap and Conflict when the gap is already closed or
    its evidence is not complete. A later engine run leaves the closed gap alone (it only closes open gaps)."""
    try:
        gap_uuid = uuid.UUID(gap_id)
    except ValueError as exc:
        raise LookupError(f"gap {gap_id} not found") from exc
    gap = conn.execute("SELECT status FROM care_gap WHERE gap_id = %s FOR UPDATE", (gap_uuid,)).fetchone()
    if gap is None:
        raise LookupError(f"gap {gap_id} not found")
    if gap[0] == "closed":
        raise Conflict("This gap is already closed")
    detail = conn.execute("SELECT review_status FROM care_gap_detail WHERE gap_id = %s FOR UPDATE",
                          (gap_uuid,)).fetchone()
    before = detail[0] if detail else None
    closed_as = _CLOSE_AS.get(before or "")
    if closed_as is None:
        raise Conflict("Confirm the evidence for every required part before closing this gap")
    conn.execute(
        "UPDATE care_gap SET status = 'closed', closed_at = now(), closed_by = %s WHERE gap_id = %s",
        (actor_user_id, gap_uuid),
    )
    conn.execute("UPDATE care_gap_detail SET review_status = %s, updated_at = now() WHERE gap_id = %s",
                 (closed_as, gap_uuid))
    audit(conn, "care_gap", str(gap_uuid), "gap.closed", {"review_status_before": before, "closed_as": closed_as},
          actor=actor)
    conn.commit()
    score_all(conn, date.today(), load_rules())
    conn.commit()
    _star_scenario(conn, gap_uuid, actor)
    return {"gap_id": str(gap_uuid), "status": "closed", "review_status": closed_as}


def clean_reviewer(reviewer: str | None) -> str:
    """The signed-in user's email, or the configured reviewer when none is given; raises ValueError for an unusable ID."""
    reviewer = (reviewer or "").strip() or SETTINGS.reviewer
    if len(reviewer) > 64 or not all(ch.isalnum() or ch in "._-@" for ch in reviewer):
        raise ValueError("reviewer ID may use letters, digits and . _ - @ only, up to 64 characters")
    return reviewer


def _evidence_correlation_id(conn: psycopg.Connection, evidence_id: uuid.UUID) -> str | None:
    """The extraction correlation ID for this proposal, if the proposal came from an extraction attempt."""
    row = conn.execute(
        """SELECT a.correlation_id
             FROM evidence_proposal_detail d
             JOIN extraction_attempts a ON a.id = d.attempt_id
            WHERE d.evidence_id = %s
            LIMIT 1""",
        (evidence_id,),
    ).fetchone()
    return row[0] if row else None


def _record_supplemental(conn: psycopg.Connection, evidence_id: uuid.UUID, decision_id: int, reviewer: str) -> int:
    """The confirmed finding as supplemental data for the engine, in the decision's transaction."""
    row = conn.execute(
        """INSERT INTO supplemental_records (evidence_id, decision_id, patient_id, measure_id, measurement_year,
               evidence_kind, evidence_type, service_date_from, service_date_to, value, document_reference_id, page_no)
           SELECT x.evidence_id, %s, g.patient_id, g.measure_id, g.measurement_year, x.evidence_kind, x.evidence_type,
                  x.service_date_from, x.service_date_to, x.value, x.document_reference_id, x.page_no
             FROM evidence_proposal_detail x JOIN care_gap g ON g.gap_id = x.gap_id
            WHERE x.evidence_id = %s
        RETURNING id""", (decision_id, evidence_id)).fetchone()
    if row is None:
        raise RuntimeError(f"evidence {evidence_id} has no detail row, so no supplemental record can be written")
    audit(conn, "supplemental_record", str(row[0]), "supplemental.recorded",
          {"evidence_id": str(evidence_id), "decision_id": decision_id}, actor=reviewer)
    return row[0]


def _star_scenario(conn: psycopg.Connection, gap_id: uuid.UUID, reviewer: str) -> str | None:
    """Snapshot of the gap's measure after the decision, from the star what-if (stars.projection).

    Low: the engine accepts none of the confirmed evidence (the baseline). High: it accepts all of it."""
    measure_id, year = conn.execute("SELECT measure_id, measurement_year FROM care_gap WHERE gap_id = %s",
                                    (gap_id,)).fetchone()
    conn.rollback()
    with stars.session() as s:
        projected = stars.projection(s, measure_id, year)
    base = projected["baseline"]
    if None in (base["sample_star_rating"], projected["projected_stars"], base["performance_rate"]):
        log.info("star scenario skipped for %s: no star rating available", measure_id)
        return None
    scenario_id = uuid.uuid4()
    conn.execute(
        """INSERT INTO star_scenario (scenario_id, measure_id, measurement_year, baseline_rate_pct, gaps_closed_assumed,
               projected_rate_low_pct, projected_rate_high_pct, projected_star_rating_low, projected_star_rating_high,
               created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (scenario_id, measure_id, year, base["performance_rate"], projected["closures_selected"],
         base["performance_rate"], projected["projected_rate"], base["sample_star_rating"],
         projected["projected_stars"], reviewer))
    conn.commit()
    return str(scenario_id)


def _refresh_gap(conn: psycopg.Connection, gap_id: uuid.UUID) -> str:
    """Pending count and review status from the gap's proposals and its rule's parts (parts.py): a gap waits for the
    engine only when every required part is confirmed, and is partially_evidenced before that. A complete exclusion
    wins over a complete closure; states the engine set (closed, excluded) stay."""
    current = conn.execute("SELECT review_status FROM care_gap_detail WHERE gap_id = %s", (gap_id,)).fetchone()[0]
    rule, age, items = parts.load(conn, str(gap_id))
    status = parts.review_status(current, parts.check(rule, age, items))
    return conn.execute(
        """UPDATE care_gap_detail d
              SET pending_count = (SELECT count(*) FROM evidence_proposal p
                                    WHERE p.gap_id = d.gap_id AND p.status = 'proposed'),
                  review_status = %(status)s, updated_at = now()
            WHERE d.gap_id = %(gap)s
        RETURNING review_status""", {"gap": gap_id, "status": status}).fetchone()[0]
