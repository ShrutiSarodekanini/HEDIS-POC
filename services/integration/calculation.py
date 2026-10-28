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
            "latest_run": run, "measures": out}


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
