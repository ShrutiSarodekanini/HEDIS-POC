"""The integration's only bridge to Pradeep's rate, star and what-if code; his files are called as they are.

- calculate_rate and compare_measure: common/measure_performance.py
- assign_star_from_sqlalchemy, load_cutpoints_sqlalchemy, assign_star_from_cutpoints: services/star_modeler/main.py

His functions take a SQLAlchemy session and read star_cutpoint and the views measures and care_gaps (migration 0005).
common/database.py is not used because its validate() checks the alembic revision; this module builds its own
engine on the app's DATABASE_URL, which already carries the schema search_path.

His rate keeps 2 decimals and the star is taken from that value. The gap prioritization score (services/gap_priority)
still rounds the rate to a whole percent first, on purpose (US-05); the two can differ near a cut point.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from common.measure_performance import calculate_rate, compare_measure
from services.document_intake.config import SETTINGS as DB_SETTINGS
from services.star_modeler.main import (assign_star_from_cutpoints, assign_star_from_sqlalchemy,
                                        load_cutpoints_sqlalchemy)

SOURCE = "pradeep.compare_measure"

_COUNTS = text("""
SELECT count(*) FILTER (WHERE in_denominator AND NOT coalesce(excluded, false)) AS denominator,
       count(*) FILTER (WHERE in_denominator AND in_numerator AND NOT coalesce(excluded, false)) AS numerator
  FROM v_measure_result_latest WHERE measure_id = :measure AND measurement_year = :year
""")

# Reviewer-confirmed gaps still open in care_gap, limited to ids his care_gaps view knows (an id it does not know
# would make compare_measure refuse the whole request).
_PENDING = text("""
SELECT g.gap_id::text, d.review_status
  FROM care_gap g JOIN care_gap_detail d ON d.gap_id = g.gap_id
  JOIN care_gaps v ON v.care_gap_id = g.gap_id::text
 WHERE g.measure_id = :measure AND g.measurement_year = :year AND g.status = 'open'
   AND d.review_status IN ('closed_pending_engine', 'excluded_pending_engine')
""")


@cache
def _engine() -> Engine:
    url = DB_SETTINGS.database_url
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def session() -> Iterator[Session]:
    """A read-only session for his functions; nothing is written through it."""
    with sessionmaker(bind=_engine(), autoflush=False)() as s:
        try:
            yield s
        finally:
            s.rollback()


def _star(s: Session, measure_id: str, year: int, rate: float | None, denominator: int) -> int | None:
    return assign_star_from_sqlalchemy(s, measure_code=measure_id, measurement_year=year, scoring_value=rate,
                                       denominator=denominator).assigned_star_rating


def measure_card(s: Session, measure_id: str, year: int) -> dict:
    """Rate (his calculate_rate) and star (his assign_star_from_sqlalchemy), exclusions left out."""
    counts = s.execute(_COUNTS, {"measure": measure_id, "year": year}).one()
    numerator, denominator = counts.numerator, counts.denominator
    rate = calculate_rate(numerator, denominator)
    result = assign_star_from_sqlalchemy(s, measure_code=measure_id, measurement_year=year, scoring_value=rate,
                                         denominator=denominator)
    return {"numerator": numerator, "denominator": denominator, "rate": rate,
            "stars": result.assigned_star_rating, "status": result.rating_status,
            "explanation": result.explanation, "cut_points": list(result.applied_cut_points)}


def projection(s: Session, measure_id: str, year: int) -> dict:
    """His compare_measure with the measure's reviewer-confirmed closures selected, plus confirmed exclusions.

    His what-if has no exclusion case yet (it only adds closures), so confirmed exclusions are applied here with his
    calculate_rate and star: numerator after the closures, denominator minus the exclusions."""
    pending = s.execute(_PENDING, {"measure": measure_id, "year": year}).all()
    closures = [gap_id for gap_id, status in pending if status == "closed_pending_engine"]
    excluded = sum(1 for _, status in pending if status == "excluded_pending_engine")
    compared = compare_measure(s, measure_id, closures)
    after = compared["recalculated"]
    rate = calculate_rate(after["numerator"], after["denominator"] - excluded)
    return {"source": SOURCE, "baseline": compared["baseline"], "recalculated": after,
            "changes": compared["changes"], "warnings": compared["warnings"], "closures_selected": len(closures),
            "exclusions": excluded,
            "projected_rate": rate if excluded else after["performance_rate"],
            "projected_stars": (_star(s, measure_id, year, rate, after["denominator"] - excluded) if excluded
                                else after["sample_star_rating"])}


def gap_impact(s: Session, gap_id: str) -> dict | None:
    """What closing this one gap would do (his compare_measure), the closures still needed for the next star after
    it, and what excluding the patient instead would do. None when the gap is unknown."""
    row = s.execute(text("SELECT measure_id, measurement_year FROM care_gap WHERE gap_id::text = :gap"),
                    {"gap": gap_id}).first()
    if row is None:
        return None
    measure_id, year = row
    if s.execute(text("SELECT 1 FROM care_gaps WHERE care_gap_id = :gap"), {"gap": gap_id}).first() is None:
        return {"source": SOURCE, "measure": measure_id, "available": False,
                "note": "The engine's latest result does not count this patient in the measure, so there is no "
                        "rate change to show."}
    compared = compare_measure(s, measure_id, [gap_id])
    before, after = compared["baseline"], compared["recalculated"]
    cuts = load_cutpoints_sqlalchemy(s, measure_code=measure_id, measurement_year=year)
    denominator, numerator = before["denominator"], before["numerator"]
    excluded_rate = calculate_rate(numerator, denominator - 1)
    excluded_stars = _star(s, measure_id, year, excluded_rate, denominator - 1)
    return {
        "source": SOURCE, "measure": measure_id, "available": True,
        "rate_now": before["performance_rate"], "rate_after": after["performance_rate"],
        "change_points": compared["changes"]["performance_rate_change_percentage_points"],
        "stars_now": before["sample_star_rating"], "stars_after": after["sample_star_rating"],
        "star_changed": compared["changes"]["star_rating_changed"],
        "closures_for_next_star": _closures_for_next_star(measure_id, year, after["numerator"], denominator, cuts),
        "exclusion": {"rate_after": excluded_rate, "stars_after": excluded_stars,
                      "change_points": (None if excluded_rate is None or before["performance_rate"] is None
                                        else round(excluded_rate - before["performance_rate"], 2)),
                      "star_changed": excluded_stars is not None and excluded_stars != before["sample_star_rating"],
                      "note": "His what-if has no exclusion case yet; computed with his rate and star functions."},
    }


def _closures_for_next_star(measure_id: str, year: int, numerator: int, denominator: int,
                            cuts: list[tuple[int, float]]) -> int | None:
    """More closures, on top of the ones counted in numerator, that raise the star by one. None at 5 stars, or when
    closing every remaining gap would not reach the next star."""
    def stars(n: int) -> int | None:
        return assign_star_from_cutpoints(measure_code=measure_id, measurement_year=year,
                                          scoring_value=calculate_rate(n, denominator), denominator=denominator,
                                          cut_points=cuts).assigned_star_rating

    now = stars(numerator)
    if now is None or now >= 5:
        return None
    return next((k for k in range(1, denominator - numerator + 1) if (stars(numerator + k) or 0) > now), None)


_CATALOG = text("""
SELECT r.measure_id, r.name, r.measurement_year, w.weight,
       coalesce(jsonb_agg(jsonb_build_object('stars', c.star_rating, 'low', c.rate_low_pct, 'high', c.rate_high_pct)
                          ORDER BY c.star_rating) FILTER (WHERE c.star_rating IS NOT NULL), '[]'::jsonb) AS cut_points
  FROM measure_rule r
  LEFT JOIN measure_star_weight w ON w.measure_id = r.measure_id AND w.measurement_year = r.measurement_year
                                 AND w.star_year = :star_year
  LEFT JOIN star_cutpoint c ON c.measure_id = r.measure_id AND c.measurement_year = r.measurement_year
 WHERE r.measurement_year = :year
 GROUP BY r.measure_id, r.name, r.measurement_year, w.weight
 ORDER BY r.measure_id
""")


def catalog(s: Session, year: int, star_year: int) -> list[dict]:
    """Every measure with its Star weight and cut points (the tables his catalog screen reads)."""
    return [dict(r._mapping) for r in s.execute(_CATALOG, {"year": year, "star_year": star_year})]


def calculate(s: Session, measure_id: str, year: int, rate: float | None, denominator: int) -> dict:
    """His direct star calculation for a given rate and denominator; nothing is stored."""
    return assign_star_from_sqlalchemy(s, measure_code=measure_id, measurement_year=year, scoring_value=rate,
                                       denominator=denominator).to_dict()


def what_if(s: Session, measure_id: str, gap_ids: list[str]) -> dict:
    """His compare_measure for the chosen gaps (ids from his care_gaps view); nothing is stored. Raises LookupError
    for a measure his measures view does not know."""
    try:
        return compare_measure(s, measure_id, gap_ids)
    except KeyError as exc:
        raise LookupError(f"measure {measure_id} has no results yet") from exc
