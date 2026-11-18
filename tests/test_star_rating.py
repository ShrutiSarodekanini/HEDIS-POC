from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import create_app
from common.config import AppConfig
from common.star_rating import (
    CMS_TECHNICAL_NOTES_URL,
    FinalizedMeasureResult,
    assign_star_rating,
    compare_star_ratings,
)


def _result(**overrides) -> FinalizedMeasureResult:
    values = {
        "measure_code": "CBP",
        "measurement_year": 2026,
        "scoring_value": 84,
        "denominator": 100,
        "calculation_version": "hedis-engine-1",
        "specification_version": "MY2026",
    }
    values.update(overrides)
    return FinalizedMeasureResult(**values)


def test_assigns_official_rating_and_trace_metadata():
    result = assign_star_rating(_result(), 2027)
    assert result.rating_status == "assigned"
    assert result.assigned_star_rating == 4
    assert result.scoring_direction == "higher_is_better"
    assert result.source_url == CMS_TECHNICAL_NOTES_URL
    assert result.methodology_version == "CMS-2027-Technical-Notes-2026-09-30"
    assert result.input_scoring_value == 84


def test_gsd_requires_cms_scoring_value_and_does_not_reverse_score():
    assigned = assign_star_rating(
        _result(measure_code="GSD", scoring_value=82), 2027
    )
    assert assigned.assigned_star_rating == 3

    rejected = assign_star_rating(
        _result(
            measure_code="GSD",
            scoring_value=18,
            scoring_value_kind="hedis_rate",
        ),
        2027,
    )
    assert rejected.rating_status == "unavailable"
    assert "does not reverse-score" in rejected.explanation


def test_unavailable_for_zero_invalid_suppressed_and_unreliable_results():
    for result in (
        _result(denominator=0),
        _result(scoring_value=float("nan")),
        _result(suppression_status="suppressed"),
        _result(reliability_status="failed"),
        _result(result_status="pending"),
    ):
        assert assign_star_rating(result, 2027).rating_status == "unavailable"


def test_unavailable_for_unsupported_measure_and_rating_year():
    unsupported = assign_star_rating(_result(measure_code="CCS-E"), 2027)
    missing_year = assign_star_rating(_result(), 2026)
    assert unsupported.assigned_star_rating is None
    assert missing_year.assigned_star_rating is None
    assert "No verified CMS Star mapping" in missing_year.explanation


def test_fractional_values_are_not_rounded_for_assignment():
    below = assign_star_rating(_result(scoring_value=83.99), 2027)
    at_boundary = assign_star_rating(_result(scoring_value=84.0), 2027)
    assert below.assigned_star_rating == 3
    assert at_boundary.assigned_star_rating == 4


def test_compare_reports_star_change_only_when_both_are_assigned():
    comparison = compare_star_ratings(
        _result(scoring_value=83),
        _result(scoring_value=90),
        2027,
    )
    assert comparison["before"]["assigned_star_rating"] == 3
    assert comparison["after"]["assigned_star_rating"] == 5
    assert comparison["star_rating_changed"] is True

    unavailable = compare_star_ratings(
        _result(denominator=0),
        _result(scoring_value=90),
        2027,
    )
    assert unavailable["star_rating_changed"] is False


def test_star_rating_api_accepts_finalized_engine_result(tmp_path: Path):
    app = create_app(
        AppConfig(evidence_storage_dir=tmp_path / "evidence"),
        validate_database=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/star-rating/calculate",
            json={
                "cms_rating_year": 2027,
                "result": {
                    "measure_code": "CBP",
                    "measurement_year": 2026,
                    "scoring_value": 84,
                    "denominator": 100,
                    "result_status": "completed",
                    "calculation_version": "hedis-engine-1",
                    "specification_version": "MY2026",
                },
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["assigned_star_rating"] == 4
    assert payload["measurement_year"] == 2026
    assert payload["cms_rating_year"] == 2027
    assert payload["calculation_version"] == "hedis-engine-1"
