"""Turns one gap into the prompts sent to the model: the gap's facts, its measure rule and the member's pages.

The header holds age and sex only, never the member's name, birth date, address or member ID. Pages are never
dropped or cut to fit the token budget; a large chart becomes several prompts that share the same header.
"""

import json
import math
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from services.evidence_extraction.config import SETTINGS
from services.evidence_extraction.contracts import GapContext, PageRef, Prompt

SYSTEM_PROMPT = """\
You find evidence in clinical documents for a HEDIS care gap. A trained person reviews everything you return.
Answer with JSON only, in this shape:
{"findings": [{"document_id": string, "page_no": integer, "excerpt": string,
  "evidence_kind": "numerator" or "exclusion", "evidence_type": string, "service_date": string,
  "value": string, "confidence": number, "reason": string}]}
Rules:
- Return only evidence about this patient. Ignore family history.
- Only completed care counts. Ignore planned, recommended, ordered, due or declined care.
- Approximate measurements do not count: ignore readings or results described as 'around', 'about' or 'usually',
  and summaries of several readings. This is about measured values only. A year alone is a valid date for a past
  procedure or test, for example 'Colonoscopy 2019'.
- "document_id" and "page_no" come from the label of the page you quote: [DOC D-101 | page 2 | ...] means
  "document_id": "D-101" and "page_no": 2.
- "excerpt" must be copied word for word from one page: only the single sentence that contains the evidence,
  never several sentences.
- "evidence_kind" is "numerator" for care that closes the gap, or "exclusion" for a reason to remove the member
  from the measure.
- "evidence_type" must be exactly one of the allowed values listed for that evidence_kind.
- "service_date" is when the care happened, as YYYY, YYYY-MM or YYYY-MM-DD. Use the date in the excerpt; if the
  excerpt has no date, use the document date from the page label.
- "value" is the result or reading as written, for example "132/78"; use "" when there is none.
- "confidence" is a number from 0 to 1. "reason" says in one sentence why the excerpt qualifies.
- If nothing qualifies, return {"findings": []}."""

#: Where each prompt_template placeholder comes from: ("gap", name) is worked out from the gap,
#: ("window", evidence_type, "from" or "to") is that end of the measure window for the evidence type,
#: ("threshold", key) is measures.thresholds[key].
PLACEHOLDER_SOURCES: dict[str, tuple[str, ...]] = {
    "measurement_year": ("gap", "measurement_year"),
    "prior_year": ("gap", "prior_year"),
    "window_end": ("gap", "window_end"),
    "last_event_date": ("gap", "last_event_date"),
    "window_start": ("window", "bronchitis_visit_intake_period", "from"),
    "colonoscopy_from": ("window", "colonoscopy", "from"),
    "ct_colonography_from": ("window", "ct_colonography", "from"),
    "stool_dna_from": ("window", "stool_dna_with_fit", "from"),
    "fit_from": ("window", "fobt_or_fit", "from"),
    "mammogram_from": ("window", "mammogram", "from"),
    "cytology_from": ("window", "cervical_cytology", "from"),
    "hrhpv_from": ("window", "hrhpv_test_age_30_plus", "from"),
    "eye_exam_from": ("window", "retinal_or_dilated_eye_exam", "from"),
    "negative_exam_from": ("window", "retinal_exam_negative_for_retinopathy_prior_year", "from"),
    "negative_exam_to": ("window", "retinal_exam_negative_for_retinopathy_prior_year", "to"),
    "ked_from": ("window", "egfr", "from"),
    "condition_from": ("window", "pregnancy_ivf_or_clomiphene", "from"),
    "hba1c_lt": ("threshold", "hba1c_lt"),
    "systolic_lt": ("threshold", "systolic_lt"),
    "diastolic_lt": ("threshold", "diastolic_lt"),
    "uacr_unit": ("threshold", "uacr_unit"),
    "albumin_creatinine_max_days_apart": ("threshold", "albumin_creatinine_max_days_apart"),
}

NO_NUMERATOR = "none: return exclusions only"
SEX_WORDS = {"M": "male", "F": "female"}


def load_gap_context(conn: psycopg.Connection, gap_id: str) -> GapContext:
    """The gap with the member's age and sex. The birth date is read only to work out the age and goes no further."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT g.gap_id, g.member_id, g.measure_code, g.measurement_year, g.gap_type, g.missing_element,
                      g.last_event, g.exclusion_check, g.episode, g.deadline, m.sex, m.birth_date
               FROM gaps g JOIN members m ON m.member_id = g.member_id
               WHERE g.gap_id = %s""",
            (gap_id,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"Gap {gap_id} not found")
    birth_date = row.pop("birth_date")
    # Every birthday falls on or before 31 December, so completed years on that day are year minus birth year.
    return GapContext(age=row["measurement_year"] - birth_date.year, **row)


def load_measure(conn: psycopg.Connection, code: str, measurement_year: int) -> dict[str, Any]:
    """The measure row; its windows, thresholds and allowed types drive both the prompt and the validator."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM measures WHERE code = %s AND measurement_year = %s", (code, measurement_year))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"Measure {code} for {measurement_year} not found")
    return row


def select_pages(conn: psycopg.Connection, gap: GapContext) -> list[PageRef]:
    """The member's readable pages, newest first. Near-duplicates are skipped: they only repeat evidence."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT p.document_id, p.page_no, d.doc_type, d.document_date, d.source_org_id, p.method,
                      p.handwritten, p.low_confidence, p.text
               FROM documents d JOIN document_pages p ON p.document_id = d.id
               WHERE d.member_id = %s AND d.conversion_status = 'converted' AND d.near_duplicate_of IS NULL
               ORDER BY d.document_date DESC NULLS LAST, d.id, p.page_no""",
            (gap.member_id,))
        return [PageRef(**row) for row in cur if row["text"].strip()]


def build_prompts(gap: GapContext, measure: dict[str, Any], pages: Sequence[PageRef],
                  token_budget: int = SETTINGS.token_budget) -> list[Prompt]:
    """One prompt per group of pages that fits the budget, in page order. A page is never cut or dropped: one larger
    than the budget gets a prompt of its own. No pages means nothing to ask, so no prompts."""
    header = build_header(gap, measure)
    groups: list[list[PageRef]] = []
    for page in pages:
        if groups and estimate_tokens(_user_text(header, [*groups[-1], page])) <= token_budget:
            groups[-1].append(page)
        else:
            groups.append([page])
    return [Prompt(system=SYSTEM_PROMPT, user=_user_text(header, group), pages=tuple(group)) for group in groups]


def build_header(gap: GapContext, measure: dict[str, Any]) -> str:
    """Everything before the pages. Age and sex only: no name, birth date, address or member ID."""
    last = gap.last_event
    lines = [
        f"Gap ID: {gap.gap_id}",
        f"Measure: {measure['code']} ({measure['name']})",
        f"Measurement year: {gap.measurement_year}",
        f"Member: age {gap.age}, {SEX_WORDS[gap.sex]}",
        f"Missing: {gap.missing_element}",
        f"Last known event: {last['value']} on {last['date']}" if last else "Last known event: none",
    ]
    if gap.exclusion_check and gap.exclusion_check.get("reason"):
        lines.append(f"Exclusion note: {gap.exclusion_check['reason']}")
    if gap.gap_type == "episode" and gap.episode:
        lines.append(f"Visit: {json.dumps(gap.episode, separators=(',', ':'))}")
    allowed = measure["allowed_evidence_types"]
    lines += [
        "",
        "Rule:",
        _render_rule(gap, measure),
        "",
        "Allowed evidence_type values:",
        f"numerator: {', '.join(allowed.get('numerator') or []) or NO_NUMERATOR}",
        f"exclusion: {', '.join(allowed.get('exclusion') or []) or 'none'}",
        "",
        "Pages:",
    ]
    return "\n".join(lines)


def placeholder_values(gap: GapContext, measure: dict[str, Any]) -> dict[str, Any]:
    """Values for the measure's prompt_placeholders. An unknown or missing one is a measure library error, so it
    fails loudly instead of sending the model a rule with a gap in it."""
    year = gap.measurement_year
    from_gap = {
        "measurement_year": year,
        "prior_year": year - 1,
        "window_end": f"{year}-12-31",
        "last_event_date": gap.last_event["date"] if gap.last_event else f"{year}-01-01",
    }
    windows = {w["evidence_type"]: w for w in measure["windows"]}
    thresholds = measure["thresholds"] or {}
    values: dict[str, Any] = {}
    for name in measure["prompt_placeholders"]:
        source = PLACEHOLDER_SOURCES.get(name)
        if source is None:
            raise ValueError(f"Unknown prompt placeholder {name!r} in measure {measure['code']}")
        kind, key, *end = source
        if kind == "gap":
            value = from_gap[key]
        elif kind == "window":
            value = windows.get(key, {}).get(end[0])
        else:
            value = thresholds.get(key)
        if value is None:
            raise ValueError(f"Measure {measure['code']} has no value for prompt placeholder {name!r}")
        values[name] = value
    return values


def page_label(page: PageRef) -> str:
    """The label the model quotes back as document_id and page_no; the flags warn it the text may be misread."""
    parts = [f"DOC {page.document_id}", f"page {page.page_no}", page.doc_type or "unknown type",
             page.document_date.isoformat() if page.document_date else "no date",
             page.source_org_id or "unknown source", page.method]
    if page.handwritten:
        parts.append("handwritten")
    if page.low_confidence:
        parts.append("low confidence")
    return "[" + " | ".join(parts) + "]"


def estimate_tokens(text: str) -> int:
    """About 4 characters per token: close enough to size prompts without a tokenizer for every model."""
    return math.ceil(len(text) / 4)


def _render_rule(gap: GapContext, measure: dict[str, Any]) -> str:
    """The measure's prompt_template with its placeholders filled."""
    values = placeholder_values(gap, measure)
    try:
        return measure["prompt_template"].format_map(values).strip()
    except KeyError as exc:
        raise ValueError(f"Measure {measure['code']}: prompt_template uses {{{exc.args[0]}}}, which is not listed "
                         "in prompt_placeholders") from exc


def _user_text(header: str, pages: Sequence[PageRef]) -> str:
    return header + "\n" + "\n\n".join(f"{page_label(page)}\n{page.text}" for page in pages)
