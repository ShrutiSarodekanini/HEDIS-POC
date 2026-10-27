"""Checks every finding the model returns against the gap, the measure row and the pages that were sent.

The model only proposes. A finding becomes a candidate only when its quote is really on a page that was sent and
its type, date and value satisfy the measure row; otherwise it is dropped with a plain-English reason that is stored
on the attempt. The rules are read from the measure dict (windows, thresholds, allowed types), never coded per
measure, so a measure library change needs no code change.
"""

import calendar
import json
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from services.evidence_extraction.config import SETTINGS
from services.evidence_extraction.contracts import (Candidate, Dropped, Finding, GapContext, PageRef, UnusableAnswer,
                                                    ValidationResult)

KINDS = ("numerator", "exclusion")
BLOOD_PRESSURE = "blood_pressure_reading"
GLYCEMIC_TYPES = ("hba1c", "gmi")
UACR_RATIO = "uacr_ratio"
URINE_ALBUMIN = "urine_albumin_quantitative"
URINE_CREATININE = "urine_creatinine"
HRHPV = "hrhpv_test_age_30_plus"
LONG_QUOTE_KEPT = "long quote kept"
DATE_FROM_DOCUMENT = "date taken from the document date"
DATE_ON_PAGE = "date found on the page, not in the quote"
PLAUSIBLE_SYSTOLIC = range(60, 301)
PLAUSIBLE_DIASTOLIC = range(30, 201)

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "vs", "approx", "no"}
_SENTENCE_END = re.compile(r"([A-Za-z0-9)%]+)\.\s+(?=[A-Z])")
_BP_PAIR = re.compile(r"(?<![\d/])(\d{2,3})\s*/\s*(\d{2,3})(?![\d/])")
# A hyphen before the number means it is the top of a range such as 4.0-5.6 %, not a result.
_PERCENT = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*%")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w)")
_ORDINAL = r"(?:st|nd|rd|th)?"


class _Drop(Exception):
    """Raised by a check with the reason the finding is dropped."""


# The model's reason is shown to reviewers only; it never decides whether a finding is kept.
MAX_REASON_CHARS = 800
PARTS = ("frailty", "advanced_illness")


# ---------------------------------------------------------------- parsing the answer

def parse_findings(text: str) -> list[Finding]:
    """Turn the model's JSON into Findings, forgiving small type slips (page "1", confidence 85) but nothing else:
    an answer that is not JSON with a findings list is a failed attempt, never 'no evidence'."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnusableAnswer(f"the answer is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise UnusableAnswer('the answer has no "findings" list')
    findings = []
    for number, item in enumerate(data["findings"], start=1):
        if not isinstance(item, dict):
            raise UnusableAnswer(f"finding {number} is not an object")
        findings.append(Finding(
            document_id=_as_text(item.get("document_id")),
            page_no=_as_page_no(item.get("page_no")),
            excerpt=_as_text(item.get("excerpt")),
            evidence_kind=_as_text(item.get("evidence_kind")),
            evidence_type=_as_text(item.get("evidence_type")),
            service_date=_as_text(item.get("service_date")),
            value=_as_text(item.get("value")),
            confidence=_as_confidence(item.get("confidence")),
            reason=_as_text(item.get("reason")).strip()[:MAX_REASON_CHARS],
            part=_as_part(item.get("part")),
        ))
    return findings


def _as_part(value: Any) -> str:
    """'frailty' or 'advanced_illness' (spaces, hyphens and case forgiven); anything else means no part."""
    part = re.sub(r"[\s-]+", "_", _as_text(value).strip().lower())
    return part if part in PARTS else ""


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_page_no(value: Any) -> int | None:
    """1, 1.0, "1" and "page 1" all mean page 1; anything else means the model did not say."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"\d+", value) if isinstance(value, str) else None
    return int(match[0]) if match else None


def _as_confidence(value: Any) -> float:
    """0 to 1; some models answer in percent, so 85 means 0.85. Missing counts as 0, which the checks then drop."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"\d+(?:\.\d+)?", _as_text(value))
        number = float(match[0]) if match else 0.0
    return number / 100 if number > 1 else number


# ---------------------------------------------------------------- validation

def validate(gap: GapContext, measure: dict[str, Any], pages: Sequence[PageRef], findings: Sequence[Finding],
             min_confidence: float = SETTINGS.min_confidence,
             max_excerpt_chars: int = SETTINGS.max_excerpt_chars) -> ValidationResult:
    """Keep or drop every finding. Checks run in a fixed order and the first failure is the reason given."""
    result = ValidationResult()
    accepted: list[tuple[Finding, Candidate]] = []
    seen: set[tuple[str, int, int, int]] = set()
    for finding in findings:
        try:
            candidate = _check(gap, measure, pages, finding, min_confidence, max_excerpt_chars)
        except _Drop as drop:
            result.dropped.append(Dropped(finding, str(drop)))
            continue
        key = (candidate.document_id, candidate.page_no, candidate.char_start, candidate.char_end)
        if key in seen:
            result.dropped.append(Dropped(finding, "same quote as an earlier finding"))
            continue
        seen.add(key)
        accepted.append((finding, candidate))

    unpaired = _unpaired_urine_tests([c for _, c in accepted], measure)
    for finding, candidate in accepted:
        if candidate in unpaired:
            result.dropped.append(Dropped(finding, unpaired[candidate]))
        else:
            result.kept.append(candidate)
    return result


def _check(gap: GapContext, measure: dict[str, Any], pages: Sequence[PageRef], finding: Finding,
           min_confidence: float, max_excerpt_chars: int) -> Candidate:
    """Checks a to j for one finding; raises _Drop at the first one that fails."""
    if finding.confidence < min_confidence:
        raise _Drop(f"the model's own confidence is {finding.confidence:.2f}")

    page, start, end = _find_quote(finding, pages)
    evidence_type = normalise_type(finding.evidence_type)
    start, end, warnings = _narrow(page.text, start, end, finding.value, evidence_type, max_excerpt_chars)
    excerpt = page.text[start:end]

    kind = finding.evidence_kind.strip().lower()
    _check_type(kind, evidence_type, measure)

    dates = parse_date(finding.service_date)
    from_document = dates is None and page.document_date is not None
    if from_document:
        dates = (page.document_date, page.document_date)
        warnings += (DATE_FROM_DOCUMENT,)
    _check_age(gap, evidence_type, dates)
    if "frailty" in evidence_type and not finding.part:
        raise _Drop(f"{evidence_type} needs two findings, and this one does not say whether it is the frailty or "
                    "the advanced illness part")
    if dates is None:
        raise _Drop(f"service date '{finding.service_date}' is not readable and the page has no document date")
    if not from_document:
        warnings += _check_date_supported(finding.service_date, dates, page, excerpt)

    _check_window(gap, measure, evidence_type, dates)
    _check_most_recent(gap, measure, kind, dates)
    _check_thresholds(measure, evidence_type, finding.value, excerpt)

    return Candidate(
        document_id=page.document_id, page_no=page.page_no, excerpt=excerpt, char_start=start, char_end=end,
        evidence_kind=kind, evidence_type=evidence_type, service_date_raw=finding.service_date,
        service_date_from=dates[0], service_date_to=dates[1], value=finding.value, confidence=finding.confidence,
        reason=finding.reason, handwritten=page.handwritten, low_confidence=page.low_confidence, warnings=warnings,
        part=finding.part if "frailty" in evidence_type else "")


def _find_quote(finding: Finding, pages: Sequence[PageRef]) -> tuple[PageRef, int, int]:
    """Checks b and c: the document and page were sent for this gap, and the quote is really on the page."""
    document_id = _normalise_document_id(finding.document_id)
    sent = [p for p in pages if p.document_id.upper() == document_id]
    if not sent:
        raise _Drop(f"document {finding.document_id or '(none)'} was not sent")
    if finding.page_no is not None:
        sent = [p for p in sent if p.page_no == finding.page_no]
        if not sent:
            raise _Drop(f"page {finding.page_no} of {document_id} was not sent")
    if not finding.excerpt.strip():
        raise _Drop("the quote is empty")
    for page in sent:
        offsets = locate_quote(page.text, finding.excerpt)
        if offsets is not None:
            return page, *offsets
    where = f"page {sent[0].page_no} of {document_id}" if len(sent) == 1 else f"any sent page of {document_id}"
    raise _Drop(f"the quote is not on {where}")


def _normalise_document_id(raw: str) -> str:
    """Models sometimes copy the label: 'DOC D-601', '[DOC D-601 | page 1', or write 'd-601'."""
    text = raw.split("|")[0].strip().strip("[]").strip()
    return re.sub(r"^doc\b[\s:#]*", "", text, flags=re.IGNORECASE).strip().upper()


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Offsets of the quote in the page text: an exact match first, then one ignoring case and runs of whitespace.
    The offsets always point into the original text, so text[start:end] is what the database stores."""
    quote = quote.strip()
    if not quote:
        return None
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    folded_text, index = _fold(text)
    folded_quote, _ = _fold(quote)
    start = folded_text.find(folded_quote)
    if start < 0:
        return None
    return index[start], index[start + len(folded_quote) - 1] + 1


def _fold(text: str) -> tuple[str, list[int]]:
    """Lower case with every run of whitespace as one space, plus the original index of each folded character."""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            if chars and chars[-1] == " ":
                continue
            lowered = " "
        else:
            lowered = ch.lower()
        chars.extend(lowered)
        index.extend([i] * len(lowered))
    return "".join(chars), index


def _narrow(text: str, start: int, end: int, value: str, evidence_type: str,
            max_chars: int) -> tuple[int, int, tuple[str, ...]]:
    """Check d: shorten a long quote to the one sentence holding the value or type. A correct finding is never
    dropped for a long quote; when no single sentence is clearly best the quote is kept with a warning."""
    quote = text[start:end]
    spans = _sentence_spans(quote)
    if len(quote) <= max_chars and len(spans) <= 1:
        return start, end, ()
    keys = [k.lower() for k in re.findall(r"\d+(?:[./]\d+)?|[A-Za-z]{4,}", f"{value} {evidence_type}")]
    scores = sorted(((sum(k in quote[s:e].lower() for k in keys), (s, e)) for s, e in spans), reverse=True)
    best_score, (s, e) = scores[0]
    clear = best_score > 0 and (len(scores) == 1 or best_score > scores[1][0])
    if not clear or (s, e) == (0, len(quote)):
        return start, end, (LONG_QUOTE_KEPT,)
    return start + s, start + e, ()


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence offsets, ignoring full stops after common abbreviations such as 'Dr.' and inside numbers."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match[1].lower() not in _ABBREVIATIONS:
            bounds.append((start, match.end(1) + 1))
            start = match.end()
    bounds.append((start, len(text)))
    spans = []
    for s, e in bounds:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            spans.append((s, e))
    return spans


def normalise_type(evidence_type: str) -> str:
    """'Blood Pressure Reading' and 'blood-pressure-reading' both become blood_pressure_reading."""
    return re.sub(r"[\s-]+", "_", evidence_type.strip().lower())


def _check_type(kind: str, evidence_type: str, measure: dict[str, Any]) -> None:
    """Check e: the kind and type are ones this measure accepts from documents."""
    if kind not in KINDS:
        raise _Drop(f"evidence_kind '{kind}' is neither numerator nor exclusion")
    allowed = (measure.get("allowed_evidence_types") or {}).get(kind) or []
    if not allowed:
        raise _Drop(f"{measure['code']} accepts no {kind} evidence from documents")
    if evidence_type not in allowed:
        raise _Drop(f"evidence_type '{evidence_type}' is not an allowed {kind} type for {measure['code']}")


def _check_age(gap: GapContext, evidence_type: str, dates: tuple[date, date] | None) -> None:
    """Check f: age-limited types. Exclusions use the age on 31 December; the HPV test uses the age in its year."""
    if "age_66" in evidence_type and gap.age < 66:
        raise _Drop(f"{evidence_type} needs the member to be 66 or older, but the member is {gap.age}")
    if evidence_type == HRHPV and dates is not None:
        year = dates[1].year
        age_then = gap.age - (gap.measurement_year - year)
        if age_then < 30:
            raise _Drop(f"a high-risk HPV test counts from age 30, but the member was {age_then} in {year}")


def parse_date(text: str) -> tuple[date, date] | None:
    """The first and last day a written date can mean: '2026' is the whole year and 'May 2026' the whole month.
    Accepts YYYY-MM-DD, M/D/YYYY, YYYY-MM, M/YYYY, YYYY and 'Month YYYY'."""
    s = text.strip()
    try:
        if match := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
            day = date(int(match[1]), int(match[2]), int(match[3]))
            return day, day
        if match := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
            day = date(int(match[3]), int(match[1]), int(match[2]))
            return day, day
        if match := re.fullmatch(r"(\d{4})-(\d{1,2})", s):
            return _whole_month(int(match[1]), int(match[2]))
        if match := re.fullmatch(r"(\d{1,2})/(\d{4})", s):
            return _whole_month(int(match[2]), int(match[1]))
        if match := re.fullmatch(r"([A-Za-z]{3,})\.?,?\s+(\d{4})", s):
            month = next((n for n, name in enumerate(_MONTHS, start=1) if name.startswith(match[1].lower())), None)
            return _whole_month(int(match[2]), month) if month else None
        if re.fullmatch(r"\d{4}", s):
            return date(int(s), 1, 1), date(int(s), 12, 31)
    except ValueError:  # an impossible date such as 2026-02-30
        return None
    return None


def _whole_month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _show(dates: tuple[date, date]) -> str:
    return dates[0].isoformat() if dates[0] == dates[1] else f"{dates[0]} to {dates[1]}"


def _check_date_supported(service_date: str, dates: tuple[date, date], page: PageRef, excerpt: str) -> tuple[str, ...]:
    """Check g2: the date must come from the page, so a model cannot invent one. It is supported when the quote
    writes it or it covers the document date (the model is told to use that when the quote has no date). Written
    only elsewhere on the page it is kept with a warning for the reviewer."""
    written = _written_date_pattern(dates)
    if written.search(excerpt):
        return ()
    if page.document_date is not None and dates[0] <= page.document_date <= dates[1]:
        return ()
    if written.search(page.text):
        return (DATE_ON_PAGE,)
    raise _Drop(f"service date {service_date} is not in the quote, on the page, or the document date")


def _written_date_pattern(dates: tuple[date, date]) -> re.Pattern[str]:
    """The ways a page may write this date, at the date's own precision: a year supports neither a month nor a day,
    and a month does not support a day. Numeric dates are read month first, as US records write them."""
    start, end = dates
    year = str(start.year)
    if start != end and (start.month, start.day, end.month, end.day) == (1, 1, 12, 31):
        return re.compile(rf"(?<!\d){year}(?!\d)")
    month_no = _optional_zero(start.month)
    full_name = _MONTHS[start.month - 1]
    month_name = rf"(?:{full_name}|{full_name[:3]})"
    if start == end:
        day_no = _optional_zero(start.day)
        any_year = rf"(?:{year}|{start.year % 100:02d})"
        forms = [
            rf"{month_no}[/-]{day_no}[/-]{any_year}",                               # 8/20/26, 08-20-2026
            rf"{year}[/-]{month_no}[/-]{day_no}",                                   # 2026-08-20, 2026/8/20
            rf"{month_name}(?:\.\s*|\s+){day_no}{_ORDINAL}(?:,\s*|\s+){year}",      # Aug. 20th, 2026
            rf"{day_no}{_ORDINAL}[\s-]+{month_name}\.?(?:,\s*|[\s-]+){year}",       # 20-Aug-2026, 20 Aug, 2026
        ]
    else:
        forms = [
            rf"{year}[/-]{month_no}",                                               # 2026-08, 2026/8
            rf"{month_no}[/-]{year}",                                               # 8/2026, 08-2026
            rf"{month_name}\.?(?:,\s*|\s+){year}",                                  # Aug. 2026, Aug, 2026
        ]
    # The edges stop 1/5/2026 matching inside 11/5/2026, and 3/2026 inside 12/3/2026 or 12-3-2026.
    return re.compile(rf"(?<![\w/-])(?:{'|'.join(forms)})(?![\d/])", re.IGNORECASE)


def _optional_zero(number: int) -> str:
    return f"0?{number}" if number < 10 else str(number)


def _check_window(gap: GapContext, measure: dict[str, Any], evidence_type: str, dates: tuple[date, date]) -> None:
    """Check h: the whole date range lies inside the type's window. A type without a window (such as death) must
    still fall by the end of the measurement year."""
    year_end = date(gap.measurement_year, 12, 31)
    window = next((w for w in measure.get("windows") or [] if w["evidence_type"] == evidence_type), None)
    if window is None:
        if dates[1] > year_end:
            raise _Drop(f"service date {_show(dates)} is after the measurement year")
        return
    lower = date.fromisoformat(str(window["from"])) if window.get("from") else None
    upper = date.fromisoformat(str(window["to"])) if window.get("to") else year_end
    if (lower is not None and dates[0] < lower) or dates[1] > upper:
        raise _Drop(f"service date {_show(dates)} is outside the {evidence_type} window "
                    f"({lower or 'any time'} to {upper})")


def _check_most_recent(gap: GapContext, measure: dict[str, Any], kind: str, dates: tuple[date, date]) -> None:
    """Check i: where only the most recent result counts, a result must be newer than the engine's last one."""
    if not (measure.get("most_recent_counts") and kind == "numerator" and gap.last_event):
        return
    last = date.fromisoformat(str(gap.last_event["date"]))
    if dates[0] != dates[1]:
        raise _Drop(f"needs an exact day to compare with the last known event on {last}")
    if dates[0] <= last:
        raise _Drop(f"{dates[0]} is not newer than the last known event on {last}")


def _check_thresholds(measure: dict[str, Any], evidence_type: str, value: str, excerpt: str) -> None:
    """Check j: values the measure row sets limits for. The value always wins over the quote, because a quote can
    also hold a reference range, a date or an older reading."""
    thresholds = measure.get("thresholds") or {}
    if evidence_type == BLOOD_PRESSURE and "systolic_lt" in thresholds and "diastolic_lt" in thresholds:
        reading = _blood_pressure(value, excerpt)
        if reading is None:
            raise _Drop("no plausible systolic/diastolic pair in the value or quote")
        systolic, diastolic = reading
        if systolic >= thresholds["systolic_lt"] or diastolic >= thresholds["diastolic_lt"]:
            raise _Drop(f"{systolic}/{diastolic} is not under {thresholds['systolic_lt']}/{thresholds['diastolic_lt']}")
    if evidence_type in GLYCEMIC_TYPES and "hba1c_lt" in thresholds:
        number = _PERCENT.search(value) or _NUMBER.search(value) or _PERCENT.search(excerpt)
        if number is None:
            raise _Drop("no numeric HbA1c or GMI value")
        if float(number[1]) >= thresholds["hba1c_lt"]:
            raise _Drop(f"{number[1]}% is not under {thresholds['hba1c_lt']}%")
    if evidence_type == UACR_RATIO and "uacr_unit" in thresholds:
        unit = thresholds["uacr_unit"]
        pattern = r"\s*/\s*".join(re.escape(part) for part in unit.split("/")) + r"\b"
        if not re.search(pattern, f"{value} {excerpt}", re.IGNORECASE):
            raise _Drop(f"labelled {UACR_RATIO} but no ratio in {unit} in the value or quote")


def _blood_pressure(value: str, excerpt: str) -> tuple[int, int] | None:
    """The first plausible reading in the value, or in the quote when the value holds no pair at all. The
    plausibility limits keep dates such as 10/12 from passing as readings."""
    pairs = _BP_PAIR.findall(value) or _BP_PAIR.findall(excerpt)
    for systolic, diastolic in ((int(s), int(d)) for s, d in pairs):
        if systolic in PLAUSIBLE_SYSTOLIC and diastolic in PLAUSIBLE_DIASTOLIC and systolic > diastolic:
            return systolic, diastolic
    return None


def _unpaired_urine_tests(kept: list[Candidate], measure: dict[str, Any]) -> dict[Candidate, str]:
    """Check k: a urine albumin counts only with a urine creatinine taken within the measure's day limit, and the
    other way round. Returns the unpaired candidates with their drop reasons."""
    max_days = (measure.get("thresholds") or {}).get("albumin_creatinine_max_days_apart")
    if max_days is None:
        return {}
    albumin = [c for c in kept if c.evidence_type == URINE_ALBUMIN]
    creatinine = [c for c in kept if c.evidence_type == URINE_CREATININE]

    def paired(test: Candidate, partners: list[Candidate]) -> bool:
        return any(abs((test.service_date_from - p.service_date_from).days) <= max_days for p in partners)

    unpaired = {a: f"urine albumin has no urine creatinine within {max_days} days" for a in albumin
                if not paired(a, creatinine)}
    unpaired.update({c: f"urine creatinine has no urine albumin within {max_days} days" for c in creatinine
                     if not paired(c, albumin)})
    return unpaired
