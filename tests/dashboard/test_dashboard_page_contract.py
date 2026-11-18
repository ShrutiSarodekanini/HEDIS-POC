from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_page_contains_core_cards_and_navigation() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Dashboard",
        "Measure performance",
        "Gap priority mix",
        "Gap workbench - human-in-the-loop closure",
        "Overall star calculation",
        "Recent member scoring rows",
        "Evidence review queue",
    ]
    required_actions = [
        "Open full review",
        "Confirm evidence",
        "Close gap",
        "Open Star Rating",
    ]

    for section in required_sections:
        assert section in source
    for action in required_actions:
        assert action in source


def test_dashboard_page_excludes_setup_or_ingestion_only_copy() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")

    excluded_fragments = [
        "Run Ingestion",
        "Loading quality capabilities from backend",
        "Loading star rating capabilities from backend",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source
