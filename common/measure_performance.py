from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from common.star_rating import (
    CMS_MEASURE_SET_URL,
    CMS_RATING_YEAR,
    CMS_TECHNICAL_NOTES_URL,
    FinalizedMeasureResult,
    assign_star_rating,
    get_star_config,
)

MOCK_DATA_DISCLAIMER = (
    "Performance inputs are mock POC data. Star assignments use the official CMS "
    "2027 measure-level cut points and are not official plan results."
)
@dataclass(frozen=True)
class MockGap:
    gap_id: str
    description: str
    evidence_status: str
    in_measurement_period: bool = True
    duplicate_of: str | None = None

    @property
    def eligible_for_closure(self) -> bool:
        return (
            self.evidence_status == "approved"
            and self.in_measurement_period
            and self.duplicate_of is None
        )


@dataclass(frozen=True)
class MockMeasure:
    code: str
    name: str
    denominator: int
    numerator: int
    measurement_year: int
    gaps: tuple[MockGap, ...]


def _approved_gaps(code: str, count: int) -> tuple[MockGap, ...]:
    return tuple(
        MockGap(
            gap_id=f"{code}-GAP-{index:02d}",
            description=f"Eligible mock care gap {index}",
            evidence_status="approved",
        )
        for index in range(1, count + 1)
    )


MOCK_MEASURES: tuple[MockMeasure, ...] = (
    MockMeasure(
        "GSD",
        "Glycemic Status Assessment for Patients With Diabetes",
        100,
        65,
        2026,
        _approved_gaps("GSD", 5)
        + (
            MockGap("GSD-GAP-06", "Awaiting human evidence review", "pending"),
            MockGap("GSD-GAP-07", "Evidence was rejected", "rejected"),
        ),
    ),
    MockMeasure(
        "EED",
        "Eye Exam for Patients With Diabetes",
        100,
        58,
        2026,
        _approved_gaps("EED", 4)
        + (MockGap("EED-GAP-05", "Evidence is outside the measurement period", "approved", False),),
    ),
    MockMeasure(
        "BPD",
        "Blood Pressure Control for Patients With Diabetes",
        100,
        62,
        2026,
        _approved_gaps("BPD", 3),
    ),
    MockMeasure(
        "KED",
        "Kidney Health Evaluation for Patients With Diabetes",
        0,
        0,
        2026,
        (),
    ),
    MockMeasure(
        "CBP",
        "Controlling High Blood Pressure",
        100,
        72,
        2026,
        _approved_gaps("CBP", 8)
        + (MockGap("CBP-GAP-09", "Duplicate of CBP-GAP-01", "approved", duplicate_of="CBP-GAP-01"),),
    ),
    MockMeasure(
        "BCS-E",
        "Breast Cancer Screening",
        100,
        68,
        2026,
        _approved_gaps("BCS-E", 6),
    ),
    MockMeasure(
        "COL-E",
        "Colorectal Cancer Screening",
        100,
        65,
        2026,
        _approved_gaps("COL-E", 5),
    ),
    MockMeasure("CCS-E", "Cervical Cancer Screening", 100, 61, 2026, _approved_gaps("CCS-E", 4)),
    MockMeasure("SPD", "Statin Therapy for Patients With Diabetes", 100, 55, 2026, _approved_gaps("SPD", 3)),
    MockMeasure(
        "AAB",
        "Avoidance of Antibiotic Treatment for Acute Bronchitis/Bronchiolitis",
        100,
        80,
        2026,
        _approved_gaps("AAB", 2),
    ),
)

_MEASURES_BY_CODE = {measure.code: measure for measure in MOCK_MEASURES}


def calculate_rate(numerator: int, denominator: int) -> float | None:
    """Return a percentage, or None when there is no eligible denominator."""
    if denominator == 0:
        return None
    return numerator / denominator * 100


def _star_rating(measure: MockMeasure, rate: float | None) -> int | None:
    if rate is None:
        return None
    result = assign_star_rating(
        FinalizedMeasureResult(
            measure_code=measure.code,
            measurement_year=measure.measurement_year,
            scoring_value=rate,
            denominator=measure.denominator,
        ),
        CMS_RATING_YEAR,
    )
    return result.assigned_star_rating


def _metric_payload(numerator: int, denominator: int, open_gaps: int, measure: MockMeasure) -> dict[str, Any]:
    rate = calculate_rate(numerator, denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "performance_rate": rate,
        "open_care_gaps": open_gaps,
        "sample_star_rating": _star_rating(measure, rate),
    }


def get_measure_catalog() -> list[dict[str, Any]]:
    return [
        {
            "code": measure.code,
            "name": measure.name,
            "measurement_year": measure.measurement_year,
            "star_eligible": get_star_config(measure.code, CMS_RATING_YEAR) is not None,
        }
        for measure in MOCK_MEASURES
    ]


def compare_measure(measure_code: str, selected_gap_ids: list[str]) -> dict[str, Any]:
    measure = _MEASURES_BY_CODE.get(measure_code.upper())
    if measure is None:
        raise KeyError(f"Unknown mock measure code: {measure_code}")

    gaps_by_id = {gap.gap_id: gap for gap in measure.gaps}
    unique_selected_ids = list(dict.fromkeys(selected_gap_ids))
    unknown_gap_ids = [gap_id for gap_id in unique_selected_ids if gap_id not in gaps_by_id]
    if unknown_gap_ids:
        raise ValueError(
            f"Selected care gap(s) do not belong to measure {measure.code}: "
            + ", ".join(unknown_gap_ids)
        )

    selected_gaps = [gaps_by_id[gap_id] for gap_id in unique_selected_ids]
    eligible_gaps = [gap for gap in selected_gaps if gap.eligible_for_closure]
    warnings = [
        f"{gap.gap_id} was not counted because evidence status is {gap.evidence_status}."
        for gap in selected_gaps
        if gap.evidence_status != "approved"
    ]
    warnings.extend(
        f"{gap.gap_id} was not counted because evidence is outside the measurement period."
        for gap in selected_gaps
        if gap.evidence_status == "approved" and not gap.in_measurement_period
    )
    warnings.extend(
        f"{gap.gap_id} was not counted because it duplicates {gap.duplicate_of}."
        for gap in selected_gaps
        if gap.duplicate_of is not None
    )

    baseline = _metric_payload(
        measure.numerator, measure.denominator, len(measure.gaps), measure
    )
    recalculated_numerator = measure.numerator + len(eligible_gaps)
    recalculated = _metric_payload(
        recalculated_numerator,
        measure.denominator,
        len(measure.gaps) - len(eligible_gaps),
        measure,
    )
    baseline_rate = baseline["performance_rate"]
    recalculated_rate = recalculated["performance_rate"]
    absolute_change = (
        None
        if baseline_rate is None or recalculated_rate is None
        else recalculated_rate - baseline_rate
    )
    relative_improvement = (
        None
        if baseline_rate in (None, 0) or recalculated_rate is None
        else (recalculated_rate - baseline_rate) / baseline_rate * 100
    )
    star_changed = (
        baseline["sample_star_rating"] is not None
        and baseline["sample_star_rating"] != recalculated["sample_star_rating"]
    )
    star_config = get_star_config(measure.code, CMS_RATING_YEAR)

    return {
        "disclaimer": MOCK_DATA_DISCLAIMER,
        "calculation_status": "completed",
        "last_calculated_at": datetime.now(timezone.utc).isoformat(),
        "measure": {
            "code": measure.code,
            "name": measure.name,
            "measurement_year": measure.measurement_year,
            "cms_rating_year": CMS_RATING_YEAR,
            "cms_measure_set_url": CMS_MEASURE_SET_URL,
            "cms_technical_notes_url": CMS_TECHNICAL_NOTES_URL,
            "rating_methodology": (
                star_config.methodology
                if star_config
                else "No verified CMS Star mapping is configured for this measure and rating year."
            ),
            "star_rating_verified": star_config is not None,
        },
        "baseline": baseline,
        "recalculated": recalculated,
        "changes": {
            "performance_rate_change_percentage_points": absolute_change,
            "relative_improvement_percent": relative_improvement,
            "gaps_closed": len(eligible_gaps),
            "star_rating_changed": star_changed,
        },
        "selected_gap_ids": unique_selected_ids,
        "eligible_gap_ids": [gap.gap_id for gap in eligible_gaps],
        "warnings": warnings,
        "care_gaps": [
            {
                "gap_id": gap.gap_id,
                "description": gap.description,
                "evidence_status": gap.evidence_status,
                "in_measurement_period": gap.in_measurement_period,
                "eligible_for_closure": gap.eligible_for_closure,
            }
            for gap in measure.gaps
        ],
    }
