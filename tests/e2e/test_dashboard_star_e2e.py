from __future__ import annotations

from typing import Any

import httpx


def _first_measure_code(catalog_payload: dict[str, Any]) -> str | None:
    measures = catalog_payload.get("measures")
    if not isinstance(measures, list) or not measures:
        return None

    first = measures[0]
    if not isinstance(first, dict):
        return None
    code = first.get("measure_code")
    return code if isinstance(code, str) and code else None


def test_dashboard_catalog_and_summary_success(client: httpx.Client, openapi_paths: set[str]) -> None:
    required = {"/api/star-rating/catalog", "/api/star-dashboard/calculate"}
    if not required.issubset(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    catalog_payload = catalog.json()
    measure_id = _first_measure_code(catalog_payload)

    summary = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": measure_id, "selected_gap_ids": []},
    )
    assert summary.status_code == 200, summary.text
    payload = summary.json()

    for key in ("cms_reference", "overall", "measure_cards", "selected_gap_ids"):
        assert key in payload
    assert isinstance(payload["measure_cards"], list)


def test_dashboard_summary_rejects_invalid_selected_gap_ids(client: httpx.Client, openapi_paths: set[str]) -> None:
    if "/api/star-dashboard/calculate" not in openapi_paths:
        return

    invalid = client.post(
        "/api/star-dashboard/calculate",
        json={"measure_id": None, "selected_gap_ids": "not-a-list"},
    )
    assert invalid.status_code == 422, invalid.text


def test_star_rating_calculate_and_compare_validation(client: httpx.Client, openapi_paths: set[str]) -> None:
    required = {"/api/star-rating/catalog", "/api/star-rating/calculate", "/api/star-rating/compare"}
    if not required.issubset(openapi_paths):
        return

    catalog = client.get("/api/star-rating/catalog")
    assert catalog.status_code == 200, catalog.text
    catalog_payload = catalog.json()
    measure_id = _first_measure_code(catalog_payload) or "CBP"

    calculate_payload = {
        "cms_rating_year": 2027,
        "result": {
            "measure_code": measure_id,
            "measurement_year": 2026,
            "scoring_value": 50.0,
            "denominator": 100,
        },
    }
    calculated = client.post("/api/star-rating/calculate", json=calculate_payload)
    assert calculated.status_code == 200, calculated.text
    calc_data = calculated.json()
    assert "assigned_star_rating" in calc_data
    assert "rating_status" in calc_data

    invalid_compare = client.post(
        "/api/star-rating/compare",
        json={"before": {"measure_code": measure_id}},
    )
    assert invalid_compare.status_code == 422, invalid_compare.text
