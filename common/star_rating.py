from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.star_modeler.main import (
    CMS_MEASURE_SET_URL,
    CMS_RATING_YEAR,
    CMS_TECHNICAL_NOTES_URL,
)


@dataclass(frozen=True)
class FinalizedMeasureResult:
    """Legacy request shape kept for backward compatibility with older callers."""

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
    """Compatibility result wrapper matching the historical response schema."""

    measure_code: str
    measurement_year: int
    cms_rating_year: int
    input_scoring_value: float | int | None
    assigned_star_rating: int | None
    rating_status: str
    explanation: str
    applied_cut_points: tuple[dict[str, Any], ...]
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
            "scoring_direction": "higher_is_better",
            "applied_cut_points": list(self.applied_cut_points),
            "methodology_version": "migrated-to-services.star_modeler.main",
            "source_url": CMS_TECHNICAL_NOTES_URL,
            "calculation_version": None,
            "specification_version": None,
        }


def get_star_config(measure_code: str, cms_rating_year: int) -> None:
    """Deprecated: star cut points now come from database lookups in star_modeler."""
    _ = (measure_code, cms_rating_year)
    return None


def assign_star_rating(
    result: FinalizedMeasureResult,
    cms_rating_year: int,
    configs: Any = None,
) -> StarRatingResult:
    """Deprecated compatibility shim; no embedded thresholds remain in this module."""
    _ = configs
    return StarRatingResult(
        measure_code=result.measure_code.upper(),
        measurement_year=result.measurement_year,
        cms_rating_year=cms_rating_year,
        input_scoring_value=result.scoring_value,
        assigned_star_rating=None,
        rating_status="unavailable",
        explanation=(
            "Legacy common.star_rating no longer stores hardcoded thresholds. "
            "Use services.star_modeler.main with database star_cutpoint data."
        ),
        applied_cut_points=(),
        applied_scoring_value=None,
    )


def compare_star_ratings(
    before: FinalizedMeasureResult,
    after: FinalizedMeasureResult,
    cms_rating_year: int,
    configs: Any = None,
) -> dict[str, Any]:
    """Compatibility compare response using legacy unavailable results."""
    _ = configs
    before_result = assign_star_rating(before, cms_rating_year)
    after_result = assign_star_rating(after, cms_rating_year)
    return {
        "before": before_result.to_dict(),
        "after": after_result.to_dict(),
        "star_rating_changed": False,
        "note": "Use services.star_modeler.main for database-driven star comparisons.",
    }
