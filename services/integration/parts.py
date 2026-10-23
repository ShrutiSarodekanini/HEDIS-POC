"""Two-part rules: which parts of a gap's evidence must be confirmed before the gap waits for the engine.

- A measure whose rule kind is "two_part" (KED) needs an eGFR and a urine part: a uACR, or a quantitative urine
  albumin and a urine creatinine no more than measure_rule.thresholds.albumin_creatinine_max_days_apart days apart.
  The rule names its kind, windows and threshold but not the parts, so the pairing below is the one written in the
  rule's window notes ("paired with urine creatinine within 4 days").
- The frailty exclusions need a frailty finding and an advanced illness finding; for
  frailty_age_66_80_with_advanced_illness_or_age_81_plus_frailty_alone, frailty alone is enough at 81 or older (age on
  31 December of the measurement year). Which part a finding is comes from the model and is kept as a
  "[part: frailty] " or "[part: advanced_illness] " prefix on evidence_proposal_detail.reason (there is no column).
- Every other measure or exclusion type is one part: one confirmed finding is enough.
"""

import re
from dataclasses import dataclass
from datetime import date

import psycopg

FRAILTY_PLUS = "frailty_and_advanced_illness_age_66_plus"
FRAILTY_BY_AGE = "frailty_age_66_80_with_advanced_illness_or_age_81_plus_frailty_alone"
FRAILTY_TYPES = (FRAILTY_PLUS, FRAILTY_BY_AGE)
PART_NAMES = {"frailty": "Frailty", "advanced_illness": "Advanced illness"}
_PREFIX = re.compile(r"^\[part: (frailty|advanced_illness)\] ")
WAITING_FOR_ENGINE = ("closed_pending_engine", "excluded_pending_engine")


def tag_reason(reason: str, part: str) -> str:
    """The reason as stored: with the part prefix when the finding is one part of a frailty exclusion."""
    return f"[part: {part}] {reason}" if part else reason


def split_reason(reason: str | None) -> tuple[str, str]:
    """(part, reason text) from a stored reason."""
    match = _PREFIX.match(reason or "")
    return (match[1], (reason or "")[match.end():]) if match else ("", reason or "")


@dataclass(frozen=True)
class Item:
    evidence_id: str
    status: str                  # proposed or confirmed
    kind: str                    # numerator or exclusion
    evidence_type: str
    date_from: date | None
    part: str


def load(conn: psycopg.Connection, gap_id: str) -> tuple[dict, int | None, list[Item]]:
    """The gap's measure rule, the patient's age on 31 December of the measurement year, and its open evidence."""
    rule = conn.execute(
        """SELECT r.measure_id, r.name, r.kind, r.windows, r.thresholds,
                  date_part('year', age(make_date(g.measurement_year, 12, 31), p.birth_date))::int AS age
             FROM care_gap g
             JOIN measure_rule r ON r.measure_id = g.measure_id AND r.measurement_year = g.measurement_year
             LEFT JOIN patient p ON p.patient_id = g.patient_id
            WHERE g.gap_id = %s::uuid""", (gap_id,)).fetchone()
    keys = ("measure_id", "name", "kind", "windows", "thresholds", "age")
    rule = dict(zip(keys, rule)) if rule else {}
    items = [Item(str(e), s, k, t, d, split_reason(r)[0]) for e, s, k, t, d, r in conn.execute(
        """SELECT p.evidence_id, p.status, x.evidence_kind, x.evidence_type, x.service_date_from, x.reason
             FROM evidence_proposal p JOIN evidence_proposal_detail x ON x.evidence_id = p.evidence_id
            WHERE p.gap_id = %s::uuid AND p.status IN ('proposed', 'confirmed')""", (gap_id,)).fetchall()]
    return rule, rule.pop("age", None), items


def check(rule: dict, age: int | None, items: list[Item]) -> dict:
    """Each required part as confirmed, proposed or missing, and whether closure or exclusion is complete."""
    numerator = [i for i in items if i.kind == "numerator"]
    if rule.get("kind") == "two_part":
        max_days = (rule.get("thresholds") or {}).get("albumin_creatinine_max_days_apart")
        groups = [{"name": "Kidney blood test (eGFR)", "met": lambda pool: any(i.evidence_type == "egfr" for i in pool),
                   "types": {"egfr"}},
                  {"name": "Kidney urine test (uACR, or urine albumin and creatinine"
                           + (f" within {max_days} days)" if max_days is not None else ")"),
                   "met": lambda pool: _urine_met(pool, max_days),
                   "types": {"uacr_ratio", "urine_albumin_quantitative", "urine_creatinine"}}]
    else:
        groups = [{"name": f"{rule.get('name') or 'Measure'} evidence", "met": bool,
                   "types": {i.evidence_type for i in numerator}}]
    numerator_parts = [_part(g, numerator) for g in groups]
    exclusions = []
    for evidence_type in sorted({i.evidence_type for i in items if i.kind == "exclusion"}):
        pool = [i for i in items if i.kind == "exclusion" and i.evidence_type == evidence_type]
        needed = _exclusion_parts(evidence_type, age)
        parts = [_part({"name": PART_NAMES[p], "met": lambda xs, p=p: any(x.part == p for x in xs), "types": {p}},
                       pool) for p in needed] if needed else [_part({"name": evidence_type, "met": bool,
                                                                     "types": {evidence_type}}, pool)]
        exclusions.append({"evidence_type": evidence_type, "parts": parts,
                           "complete": all(p["status"] == "confirmed" for p in parts)})
    closure = bool(numerator) and all(p["status"] == "confirmed" for p in numerator_parts)
    return {"two_part": rule.get("kind") == "two_part" or any(len(x["parts"]) > 1 for x in exclusions),
            "numerator": {"parts": numerator_parts, "complete": closure}, "exclusions": exclusions,
            "closure_complete": closure, "exclusion_complete": any(x["complete"] for x in exclusions),
            "any_confirmed": any(i.status == "confirmed" for i in items),
            "any_proposed": any(i.status == "proposed" for i in items), "age": age}


def review_status(current: str, result: dict) -> str:
    """The gap's review state from its parts; states the engine set (closed, excluded) stay."""
    if current in ("closed", "excluded"):
        return current
    if result["exclusion_complete"]:
        return "excluded_pending_engine"
    if result["closure_complete"]:
        return "closed_pending_engine"
    if result["any_confirmed"]:
        return "partially_evidenced"
    if result["any_proposed"]:
        return "pending_review"
    return "open"


def part_label(result: dict, kind: str, evidence_type: str, part: str) -> str | None:
    """'part 1 of 2' for a finding of a two-part rule, else None."""
    if kind == "numerator":
        parts = result["numerator"]["parts"]
        key = evidence_type
    else:
        group = next((x for x in result["exclusions"] if x["evidence_type"] == evidence_type), None)
        parts, key = (group["parts"] if group else []), part or evidence_type
    if len(parts) < 2:
        return None
    index = next((n for n, p in enumerate(parts, 1) if key in p["types"]), None)
    return f"part {index} of {len(parts)}" if index else None


def _exclusion_parts(evidence_type: str, age: int | None) -> list[str]:
    if evidence_type == FRAILTY_PLUS or (evidence_type == FRAILTY_BY_AGE and (age is None or age < 81)):
        return ["frailty", "advanced_illness"]
    if evidence_type == FRAILTY_BY_AGE:
        return ["frailty"]
    return []


def _part(group: dict, pool: list[Item]) -> dict:
    confirmed = [i for i in pool if i.status == "confirmed"]
    status = "confirmed" if group["met"](confirmed) else "proposed" if group["met"](pool) else "missing"
    return {"name": group["name"], "status": status, "types": sorted(group["types"])}


def _urine_met(pool: list[Item], max_days: int | None) -> bool:
    if any(i.evidence_type == "uacr_ratio" for i in pool):
        return True
    if max_days is None:
        return False
    albumin = [i.date_from for i in pool if i.evidence_type == "urine_albumin_quantitative" and i.date_from]
    creatinine = [i.date_from for i in pool if i.evidence_type == "urine_creatinine" and i.date_from]
    return any(abs((a - c).days) <= max_days for a in albumin for c in creatinine)
