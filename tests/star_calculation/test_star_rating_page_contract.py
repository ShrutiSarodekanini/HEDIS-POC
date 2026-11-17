from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAR_COMPONENTS_DIR = ROOT / "ui" / "src" / "components" / "stars"


def test_star_rating_page_contains_core_sections_and_actions() -> None:
    page_source = (ROOT / "ui" / "src" / "pages" / "StarRatingPage.tsx").read_text(encoding="utf-8")
    sections_source = (STAR_COMPONENTS_DIR / "StarRatingSections.tsx").read_text(encoding="utf-8")
    controls_source = (STAR_COMPONENTS_DIR / "StarControls.tsx").read_text(encoding="utf-8")
    gap_source = (STAR_COMPONENTS_DIR / "StarGapSelection.tsx").read_text(encoding="utf-8")
    snapshot_source = (STAR_COMPONENTS_DIR / "StarOverallSnapshot.tsx").read_text(encoding="utf-8")
    projection_source = (STAR_COMPONENTS_DIR / "StarMeasureProjection.tsx").read_text(encoding="utf-8")
    calc_source = (STAR_COMPONENTS_DIR / "StarCalculationResults.tsx").read_text(encoding="utf-8")
    cards_source = (STAR_COMPONENTS_DIR / "StarMeasureCards.tsx").read_text(encoding="utf-8")
    cut_points_source = (STAR_COMPONENTS_DIR / "StarCutPoints.tsx").read_text(encoding="utf-8")

    combined_source = "\n".join(
        [
            page_source,
            sections_source,
            controls_source,
            gap_source,
            snapshot_source,
            projection_source,
            calc_source,
            cards_source,
            cut_points_source,
        ]
    )

    required_sections = [
        "Star controls",
        "Overall snapshot",
        "Selected measure projection",
        "Gap selection",
        "Direct calculation results",
        "Measure cards",
        "Catalog cut points",
    ]
    required_actions = [
        "Apply selected closures",
        "Calculate current star",
        "Compare before vs after",
        "Select all open",
        "Clear",
    ]

    for section in required_sections:
        assert section in combined_source
    for action in required_actions:
        assert action in combined_source


def test_star_rating_page_includes_loading_and_retry_states() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "StarRatingPage.tsx").read_text(encoding="utf-8")

    required_fragments = [
        "Loading star rating capabilities from backend...",
        "Star rating API unavailable:",
        "Re-check star endpoints",
        "<StarRatingSections workflow={workflow} />",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_star_workflow_contains_zero_denominator_guardrails() -> None:
    source = (ROOT / "ui" / "src" / "hooks" / "useStarsWorkflow.ts").read_text(encoding="utf-8")

    required_fragments = [
        'const NO_GAPS_STAR_MESSAGE = "No gaps to calculate the star."',
        "const isZeroDenominatorMessage = (message: string): boolean => {",
        "setErrorMessage(NO_GAPS_STAR_MESSAGE)",
        "setErrorMessage(isZeroDenominatorMessage(message) ? NO_GAPS_STAR_MESSAGE : message)",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_star_page_excludes_merge_conflict_markers() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "StarRatingPage.tsx").read_text(encoding="utf-8")
    sections_source = (STAR_COMPONENTS_DIR / "StarRatingSections.tsx").read_text(encoding="utf-8")

    combined_source = f"{source}\n{sections_source}"

    assert "<<<<<<<" not in combined_source
    assert "=======" not in combined_source
    assert ">>>>>>>" not in combined_source
