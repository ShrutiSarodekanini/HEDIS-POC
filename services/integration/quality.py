"""What the Data Quality screen reads: loaded batches, measures with rules, runs with their checks and anomalies, and
the coverage report.

The checks run in dq_checks.py; this module only reads what they stored (quality_run, quality_check_result, data_anomaly
with data_anomaly_detail) and the coverage table.
"""

import uuid
from datetime import date

import psycopg

from services.data_ingestion import team_schema_loader

from . import dq_checks

_BATCHES = """
SELECT s.batch_id::text, s.started_at, b.status, b.step, coalesce(b.counts ? 'load', false),
       (SELECT count(*) FROM quality_run q WHERE q.batch_id = s.batch_id),
       (SELECT max(q.run_id) FROM quality_run q WHERE q.batch_id = s.batch_id)
  FROM source_batch s JOIN batch_run b ON b.batch_id = s.batch_id::text
 WHERE s.source_system = %s
 ORDER BY s.started_at DESC LIMIT 100
"""

_RUNS = """
SELECT run_id, batch_id::text, measure_id, status, actor, summary, error, started_at, completed_at
  FROM quality_run
"""

_CHECKS = """
SELECT check_name, resource_type, measure_id, status, metric_name, metric_value::float, threshold_value::float, details
  FROM quality_check_result WHERE run_id = %s ORDER BY check_pk
"""

_ANOMALIES = """
SELECT a.anomaly_id::text, a.anomaly_type, d.check_type, a.severity, a.resource_type, a.measure_id, d.patient_id,
       a.details, a.detected_at
  FROM data_anomaly a JOIN data_anomaly_detail d ON d.anomaly_id = a.anomaly_id
 WHERE d.run_id = %(run)s AND (%(severity)s::text IS NULL OR a.severity = %(severity)s)
 ORDER BY array_position(ARRAY['critical', 'high', 'medium', 'low'], a.severity), d.check_type, a.anomaly_id
"""

_COVERAGE = f"""
SELECT c.patient_id, c.coverage_id, c.payer_name, c.period_start, c.period_end, c.gap_detected, c.gap_days,
       c.compliant
  FROM ({dq_checks.COVERAGE_ROWS}) c
 WHERE (%(payer)s::text IS NULL OR c.payer_name = %(payer)s)
   AND (%(compliant)s::boolean IS NULL OR c.compliant = %(compliant)s)
   AND (%(start)s::date IS NULL OR c.period_end >= %(start)s)
   AND (%(end)s::date IS NULL OR c.period_start <= %(end)s)
 ORDER BY c.patient_id, c.period_start NULLS LAST, c.coverage_id
 LIMIT %(limit)s
"""

_RUN_KEYS = ("run_id", "batch_id", "measure_id", "status", "actor", "summary", "error", "started_at", "completed_at")


def overview(conn: psycopg.Connection) -> dict:
    """Uploaded batches (newest first) with their run counts, the measures that have rules, and what is not
    available on the team tables."""
    rules = dq_checks.measure_rules()
    measures = [{"measure_id": m, "phase": dq_checks.phase(r), "description": r.get("description")}
                for m, r in sorted(rules.items(), key=lambda kv: (dq_checks.phase(kv[1]), kv[0]))]
    keys = ("batch_id", "started_at", "status", "step", "loaded", "runs", "latest_run_id")
    rows = conn.execute(_BATCHES, (team_schema_loader.SOURCE_SYSTEM,)).fetchall()
    return {"measures": measures, "batches": [dict(zip(keys, r)) for r in rows],
            "not_available": dq_checks.NOT_AVAILABLE}


def runs(conn: psycopg.Connection, batch_id: uuid.UUID) -> dict:
    rows = conn.execute(_RUNS + " WHERE batch_id = %s ORDER BY run_id DESC", (batch_id,)).fetchall()
    return {"batch_id": str(batch_id), "runs": [dict(zip(_RUN_KEYS, r)) for r in rows]}


def run_detail(conn: psycopg.Connection, run_id: int, severity: str | None) -> dict | None:
    """One run with every check and its anomalies (optionally one severity), most severe first."""
    row = conn.execute(_RUNS + " WHERE run_id = %s", (run_id,)).fetchone()
    if row is None:
        return None
    check_keys = ("check_name", "resource_type", "measure_id", "status", "metric_name", "metric_value",
                  "threshold_value", "details")
    anomaly_keys = ("anomaly_id", "anomaly_type", "check_type", "severity", "resource_type", "measure_id",
                    "patient_id", "details", "detected_at")
    checks = conn.execute(_CHECKS, (run_id,)).fetchall()
    anomalies = conn.execute(_ANOMALIES, {"run": run_id, "severity": severity}).fetchall()
    return {"run": dict(zip(_RUN_KEYS, row)), "checks": [dict(zip(check_keys, c)) for c in checks],
            "anomalies": [dict(zip(anomaly_keys, a)) for a in anomalies]}


def coverage_report(conn: psycopg.Connection, payer: str | None, compliant: bool | None, start: date | None,
                    end: date | None, limit: int) -> dict:
    """Coverage rows with their patient's enrollment gap (the rule in dq_checks.COVERAGE_ROWS), and the totals."""
    rows = conn.execute(_COVERAGE, {"payer": payer, "compliant": compliant, "start": start, "end": end,
                                    "limit": limit, "gap_limit": dq_checks.COVERAGE_GAP_LIMIT_DAYS}).fetchall()
    keys = ("patient_id", "coverage_id", "payer", "period_start", "period_end", "gap_detected", "gap_days",
            "continuous_enrollment_compliant")
    records = [dict(zip(keys, r)) for r in rows]
    continuous = sum(1 for r in records if r["continuous_enrollment_compliant"])
    payers = [r[0] for r in conn.execute("SELECT DISTINCT payer_name FROM coverage WHERE payer_name IS NOT NULL "
                                         "ORDER BY 1").fetchall()]
    return {"coverage_records": records, "payers": payers,
            "summary": {"total_covered": len(records), "continuous_enrollment": continuous,
                        "hedis_violations": sum(1 for r in records if r["gap_detected"]),
                        "compliance_rate": int(continuous / len(records) * 100) if records else 0,
                        "gap_limit_days": dq_checks.COVERAGE_GAP_LIMIT_DAYS}}
