"""Read side of the HEDIS Calculation screen: the measures the engine runs, their latest populations, and the
per-patient results and gaps, all from measure_result, care_gap and care_gap_detail.

The engine screen runs the engine per patient and keeps the results in the browser; here the same engine runs
through our bridge (measures.py, which reuses its command line and helpers) and every result is stored first.
"""

import psycopg
from psycopg.rows import dict_row

from . import measures
from .config import SETTINGS

_COUNTS = """
SELECT r.measure_id,
       count(*) AS evaluated,
       count(*) FILTER (WHERE r.in_denominator) AS denominator,
       count(*) FILTER (WHERE r.in_denominator AND coalesce(r.excluded, false)) AS excluded,
       count(*) FILTER (WHERE r.in_denominator AND NOT coalesce(r.excluded, false) AND r.in_numerator) AS met,
       count(*) FILTER (WHERE r.in_denominator AND NOT coalesce(r.excluded, false) AND NOT r.in_numerator) AS not_met,
       max(r.computed_at) AS computed_at
  FROM v_measure_result_latest r
 WHERE r.measurement_year = %(year)s
 GROUP BY r.measure_id
"""

_GAPS = """
SELECT g.measure_id,
       count(*) FILTER (WHERE g.status = 'open') AS open,
       count(*) FILTER (WHERE g.status = 'closed') AS closed,
       count(*) FILTER (WHERE d.review_status IN ('closed_pending_engine', 'excluded_pending_engine'))
           AS confirmed_by_reviewer
  FROM care_gap g LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
 WHERE g.measurement_year = %(year)s
 GROUP BY g.measure_id
"""

_PATIENTS = """
SELECT r.patient_id, r.in_denominator, r.in_numerator, coalesce(r.excluded, false) AS excluded, r.computed_at,
       r.engine_version, g.gap_id::text AS gap_id, g.status AS gap_status, g.missing_element,
       coalesce(d.review_status, CASE WHEN g.gap_id IS NULL THEN NULL ELSE 'open' END) AS review_status,
       d.engine_run_id
  FROM v_measure_result_latest r
  LEFT JOIN care_gap g ON g.patient_id = r.patient_id AND g.measure_id = r.measure_id
                      AND g.measurement_year = r.measurement_year
  LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
 WHERE r.measure_id = %(measure)s AND r.measurement_year = %(year)s
 ORDER BY (g.status = 'open') DESC NULLS LAST, r.in_denominator DESC, r.patient_id
"""

_RUN_CANDIDATES = """
WITH latest_batch AS (
    SELECT batch_id, ingest_job_id
      FROM batch_run
     WHERE status IN ('ready', 'running', 'prioritized') AND ingest_job_id IS NOT NULL
     ORDER BY started_at DESC
     LIMIT 1
),
batch_size AS (
    SELECT (a.payload->>'bytes')::bigint AS bytes
      FROM latest_batch b
      LEFT JOIN audit_events a
        ON a.entity_type = 'batch'
       AND a.entity_id = b.batch_id::text
       AND a.payload ? 'bytes'
     ORDER BY a.at DESC
     LIMIT 1
)
SELECT r.resource_id AS patient_id, r.updated_at AS stored_at, s.bytes AS input_bytes
  FROM latest_batch b
  JOIN fhir_resources r
    ON r.job_id = b.ingest_job_id
   AND r.resource_type = 'Patient'
   AND r.persisted
   AND r.lifecycle_status <> 'rejected'
 CROSS JOIN batch_size s
 ORDER BY r.updated_at DESC, r.resource_id
 LIMIT %(limit)s OFFSET %(offset)s
"""

_RUN_CANDIDATE_COUNT = """
SELECT count(*)
  FROM batch_run b
  JOIN fhir_resources r
    ON r.job_id = b.ingest_job_id
   AND r.resource_type = 'Patient'
   AND r.persisted
   AND r.lifecycle_status <> 'rejected'
 WHERE b.status IN ('ready', 'running', 'prioritized')
   AND b.ingest_job_id IS NOT NULL
   AND b.started_at = (
       SELECT max(started_at)
         FROM batch_run
        WHERE status IN ('ready', 'running', 'prioritized') AND ingest_job_id IS NOT NULL
   )
"""

_LATEST_CALCULATION_DURATION = """
SELECT extract(epoch FROM (b.updated_at - e.run_at))::double precision AS seconds
  FROM engine_runs e
  JOIN batch_run b ON b.counts #>> '{recalculation,measures,engine_run_id}' = e.run_id
 ORDER BY e.run_at DESC
 LIMIT 1
"""


def overview(conn: psycopg.Connection) -> dict:
    """Every measure the engine runs, with its latest populations and gaps, and the latest engine run."""
    year = SETTINGS.measurement_year
    with conn.cursor(row_factory=dict_row) as cur:
        counts = {r["measure_id"]: r for r in cur.execute(_COUNTS, {"year": year}).fetchall()}
        gaps = {r["measure_id"]: r for r in cur.execute(_GAPS, {"year": year}).fetchall()}
        names = {r["measure_id"]: r["name"] for r in cur.execute(
            "SELECT measure_id, name FROM measure_rule WHERE measurement_year = %s", (year,)).fetchall()}
        starred = {r["measure_id"] for r in cur.execute(
            "SELECT DISTINCT measure_id FROM star_cutpoint WHERE measurement_year = %s", (year,)).fetchall()}
        run = cur.execute("""SELECT run_id, engine, engine_version, measurement_year, run_at
                               FROM engine_runs ORDER BY run_at DESC LIMIT 1""").fetchone()
        duration = cur.execute(_LATEST_CALCULATION_DURATION).fetchone()
    out = []
    for bundle in measures.measure_bundles():
        m = bundle.measure_id
        c, g = counts.get(m, {}), gaps.get(m, {})
        out.append({
            "measure": m, "name": names.get(m, m), "library": bundle.library, "library_version": bundle.library_version,
            "bundle": bundle.path.name, "star_measure": m in starred, "inverse": m in measures.INVERSE_MEASURES,
            "evaluated": c.get("evaluated", 0), "denominator": c.get("denominator", 0),
            "excluded": c.get("excluded", 0), "met": c.get("met", 0), "not_met": c.get("not_met", 0),
            "computed_at": c.get("computed_at"), "gaps_open": g.get("open", 0), "gaps_closed": g.get("closed", 0),
            "confirmed_by_reviewer": g.get("confirmed_by_reviewer", 0)})
    return {"measurement_year": year, "engine": measures.ENGINE, "engine_version": measures.ENGINE_VERSION,
            "latest_run": run, "latest_calculation_seconds": duration["seconds"] if duration else None, "measures": out}


def patients(conn: psycopg.Connection, measure_id: str) -> dict:
    """Per patient for one measure: the engine's populations from its latest result, and the gap if there is one."""
    known = {b.measure_id for b in measures.measure_bundles()}
    if measure_id not in known:
        raise LookupError(f"measure {measure_id} is not run by the engine here")
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(_PATIENTS, {"measure": measure_id, "year": SETTINGS.measurement_year}).fetchall()
    for r in rows:
        if not r["in_denominator"]:
            r["outcome"] = "not in population"
        elif r["excluded"]:
            r["outcome"] = "excluded"
        elif r["in_numerator"]:
            r["outcome"] = "met"
        else:
            r["outcome"] = "gap"
    return {"measure": measure_id, "measurement_year": SETTINGS.measurement_year,
            "inverse": measure_id in measures.INVERSE_MEASURES, "patients": rows}


def run_candidates(conn: psycopg.Connection, offset: int, limit: int) -> dict:
    """Return one small page of stored patients that may be selected for an engine run."""
    page_size = min(max(limit, 1), 10)
    page_offset = max(offset, 0)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(_RUN_CANDIDATES, {"limit": page_size, "offset": page_offset}).fetchall()
        total = cur.execute(_RUN_CANDIDATE_COUNT).fetchone()["count"]
    return {"patients": rows, "offset": page_offset, "limit": page_size, "total": total}
