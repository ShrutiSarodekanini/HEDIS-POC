from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app

ROOT = Path(__file__).resolve().parents[2]


def _row(
    gap_id: str,
    *,
    gap_status: str,
    review_status: str,
    engine_met: bool = False,
    engine_excluded: bool = False,
    projected_outcome: str | None = "closure",
) -> dict[str, object]:
    proposed_evidence = None
    if projected_outcome is not None:
        proposed_evidence = [
            {
                "evidence_id": f"evidence-{gap_id}",
                "kind": "exclusion" if projected_outcome == "exclusion" else "numerator",
                "evidence_type": "hospice" if projected_outcome == "exclusion" else "screening",
                "date_from": None,
                "reason": "Validated evidence",
            }
        ]
    return {
        "care_gap_id": gap_id,
        "member_id": f"member-{gap_id}",
        "measure_id": "COL-E",
        "measurement_year": 2026,
        "status": gap_status,
        "review_status": review_status,
        "engine_run_id": "stale-run",
        "current_in_denominator": True,
        "current_in_numerator": engine_met,
        "current_excluded": engine_excluded,
        "measure_rule_kind": "single_part",
        "measure_rule_thresholds": {},
        "patient_age": 70,
        "proposed_evidence": proposed_evidence,
        "measure_name": "Measure",
        "first_name": None,
        "last_name": None,
    }


def _dashboard(
    monkeypatch,
    *,
    rows: list[dict[str, object]],
    engine_counts: dict[tuple[str, int], dict[str, int]],
    selected_gap_ids: list[str],
) -> dict[str, object]:
    monkeypatch.setattr(app, "_load_dashboard_rows", lambda session: rows)
    monkeypatch.setattr(
        app,
        "_load_engine_measure_counts",
        lambda session: engine_counts,
    )
    monkeypatch.setattr(
        app,
        "_load_measure_catalog",
        lambda session: {
            ("COL-E", 2026): {
                "measure_id": "COL-E",
                "measurement_year": 2026,
                "measure_name": "Measure",
                "weight": 1,
                "measure_type": "star",
            }
        },
    )
    monkeypatch.setattr(app, "has_cutpoints_sqlalchemy", lambda *args, **kwargs: True)
    monkeypatch.setattr(app, "load_cutpoints_sqlalchemy", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        app,
        "assign_star_from_sqlalchemy",
        lambda session, **kwargs: SimpleNamespace(
            assigned_star_rating=None
            if kwargs["scoring_value"] is None
            else min(int(kwargs["scoring_value"] // 20) + 1, 5)
        ),
    )
    return app._build_star_dashboard(
        object(),
        measure_id="COL-E",
        selected_gap_ids=selected_gap_ids,
    )


def test_selected_gap_projection_matches_engine_rerun_with_stale_gap_detail(monkeypatch) -> None:
    projected = _dashboard(
        monkeypatch,
        rows=[_row("gap-1", gap_status="open", review_status="open")],
        engine_counts={("COL-E", 2026): {"denominator": 10, "numerator": 7}},
        selected_gap_ids=["gap-1"],
    )
    actual = _dashboard(
        monkeypatch,
        rows=[_row("gap-1", gap_status="closed", review_status="closed", engine_met=True)],
        engine_counts={("COL-E", 2026): {"denominator": 10, "numerator": 8}},
        selected_gap_ids=[],
    )

    projected_card = projected["selected_measure"]
    actual_card = actual["selected_measure"]
    assert projected_card["baseline_closed"] == 7
    assert projected_card["projected_closed"] == 8
    assert actual_card["baseline_closed"] == 8
    assert projected_card["projected_closed"] == actual_card["baseline_closed"]
    assert projected_card["projected_rate"] == actual_card["baseline_rate"]
    assert projected["overall"]["projected_star"] == actual["overall"]["baseline_star"]


def test_pending_states_are_not_counted_again_when_selected(monkeypatch) -> None:
    result = _dashboard(
        monkeypatch,
        rows=[
            _row("gap-1", gap_status="open", review_status="closed_pending_engine"),
            _row("gap-2", gap_status="open", review_status="excluded_pending_engine"),
        ],
        engine_counts={("COL-E", 2026): {"denominator": 10, "numerator": 7}},
        selected_gap_ids=["gap-1", "gap-2"],
    )

    card = result["selected_measure"]
    assert card["total_gaps"] == 9
    assert card["baseline_closed"] == 8
    assert card["projected_closed"] == 8
    assert card["projected_rate"] == card["baseline_rate"]


def test_mixed_selected_closure_and_exclusion_matches_engine_rerun(monkeypatch) -> None:
    projected = _dashboard(
        monkeypatch,
        rows=[
            _row("gap-1", gap_status="open", review_status="pending_review"),
            _row(
                "gap-2",
                gap_status="open",
                review_status="pending_review",
                projected_outcome="exclusion",
            ),
        ],
        engine_counts={("COL-E", 2026): {"denominator": 5, "numerator": 1}},
        selected_gap_ids=["gap-1", "gap-2"],
    )
    actual = _dashboard(
        monkeypatch,
        rows=[
            _row("gap-1", gap_status="closed", review_status="closed", engine_met=True),
            _row(
                "gap-2",
                gap_status="closed",
                review_status="excluded",
                engine_excluded=True,
                projected_outcome="exclusion",
            ),
        ],
        engine_counts={("COL-E", 2026): {"denominator": 4, "numerator": 2}},
        selected_gap_ids=[],
    )

    projected_card = projected["selected_measure"]
    actual_card = actual["selected_measure"]
    assert projected_card["total_gaps"] == 5
    assert projected_card["projected_total_gaps"] == 4
    assert projected_card["projected_closed"] == 2
    assert projected_card["projected_rate"] == 50.0
    assert projected_card["projected_rate"] == actual_card["baseline_rate"]
    assert projected["overall"]["projected_star"] == actual["overall"]["baseline_star"]


def test_incomplete_multi_part_exclusion_is_not_projected(monkeypatch) -> None:
    row = _row(
        "gap-1",
        gap_status="open",
        review_status="pending_review",
        projected_outcome="exclusion",
    )
    row["proposed_evidence"] = [
        {
            "evidence_id": "evidence-gap-1",
            "kind": "exclusion",
            "evidence_type": "frailty_and_advanced_illness_age_66_plus",
            "date_from": None,
            "reason": "[part: frailty] Validated evidence",
        }
    ]
    result = _dashboard(
        monkeypatch,
        rows=[row],
        engine_counts={("COL-E", 2026): {"denominator": 5, "numerator": 1}},
        selected_gap_ids=["gap-1"],
    )

    card = result["selected_measure"]
    assert card["projected_total_gaps"] == 5
    assert card["projected_closed"] == 1
    assert card["projected_rate"] == card["baseline_rate"]


def test_fallback_counts_finalized_pending_and_selected_states(monkeypatch) -> None:
    result = _dashboard(
        monkeypatch,
        rows=[
            _row("gap-1", gap_status="closed", review_status="closed"),
            _row("gap-2", gap_status="closed", review_status="excluded"),
            _row("gap-3", gap_status="open", review_status="closed_pending_engine"),
            _row("gap-4", gap_status="open", review_status="excluded_pending_engine"),
            _row("gap-5", gap_status="open", review_status="open"),
        ],
        engine_counts={},
        selected_gap_ids=["gap-3", "gap-4", "gap-5"],
    )

    card = result["selected_measure"]
    assert card["total_gaps"] == 3
    assert card["baseline_closed"] == 2
    assert card["projected_closed"] == 3


def test_gap_close_waits_for_automatic_engine_recalculation() -> None:
    hook_source = (ROOT / "ui" / "src" / "hooks" / "useGapClose.ts").read_text(encoding="utf-8")
    service_source = (ROOT / "ui" / "src" / "services" / "calculation.service.ts").read_text(encoding="utf-8")

    assert "closeCareGap(gapId)" in hook_source
    assert "runCalculationAfterActiveBatch(capabilities, [])" in hook_source
    assert "onClosed()" in hook_source
    assert "error.status !== 409" in service_source
    assert "waitForCalculation(active.batchId)" in service_source
    assert "waitForCalculation(started.batchId)" in service_source
