from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_auth_service_contains_admin_user_management_calls() -> None:
    source = (ROOT / "ui" / "src" / "services" / "auth.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/auth/signin")',
        'hasOpenApiPath(paths, "/api/auth/signup")',
        'hasOpenApiPath(paths, "/api/auth/me")',
        'hasOpenApiPath(paths, "/api/admin/users")',
        'withApiBase("/admin/users")',
        'withApiBase(`/admin/users/${userId}/access`)',
        'withApiBase(`/admin/users/${userId}/role`)',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_auth_service_excludes_quality_or_gap_paths() -> None:
    source = (ROOT / "ui" / "src" / "services" / "auth.service.ts").read_text(encoding="utf-8")

    excluded_fragments = [
        'withApiBase("/quality")',
        'withApiBase("/gaps")',
    ]

    for fragment in excluded_fragments:
        assert fragment not in source


def test_backend_source_declares_admin_endpoints() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.get("/api/admin/users")',
        '@app.put("/api/admin/users/{user_id}/access")',
        '@app.put("/api/admin/users/{user_id}/role")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source
