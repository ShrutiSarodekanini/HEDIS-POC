"""A gap closes only through an engine rerun (services/integration/gap_rerun.py); the guards before it starts."""

import uuid
from datetime import date

import pytest

from services.integration import gap_rerun
from services.integration.evidence import Conflict

GAP = str(uuid.uuid4())


class _Result:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        return self.rows


class _Conn:
    """Answers the three queries request_rerun makes; records commits."""

    def __init__(self, gap: tuple | None, records: list[tuple], batch: tuple | None) -> None:
        self.answers = {gap_rerun._GAP: [gap] if gap else [], gap_rerun._RECORDS: records,
                        gap_rerun._BATCH: [batch] if batch else []}
        self.commits = 0

    def execute(self, sql: str, params: tuple) -> _Result:
        return _Result(self.answers[sql])

    def rollback(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def _gap(status: str = "open", review: str = "closed_pending_engine") -> tuple:
    return ("p-1", "BCS-E", 2026, status, review)


_MAMMOGRAM = (11, "p-1", "BCS-E", "mammogram", date(2026, 6, 15), None)


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(gap_rerun.pipeline, "start_recalculation",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or "batch-9")
    monkeypatch.setattr(gap_rerun, "audit", lambda *args, **kwargs: calls.append(("audit", args, kwargs)))
    return calls


def test_unknown_gap_is_not_found(started: list) -> None:
    with pytest.raises(LookupError):
        gap_rerun.request_rerun(_Conn(None, [], None), GAP, 1, "reviewer@example.org")
    with pytest.raises(LookupError):
        gap_rerun.request_rerun(_Conn(None, [], None), "not-a-uuid", 1, "reviewer@example.org")


@pytest.mark.parametrize(("gap", "message"), [
    (_gap(status="closed"), "already closed"),
    (_gap(review="partially_evidenced"), "Confirm the evidence"),
    (_gap(review="open"), "Confirm the evidence"),
])
def test_refused_unless_every_part_is_confirmed(started: list, gap: tuple, message: str) -> None:
    with pytest.raises(Conflict, match=message):
        gap_rerun.request_rerun(_Conn(gap, [_MAMMOGRAM], ("b", "j")), GAP, 1, "reviewer@example.org")
    assert started == []


def test_refused_when_the_engine_cannot_read_any_confirmed_evidence(started: list) -> None:
    undated = (11, "p-1", "BCS-E", "mammogram", None, None)
    with pytest.raises(Conflict, match="has no service date"):
        gap_rerun.request_rerun(_Conn(_gap(), [undated], ("b", "j")), GAP, 1, "reviewer@example.org")
    assert started == []


def test_refused_when_no_batch_holds_the_patient(started: list) -> None:
    with pytest.raises(Conflict, match="No calculation-ready batch"):
        gap_rerun.request_rerun(_Conn(_gap(), [_MAMMOGRAM], None), GAP, 1, "reviewer@example.org")
    assert started == []


def test_starts_one_patient_one_measure_and_audits_the_request(started: list) -> None:
    conn = _Conn(_gap(), [_MAMMOGRAM], ("batch-1", "job-1"))
    result = gap_rerun.request_rerun(conn, GAP, 5, "reviewer@example.org")

    (args, kwargs), (_, audit_args, audit_kwargs) = started
    assert args == (["BCS-E"], ["p-1"], 5) and kwargs == {"batch": ("batch-1", "job-1")}
    assert audit_args[3] == "gap.engine_rerun_requested"
    assert audit_args[4]["supplemental_record_ids"] == [11]
    assert audit_kwargs == {"actor": "reviewer@example.org"}
    assert conn.commits == 1
    assert result == {"gap_id": GAP, "batch_id": "batch-9", "status": "running", "engine_inputs": 1,
                      "not_usable": []}
