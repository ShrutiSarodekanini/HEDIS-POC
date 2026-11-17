from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_data_quality_page_contains_anomaly_sections_and_controls() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "DataQualityPage.tsx").read_text(encoding="utf-8")

    required_headings = [
        "Data Anomaly",
        "Run scope",
        "Severity breakdown",
        "Anomalies",
        "Checks",
        "Coverage report",
        "Run checks now",
    ]

    for heading in required_headings:
        assert heading in source


def test_data_quality_page_applies_safe_limits_and_masks() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "DataQualityPage.tsx").read_text(encoding="utf-8")

    expected_fragments = [
        "const MAX_ANOMALIES_PER_GROUP = 20",
        "const MAX_CHECK_ROWS = 200",
        'const HIDDEN_KEYS = new Set(["patient_id", "observation_id", "check"])',
        "group.items.slice(0, MAX_ANOMALIES_PER_GROUP)",
        "filteredChecks.slice(0, MAX_CHECK_ROWS)",
        "coverage.coverage_records.slice(0, 100)",
        "shortPatient(anomaly.patient_id)",
        "shortPatient(row.patient_id)",
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_data_quality_page_excludes_unrelated_workflows() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "DataQualityPage.tsx").read_text(encoding="utf-8")

    excluded_fragments = [
        "Run AI evidence extraction",
        "Apply selected closures",
        "Download selected FHIR",
    ]

    for fragment in excluded_fragments:
        assert fragment not in source
