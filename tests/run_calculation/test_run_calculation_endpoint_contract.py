from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_calculation_service_contains_integration_and_star_profiles() -> None:
    source = (ROOT / "ui" / "src" / "services" / "calculation.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/calculation")',
        'hasOpenApiPath(paths, "/api/calculation/run")',
        'hasOpenApiPath(paths, "/api/star-dashboard/calculate")',
        'hasOpenApiPath(paths, "/api/automation/process-gaps")',
        'withApiBase("/calculation")',
        'withApiBase(`/calculation/${encodeURIComponent(measureId)}/patients`)',
        'withApiBase("/calculation/run")',
        'withApiBase(`/batches/${encodeURIComponent(batchId)}`)',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_calculation_service_excludes_auth_runtime_calls() -> None:
    source = (ROOT / "ui" / "src" / "services" / "calculation.service.ts").read_text(encoding="utf-8")

    assert 'withApiBase("/auth/signin")' not in source


def test_backend_source_declares_calculation_endpoints() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.get("/api/calculation")',
        '@app.get("/api/calculation/{measure_id}/patients")',
        '@app.post("/api/calculation/run", status_code=202)',
        '@app.post("/api/automation/process-gaps")',
        '@app.post("/api/star-dashboard/calculate")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source
