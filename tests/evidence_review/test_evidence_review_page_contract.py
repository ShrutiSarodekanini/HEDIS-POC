from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_evidence_review_page_contains_review_sections_and_actions() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "EvidenceReviewPage.tsx").read_text(encoding="utf-8")

    required_sections = [
        "Evidence Review",
        "Run AI evidence extraction",
        "Audit-first by design",
        "Gap panel",
        "Review queue",
    ]
    required_actions = [
        "Run extraction",
        "Run process now",
        "Export approved evidence to engine",
        "Confirm finding",
        "Reject finding",
    ]

    for section in required_sections:
        assert section in source
    for action in required_actions:
        assert action in source


def test_evidence_review_page_enforces_decision_reason_and_safe_queue_copy() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "EvidenceReviewPage.tsx").read_text(encoding="utf-8")

    expected_fragments = [
        "Decision notes (required on reject)",
        "No proposed evidence is currently waiting for review.",
        "Enforced by table constraints, not UI promise",
    ]

    excluded_fragments = [
        "delete from evidence",
        "skip reviewer",
    ]

    for fragment in expected_fragments:
        assert fragment in source
    for fragment in excluded_fragments:
        assert fragment not in source
