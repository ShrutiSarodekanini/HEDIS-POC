from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


CMS_TECHNICAL_NOTES_URL = (
    "https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf"
)
CMS_MEASURE_SET_URL = (
    "https://www.cms.gov/files/document/2027-star-ratings-measures.pdf"
)
CMS_RATING_YEAR = 2027


@dataclass(frozen=True)
class StarCutPoint:
    stars: int
    minimum_value: Decimal | None


@dataclass(frozen=True)
class StarRatingConfig:
    hedis_measure_code: str
    cms_measure_code: str
    cms_rating_year: int
    scoring_direction: str
    input_value_kind: str
    cut_points: tuple[StarCutPoint, ...]
    rounding_rule: str
    boundary_rule: str
    source_url: str
    methodology_version: str
    methodology: str


@dataclass(frozen=True)
class FinalizedMeasureResult:
    measure_code: str
    measurement_year: int
    scoring_value: float | int | None
    denominator: int | None
    result_status: str = "completed"
    suppression_status: str | None = None
    reliability_status: str | None = None
    calculation_version: str | None = None
    specification_version: str | None = None
    scoring_value_kind: str = "cms_scoring_value"


@dataclass(frozen=True)
class StarRatingResult:
    measure_code: str
    measurement_year: int
    cms_rating_year: int
    input_scoring_value: float | int | None
    assigned_star_rating: int | None
    rating_status: str
    explanation: str
    scoring_direction: str | None
    applied_cut_points: tuple[dict[str, Any], ...]
    methodology_version: str | None
    source_url: str | None
    calculation_version: str | None
    specification_version: str | None
    applied_scoring_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "measure_code": self.measure_code,
            "measurement_year": self.measurement_year,
            "cms_rating_year": self.cms_rating_year,
            "input_scoring_value": self.input_scoring_value,
            "applied_scoring_value": self.applied_scoring_value,
            "assigned_star_rating": self.assigned_star_rating,
            "rating_status": self.rating_status,
            "explanation": self.explanation,
            "scoring_direction": self.scoring_direction,
            "applied_cut_points": list(self.applied_cut_points),
            "methodology_version": self.methodology_version,
            "source_url": self.source_url,
            "calculation_version": self.calculation_version,
            "specification_version": self.specification_version,
        }


def _cut_points(*values: float) -> tuple[StarCutPoint, ...]:
    return tuple(
        [StarCutPoint(1, None)]
        + [
            StarCutPoint(stars, Decimal(str(value)))
            for stars, value in zip(range(2, 6), values)
        ]
    )


def _config(
    hedis_code: str,
    cms_code: str,
    thresholds: tuple[float, float, float, float],
    measure_name: str,
) -> StarRatingConfig:
    return StarRatingConfig(
        hedis_measure_code=hedis_code,
        cms_measure_code=cms_code,
        cms_rating_year=2027,
        scoring_direction="higher_is_better",
        input_value_kind="cms_scoring_value",
        cut_points=_cut_points(*thresholds),
        rounding_rule=(
            "No assignment rounding; CMS Technical Notes specify percentage "
            "display with no decimal place. Boundary comparisons are inclusive."
        ),
        boundary_rule="Each listed lower bound is inclusive; values below 2-Star are 1 Star.",
        source_url=CMS_TECHNICAL_NOTES_URL,
        methodology_version="CMS-2027-Technical-Notes-2026-09-30",
        methodology=f"CMS 2027 Technical Notes, {cms_code} {measure_name}.",
    )


# Official CMS 2027 Part C measure-level cut points. This is the only threshold registry.
STAR_RATING_CONFIGS: Mapping[tuple[str, int], StarRatingConfig] = {
    (config.hedis_measure_code, config.cms_rating_year): config
    for config in (
        _config("BCS-E", "C01", (63, 70, 77, 83), "Breast Cancer Screening"),
        _config("COL-E", "C02", (59, 68, 75, 82), "Colorectal Cancer Screening"),
        _config("EED", "C11", (61, 72, 79, 86), "Diabetes Care - Eye Exam"),
        _config(
            "GSD",
            "C12",
            (59, 82, 89, 92),
            "Diabetes Care - Blood Sugar Controlled",
        ),
        _config(
            "KED",
            "C13",
            (46, 63, 72, 83),
            "Kidney Health Evaluation for Patients with Diabetes",
        ),
        _config("CBP", "C14", (72, 79, 84, 90), "Controlling Blood Pressure"),
    )
}


def get_star_config(measure_code: str, cms_rating_year: int) -> StarRatingConfig | None:
    return STAR_RATING_CONFIGS.get((measure_code.upper(), cms_rating_year))


def _unavailable(
    result: FinalizedMeasureResult,
    cms_rating_year: int,
    explanation: str,
    config: StarRatingConfig | None = None,
) -> StarRatingResult:
    return StarRatingResult(
        measure_code=result.measure_code.upper(),
        measurement_year=result.measurement_year,
        cms_rating_year=cms_rating_year,
        input_scoring_value=result.scoring_value,
        assigned_star_rating=None,
        rating_status="unavailable",
        explanation=explanation,
        scoring_direction=config.scoring_direction if config else None,
        applied_cut_points=_cut_point_dicts(config) if config else (),
        methodology_version=config.methodology_version if config else None,
        source_url=config.source_url if config else None,
        calculation_version=result.calculation_version,
        specification_version=result.specification_version,
    )


def _cut_point_dicts(config: StarRatingConfig) -> tuple[dict[str, Any], ...]:
    return tuple(
        {"stars": point.stars, "minimum_value": float(point.minimum_value)
         if point.minimum_value is not None else None}
        for point in config.cut_points
    )


def assign_star_rating(
    result: FinalizedMeasureResult,
    cms_rating_year: int,
    configs: Mapping[tuple[str, int], StarRatingConfig] = STAR_RATING_CONFIGS,
) -> StarRatingResult:
    measure_code = result.measure_code.strip().upper()
    if not measure_code:
        return _unavailable(result, cms_rating_year, "Measure code is required.")
    if result.measurement_year < 1:
        return _unavailable(result, cms_rating_year, "Measurement year must be positive.")
    if result.denominator is None or result.denominator < 0:
        return _unavailable(result, cms_rating_year, "Denominator must be zero or greater.")
    if result.denominator == 0:
        return _unavailable(result, cms_rating_year, "Star rating is unavailable for a zero denominator.")
    if result.result_status.lower() != "completed":
        return _unavailable(
            result, cms_rating_year, f"Result status is {result.result_status}; a completed result is required."
        )
    if result.suppression_status and result.suppression_status.lower() not in {"none", "not_suppressed"}:
        return _unavailable(result, cms_rating_year, f"Result is suppressed: {result.suppression_status}.")
    if result.reliability_status and result.reliability_status.lower() not in {"met", "passed", "not_applicable"}:
        return _unavailable(result, cms_rating_year, f"Reliability requirement not met: {result.reliability_status}.")

    config = configs.get((measure_code, cms_rating_year))
    if config is None:
        return _unavailable(
            result,
            cms_rating_year,
            f"No verified CMS Star mapping is configured for {measure_code} in rating year {cms_rating_year}.",
        )
    if result.scoring_value_kind != config.input_value_kind:
        return _unavailable(
            result,
            cms_rating_year,
            f"Expected {config.input_value_kind}; received {result.scoring_value_kind}. "
            "The Star component does not reverse-score or otherwise transform the input.",
            config,
        )
    if result.scoring_value is None:
        return _unavailable(result, cms_rating_year, "A finalized scoring value is required.", config)
    if isinstance(result.scoring_value, bool) or not math.isfinite(float(result.scoring_value)):
        return _unavailable(result, cms_rating_year, "Scoring value must be a finite number.", config)
    try:
        scoring_value = Decimal(str(result.scoring_value))
    except (InvalidOperation, ValueError):
        return _unavailable(result, cms_rating_year, "Scoring value must be numeric.", config)
    if scoring_value < 0 or scoring_value > 100:
        return _unavailable(result, cms_rating_year, "Scoring value must be between 0 and 100.", config)

    assigned = max(
        point.stars
        for point in config.cut_points
        if point.minimum_value is None or scoring_value >= point.minimum_value
    )
    return StarRatingResult(
        measure_code=measure_code,
        measurement_year=result.measurement_year,
        cms_rating_year=cms_rating_year,
        input_scoring_value=result.scoring_value,
        assigned_star_rating=assigned,
        rating_status="assigned",
        explanation=config.methodology,
        scoring_direction=config.scoring_direction,
        applied_cut_points=_cut_point_dicts(config),
        methodology_version=config.methodology_version,
        source_url=config.source_url,
        calculation_version=result.calculation_version,
        specification_version=result.specification_version,
        applied_scoring_value=float(scoring_value),
    )


def compare_star_ratings(
    before: FinalizedMeasureResult,
    after: FinalizedMeasureResult,
    cms_rating_year: int,
    configs: Mapping[tuple[str, int], StarRatingConfig] = STAR_RATING_CONFIGS,
) -> dict[str, Any]:
    before_result = assign_star_rating(before, cms_rating_year, configs)
    after_result = assign_star_rating(after, cms_rating_year, configs)
    return {
        "before": before_result.to_dict(),
        "after": after_result.to_dict(),
        "star_rating_changed": (
            before_result.rating_status == "assigned"
            and after_result.rating_status == "assigned"
            and before_result.assigned_star_rating != after_result.assigned_star_rating
        ),
    }
