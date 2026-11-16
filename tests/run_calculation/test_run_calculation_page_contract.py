from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_run_calculation_page_contains_core_sections_and_controls() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "RunCalculationPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Run Gap Calculation",
        "Engine run",
        "Calculation summary",
        "Measure outcome table",
        "Patient-level outcomes",
        "Gap and blocker worklist",
    ]
    required_actions = [
        "Run calculation",
        "Select all",
        "Clear",
        "Open in Evidence Review",
        "Prioritization",
    ]

    for section in required_sections:
        assert section in source
    for action in required_actions:
        assert action in source


def test_run_calculation_page_excludes_unrelated_auth_copy() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "RunCalculationPage.tsx").read_text(encoding="utf-8")

    excluded_fragments = [
        "Authentication endpoints are not available",
        "Create account",
        "Run AI evidence extraction",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source
