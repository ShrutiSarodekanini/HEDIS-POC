from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.star_modeler.main import (
    CMS_MEASURE_SET_URL,
    CMS_RATING_YEAR,
    CMS_TECHNICAL_NOTES_URL,
    assign_star_from_sqlalchemy,
    has_cutpoints_sqlalchemy,
)

LIVE_DATA_DISCLAIMER = (
    "Performance inputs are derived from the database state. "
    "Star assignments use thresholds in star_cutpoint."
)


def calculate_rate(numerator: int, denominator: int) -> float | None:
    """Return a percentage, or None when denominator is not positive."""
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100, 2)


def _metric_payload(
    session: Session,
    *,
    measure_code: str,
    measurement_year: int,
    numerator: int,
    denominator: int,
    open_gaps: int,
) -> dict[str, Any]:
    rate = calculate_rate(numerator, denominator)
    star = assign_star_from_sqlalchemy(
        session,
        measure_code=measure_code,
        measurement_year=measurement_year,
        scoring_value=rate,
        denominator=denominator,
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "performance_rate": rate,
        "open_care_gaps": open_gaps,
        "sample_star_rating": star.assigned_star_rating,
        "star_rating_status": star.rating_status,
    }


def get_measure_catalog(session: Session) -> list[dict[str, Any]]:
    """List measures from the database with star-cutpoint availability."""
    rows = session.execute(
        text(
            """SELECT measure_id, name, measurement_year
                 FROM measures
                ORDER BY measure_id"""
        )
    ).all()

    out: list[dict[str, Any]] = []
    for row in rows:
        measure_code = str(row[0])
        measurement_year = int(row[2])
        out.append(
            {
                "code": measure_code,
                "name": str(row[1]),
                "measurement_year": measurement_year,
                "star_eligible": has_cutpoints_sqlalchemy(
                    session,
                    measure_code=measure_code,
                    measurement_year=measurement_year,
                ),
            }
        )
    return out


def compare_measure(
    session: Session,
    measure_code: str,
    selected_gap_ids: list[str],
) -> dict[str, Any]:
    """Compare baseline and projected performance for one measure from live DB rows."""
    code = measure_code.upper().strip()
    measure_row = session.execute(
        text(
            """SELECT measure_id, name, measurement_year
                 FROM measures
                WHERE upper(measure_id) = :code"""
        ),
        {"code": code},
    ).first()
    if measure_row is None:
        raise KeyError(f"Unknown measure code: {measure_code}")

    actual_code = str(measure_row[0])
    measurement_year = int(measure_row[2])

    gap_rows = session.execute(
        text(
            """SELECT care_gap_id, description, status
                 FROM care_gaps
                WHERE upper(measure_id) = :code
                ORDER BY care_gap_id"""
        ),
        {"code": code},
    ).all()

    gaps_by_id = {str(row[0]): row for row in gap_rows}
    unique_selected_ids = list(dict.fromkeys(selected_gap_ids))
    unknown_gap_ids = [gap_id for gap_id in unique_selected_ids if gap_id not in gaps_by_id]
    if unknown_gap_ids:
        raise ValueError(
            f"Selected care gap(s) do not belong to measure {actual_code}: "
            + ", ".join(unknown_gap_ids)
        )

    total_gaps = len(gap_rows)
    closed_statuses = {"closed", "complete", "completed", "resolved"}
    baseline_closed = sum(1 for row in gap_rows if str(row[2] or "").strip().lower() in closed_statuses)

    warnings: list[str] = []
    eligible_selected: list[str] = []
    for gap_id in unique_selected_ids:
        status = str(gaps_by_id[gap_id][2] or "").strip().lower()
        if status in closed_statuses:
            warnings.append(f"{gap_id} was not counted because it is already closed.")
            continue
        eligible_selected.append(gap_id)

    baseline = _metric_payload(
        session,
        measure_code=actual_code,
        measurement_year=measurement_year,
        numerator=baseline_closed,
        denominator=total_gaps,
        open_gaps=total_gaps - baseline_closed,
    )
    recalculated_closed = baseline_closed + len(eligible_selected)
    recalculated = _metric_payload(
        session,
        measure_code=actual_code,
        measurement_year=measurement_year,
        numerator=recalculated_closed,
        denominator=total_gaps,
        open_gaps=max(total_gaps - recalculated_closed, 0),
    )

    baseline_rate = baseline["performance_rate"]
    recalculated_rate = recalculated["performance_rate"]
    absolute_change = (
        None
        if baseline_rate is None or recalculated_rate is None
        else round(recalculated_rate - baseline_rate, 2)
    )
    relative_improvement = (
        None
        if baseline_rate in (None, 0) or recalculated_rate is None
        else round(((recalculated_rate - baseline_rate) / baseline_rate) * 100, 2)
    )

    baseline_star = baseline["sample_star_rating"]
    recalculated_star = recalculated["sample_star_rating"]
    star_changed = (
        baseline_star is not None
        and recalculated_star is not None
        and baseline_star != recalculated_star
    )

    return {
        "disclaimer": LIVE_DATA_DISCLAIMER,
        "calculation_status": "completed",
        "last_calculated_at": datetime.now(timezone.utc).isoformat(),
        "measure": {
            "code": actual_code,
            "name": str(measure_row[1]),
            "measurement_year": measurement_year,
            "cms_rating_year": CMS_RATING_YEAR,
            "cms_measure_set_url": CMS_MEASURE_SET_URL,
            "cms_technical_notes_url": CMS_TECHNICAL_NOTES_URL,
            "rating_methodology": "Database star_cutpoint thresholds",
            "star_rating_verified": has_cutpoints_sqlalchemy(
                session,
                measure_code=actual_code,
                measurement_year=measurement_year,
            ),
        },
        "baseline": baseline,
        "recalculated": recalculated,
        "changes": {
            "performance_rate_change_percentage_points": absolute_change,
            "relative_improvement_percent": relative_improvement,
            "gaps_closed": len(eligible_selected),
            "star_rating_changed": star_changed,
        },
        "selected_gap_ids": unique_selected_ids,
        "eligible_gap_ids": eligible_selected,
        "warnings": warnings,
        "care_gaps": [
            {
                "gap_id": str(row[0]),
                "description": str(row[1]),
                "evidence_status": str(row[2]),
                "in_measurement_period": True,
                "eligible_for_closure": str(row[2] or "").strip().lower() not in closed_statuses,
            }
            for row in gap_rows
        ],
    }
