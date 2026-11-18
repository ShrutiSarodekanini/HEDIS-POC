from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ingestion_service_supports_adapter_and_direct_profiles() -> None:
    source = (ROOT / "ui" / "src" / "services" / "ingestion.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/ingestion/jobs")',
        'hasOpenApiPath(paths, "/api/ingestion/jobs/{job_id}")',
        'hasOpenApiPath(paths, "/api/jobs")',
        'hasOpenApiPath(paths, "/api/jobs/{job_id}")',
        'return { prefix: "/api/ingestion", flowPath }',
        'return { prefix: "/api", flowPath }',
        'withPrefix(mode, "/jobs")',
        'withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}`)',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_ingestion_service_does_not_route_through_quality_or_auth_paths() -> None:
    source = (ROOT / "ui" / "src" / "services" / "ingestion.service.ts").read_text(encoding="utf-8")

    excluded_fragments = [
        'withApiBase("/quality")',
        'withApiBase("/auth/signin")',
        'withApiBase("/stars/catalog")',
    ]

    for fragment in excluded_fragments:
        assert fragment not in source


def test_backend_source_declares_ingestion_router_and_job_routes() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    ingestion_api_source = (ROOT / "services" / "data_ingestion" / "app" / "api.py").read_text(encoding="utf-8")

    expected_fragments = [
        'app.include_router(_ingestion_routes(), prefix="/api/ingestion")',
        '@router.get("/jobs")',
        '@router.get("/jobs/{job_id}")',
        '@router.post("/jobs/start")',
    ]

    assert expected_fragments[0] in app_source
    for fragment in expected_fragments[1:]:
        assert fragment in ingestion_api_source
