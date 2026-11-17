from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_star_service_supports_integration_and_dashboard_profiles() -> None:
    source = (ROOT / "ui" / "src" / "services" / "stars.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        '"/api/stars/catalog"',
        '"/api/stars/calculate"',
        '"/api/stars/what-if"',
        '"/api/star-rating/catalog"',
        '"/api/star-rating/calculate"',
        '"/api/star-dashboard/calculate"',
        'return { mode: "integration" }',
        'return { mode: "dashboard" }',
        "mapCatalogCutPoints",
        "mapCutPointsFromCalculation",
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_star_service_excludes_auth_and_quality_paths() -> None:
    source = (ROOT / "ui" / "src" / "services" / "stars.service.ts").read_text(encoding="utf-8")

    excluded_fragments = [
        'withApiBase("/auth/signin")',
        'withApiBase("/quality")',
    ]

    for fragment in excluded_fragments:
        assert fragment not in source


def test_star_service_contains_cache_and_force_refresh_paths() -> None:
    source = (ROOT / "ui" / "src" / "services" / "stars.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        "STAR_OVERVIEW_CACHE_MS",
        "STAR_WHAT_IF_CACHE_MS",
        "const starOverviewCache = new Map",
        "const starWhatIfCache = new Map",
        "forceFresh",
        "starOverviewInFlight",
        "starWhatIfInFlight",
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_star_service_sends_selected_gap_ids_for_projection_requests() -> None:
    source = (ROOT / "ui" / "src" / "services" / "stars.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        "selected_gap_ids: selectedGapIds",
        "withApiBase(envConfig.dashboardSummaryPath)",
        "withApiBase(\"/stars/what-if\")",
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_backend_source_declares_dashboard_star_routes() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@app.get("/api/star-rating/catalog")',
        '@app.post("/api/star-rating/calculate")',
        '@app.post("/api/star-rating/compare")',
        '@app.post("/api/star-dashboard/calculate")',
    ]

    for decorator in expected_decorators:
        assert decorator in app_source


def test_backend_source_declares_integration_star_routes() -> None:
    integration_source = (ROOT / "services" / "integration" / "routes.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@router.get("/api/stars/catalog")',
        '@router.post("/api/stars/calculate")',
        '@router.post("/api/stars/what-if")',
    ]

    for decorator in expected_decorators:
        assert decorator in integration_source
