"""Write side of the integration API: AI evidence for a gap, a reviewer's decision, and a re-score after each.

Extraction runs the same code path as the demo page's "Find evidence with AI" (prompt builder, OpenRouter client,
validator) and stores every kept finding as proposed evidence the way its "Save" does. Only synthetic data is sent
to OpenRouter. The model only proposes: a person confirms or rejects, and the database triggers enforce that a
proposal changes status once, with a matching review decision.
"""

import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import date

import httpx
import psycopg

from services.document_intake.db import audit
from services.evidence_extraction import prompt
from services.evidence_extraction.contracts import ModelError, UnusableAnswer
from services.evidence_extraction.model_client import OpenRouterClient
from services.evidence_extraction.validator import parse_findings, validate
from services.gap_priority.config import load_rules
from services.gap_priority.runner import score_all

from .config import SETTINGS

log = logging.getLogger(__name__)

DECISIONS = {"confirm": "confirmed", "reject": "rejected"}


class Conflict(ValueError):
    """The request is valid but the record is in a state that does not allow it."""


class _Capture(httpx.BaseTransport):
    """Records each request and response for the review screen, with the API key hidden (as on the demo page)."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.exchanges: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {k: ("Bearer [hidden]" if k.lower() == "authorization" else v) for k, v in request.headers.items()}
        started = time.perf_counter()
        response = self.inner.handle_request(request)
        content = response.read()
        try:
            body = json.loads(content)
        except json.JSONDecodeError:
            body = content.decode("utf-8", errors="replace")
        self.exchanges.append({"url": str(request.url), "headers": headers, "request": json.loads(request.content),
                               "status": response.status_code, "seconds": round(time.perf_counter() - started, 2),
                               "response": body})
        kept = [(k, v) for k, v in response.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(response.status_code, headers=kept, content=content, request=request)


def find_evidence(conn: psycopg.Connection, key: str) -> dict:
    """Run the extraction for one gap, save kept findings as proposed, then re-score. Raises LookupError."""
    gap = prompt.load_gap_context(conn, key)
    measure = prompt.load_measure(conn, gap.measure_code, gap.measurement_year)
    pages = prompt.select_pages(conn, gap)
    conn.rollback()
    page_list = [{"document_id": p.document_id, "page_no": p.page_no, "doc_type": p.doc_type,
                  "document_date": p.document_date, "method": p.method, "handwritten": p.handwritten}
                 for p in pages]
    if not pages:
        return {"pages": [], "calls": [], "kept": [], "dropped": [], "saved": 0, "skipped_already_saved": 0,
                "note": "No converted documents for this patient yet: upload one first. No model call was made."}
    capture = _Capture()
    client = OpenRouterClient(transport=capture)
    calls, kept, dropped = [], [], []
    for p in prompt.build_prompts(gap, measure, pages):
        try:
            result = client.complete(p)
            findings = parse_findings(result.text)
        except (ModelError, UnusableAnswer) as exc:
            calls.append({**(capture.exchanges[-1] if capture.exchanges else {}), "error": str(exc)})
            continue
        calls.append({**capture.exchanges[-1], "answer": result.text, "model": result.model})
        checked = validate(gap, measure, list(p.pages), findings)
        kept += [{**asdict(c), "model": result.model, "prompt_version": p.prompt_version} for c in checked.kept]
        dropped += [{"evidence_type": d.finding.evidence_type, "document_id": d.finding.document_id,
                     "excerpt": d.finding.excerpt, "reason": d.reason} for d in checked.dropped]
    saved, skipped = _save_proposed(conn, uuid.UUID(gap.gap_id), kept)
    conn.commit()
    tiers = score_all(conn, date.today(), load_rules()).tiers if saved else None
    conn.commit()
    log.info("gap %s: %d calls, %d kept, %d dropped, %d saved", gap.gap_id, len(calls), len(kept), len(dropped), saved)
    return {"pages": page_list, "calls": calls, "kept": kept, "dropped": dropped, "saved": saved,
            "skipped_already_saved": skipped, "tiers": tiers, "note": None}


def _save_proposed(conn: psycopg.Connection, gap_id: uuid.UUID, kept: list[dict]) -> tuple[int, int]:
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
            """INSERT INTO evidence_proposal_detail (evidence_id, gap_id, document_reference_id, page_no, char_start,
                   char_end, evidence_kind, evidence_type, service_date_raw, service_date_from, service_date_to, value,
                   reason, prompt_version, handwritten, low_confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (evidence_id, gap_id, c["document_id"], c["page_no"], c["char_start"], c["char_end"], c["evidence_kind"],
             c["evidence_type"], c["service_date_raw"], c["service_date_from"], c["service_date_to"], c["value"],
             c["reason"], c["prompt_version"], c["handwritten"], c["low_confidence"]))
        audit(conn, "evidence", str(evidence_id), "evidence.proposed", {"gap_id": str(gap_id)}, actor=SETTINGS.actor)
        saved += 1
    _refresh_gap(conn, gap_id)
    return saved, skipped


def decide(conn: psycopg.Connection, evidence_id: str, decision: str, reason: str | None,
           reviewer: str | None = None) -> dict:
    """Record a confirm or reject, update the proposal and its gap, then re-score.

    reviewer is the ID typed on the review screen; without one the configured reviewer is recorded (the POC has no
    sign-in yet). Raises LookupError for an unknown proposal, ValueError for a bad decision, a reject without a reason
    or an unusable reviewer ID, and Conflict when the proposal was already decided."""
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
    reviewer = (reviewer or "").strip() or SETTINGS.reviewer
    if len(reviewer) > 64 or not all(ch.isalnum() or ch in "._-@" for ch in reviewer):
        raise ValueError("reviewer ID may use letters, digits and . _ - @ only, up to 64 characters")
    conn.execute(
        """INSERT INTO review_decisions (evidence_id, reviewer, decision, reject_reason_code, reason)
           VALUES (%s, %s, %s, %s, %s)""",
        (evidence_uuid, reviewer, status, "other" if status == "rejected" else None, reason))
    conn.execute("UPDATE evidence_proposal SET status = %s, reviewer_id = %s, decided_at = now() WHERE evidence_id = %s",
                 (status, reviewer, evidence_uuid))
    review_status = _refresh_gap(conn, gap_id)
    audit(conn, "evidence", evidence_id, f"evidence.{status}",
          {"gap_id": str(gap_id), "review_status": review_status}, actor=reviewer)
    conn.commit()
    summary = score_all(conn, date.today(), load_rules())
    conn.commit()
    return {"evidence_id": evidence_id, "status": status, "gap_id": str(gap_id), "review_status": review_status,
            "tiers": summary.tiers}


def _refresh_gap(conn: psycopg.Connection, gap_id: uuid.UUID) -> str:
    """Pending count and review status from the gap's proposals. A confirmed exclusion waits for the engine as
    excluded_pending_engine and wins over a confirmed closure; states the engine set (closed, excluded) stay."""
    return conn.execute(
        """UPDATE care_gap_detail d
              SET pending_count = (SELECT count(*) FROM evidence_proposal p
                                    WHERE p.gap_id = d.gap_id AND p.status = 'proposed'),
                  review_status = CASE
                      WHEN review_status IN ('closed', 'excluded') THEN review_status
                      WHEN EXISTS (SELECT 1 FROM evidence_proposal p
                                     JOIN evidence_proposal_detail x ON x.evidence_id = p.evidence_id
                                    WHERE p.gap_id = d.gap_id AND p.status = 'confirmed'
                                      AND x.evidence_kind = 'exclusion') THEN 'excluded_pending_engine'
                      WHEN EXISTS (SELECT 1 FROM evidence_proposal p
                                     LEFT JOIN evidence_proposal_detail x ON x.evidence_id = p.evidence_id
                                    WHERE p.gap_id = d.gap_id AND p.status = 'confirmed'
                                      AND x.evidence_kind IS DISTINCT FROM 'exclusion') THEN 'closed_pending_engine'
                      WHEN EXISTS (SELECT 1 FROM evidence_proposal p
                                    WHERE p.gap_id = d.gap_id AND p.status = 'proposed') THEN 'pending_review'
                      WHEN review_status = 'pending_review' THEN 'open'
                      ELSE review_status END,
                  updated_at = now()
            WHERE d.gap_id = %(gap)s
        RETURNING review_status""", {"gap": gap_id}).fetchone()[0]
