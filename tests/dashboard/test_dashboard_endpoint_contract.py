from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_service_uses_catalog_and_dashboard_summary_endpoints() -> None:
    source = (ROOT / "ui" / "src" / "services" / "dashboard.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        "envConfig.dashboardCatalogPath",
        "envConfig.dashboardSummaryPath",
        "selected_gap_ids: []",
        "SNAPSHOT_CACHE_TTL_MS",
        "loadSectionGet<StarCatalogResponse>",
        "loadSectionPost<StarDashboardResponse>",
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_dashboard_service_excludes_auth_and_admin_runtime_calls() -> None:
    source = (ROOT / "ui" / "src" / "services" / "dashboard.service.ts").read_text(encoding="utf-8")

    excluded_fragments = [
        "/auth/signin",
        "/admin/users",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source


def test_backend_source_declares_dashboard_endpoints() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.get("/api/star-rating/catalog")',
        '@app.post("/api/star-dashboard/calculate")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source
