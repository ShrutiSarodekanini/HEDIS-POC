from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_administrator_page_contains_access_summary_and_actions() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "AdministratorPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Administrator",
        "Access summary",
        "Registered users",
        "Pending approval",
        "Administrator access required.",
    ]
    required_actions = [
        "Approve",
        "Reject",
        "Set {labelCase(nextRole)}",
    ]

    for section in required_sections:
        assert section in source
    for action in required_actions:
        assert action in source


def test_administrator_page_excludes_merge_conflict_and_debug_markers() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "AdministratorPage.tsx").read_text(encoding="utf-8")

    excluded_fragments = [
        "<<<<<<<",
        ">>>>>>>",
        "console.log(",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source
