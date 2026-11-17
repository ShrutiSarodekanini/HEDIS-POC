from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest


def _first_gap_id(gaps_payload: Any) -> str | None:
    if not isinstance(gaps_payload, list) or not gaps_payload:
        return None
    first = gaps_payload[0]
    if not isinstance(first, dict):
        return None
    gap_id = first.get("gapId")
    return gap_id if isinstance(gap_id, str) and gap_id else None


def _first_measure_id_from_calculation(payload: dict[str, Any]) -> str | None:
    measures = payload.get("measures")
    if not isinstance(measures, list) or not measures:
        return None
    first = measures[0]
    if not isinstance(first, dict):
        return None
    for key in ("measure_id", "measure"):
        value = first.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def test_prioritization_measures_and_gaps_with_auth(
    client: httpx.Client,
    user_headers: dict[str, str],
    openapi_paths: set[str],
) -> None:
    required = {"/api/measures", "/api/gaps"}
    if not required.issubset(openapi_paths):
        return

    measures = client.get("/api/measures", headers=user_headers)
    assert measures.status_code == 200, measures.text
    measures_payload = measures.json()
    assert isinstance(measures_payload, dict)

    gaps = client.get("/api/gaps?limit=5", headers=user_headers)
    assert gaps.status_code == 200, gaps.text
    assert isinstance(gaps.json(), list)


def test_protected_worklist_rejects_missing_auth(client: httpx.Client, openapi_paths: set[str]) -> None:
    if "/api/gaps" not in openapi_paths:
        return

    response = client.get("/api/gaps?limit=1")
    assert response.status_code in {401, 403}, response.text


def test_gap_detail_positive_and_negative(
    client: httpx.Client,
    user_headers: dict[str, str],
    openapi_paths: set[str],
) -> None:
    if "/api/gaps/{gap_id}" not in openapi_paths or "/api/gaps" not in openapi_paths:
        return

    gaps = client.get("/api/gaps?limit=10", headers=user_headers)
    assert gaps.status_code == 200, gaps.text
    gap_id = _first_gap_id(gaps.json())

    if gap_id is not None:
        detail = client.get(f"/api/gaps/{gap_id}", headers=user_headers)
        assert detail.status_code == 200, detail.text
        assert isinstance(detail.json(), dict)

    missing = client.get(f"/api/gaps/{uuid.uuid4()}", headers=user_headers)
    assert missing.status_code in {404, 422}, missing.text


def test_evidence_actions_require_admin(
    client: httpx.Client,
    user_headers: dict[str, str],
    openapi_paths: set[str],
) -> None:
    required = {"/api/gaps", "/api/gaps/{gap_id}/evidence", "/api/supplemental/export"}
    if not required.issubset(openapi_paths):
        return

    gaps = client.get("/api/gaps?limit=10", headers=user_headers)
    assert gaps.status_code == 200, gaps.text
    gap_id = _first_gap_id(gaps.json())
    if gap_id is None:
        pytest.skip("No gap rows are available to test evidence admin-guard behavior.")

    evidence_call = client.post(f"/api/gaps/{gap_id}/evidence", headers=user_headers, json={})
    assert evidence_call.status_code in {401, 403}, evidence_call.text

    export_call = client.post("/api/supplemental/export", headers=user_headers, json={"reviewer": "qa.reviewer"})
    assert export_call.status_code in {401, 403}, export_call.text


def test_quality_endpoints_and_negative_cases(client: httpx.Client, openapi_paths: set[str]) -> None:
    required = {"/api/quality", "/api/quality/runs", "/api/quality/runs/{run_id}"}
    if not required.issubset(openapi_paths):
        return

    overview = client.get("/api/quality")
    assert overview.status_code == 200, overview.text
    assert isinstance(overview.json(), dict)

    missing_param = client.get("/api/quality/runs")
    assert missing_param.status_code == 422, missing_param.text

    unknown_run = client.get("/api/quality/runs/999999999")
    assert unknown_run.status_code in {404, 422}, unknown_run.text


def test_calculation_endpoints_and_validation(client: httpx.Client, openapi_paths: set[str]) -> None:
    required = {"/api/calculation", "/api/calculation/{measure_id}/patients", "/api/calculation/run"}
    if not required.issubset(openapi_paths):
        return

    overview = client.get("/api/calculation")
    assert overview.status_code == 200, overview.text
    overview_payload = overview.json()
    assert isinstance(overview_payload, dict)

    measure_id = _first_measure_id_from_calculation(overview_payload)
    if measure_id is not None:
        patients = client.get(f"/api/calculation/{measure_id}/patients")
        assert patients.status_code == 200, patients.text
        assert isinstance(patients.json(), dict)

    invalid_run = client.post("/api/calculation/run", json={"measures": "not-a-list"})
    assert invalid_run.status_code == 422, invalid_run.text


def test_ingestion_and_flow_profile_endpoints(client: httpx.Client, openapi_paths: set[str]) -> None:
    if "/api/ingestion/jobs" in openapi_paths:
        jobs = client.get("/api/ingestion/jobs")
        assert jobs.status_code == 200, jobs.text
        assert isinstance(jobs.json(), dict)
    elif "/api/jobs" in openapi_paths:
        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200, jobs.text
        assert isinstance(jobs.json(), dict)
    else:
        pytest.skip("No ingestion jobs endpoint profile is available.")

    if "/api/flow" in openapi_paths:
        flow = client.get("/api/flow")
        assert flow.status_code == 200, flow.text
        assert isinstance(flow.json(), dict)
