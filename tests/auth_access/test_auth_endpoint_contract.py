from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_auth_service_contains_core_auth_calls() -> None:
    source = (ROOT / "ui" / "src" / "services" / "auth.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/auth/signin")',
        'hasOpenApiPath(paths, "/api/auth/signup")',
        'hasOpenApiPath(paths, "/api/auth/me")',
        'withApiBase("/auth/signin")',
        'withApiBase("/auth/signup")',
        'withApiBase("/auth/me")',
        'withApiBase("/auth/signout")',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_auth_service_excludes_evidence_export_path() -> None:
    source = (ROOT / "ui" / "src" / "services" / "auth.service.ts").read_text(encoding="utf-8")

    assert 'withApiBase("/supplemental/export")' not in source


def test_backend_source_declares_auth_endpoints() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.post("/api/auth/signin")',
        '@app.post("/api/auth/signup")',
        '@app.get("/api/auth/me")',
        '@app.post("/api/auth/signout")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source
