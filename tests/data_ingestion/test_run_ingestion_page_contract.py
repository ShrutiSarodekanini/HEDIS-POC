from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_run_ingestion_page_contains_core_sections_and_actions() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "RunIntegrationPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Run Ingestion",
        "New ingestion run",
        "Live run status",
        "Feed completeness scorecard",
        "Ingestion Results",
        "Validation issues",
        "Patients and FHIR",
        "Job history",
    ]
    required_actions = [
        "Upload files",
        "Inspect dataset",
        "Start ingestion",
        "Download selected FHIR",
        "Download all FHIR",
    ]

    for section in required_sections:
        assert section in source
    for action in required_actions:
        assert action in source


def test_run_ingestion_page_excludes_stale_or_unwired_copy() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "RunIntegrationPage.tsx").read_text(encoding="utf-8")

    excluded = [
        "Run selected measures",
        "Run AI evidence extraction",
        "Star controls",
        "TODO",
        "<<<<<<<",
        ">>>>>>>",
    ]

    for fragment in excluded:
        assert fragment not in source
