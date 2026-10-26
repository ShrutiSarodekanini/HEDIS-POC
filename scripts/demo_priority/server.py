"""Local demo page: gap prioritization (US-05) and AI evidence extraction (US-03) on one page.

    python scripts/demo_priority/server.py      then open http://localhost:8771

Shows the star position of every measure, the ranked work list per role, and for any gap exactly how its three
scores were built. "Run scoring" appends a new scoring run. "Find evidence with AI" runs the real prompt builder,
OpenRouter client and validator for one gap and shows the exact request and response; nothing is saved until
"Save as proposed evidence" stores the kept findings (a simplified stand-in for the US-03 runner) and re-scores.

Uses the database in the repo-root .env and its OPENROUTER_API_KEY / EXTRACTION_MODEL. Synthetic data only.
The work list carries seed codes, measures and scores only; the evidence panel shows document text, which is
synthetic in this POC. Listens on 127.0.0.1 only.
"""

import json
import math
import sys
import time
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.document_intake import db  # noqa: E402
from services.document_intake.db import audit  # noqa: E402
from services.evidence_extraction import prompt  # noqa: E402
from services.evidence_extraction.contracts import ModelError, UnusableAnswer  # noqa: E402
from services.evidence_extraction.model_client import OpenRouterClient  # noqa: E402
from services.evidence_extraction.validator import parse_findings, validate  # noqa: E402
from services.gap_priority import scoring  # noqa: E402
from services.gap_priority.config import load_rules  # noqa: E402
from services.gap_priority.inputs import read_inputs  # noqa: E402
from services.gap_priority.runner import score_all  # noqa: E402
from common.evidence import generate_document, prepare_evidence_data  # noqa: E402
from services.star_modeler.main import CMS_RATING_YEAR, assign_star_from_cutpoints  # noqa: E402

PORT = 8771
PAGE = Path(__file__).with_name("index.html")
ROLES = {
    "reviewer": ("abstractor_review",),
    "outreach": ("member_outreach_call", "mailed_fit_kit"),
    "doctor": ("point_of_care_alert", "provider_gap_list", "chart_review"),
}
app = FastAPI(title="HEDIS gap prioritization demo")


class RunIn(BaseModel):
    as_of: date


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _rounded(numerator: int, denominator: int) -> int:
    return (200 * numerator + denominator) // (2 * denominator) if denominator else 0


def _rate_pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100 * numerator / denominator, 2)


def _star_result(
    measure_id: str,
    measurement_year: int,
    numerator: int,
    denominator: int,
    cut_points: list[tuple[int, float]],
):
    rate = _rate_pct(numerator, denominator)
    if rate is None:
        return None
    return assign_star_from_cutpoints(
        measure_code=measure_id,
        measurement_year=measurement_year,
        scoring_value=rate,
        denominator=denominator,
        cut_points=cut_points,
    )


def _next_cut_for_star(cut_points: list[tuple[int, float]], current_star: int | None) -> float | None:
    if current_star is None:
        return None
    next_star = current_star + 1
    for stars, threshold in cut_points:
        if stars == next_star:
            return float(threshold)
    return None


def _generate_gap_evidence_document(conn, gap_id: str) -> dict | None:
    row = conn.execute(
        """SELECT p.mrn, p.birth_date, p.gender, g.gap_id, g.status, g.measurement_year, g.missing_element,
                     g.opened_at::date, g.measure_id
              FROM care_gap g
              JOIN patient p ON p.patient_id = g.patient_id
             WHERE g.gap_id = %s""",
        (gap_id,),
    ).fetchone()
    if not row:
        return None

    member = {
        "member_id": row[0],
        "date_of_birth": row[1],
        "gender": row[2],
    }
    care_gap = {
        "care_gap_id": str(row[3]),
        "description": row[6],
        "status": row[4],
        "date_identified": row[7],
        "measurement_year": row[5],
    }
    measure = {
        "measure_id": row[8],
        "name": row[8],
        "description": "HEDIS measure",
    }

    evidence_rows = conn.execute(
        """SELECT status, proposed_finding, passage_citation, extraction_model_version, decided_at
               FROM evidence_proposal
              WHERE gap_id = %s
              ORDER BY decided_at DESC NULLS LAST
              LIMIT 8""",
        (gap_id,),
    ).fetchall()
    supporting_rows = [
        {
            "source_type": "EvidenceProposal",
            "source_date": row_[4] or date.today().isoformat(),
            "fact_key": row_[0] or "proposed",
            "fact_value": row_[1] or row_[2] or "No finding text",
            "provenance": row_[3] or "demo_priority",
        }
        for row_ in evidence_rows
    ]

    data = prepare_evidence_data(member, care_gap, measure, supporting_rows)
    out_dir = ROOT / "generated_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"EVID-{str(gap_id).replace('-', '')[:12].upper()}-{int(time.time())}.pdf"
    destination = out_dir / file_name
    generate_document("pdf", destination, data)
    audit(conn, "evidence", str(gap_id), "evidence.document_generated", {"file_name": file_name}, actor="demo_priority")
    return {"file_name": file_name, "file_path": str(destination.resolve())}


@app.get("/api/summary")
def summary(as_of: date | None = None) -> dict:
    rules = load_rules()
    as_of = as_of or date.today()
    with db.connect() as conn:
        _, measures = read_inputs(conn, as_of, rules.star_year)
        last = conn.execute("SELECT max(scored_at) FROM gap_priority_score").fetchone()[0]
        tiers = dict(conn.execute("SELECT priority_tier, count(*) FROM v_gap_priority_latest GROUP BY 1").fetchall())
        open_gaps = dict(conn.execute(
            "SELECT measure_id, count(*) FROM care_gap WHERE status = 'open' GROUP BY 1").fetchall())
        conn.rollback()
    out = []
    for (measure_id, _year), m in sorted(measures.items()):
        value = scoring.measure_value(m, rules)
        rounded = _rounded(m.numerator, m.denominator)
        rate = _rate_pct(m.numerator, m.denominator)
        cut_points = [(int(star), float(low)) for star, low in m.cut_points]
        star_result = _star_result(measure_id, m.measurement_year, m.numerator, m.denominator, cut_points)
        stars = star_result.assigned_star_rating if star_result and star_result.rating_status == "assigned" else None
        next_cut = _next_cut_for_star(cut_points, stars)
        out.append({"measure": measure_id, "weight": m.weight, "numerator": m.numerator, "denominator": m.denominator,
                    "rate": rate,
                    "rounded": rounded, "stars": stars, "next_cut": next_cut,
                    "star_status": star_result.rating_status if star_result else "unavailable",
                    "cms_rating_year": CMS_RATING_YEAR,
                    "value": round(value.value, 4), "value_reason": value.reason,
                    "open_gaps": open_gaps.get(measure_id, 0)})
    return {"measures": out, "tiers": tiers, "last_scored_at": last, "rules_version": rules.version}


@app.get("/api/gaps")
def gaps(tier: str | None = None, measure: str | None = None, role: str | None = None, limit: int = 400) -> list:
    channels = list(ROLES.get(role, ())) or None
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT coalesce(d.seed_code, s.gap_id::text), g.measure_id, s.priority_tier, s.composite_score,
                      s.closure_likelihood_score, s.financial_value_score, s.engagement_score, s.recommended_channel,
                      r.reasons, r.action_reason
               FROM v_gap_priority_latest s
               JOIN care_gap g ON g.gap_id = s.gap_id
               LEFT JOIN care_gap_detail d ON d.gap_id = s.gap_id
               LEFT JOIN gap_priority_reason r ON r.score_pk = s.score_pk
               WHERE g.status = 'open' AND (%(tier)s::text IS NULL OR s.priority_tier = %(tier)s::text)
                 AND (%(measure)s::text IS NULL OR g.measure_id = %(measure)s::text)
                 AND (%(channels)s::text[] IS NULL OR s.recommended_channel = ANY(%(channels)s::text[]))
               ORDER BY array_position(ARRAY['high','medium','low'], s.priority_tier), s.composite_score DESC,
                        s.closure_likelihood_score DESC, 1
               LIMIT %(limit)s""",
            {"tier": tier, "measure": measure, "channels": channels, "limit": limit}).fetchall()
        conn.rollback()
    keys = ("key", "measure", "tier", "composite", "likelihood", "value", "engagement", "channel", "reasons", "action")
    return [dict(zip(keys, r)) for r in rows]


@app.get("/api/gaps/{key}/breakdown")
def breakdown(key: str, as_of: date | None = None) -> dict:
    """Recompute one gap's scores step by step from the same inputs and rules the scoring run uses."""
    rules = load_rules()
    as_of = as_of or date.today()
    with db.connect() as conn:
        gap_list, measures = read_inputs(conn, as_of, rules.star_year)
        conn.rollback()
    gap = next((g for g in gap_list if g.key == key), None)
    if gap is None:
        raise HTTPException(404, f"open gap {key} not found")
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
    raw = sum(x["change"] for x in steps)
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

    m = measures[(gap.measure_id, gap.measurement_year)]
    value = scoring.measure_value(m, rules)
    rounded = _rounded(m.numerator, m.denominator)
    rate = _rate_pct(m.numerator, m.denominator)
    cut_points = [(int(star), float(low)) for star, low in m.cut_points]
    star_result = _star_result(gap.measure_id, gap.measurement_year, m.numerator, m.denominator, cut_points)
    stars = star_result.assigned_star_rating if star_result and star_result.rating_status == "assigned" else None
    next_cut = _next_cut_for_star(cut_points, stars)
    target = math.ceil((next_cut / 100) * m.denominator) if next_cut is not None else None
    needed = max((target - m.numerator), 0) if target is not None else None
    return {
        "key": gap.key, "measure": gap.measure_id, "as_of": as_of,
        "likelihood": {"steps": steps, "raw": round(raw, 4), "min": rules.likelihood_min, "max": rules.likelihood_max,
                       "result": round(likelihood, 4)},
        "engagement": {"steps": eng_steps, "cap": rules.engagement_cap,
                       "result": round(scoring.engagement(gap, as_of, rules), 4)},
        "value": {"weight": m.weight, "numerator": m.numerator, "denominator": m.denominator,
                  "rate": rate,
                  "rounded": rounded, "stars": stars, "next_cut": next_cut, "target": target,
                  "needed": needed, "divisor": rules.value_divisor,
                  "cms_rating_year": CMS_RATING_YEAR,
                  "star_status": star_result.rating_status if star_result else "unavailable",
                  "reason": value.reason, "result": round(value.value, 4), "non_star_value": rules.non_star_value},
        "composite": round(likelihood * value.value, 4),
        "channel": scoring.channel(gap, as_of, rules),
    }


# ---------------------------------------------------------------- AI evidence extraction for one gap (demo only)

_LAST_RUN: dict[str, dict] = {}   # the last extraction per gap, so "save" stores exactly what was shown


class _Capture(httpx.BaseTransport):
    """Records the exact request and response, with the API key hidden."""

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


@app.post("/api/gaps/{key}/extract")
def extract(key: str) -> dict:
    """Run the real prompt builder, model client and validator for one gap. Nothing is saved here."""
    with db.connect() as conn:
        try:
            gap = prompt.load_gap_context(conn, key)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        measure = prompt.load_measure(conn, gap.measure_code, gap.measurement_year)
        pages = prompt.select_pages(conn, gap)
        conn.rollback()
    page_list = [{"document_id": p.document_id, "page_no": p.page_no, "doc_type": p.doc_type,
                  "document_date": p.document_date, "method": p.method, "handwritten": p.handwritten,
                  "text": p.text} for p in pages]
    if not pages:
        _LAST_RUN.pop(key, None)
        return {"pages": [], "calls": [], "kept": [], "dropped": [],
                "note": "No documents for this member: recorded as no evidence, no model call."}
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
    _LAST_RUN[key] = {"gap_id": gap.gap_id, "kept": kept}
    return {"pages": page_list, "calls": calls, "kept": kept, "dropped": dropped, "note": None}


class SaveIn(BaseModel):
    as_of: date


@app.post("/api/gaps/{key}/save")
def save(key: str, body: SaveIn) -> dict:
    """Store the last kept findings as proposed evidence (simplified runner, demo only), then re-score all gaps."""
    run_ = _LAST_RUN.get(key)
    if not run_ or not run_["kept"]:
        raise HTTPException(409, "Nothing to save: run 'Find evidence with AI' first and keep at least one finding.")
    saved = skipped = 0
    generated_document = None
    generated_document_error = None
    with db.connect() as conn:
        for c in run_["kept"]:
            exists = conn.execute(
                """SELECT 1 FROM evidence_proposal_detail WHERE gap_id = %s AND document_reference_id = %s
                     AND page_no = %s AND char_start = %s AND char_end = %s""",
                (run_["gap_id"], c["document_id"], c["page_no"], c["char_start"], c["char_end"])).fetchone()
            if exists:
                skipped += 1
                continue
            evidence_id = uuid.uuid4()
            conn.execute(
                """INSERT INTO evidence_proposal (evidence_id, gap_id, source_document_reference_id, passage_citation,
                       proposed_finding, confidence_score, extraction_model_version, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed')""",
                (evidence_id, run_["gap_id"], c["document_id"], c["excerpt"],
                 f"{c['evidence_kind']} {c['evidence_type']} {c['service_date_raw']} {c['value']}".strip(),
                 c["confidence"], c["model"]))
            conn.execute(
                """INSERT INTO evidence_proposal_detail (evidence_id, gap_id, document_reference_id, page_no,
                       char_start, char_end, evidence_kind, evidence_type, service_date_raw, service_date_from,
                       service_date_to, value, reason, prompt_version, handwritten, low_confidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (evidence_id, run_["gap_id"], c["document_id"], c["page_no"], c["char_start"], c["char_end"],
                 c["evidence_kind"], c["evidence_type"], c["service_date_raw"], c["service_date_from"],
                 c["service_date_to"], c["value"], c["reason"], c["prompt_version"], c["handwritten"],
                 c["low_confidence"]))
            audit(conn, "evidence", str(evidence_id), "evidence.proposed", {"gap_id": str(run_["gap_id"])},
                  actor="demo_priority")
            saved += 1
        conn.execute(
            """UPDATE care_gap_detail d SET pending_count = (SELECT count(*) FROM evidence_proposal p
                      WHERE p.gap_id = d.gap_id AND p.status = 'proposed'),
                   review_status = CASE WHEN review_status = 'open' THEN 'pending_review' ELSE review_status END,
                   updated_at = now()
               WHERE d.gap_id = %s""", (run_["gap_id"],))
        conn.commit()
        try:
            generated_document = _generate_gap_evidence_document(conn, str(run_["gap_id"]))
            if generated_document is not None:
                conn.commit()
        except Exception as exc:  # pragma: no cover - best effort side effect
            conn.rollback()
            generated_document_error = type(exc).__name__
        result = score_all(conn, body.as_of, load_rules())
    return {
        "saved": saved,
        "skipped_already_saved": skipped,
        "tiers": result.tiers,
        "generated_document": generated_document,
        "generated_document_error": generated_document_error,
    }


@app.post("/api/score")
def run(body: RunIn) -> dict:
    rules = load_rules()
    with db.connect() as conn:
        result = score_all(conn, body.as_of, rules)
    return {"gaps_scored": result.gaps_scored, "tiers": result.tiers, "channels": result.channels,
            "scored_at": result.scored_at, "rules_version": result.rules_version}


if __name__ == "__main__":
    print(f"Gap prioritization demo on http://localhost:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
