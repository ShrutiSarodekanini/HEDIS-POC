"""Test suite for measure_performance.compare_measure() star calculation with exclusions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common.measure_performance import compare_measure


def test_compare_measure_handles_exclusions_in_star_calculation() -> None:
    """
    Test that exclusion gaps correctly reduce the denominator in recalculated star.

    Scenario:
    - Baseline: 10 gaps, 8 closed (rate: 80%)
    - Selected: 2 gaps to close; 1 is exclusion, 1 is closure
    - Expected after closing: 9 gaps (denominator), 8 closed (numerator)
      = 88.89% rate (not 90% from 10 gaps)

    This ensures the projected star matches what actually happens after gap closure.
    """
    session = MagicMock()

    measure_row = ("COL-E", "Colorectal Cancer Screening", 2026)
    # Cutpoints: (stars, minimum_rate)
    star_cutpoints = [(1, 0), (2, 25), (3, 50), (4, 75), (5, 90)]

    def execute_side_effect(text_obj, params=None):
        query_str = str(text_obj)
        mock = MagicMock()

        if "measure_id, name, measurement_year" in query_str:
            mock.first.return_value = measure_row
            return mock
        elif "SELECT care_gap_id, description, status" in query_str:
            mock.all.return_value = [
                ("gap-1", "Screening due", "open"),
                ("gap-2", "Screening due", "open"),
                ("gap-3", "Screening due", "closed"),
            ]
            return mock
        elif "count(*) FILTER" in query_str:
            mock.first.return_value = (10, 8)  # engine counts: denom=10, num=8
            return mock
        elif "d.review_status" in query_str:
            mock.all.return_value = []  # no pending counts
            return mock
        elif "evidence_proposal p" in query_str:
            mock.scalar.return_value = 1  # 1 exclusion in selected gaps
            return mock
        elif "star_rating, rate_low_pct" in query_str:
            mock.all.return_value = star_cutpoints
            return mock
        return mock

    session.execute.side_effect = execute_side_effect

    result = compare_measure(session, "COL-E", ["gap-1", "gap-2"])

    # Baseline: 8/10 = 80% -> 4 stars (80 >= 75, < 90)
    assert result["baseline"]["numerator"] == 8
    assert result["baseline"]["denominator"] == 10
    assert result["baseline"]["performance_rate"] == 80.0
    assert result["baseline"]["sample_star_rating"] == 4

    # Recalculated: gap-1 is exclusion (removes from denom), gap-2 is closure (adds to num)
    # 9/9 = 100% -> 5 stars (100 >= 90)
    assert result["recalculated"]["numerator"] == 9
    assert result["recalculated"]["denominator"] == 9
    assert result["recalculated"]["performance_rate"] == 100.0
    assert result["recalculated"]["sample_star_rating"] == 5
    assert result["changes"]["star_rating_changed"] is True


def test_compare_measure_all_closures_no_exclusions() -> None:
    """Test the case where selected gaps are all closures (no exclusions)."""
    session = MagicMock()

    measure_row = ("CBP", "Controlling Blood Pressure", 2026)
    star_cutpoints = [(1, 0), (2, 25), (3, 50), (4, 75), (5, 90)]

    def execute_side_effect(text_obj, params=None):
        query_str = str(text_obj)
        mock = MagicMock()

        if "measure_id, name, measurement_year" in query_str:
            mock.first.return_value = measure_row
            return mock
        elif "SELECT care_gap_id, description, status" in query_str:
            mock.all.return_value = [
                ("gap-1", "BP check due", "open"),
                ("gap-2", "BP check due", "open"),
            ]
            return mock
        elif "count(*) FILTER" in query_str:
            mock.first.return_value = (10, 7)  # engine counts
            return mock
        elif "d.review_status" in query_str:
            mock.all.return_value = []  # pending_counts
            return mock
        elif "evidence_proposal p" in query_str:
            mock.scalar.return_value = 0  # no exclusions
            return mock
        elif "star_rating, rate_low_pct" in query_str:
            mock.all.return_value = star_cutpoints
            return mock
        return mock

    session.execute.side_effect = execute_side_effect

    result = compare_measure(session, "CBP", ["gap-1", "gap-2"])

    # Baseline: 7/10 = 70% -> 3 stars (70 >= 50, < 75)
    assert result["baseline"]["performance_rate"] == 70.0
    assert result["baseline"]["sample_star_rating"] == 3

    # Recalculated: both are closures, denominator unchanged, numerator increases
    # 9/10 = 90% -> 5 stars (90 >= 90)
    assert result["recalculated"]["numerator"] == 9
    assert result["recalculated"]["denominator"] == 10
    assert result["recalculated"]["performance_rate"] == 90.0
    assert result["recalculated"]["sample_star_rating"] == 5
    assert result["changes"]["star_rating_changed"] is True


def test_compare_measure_denominator_adjustment_with_exclusion() -> None:
    """Test that denominator correctly adjusts when exclusion evidence is applied."""
    session = MagicMock()

    measure_row = ("COL-E", "Colorectal Cancer Screening", 2026)
    star_cutpoints = [(1, 0), (2, 25), (3, 50), (4, 70), (5, 90)]

    def execute_side_effect(text_obj, params=None):
        query_str = str(text_obj)
        mock = MagicMock()

        if "measure_id, name, measurement_year" in query_str:
            mock.first.return_value = measure_row
            return mock
        elif "SELECT care_gap_id, description, status" in query_str:
            mock.all.return_value = [
                ("gap-1", "Screening due", "open"),
                ("gap-2", "Screening due", "open"),
            ]
            return mock
        elif "count(*) FILTER" in query_str:
            mock.first.return_value = (5, 4)  # engine: 4/5 = 80%
            return mock
        elif "d.review_status" in query_str:
            mock.all.return_value = []  # pending_counts
            return mock
        elif "evidence_proposal p" in query_str:
            mock.scalar.return_value = 1  # gap-1 is exclusion
            return mock
        elif "star_rating, rate_low_pct" in query_str:
            mock.all.return_value = star_cutpoints
            return mock
        return mock

    session.execute.side_effect = execute_side_effect

    result = compare_measure(session, "COL-E", ["gap-1", "gap-2"])

    # Baseline: 4/5 = 80% -> 4 stars (80 >= 70, < 90)
    assert result["baseline"]["numerator"] == 4
    assert result["baseline"]["denominator"] == 5
    assert result["baseline"]["performance_rate"] == 80.0
    assert result["baseline"]["sample_star_rating"] == 4

    # Recalculated: 1 exclusion reduces denom from 5 to 4, no new numerator additions
    # 4/4 = 100% -> 5 stars (100 >= 90)
    assert result["recalculated"]["numerator"] == 4
    assert result["recalculated"]["denominator"] == 4
    assert result["recalculated"]["performance_rate"] == 100.0
    assert result["recalculated"]["sample_star_rating"] == 5
    assert result["changes"]["star_rating_changed"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
