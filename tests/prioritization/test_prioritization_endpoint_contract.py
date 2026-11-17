from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prioritization_service_contains_dual_profile_capabilities() -> None:
    source = (ROOT / "ui" / "src" / "services" / "prioritization.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/measures")',
        'hasOpenApiPath(paths, "/api/gaps")',
        'hasOpenApiPath(paths, "/api/gaps/{gap_id}")',
        'hasOpenApiPath(paths, "/api/star-dashboard/calculate")',
        'hasOpenApiPath(paths, "/api/star-rating/catalog")',
        'withApiBase("/measures")',
        'withApiBase(`/gaps?${queryString}`)',
        'withApiBase(`/gaps/${encodeURIComponent(gapId)}`)',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_prioritization_service_excludes_auth_calls() -> None:
    source = (ROOT / "ui" / "src" / "services" / "prioritization.service.ts").read_text(encoding="utf-8")

    assert 'withApiBase("/auth/signin")' not in source


def test_backend_source_declares_prioritization_endpoints() -> None:
    integration_source = (ROOT / "services" / "integration" / "routes.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@router.get("/api/measures")',
        '@router.get("/api/gaps")',
        '@router.get("/api/gaps/{gap_id}")',
    ]

    for decorator in expected_decorators:
        assert decorator in integration_source
