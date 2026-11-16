from __future__ import annotations

from services.integration import evidence, measures


class _Result:
    def __init__(self, row: tuple[str] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[str] | None:
        return self._row


class _CloseConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Result:
        self.statements.append(statement)
        if statement.startswith("SELECT status FROM care_gap"):
            return _Result(("open",))
        if statement.startswith("SELECT review_status FROM care_gap_detail"):
            return _Result(("closed_pending_engine",))
        raise AssertionError(f"Unexpected SQL statement: {statement}")

    def commit(self) -> None:
        self.commits += 1


def test_confirmed_supplemental_outcomes_update_engine_populations() -> None:
    patients = ["patient-1", "patient-2", "patient-3"]
    denominator = [True, True, True]
    numerator = [False, False, False]
    excluded = [False, False, False]

    measures._apply_supplemental_outcomes(
        patients,
        denominator,
        numerator,
        excluded,
        {
            "patient-1": "closed_pending_engine",
            "patient-2": "excluded_pending_engine",
        },
    )

    assert denominator == [True, True, True]
    assert numerator == [True, False, False]
    assert excluded == [False, True, False]


def test_close_request_leaves_gap_pending_until_engine_rerun(monkeypatch) -> None:
    conn = _CloseConnection()
    monkeypatch.setattr(evidence, "audit", lambda *args, **kwargs: None)

    result = evidence.close_gap(
        conn,
        "00000000-0000-0000-0000-000000000001",
        actor_user_id=7,
        actor="reviewer",
    )

    assert result["status"] == "open"
    assert result["review_status"] == "closed_pending_engine"
    assert conn.commits == 1
    assert all(not statement.startswith("UPDATE care_gap") for statement in conn.statements)
