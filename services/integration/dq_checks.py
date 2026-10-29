"""The integration's bridge to the data quality checks: their rule set and thresholds, run on the team tables.

The worker (services/data_quality_rules/main.py) reads its own tables, which reuse the lead's table names with
different columns, so it cannot run on the one schema the app uses. This module runs the same checks, with the rule
file read as it is (services/data_quality_rules/config/dq_rules.json) and its thresholds, over the team tables. Those
files are not changed. Results go where the team schema keeps them: anomalies in data_anomaly (plus
data_anomaly_detail), the batch's record counts in feed_completeness_report, and the run with every check in
quality_run and quality_check_result (migration 0007).

Where each input comes from on the team tables:
- records per resource type: the approved resources of the batch's ingestion job (fhir_resources), as loaded
- codes: the batch's observation, condition, procedure, medication_request and immunization rows
- observation unit: valueQuantity.unit of the stored resource (fhir_resource_raw); value: observation.value_quantity
- validation errors: validation_issues of the batch's ingestion job
- enrollment gaps: each patient's coverage periods, with the gap rule (detect_enrollment_gaps) in SQL

The multi-source ingestion (EHR FHIR, C-CDA, claims 837 and NCPDP adapters) writes its own tables and has no
counterpart here; NOT_AVAILABLE lists it for the screen.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from services.document_intake import db
from services.document_intake.db import audit

from .config import REPO_ROOT

log = logging.getLogger(__name__)

RULES_PATH = REPO_ROOT / "services" / "data_quality_rules" / "config" / "dq_rules.json"

# The worker's constants (services/data_quality_rules/main.py), kept equal to them.
REQUIRED_RESOURCE_TYPES = ("Patient", "Encounter", "Condition", "Observation", "Coverage")
MEASURE_RELEVANT_TYPES = {"Patient", "Encounter", "Condition", "Observation", "Procedure", "MedicationRequest",
                          "Coverage"}
VOLUME_DROP_THRESHOLD_PCT = 40.0
VOLUME_SPIKE_THRESHOLD_PCT = 100.0
MAPPING_ERROR_RATE_THRESHOLD = 0.02
COVERAGE_GAP_LIMIT_DAYS = 45
MEASURE_ID_ALIASES = {"COL-E": "COL", "COLE": "COL", "BCS-E": "BCS", "BCSE": "BCS", "COA-FSA": "COA",
                      "COA-MDR": "COA", "GSD-HBAOPOR": "GSD"}

# data_anomaly.anomaly_type allows these (team schema); any other check type is stored as 'other'.
LEAD_ANOMALY_TYPES = {"feed_dropout", "volume_drop", "volume_spike", "denominator_drop", "mapping_error"}

NOT_AVAILABLE = [{
    "name": "Multi-source ingestion (EHR FHIR R4, C-CDA / HIE, Claims 837, NCPDP pharmacy)",
    "reason": "It writes its own tables, which reuse the team table names with different columns, so it cannot run "
              "on the one schema. Synthea CSV files are ingested on Data Ingestion and checked here.",
}]

# Where each code list of a rule is matched: (rule key, resource type, team table, code column).
_EVIDENCE_SOURCES = (
    ("observation_codes", "Observation", "observation", "code"),
    ("required_condition_codes", "Condition", "condition", "code"),
    ("required_procedure_codes", "Procedure", "procedure", "code"),
    ("required_medication_codes", "MedicationRequest", "medication_request", "medication_code"),
    ("required_immunization_codes", "Immunization", "immunization", "vaccine_code"),
)

# The enrollment gap rule per patient: days between consecutive periods (sorted by start), positive gaps only; the
# patient is compliant when every gap is within the limit. Each coverage row carries its patient's total gap days.
COVERAGE_ROWS = """
WITH periods AS (
    SELECT patient_id, period_start, period_end,
           lead(period_start) OVER (PARTITION BY patient_id ORDER BY period_start, period_end) AS next_start
      FROM coverage
     WHERE period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end
), gaps AS (
    SELECT patient_id, sum(next_start - period_end - 1)::int AS gap_days,
           bool_and(next_start - period_end - 1 <= %(gap_limit)s) AS compliant
      FROM periods
     WHERE next_start - period_end - 1 > 0
     GROUP BY patient_id
)
SELECT c.coverage_id, c.patient_id, c.payer_name, c.period_start, c.period_end, c.batch_id,
       g.patient_id IS NOT NULL AS gap_detected, g.gap_days, coalesce(g.compliant, true) AS compliant
  FROM coverage c
  LEFT JOIN gaps g ON g.patient_id = c.patient_id
"""


@dataclass
class _Results:
    """The checks and anomalies of one run, stored together at the end of the run."""
    checks: list[tuple] = field(default_factory=list)
    anomalies: list[tuple] = field(default_factory=list)

    def check(self, name: str, passed: bool, metric: str, value: float, threshold: float | None, details: dict,
              resource_type: str | None = None, measure_id: str | None = None) -> None:
        self.checks.append((name, resource_type, measure_id, "passed" if passed else "failed", metric, value,
                            threshold, Jsonb(details)))

    def anomaly(self, check_type: str, severity: str, details: dict, resource_type: str | None = None,
                measure_id: str | None = None, patient_id: str | None = None) -> None:
        self.anomalies.append((check_type, severity, resource_type, measure_id, details, patient_id))

    def failed(self) -> int:
        return sum(1 for c in self.checks if c[3] == "failed")


def measure_rules() -> dict[str, dict]:
    """The rule file as it is: measure id -> rule."""
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the data quality rule file must hold one object per measure")
    return data


def phase(rule: dict) -> int:
    number = _num(rule.get("phase"))
    return 99 if number is None else int(number)


def resolve_measure(measure_id: str | None, rules: dict[str, dict]) -> str | None:
    """The measure id normalisation: a rule id, or an alias such as COL-E for COL. None means every measure."""
    if not measure_id:
        return None
    token = measure_id.strip().upper().replace("_", "-").replace(" ", "")
    resolved = token if token in rules else MEASURE_ID_ALIASES.get(token)
    if resolved not in rules:
        raise ValueError(f"measure {measure_id} has no data quality rules; use one of {', '.join(sorted(rules))}")
    return resolved


def status() -> dict:
    """For the health answer: where the screen reads, how many measures have rules, and what is not available."""
    return {"enabled": True, "prefix": "/api/quality", "measures_with_rules": len(measure_rules()),
            "not_available": NOT_AVAILABLE}


def run_checks(batch_id: uuid.UUID, measure_id: str | None, actor: str) -> dict:
    """Run the checks on one loaded batch, for every measure or one. The run row is committed first, so a failure is
    stored on it with its reason (and raised) instead of disappearing with the rolled back results."""
    rules = measure_rules()
    measure = resolve_measure(measure_id, rules)
    with db.connect() as conn:
        found = conn.execute("""SELECT b.ingest_job_id, coalesce(b.counts ? 'load', false)
                                  FROM source_batch s LEFT JOIN batch_run b ON b.batch_id = s.batch_id::text
                                 WHERE s.batch_id = %s""", (batch_id,)).fetchone()
        if found is None:
            raise LookupError(f"batch {batch_id} not found")
        job_id, loaded = found
        if not loaded or job_id is None:
            raise ValueError(f"batch {batch_id} has not been loaded into the patient tables yet")
        run_id = conn.execute("""INSERT INTO quality_run (batch_id, measure_id, status, actor)
                                 VALUES (%s, %s, 'running', %s) RETURNING run_id""",
                              (batch_id, measure, actor)).fetchone()[0]
        audit(conn, "quality_run", str(run_id), "quality.started", {"batch_id": str(batch_id), "measure_id": measure},
              actor=actor)
    try:
        with db.connect() as conn:
            summary = _check(conn, run_id, batch_id, job_id, rules, measure)
            conn.execute("""UPDATE quality_run SET status = 'completed', summary = %s, completed_at = now()
                             WHERE run_id = %s""", (Jsonb(summary), run_id))
            result = {"run_id": run_id, "measure_id": measure, "checks": summary["checks_total"],
                      "failed": summary["checks_failed"], "anomalies": summary["anomalies_created"]}
            _event(conn, batch_id, "quality.checked", result)
            audit(conn, "quality_run", str(run_id), "quality.checked", result, actor=actor)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:300]}"
        log.error("data quality run %s on batch %s failed: %s", run_id, batch_id, type(exc).__name__)
        with db.connect() as conn:
            conn.execute("""UPDATE quality_run SET status = 'failed', error = %s, completed_at = now()
                             WHERE run_id = %s""", (reason, run_id))
            _event(conn, batch_id, "quality.failed", {"run_id": run_id, "reason": reason})
            audit(conn, "quality_run", str(run_id), "quality.failed", {"batch_id": str(batch_id)}, actor=actor)
        raise
    log.info("data quality run %s on batch %s: %s checks, %s failed, %s anomalies", run_id, batch_id,
             result["checks"], result["failed"], result["anomalies"])
    return {**result, "status": "completed"}


def _check(conn: psycopg.Connection, run_id: int, batch_id: uuid.UUID, job_id: str, rules: dict[str, dict],
           measure: str | None) -> dict:
    """The _run_dq_checks on the team tables, in the caller's transaction. Returns the run summary."""
    out = _Results()
    counts, feed_report = _feed_counts(conn, batch_id, job_id)
    source_system = conn.execute("SELECT source_system FROM source_batch WHERE batch_id = %s",
                                 (batch_id,)).fetchone()[0]
    baseline = dict(conn.execute(
        """SELECT f.resource_type, avg(f.record_count)::float FROM feed_completeness_report f
             JOIN source_batch s ON s.batch_id = f.batch_id
            WHERE s.source_system = %s AND f.batch_id <> %s GROUP BY 1""", (source_system, batch_id)).fetchall())
    active = [measure] if measure else sorted(rules, key=lambda m: (phase(rules[m]), m))
    required = set(REQUIRED_RESOURCE_TYPES) | {t for m in active for t in rules[m].get("required_resource_types", [])}

    _feed_volume(out, counts, baseline, required, [rules[m] for m in active])
    _required_presence(out, counts, required)
    per_measure = {m: _measure_checks(conn, out, batch_id, m, rules[m]) for m in active}
    _mapping_error_rate(conn, out, job_id, counts)
    _coverage_gaps(conn, out, batch_id)
    _store(conn, out, run_id, batch_id)
    return {"source_system": source_system, "measure_id": measure, "active_measure_ids": active,
            "resource_counts": counts, "baseline_counts": {k: round(v, 2) for k, v in baseline.items()},
            "feed_report": feed_report, "anomalies_created": len(out.anomalies), "checks_total": len(out.checks),
            "checks_failed": out.failed(), "measure_summary": per_measure}


def _feed_counts(conn: psycopg.Connection, batch_id: uuid.UUID, job_id: str) -> tuple[dict[str, int], str]:
    """Approved records per resource type, written to feed_completeness_report once per batch."""
    counts = dict(conn.execute(
        """SELECT resource_type, count(*) FROM fhir_resources
            WHERE job_id = %s AND persisted AND lifecycle_status <> 'rejected' GROUP BY 1 ORDER BY 1""",
        (job_id,)).fetchall())
    if conn.execute("SELECT 1 FROM feed_completeness_report WHERE batch_id = %s LIMIT 1", (batch_id,)).fetchone():
        return counts, "already written for this batch"
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO feed_completeness_report (batch_id, resource_type, record_count, measure_relevant,
                                                     generated_at)
               VALUES (%s, %s, %s, %s, now())""",
            [(batch_id, rtype, n, rtype in MEASURE_RELEVANT_TYPES) for rtype, n in counts.items()])
    return counts, "written"


def _feed_volume(out: _Results, counts: dict[str, int], baseline: dict[str, float], required: set[str],
                 active_rules: list[dict]) -> None:
    """Each feed against its average in earlier batches of the same source; without a baseline it passes."""
    for rtype in sorted(set(counts) | required):
        current = float(counts.get(rtype, 0))
        base = baseline.get(rtype)
        matching = [r for r in active_rules if rtype in r.get("required_resource_types", [])]
        drop_limit = min((_num(r.get("volume_drop_threshold_pct")) or VOLUME_DROP_THRESHOLD_PCT for r in matching),
                         default=VOLUME_DROP_THRESHOLD_PCT)
        spike_limit = min((_num(r.get("volume_spike_threshold_pct")) or VOLUME_SPIKE_THRESHOLD_PCT for r in matching),
                          default=VOLUME_SPIKE_THRESHOLD_PCT)
        if not base or base <= 0:
            out.check("feed_volume", True, "record_count", current, base,
                      {"current_count": int(current), "baseline_avg_count": None, "note": "insufficient_baseline"},
                      resource_type=rtype)
            continue
        drop = (base - current) / base * 100 if current < base else 0.0
        spike = (current - base) / base * 100 if current > base else 0.0
        details = {"current_count": int(current), "baseline_avg_count": round(base, 2), "drop_pct": round(drop, 2),
                   "spike_pct": round(spike, 2), "drop_threshold_pct": drop_limit, "spike_threshold_pct": spike_limit}
        if current == 0:
            out.anomaly("feed_dropout", "critical", details, resource_type=rtype)
        elif drop >= drop_limit:
            out.anomaly("volume_drop", "high" if drop < 70 else "critical", details, resource_type=rtype)
        elif spike >= spike_limit:
            out.anomaly("volume_spike", "medium" if spike < 150 else "high", details, resource_type=rtype)
        out.check("feed_volume", drop < drop_limit and spike < spike_limit, "record_count", current, base, details,
                  resource_type=rtype)


def _required_presence(out: _Results, counts: dict[str, int], required: set[str]) -> None:
    for rtype in sorted(required):
        current = counts.get(rtype, 0)
        if current == 0:
            out.anomaly("required_feed_missing", "high", {"resource_type": rtype, "record_count": current},
                        resource_type=rtype)
        out.check("required_resource_presence", current > 0, "record_count", float(current), 1.0,
                  {"resource_type": rtype}, resource_type=rtype)


def _measure_checks(conn: psycopg.Connection, out: _Results, batch_id: uuid.UUID, mid: str, rule: dict) -> dict:
    """Clinical evidence and patient coverage for one measure's codes, then its observation checks."""
    evidence = []
    for key, rtype, table, column in _EVIDENCE_SOURCES:
        codes = sorted(set(rule.get(key, [])))
        if codes:
            rows, patients = conn.execute(
                sql.SQL("SELECT count(*), count(DISTINCT patient_id) FROM {} WHERE batch_id = %s AND {} = ANY(%s)")
                .format(sql.Identifier(table), sql.Identifier(column)), (batch_id, codes)).fetchone()
            evidence.append({"resource_type": rtype, "codes": codes, "row_count": rows, "patient_count": patients})
    mode = str(rule.get("clinical_evidence_mode", "any")).lower()
    min_patients = _num(rule.get("min_distinct_patients_with_evidence"))
    min_patients = 1.0 if min_patients is None else min_patients
    if evidence:
        found = [e for e in evidence if e["row_count"] > 0]
        has_evidence = len(found) == len(evidence) if mode == "all" else bool(found)
        best = max(e["patient_count"] for e in evidence)
        if not has_evidence:
            out.anomaly("measure_evidence_missing", "high", {"measure_id": mid, "clinical_evidence_mode": mode,
                                                             "evidence_checks": evidence}, measure_id=mid)
        if best < min_patients:
            out.anomaly("measure_low_patient_coverage", "medium",
                        {"measure_id": mid, "max_patient_count": best, "min_distinct_patients_with_evidence":
                         min_patients, "evidence_checks": evidence}, measure_id=mid)
        out.check("measure_clinical_evidence", has_evidence, f"{mid}_evidence_rows",
                  float(sum(e["row_count"] for e in evidence)), 1.0,
                  {"measure_id": mid, "phase": phase(rule), "clinical_evidence_mode": mode,
                   "evidence_checks": evidence}, measure_id=mid)
        out.check("measure_patient_coverage", best >= min_patients, f"{mid}_distinct_patients", float(best),
                  min_patients, {"measure_id": mid, "phase": phase(rule), "max_patient_count": best,
                                 "min_distinct_patients_with_evidence": min_patients}, measure_id=mid)
    observed, unit_failures, range_failures = _observation_checks(conn, out, batch_id, mid, rule)
    return {"phase": phase(rule), "observations_evaluated": observed, "unit_failures": unit_failures,
            "value_range_failures": range_failures}


def _observation_checks(conn: psycopg.Connection, out: _Results, batch_id: uuid.UUID, mid: str,
                        rule: dict) -> tuple[int, int, int]:
    """Presence, unit and value range of the measure's observations; one anomaly per observation that fails."""
    codes = sorted(set(rule.get("observation_codes", [])))
    if not codes:
        return 0, 0, 0
    rows = conn.execute(
        """SELECT o.observation_id, o.patient_id, o.code, o.value_quantity::float, r.raw->'valueQuantity'->>'unit'
             FROM observation o
             LEFT JOIN fhir_resource_raw r ON r.resource_type = 'Observation' AND r.resource_id = o.observation_id
            WHERE o.batch_id = %s AND o.code = ANY(%s) ORDER BY o.observation_id""", (batch_id, codes)).fetchall()
    unit = rule.get("requires_unit")
    low, high = _num(rule.get("value_range", {}).get("min")), _num(rule.get("value_range", {}).get("max"))
    if not rows:
        out.anomaly("measure_observation_missing", "high", {"measure_id": mid, "observation_codes": codes},
                    resource_type="Observation", measure_id=mid)
    out.check("measure_observation_presence", bool(rows), f"{mid}_observations_evaluated", float(len(rows)), 1.0,
              {"measure_id": mid, "observation_codes": codes}, resource_type="Observation", measure_id=mid)
    unit_failures = range_failures = 0
    for observation_id, patient_id, code, value, actual_unit in rows:
        if unit and actual_unit != unit:
            unit_failures += 1
            out.anomaly("mapping_error", "high", {"measure_id": mid, "observation_id": observation_id, "code": code,
                                                  "reason": "missing_or_invalid_unit", "expected_unit": unit,
                                                  "actual_unit": actual_unit},
                        resource_type="Observation", measure_id=mid, patient_id=patient_id)
        if value is None or (low is not None and value < low) or (high is not None and value > high):
            range_failures += 1
            out.anomaly("mapping_error", "high", {"measure_id": mid, "observation_id": observation_id, "code": code,
                                                  "reason": "value_out_of_expected_range", "min_expected": low,
                                                  "max_expected": high, "actual_value": value},
                        resource_type="Observation", measure_id=mid, patient_id=patient_id)
    out.check("measure_required_unit", unit_failures == 0, f"{mid}_unit_failures", float(unit_failures), 0.0,
              {"measure_id": mid, "required_unit": unit, "observation_codes": codes}, resource_type="Observation",
              measure_id=mid)
    out.check("measure_value_range", range_failures == 0, f"{mid}_value_range_failures", float(range_failures), 0.0,
              {"measure_id": mid, "min": low, "max": high, "observation_codes": codes}, resource_type="Observation",
              measure_id=mid)
    return len(rows), unit_failures, range_failures


def _mapping_error_rate(conn: psycopg.Connection, out: _Results, job_id: str, counts: dict[str, int]) -> None:
    """Validation errors of the batch's ingestion job against its patients, encounters and observations."""
    errors, warnings, generated = conn.execute(
        """SELECT count(*) FILTER (WHERE v.severity = 'error'), count(*) FILTER (WHERE v.severity = 'warning'),
                  (SELECT resources_generated FROM conversion_jobs WHERE id = %(job)s)
             FROM validation_issues v WHERE v.job_id = %(job)s""", {"job": job_id}).fetchone()
    total = counts.get("Patient", 0) + counts.get("Encounter", 0) + counts.get("Observation", 0)
    if total <= 0:
        total = max(generated or 0, 1)
    rate = errors / total
    details = {"error_count": errors, "warning_count": warnings, "total_resources": total}
    if rate > MAPPING_ERROR_RATE_THRESHOLD:
        out.anomaly("mapping_error_rate", "high", {**details, "error_rate": round(rate, 4)})
    out.check("mapping_error_rate", rate <= MAPPING_ERROR_RATE_THRESHOLD, "error_rate", rate,
              MAPPING_ERROR_RATE_THRESHOLD, details)


def _coverage_gaps(conn: psycopg.Connection, out: _Results, batch_id: uuid.UUID) -> None:
    """Coverage rows of the batch whose patient has more than the allowed enrollment gap days in total."""
    violations = conn.execute(
        f"SELECT count(*) FROM ({COVERAGE_ROWS}) c WHERE c.batch_id = %(batch)s AND c.gap_detected "
        "AND c.gap_days > %(gap_limit)s", {"batch": batch_id, "gap_limit": COVERAGE_GAP_LIMIT_DAYS}).fetchone()[0]
    if violations:
        out.anomaly("coverage_gap_violation", "high",
                    {"violation_count": violations, "threshold_gap_days": COVERAGE_GAP_LIMIT_DAYS},
                    resource_type="Coverage")
    out.check(f"coverage_gap_{COVERAGE_GAP_LIMIT_DAYS}_days", violations == 0, "violation_count", float(violations),
              0.0, {"threshold_gap_days": COVERAGE_GAP_LIMIT_DAYS}, resource_type="Coverage")


def _store(conn: psycopg.Connection, out: _Results, run_id: int, batch_id: uuid.UUID) -> None:
    """Checks to quality_check_result; anomalies to data_anomaly with their data_anomaly_detail."""
    anomalies = [(uuid.uuid4(), *a) for a in out.anomalies]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO quality_check_result (run_id, check_name, resource_type, measure_id, status, metric_name,
                                                 metric_value, threshold_value, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", [(run_id, *c) for c in out.checks])
        cur.executemany(
            """INSERT INTO data_anomaly (anomaly_id, resource_type, measure_id, anomaly_type, severity, details,
                                         batch_id, detected_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, now())""",
            [(aid, rtype, mid, check if check in LEAD_ANOMALY_TYPES else "other", severity,
              Jsonb({"check": check, **details}), batch_id)
             for aid, check, severity, rtype, mid, details, _ in anomalies])
        cur.executemany(
            "INSERT INTO data_anomaly_detail (anomaly_id, run_id, check_type, patient_id) VALUES (%s, %s, %s, %s)",
            [(aid, run_id, check, patient_id) for aid, check, _, _, _, _, patient_id in anomalies])


def _event(conn: psycopg.Connection, batch_id: uuid.UUID, event_type: str, payload: dict) -> None:
    conn.execute("INSERT INTO domain_event (event_type, batch_id, payload, published) VALUES (%s, %s, %s, false)",
                 (event_type, batch_id, Jsonb(payload)))


def _num(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
