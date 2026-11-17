from __future__ import annotations

from typing import Any

import httpx
import pytest


DASHBOARD_REQUIRED_PATHS = {
    "/api/star-rating/catalog",
    "/api/star-rating/calculate",
    "/api/star-rating/compare",
    "/api/star-dashboard/calculate",
}


def _dashboard_available(paths: set[str]) -> bool:
    return DASHBOARD_REQUIRED_PATHS.issubset(paths)


def _first_catalog_measure(catalog_payload: dict[str, Any]) -> dict[str, Any] | None:
    measures = catalog_payload.get("measures")
    if not isinstance(measures, list) or not measures:
        return None

    first = measures[0]
    return first if isinstance(first, dict) else None


def _first_open_gap_id(summary_payload: dict[str, Any]) -> str | None:
    gaps = summary_payload.get("selected_measure_gaps")
    if not isinstance(gaps, list):
        return None

    for row in gaps:
        if not isinstance(row, dict):
            continue
        if bool(row.get("is_closed")):
            continue
        gap_id = row.get("care_gap_id")
        if isinstance(gap_id, str) and gap_id:
            return gap_id
    return None


def test_star_dashboard_summary_positive_shape(client: httpx.Client, openapi_paths: set[str]) -> None:
    if not _dashboard_available(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    catalog_payload = catalog.json()

    first_measure = _first_catalog_measure(catalog_payload)
    measure_id = first_measure.get("measure_code") if first_measure else None

    summary = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": measure_id, "selected_gap_ids": []},
    )
    assert summary.status_code == 200, summary.text
    payload = summary.json()

    for key in ("cms_reference", "overall", "measure_cards", "selected_gap_ids"):
        assert key in payload

    assert isinstance(payload["measure_cards"], list)
    assert isinstance(payload["selected_gap_ids"], list)

    overall = payload.get("overall")
    assert isinstance(overall, dict)
    assert "baseline_star" in overall
    assert "projected_star" in overall


def test_star_dashboard_projection_increases_or_holds_projected_rate(client: httpx.Client, openapi_paths: set[str]) -> None:
    if not _dashboard_available(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    first_measure = _first_catalog_measure(catalog.json())
    if first_measure is None:
        pytest.skip("Catalog has no measure rows to project.")

    measure_id = first_measure.get("measure_code")
    assert isinstance(measure_id, str) and measure_id

    baseline_summary = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": measure_id, "selected_gap_ids": []},
    )
    assert baseline_summary.status_code == 200, baseline_summary.text
    baseline_payload = baseline_summary.json()

    selected_measure = baseline_payload.get("selected_measure")
    if not isinstance(selected_measure, dict):
        pytest.skip("No selected_measure payload is available for this backend data state.")

    baseline_projected_rate = selected_measure.get("projected_rate")
    baseline_projected_closed = selected_measure.get("projected_closed")

    open_gap_id = _first_open_gap_id(baseline_payload)
    if open_gap_id is None:
        pytest.skip("No open gaps are available for projection in the selected measure.")

    projected_summary = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": measure_id, "selected_gap_ids": [open_gap_id]},
    )
    assert projected_summary.status_code == 200, projected_summary.text
    projected_payload = projected_summary.json()

    projected_measure = projected_payload.get("selected_measure")
    assert isinstance(projected_measure, dict)

    next_projected_rate = projected_measure.get("projected_rate")
    next_projected_closed = projected_measure.get("projected_closed")

    assert isinstance(projected_payload.get("selected_gap_ids"), list)
    assert open_gap_id in projected_payload["selected_gap_ids"]

    if isinstance(baseline_projected_closed, int) and isinstance(next_projected_closed, int):
        assert next_projected_closed >= baseline_projected_closed

    if isinstance(baseline_projected_rate, (int, float)) and isinstance(next_projected_rate, (int, float)):
        assert float(next_projected_rate) >= float(baseline_projected_rate)


def test_star_dashboard_invalid_payload_negative(client: httpx.Client, openapi_paths: set[str]) -> None:
    if "/api/star-dashboard/calculate" not in openapi_paths:
        return

    invalid_selected_gap_ids = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": None, "selected_gap_ids": "not-a-list"},
    )
    assert invalid_selected_gap_ids.status_code == 422, invalid_selected_gap_ids.text

    invalid_measure_id = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": ["CBP"], "selected_gap_ids": []},
    )
    assert invalid_measure_id.status_code == 422, invalid_measure_id.text


def test_star_rating_calculate_zero_denominator_unavailable(client: httpx.Client, openapi_paths: set[str]) -> None:
    if not _dashboard_available(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    first_measure = _first_catalog_measure(catalog.json())
    measure_id = first_measure.get("measure_code") if first_measure else "CBP"
    assert isinstance(measure_id, str) and measure_id

    payload = {
        "cms_rating_year": 2027,
        "result": {
            "measure_code": measure_id,
            "measurement_year": 2026,
            "scoring_value": 50.0,
            "denominator": 0,
        },
    }
    calculated = client.post("/api/star-rating/calculate", json=payload)
    assert calculated.status_code == 200, calculated.text
    body = calculated.json()

    assert body.get("rating_status") == "unavailable"
    assert body.get("assigned_star_rating") is None


def test_star_rating_compare_positive_and_negative_validation(client: httpx.Client, openapi_paths: set[str]) -> None:
    if not _dashboard_available(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    first_measure = _first_catalog_measure(catalog.json())
    measure_id = first_measure.get("measure_code") if first_measure else "CBP"
    assert isinstance(measure_id, str) and measure_id

    valid_compare = {
        "cms_rating_year": 2027,
        "before": {
            "measure_code": measure_id,
            "measurement_year": 2026,
            "scoring_value": 50.0,
            "denominator": 100,
        },
        "after": {
            "measure_code": measure_id,
            "measurement_year": 2026,
            "scoring_value": 65.0,
            "denominator": 100,
        },
    }
    compared = client.post("/api/star-rating/compare", json=valid_compare)
    assert compared.status_code == 200, compared.text
    compared_payload = compared.json()

    for key in ("before", "after", "star_rating_changed"):
        assert key in compared_payload

    invalid_compare = client.post(
        "/api/star-rating/compare",
        json={"before": {"measure_code": measure_id}},
    )
    assert invalid_compare.status_code == 422, invalid_compare.text
