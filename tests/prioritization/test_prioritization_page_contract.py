from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prioritization_page_contains_core_sections_and_navigation() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "PrioritizationPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Prioritization",
        "Scoring snapshot",
        "Filters",
        "Prioritized worklist",
        "Gap score breakdown",
        "Star impact",
    ]
    required_fragments = [
        "member_outreach_call: \"Member outreach call\"",
        "Re-check prioritization endpoints",
        "No open gaps found for selected filters.",
        "Select a gap from the worklist to inspect details.",
        "prio-channel-kpi",
    ]

    for section in required_sections:
        assert section in source
    for fragment in required_fragments:
        assert fragment in source


def test_prioritization_page_excludes_irrelevant_auth_or_quality_text() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "PrioritizationPage.tsx").read_text(encoding="utf-8")

    excluded_fragments = [
        "Authentication endpoints are not available",
        "Run checks now",
        "Sign in with your approved account",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source
