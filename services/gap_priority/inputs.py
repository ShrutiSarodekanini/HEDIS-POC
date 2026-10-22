"""Everything a scoring run needs, read for all open gaps at once in a few set-based queries.

Only facts the scores use leave the database: dates of visits, the practice's closure rate, counts and cut points.
No patient ID, name, birth date or document text is carried into Python.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .config import SETTINGS

# Gaps that still need work: open for the engine and not settled by our review.
_GAPS_SQL = """
WITH open_gaps AS (
    SELECT g.gap_id, g.patient_id, g.measure_id, g.measurement_year, g.deadline_date, d.seed_code, d.attributed_org_id
      FROM care_gap g
      LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
     WHERE g.status = 'open' AND (d.review_status IS NULL OR d.review_status NOT IN ('closed', 'excluded'))
), encounter_days AS (
    SELECT patient_id, status, (period_start AT TIME ZONE %(tz)s)::date AS day
      FROM encounter
     WHERE patient_id IN (SELECT patient_id FROM open_gaps)
), visits AS (
    SELECT patient_id,
           min(day) FILTER (WHERE status = 'planned' AND day >= %(as_of)s) AS next_visit,
           max(day) FILTER (WHERE status = 'finished' AND day <= %(as_of)s) AS last_contact
      FROM encounter_days
     GROUP BY patient_id
), waiting AS (
    SELECT DISTINCT gap_id FROM evidence_proposal WHERE status = 'proposed'
)
SELECT o.gap_id, coalesce(o.seed_code, o.gap_id::text) AS key, o.measure_id, o.measurement_year,
       o.deadline_date AS deadline, o.attributed_org_id, q.closure_rate,
       w.gap_id IS NOT NULL AS evidence_waiting, v.next_visit, v.last_contact
  FROM open_gaps o
  LEFT JOIN organization_quality q ON q.organization_id = o.attributed_org_id
  LEFT JOIN visits v ON v.patient_id = o.patient_id
  LEFT JOIN waiting w ON w.gap_id = o.gap_id
 ORDER BY key
"""

# Excluded members leave the denominator, as in the measure itself.
_MEASURES_SQL = """
WITH rates AS (
    SELECT measure_id, measurement_year,
           count(*) FILTER (WHERE coalesce(in_denominator, false) AND NOT coalesce(excluded, false)) AS denominator,
           count(*) FILTER (WHERE coalesce(in_denominator, false) AND NOT coalesce(excluded, false)
                              AND coalesce(in_numerator, false)) AS numerator
      FROM v_measure_result_latest
     GROUP BY measure_id, measurement_year
)
SELECT m.measure_id, m.measurement_year, w.weight,
       coalesce(r.numerator, 0) AS numerator, coalesce(r.denominator, 0) AS denominator
  FROM (SELECT DISTINCT measure_id, measurement_year FROM care_gap WHERE status = 'open') m
  LEFT JOIN rates r ON r.measure_id = m.measure_id AND r.measurement_year = m.measurement_year
  LEFT JOIN measure_star_weight w
         ON w.measure_id = m.measure_id AND w.measurement_year = m.measurement_year AND w.star_year = %(star_year)s
"""

_CUT_POINTS_SQL = """
SELECT DISTINCT measure_id, measurement_year, star_rating, rate_low_pct
  FROM star_cutpoint
 WHERE (measure_id, measurement_year) IN (SELECT measure_id, measurement_year FROM care_gap WHERE status = 'open')
 ORDER BY measure_id, measurement_year, star_rating
"""


@dataclass(frozen=True)
class GapInput:
    """One open gap and the facts about it that the scores use."""
    gap_id: UUID
    key: str                       # seed code (G-0001) or the gap id: shown on screens and used to break ties
    measure_id: str
    measurement_year: int
    deadline: date | None
    attributed_org_id: str | None
    closure_rate: float | None     # the attributed practice's track record, when known
    evidence_waiting: bool         # an evidence proposal for this gap is waiting for review
    next_visit: date | None        # earliest planned visit on or after the run date
    last_contact: date | None      # latest finished visit on or before the run date


@dataclass(frozen=True)
class MeasureInput:
    """One measure's current rate and star position."""
    measure_id: str
    measurement_year: int
    weight: int | None             # None: not in Star Ratings
    numerator: int
    denominator: int
    cut_points: tuple[tuple[int, Decimal], ...]   # (star, rate_low_pct), lowest star first


def read_inputs(conn: psycopg.Connection, as_of: date, star_year: int,
                timezone: str = SETTINGS.timezone) -> tuple[list[GapInput], dict[tuple[str, int], MeasureInput]]:
    """All open gaps, and every measure they belong to keyed by (measure_id, measurement_year)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_GAPS_SQL, {"tz": timezone, "as_of": as_of})
        gaps = [GapInput(**{**row, "closure_rate": _as_float(row["closure_rate"])}) for row in cur.fetchall()]
        cur.execute(_CUT_POINTS_SQL)
        cut_points = _group_cut_points(cur.fetchall())
        cur.execute(_MEASURES_SQL, {"star_year": star_year})
        measures = {
            (row["measure_id"], row["measurement_year"]): MeasureInput(
                **row, cut_points=cut_points.get((row["measure_id"], row["measurement_year"]), ()))
            for row in cur.fetchall()
        }
    return gaps, measures


def _group_cut_points(rows: list[dict]) -> dict[tuple[str, int], tuple[tuple[int, Decimal], ...]]:
    """Cut points per measure. Two different lower bounds for one star would make the star position a guess."""
    grouped: dict[tuple[str, int], dict[int, Decimal]] = {}
    for row in rows:
        measure = (row["measure_id"], row["measurement_year"])
        stars = grouped.setdefault(measure, {})
        low = row["rate_low_pct"]
        if low is None or (row["star_rating"] in stars and stars[row["star_rating"]] != low):
            raise ValueError(f"star_cutpoint for {measure[0]} {measure[1]} star {row['star_rating']} has no single "
                             "rate_low_pct; fix the cut points before scoring")
        stars[row["star_rating"]] = low
    return {measure: tuple(sorted(stars.items())) for measure, stars in grouped.items()}


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
