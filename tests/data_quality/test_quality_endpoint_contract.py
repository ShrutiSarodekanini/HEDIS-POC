from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quality_service_contains_quality_profile_endpoints_only() -> None:
    source = (ROOT / "ui" / "src" / "services" / "quality.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        '"/api/quality"',
        '"/api/quality/runs"',
        '"/api/quality/runs/{run_id}"',
        '"/api/quality/coverage"',
        'withApiBase("/quality")',
        'withApiBase("/quality/runs")',
        'withApiBase(`/quality/runs/${encodeURIComponent(String(runId))}${query}`)',
        'withApiBase(`/quality/coverage?${query}`)',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_quality_service_excludes_unrelated_runtime_paths() -> None:
    source = (ROOT / "ui" / "src" / "services" / "quality.service.ts").read_text(encoding="utf-8")

    excluded_fragments = [
        'withApiBase("/auth/signin")',
        'withApiBase("/stars/catalog")',
        'withApiBase("/supplemental/export")',
    ]

    for fragment in excluded_fragments:
        assert fragment not in source


def test_backend_source_declares_quality_routes() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    integration_source = (ROOT / "services" / "integration" / "routes.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.get("/api/quality")',
        '@app.get("/api/quality/runs")',
        '@app.get("/api/quality/runs/{run_id}")',
        '@app.post("/api/quality/runs")',
        '@app.get("/api/quality/coverage")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source or decorator.replace("@app", "@router") in integration_source
