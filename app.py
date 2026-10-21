from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import datetime as dt
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.config import AppConfig
from common.database import Database
from common.evidence import (
    ensure_supported_formats,
    extension_for_format,
    generate_document,
    prepare_evidence_data,
)
from common.repository import Repository, row_to_dict
from common.measure_performance import compare_measure, get_measure_catalog
from common.models import CareGap, Measure, Member
from common.star_rating import (
    CMS_MEASURE_SET_URL,
    CMS_RATING_YEAR,
    CMS_TECHNICAL_NOTES_URL,
    FinalizedMeasureResult,
    assign_star_rating,
    compare_star_ratings,
    get_star_config,
)


class GenerateEvidenceRequest(BaseModel):
    member_id: str = Field(min_length=1)
    care_gap_id: str = Field(min_length=1)
    format: str = Field(min_length=1)


class MeasureComparisonRequest(BaseModel):
    measure_code: str = Field(min_length=1, max_length=10)
    selected_gap_ids: list[str] = Field(default_factory=list, max_length=100)


class FinalizedMeasureResultRequest(BaseModel):
    measure_code: str = Field(min_length=1, max_length=10)
    measurement_year: int = Field(ge=1)
    scoring_value: float | None = None
    denominator: int | None = Field(default=None, ge=0)
    result_status: str = Field(default="completed", min_length=1)
    suppression_status: str | None = None
    reliability_status: str | None = None
    calculation_version: str | None = None
    specification_version: str | None = None
    scoring_value_kind: str = "cms_scoring_value"


class StarRatingRequest(BaseModel):
    result: FinalizedMeasureResultRequest
    cms_rating_year: int = Field(ge=1)
    member_id: str | None = Field(default=None, min_length=1)
    care_gap_id: str | None = Field(default=None, min_length=1)


class StarRatingComparisonRequest(BaseModel):
    before: FinalizedMeasureResultRequest
    after: FinalizedMeasureResultRequest
    cms_rating_year: int = Field(ge=1)


class StarDashboardRequest(BaseModel):
    measure_id: str | None = None
    selected_gap_ids: list[str] = Field(default_factory=list)


def _finalized_result(request: FinalizedMeasureResultRequest) -> FinalizedMeasureResult:
    return FinalizedMeasureResult(**request.model_dump())


def _is_closed_status(status: str | None) -> bool:
    normalized = (status or "").strip().lower()
    return normalized in {"closed", "complete", "completed", "resolved"}


def _star_from_rate(rate_percent: float | None) -> float | None:
    if rate_percent is None:
        return None
    raw = 1.0 + 4.0 * (rate_percent / 100.0)
    bounded = min(5.0, max(1.0, raw))
    return round(bounded, 2)


def _gap_rate(closed_count: int, total_count: int) -> float | None:
    if total_count <= 0:
        return None
    return round((closed_count / total_count) * 100.0, 2)


def _build_star_dashboard(
    session: Session,
    *,
    measure_id: str | None,
    selected_gap_ids: list[str],
) -> dict[str, object]:
    selected_set = set(selected_gap_ids)

    rows = session.execute(
        select(
            CareGap.care_gap_id,
            CareGap.member_id,
            CareGap.measure_id,
            CareGap.status,
            Measure.name,
            Member.first_name,
            Member.last_name,
        ).join(Measure, CareGap.measure_id == Measure.measure_id)
         .join(Member, CareGap.member_id == Member.member_id)
    ).all()

    measure_stats: dict[str, dict[str, object]] = {}
    patient_stats: dict[str, dict[str, object]] = {}
    gaps_by_measure: dict[str, list[dict[str, object]]] = defaultdict(list)

    for row in rows:
        gap_id = row.care_gap_id
        m_id = row.measure_id
        p_id = row.member_id
        is_closed = _is_closed_status(row.status)
        projected_closed = is_closed or (gap_id in selected_set)

        if m_id not in measure_stats:
            measure_stats[m_id] = {
                "measure_id": m_id,
                "measure_name": row.name,
                "total": 0,
                "baseline_closed": 0,
                "projected_closed": 0,
            }

        measure_stats[m_id]["total"] = int(measure_stats[m_id]["total"]) + 1
        if is_closed:
            measure_stats[m_id]["baseline_closed"] = int(measure_stats[m_id]["baseline_closed"]) + 1
        if projected_closed:
            measure_stats[m_id]["projected_closed"] = int(measure_stats[m_id]["projected_closed"]) + 1

        patient_key = f"{p_id}:{m_id}"
        if patient_key not in patient_stats:
            full_name = " ".join(
                part for part in [row.first_name, row.last_name] if part
            ).strip() or p_id
            patient_stats[patient_key] = {
                "member_id": p_id,
                "member_name": full_name,
                "measure_id": m_id,
                "measure_name": row.name,
                "total": 0,
                "baseline_closed": 0,
                "projected_closed": 0,
            }

        patient_stats[patient_key]["total"] = int(patient_stats[patient_key]["total"]) + 1
        if is_closed:
            patient_stats[patient_key]["baseline_closed"] = int(patient_stats[patient_key]["baseline_closed"]) + 1
        if projected_closed:
            patient_stats[patient_key]["projected_closed"] = int(patient_stats[patient_key]["projected_closed"]) + 1

        gaps_by_measure[m_id].append(
            {
                "care_gap_id": gap_id,
                "member_id": p_id,
                "member_name": patient_stats[patient_key]["member_name"],
                "status": row.status,
                "is_closed": is_closed,
                "selected_for_projection": gap_id in selected_set,
            }
        )

    measure_cards: list[dict[str, object]] = []
    for stat in measure_stats.values():
        baseline_rate = _gap_rate(int(stat["baseline_closed"]), int(stat["total"]))
        projected_rate = _gap_rate(int(stat["projected_closed"]), int(stat["total"]))
        star_eligible = get_star_config(str(stat["measure_id"]), CMS_RATING_YEAR) is not None
        baseline_star = _star_from_rate(baseline_rate) if star_eligible else None
        projected_star = _star_from_rate(projected_rate) if star_eligible else None
        measure_cards.append(
            {
                "measure_id": stat["measure_id"],
                "measure_name": stat["measure_name"],
                "star_eligible": star_eligible,
                "rating_note": (
                    "Projected star based on current measure closure rate."
                    if star_eligible
                    else "No verified CMS star mapping configured for this measure."
                ),
                "total_gaps": stat["total"],
                "baseline_closed": stat["baseline_closed"],
                "projected_closed": stat["projected_closed"],
                "baseline_rate": baseline_rate,
                "projected_rate": projected_rate,
                "baseline_star": baseline_star,
                "projected_star": projected_star,
                "star_delta": None
                if baseline_star is None or projected_star is None
                else round(projected_star - baseline_star, 2),
            }
        )

    measure_cards.sort(key=lambda item: str(item["measure_id"]))

    rated_measure_cards = [
        item for item in measure_cards if item["baseline_star"] is not None
    ]
    baseline_overall = (
        round(
            sum(float(item["baseline_star"]) for item in rated_measure_cards)
            / len(rated_measure_cards),
            2,
        )
        if rated_measure_cards
        else None
    )
    projected_overall = (
        round(
            sum(float(item["projected_star"]) for item in rated_measure_cards)
            / len(rated_measure_cards),
            2,
        )
        if rated_measure_cards
        else None
    )

    patient_rows: list[dict[str, object]] = []
    for stat in patient_stats.values():
        baseline_rate = _gap_rate(int(stat["baseline_closed"]), int(stat["total"]))
        projected_rate = _gap_rate(int(stat["projected_closed"]), int(stat["total"]))
        star_eligible = get_star_config(str(stat["measure_id"]), CMS_RATING_YEAR) is not None
        baseline_star = _star_from_rate(baseline_rate) if star_eligible else None
        projected_star = _star_from_rate(projected_rate) if star_eligible else None
        patient_rows.append(
            {
                "member_id": stat["member_id"],
                "member_name": stat["member_name"],
                "measure_id": stat["measure_id"],
                "measure_name": stat["measure_name"],
                "star_eligible": star_eligible,
                "total_gaps": stat["total"],
                "baseline_closed": stat["baseline_closed"],
                "projected_closed": stat["projected_closed"],
                "baseline_rate": baseline_rate,
                "projected_rate": projected_rate,
                "baseline_star": baseline_star,
                "projected_star": projected_star,
                "star_delta": None
                if baseline_star is None or projected_star is None
                else round(projected_star - baseline_star, 2),
            }
        )

    if measure_id:
        patient_rows = [row for row in patient_rows if row["measure_id"] == measure_id]
        target_measure = next(
            (item for item in measure_cards if item["measure_id"] == measure_id),
            None,
        )
        target_gaps = gaps_by_measure.get(measure_id, [])
    else:
        target_measure = None
        target_gaps = []

    patient_rows.sort(key=lambda item: (str(item["measure_id"]), str(item["member_id"])))

    return {
        "cms_reference": {
            "cms_rating_year": CMS_RATING_YEAR,
            "technical_notes_url": CMS_TECHNICAL_NOTES_URL,
            "measure_set_url": CMS_MEASURE_SET_URL,
            "mapped_measure_ids": [
                str(item["measure_id"])
                for item in measure_cards
                if bool(item["star_eligible"])
            ],
            "star_cut_points": [
                {
                    "measure_id": str(item["measure_id"]),
                    "measure_name": str(item["measure_name"]),
                    "two_star_min": next(
                        (
                            point.minimum_value
                            for point in (get_star_config(str(item["measure_id"]), CMS_RATING_YEAR).cut_points)
                            if point.stars == 2
                        ),
                        None,
                    ),
                    "three_star_min": next(
                        (
                            point.minimum_value
                            for point in (get_star_config(str(item["measure_id"]), CMS_RATING_YEAR).cut_points)
                            if point.stars == 3
                        ),
                        None,
                    ),
                    "four_star_min": next(
                        (
                            point.minimum_value
                            for point in (get_star_config(str(item["measure_id"]), CMS_RATING_YEAR).cut_points)
                            if point.stars == 4
                        ),
                        None,
                    ),
                    "five_star_min": next(
                        (
                            point.minimum_value
                            for point in (get_star_config(str(item["measure_id"]), CMS_RATING_YEAR).cut_points)
                            if point.stars == 5
                        ),
                        None,
                    ),
                }
                for item in measure_cards
                if bool(item["star_eligible"])
            ],
            "note": (
                "Measure-level star projections use the configured CMS 2027 mapping. "
                "Measures without verified mapping are displayed as Not Mapped / N/A."
            ),
        },
        "overall": {
            "rated_measure_count": len(rated_measure_cards),
            "baseline_star": baseline_overall,
            "projected_star": projected_overall,
            "star_delta": None
            if baseline_overall is None or projected_overall is None
            else round(projected_overall - baseline_overall, 2),
        },
        "measure_cards": measure_cards,
        "selected_measure": target_measure,
        "selected_measure_gaps": target_gaps,
        "patient_rows": patient_rows,
        "selected_gap_ids": sorted(selected_set),
    }


def _seed_dashboard_sample_data(session: Session) -> dict[str, int]:
    inserted_members = 0
    inserted_measures = 0
    inserted_gaps = 0

    sample_measures = [
        ("CBP", "Controlling High Blood Pressure"),
        ("BCS-E", "Breast Cancer Screening"),
        ("BPD", "Blood Pressure Control for Patients With Diabetes"),
        ("CCS-E", "Cervical Cancer Screening"),
        ("AAB", "Avoidance of Antibiotic Treatment for Acute Bronchitis/Bronchiolitis"),
        ("COL-E", "Colorectal Cancer Screening"),
        ("KED", "Kidney Health Evaluation for Patients with Diabetes"),
        ("SPD", "Statin Therapy for Patients With Diabetes"),
        ("GSD", "Glycemic Status Assessment"),
        ("EED", "Eye Exam for Patients With Diabetes"),
        # Keep COL to support existing demo data already in this repo.
        ("COL", "Colorectal Cancer Screening"),
    ]

    existing_measure_ids = {
        row[0] for row in session.execute(select(Measure.measure_id)).all()
    }
    for measure_id, name in sample_measures:
        if measure_id not in existing_measure_ids:
            session.add(
                Measure(
                    measure_id=measure_id,
                    name=name,
                    description=f"Sample measure for dashboard realism: {name}",
                    measurement_year=2026,
                )
            )
            inserted_measures += 1

    # Persist parent rows before inserting care gaps that reference them.
    session.flush()

    sample_members = [
        (f"M{1000 + index}", f"Patient{index:02d}", f"Demo{index:02d}")
        for index in range(1, 21)
    ]
    existing_member_ids = {
        row[0] for row in session.execute(select(Member.member_id)).all()
    }
    for member_id, first_name, last_name in sample_members:
        if member_id not in existing_member_ids:
            session.add(
                Member(
                    member_id=member_id,
                    first_name=first_name,
                    last_name=last_name,
                    date_of_birth=dt.date(1980, 1, 1),
                    gender="Unknown",
                )
            )
            inserted_members += 1

    # Persist parent member rows before inserting care-gap children.
    session.flush()

    existing_gap_ids = {
        row[0] for row in session.execute(select(CareGap.care_gap_id)).all()
    }

    status_pattern = ["Open", "Open", "Closed", "Open", "Closed"]
    for idx, (member_id, _, _) in enumerate(sample_members, start=1):
        for m_idx, (measure_id, _) in enumerate(sample_measures, start=1):
            for seq in range(1, 26):
                care_gap_id = f"CG-{measure_id}-{member_id}-{seq}"
                if care_gap_id in existing_gap_ids:
                    continue
                status = status_pattern[(idx + m_idx + seq) % len(status_pattern)]
                session.add(
                    CareGap(
                        care_gap_id=care_gap_id,
                        member_id=member_id,
                        measure_id=measure_id,
                        description=(
                            f"Sample gap {seq} for {measure_id} and {member_id}"
                        ),
                        status=status,
                        date_identified=dt.date(2026, ((idx + seq) % 12) + 1, ((m_idx + seq) % 27) + 1),
                        measurement_year=2026,
                    )
                )
                inserted_gaps += 1

    session.flush()
    return {
        "inserted_members": inserted_members,
        "inserted_measures": inserted_measures,
        "inserted_care_gaps": inserted_gaps,
    }


_seed_lock = threading.Lock()


def _process_gap_automation(repo: Repository, cfg: AppConfig) -> dict[str, object]:
    summary: dict[str, object] = {
        "processed": 0,
        "evidence_created": 0,
        "star_baseline_created": 0,
        "star_closed_compare_created": 0,
        "errors": [],
    }
    error_list = summary["errors"]
    assert isinstance(error_list, list)

    for care_gap_row in repo.list_care_gaps():
        care_gap = row_to_dict(care_gap_row) or {}
        member_id = str(care_gap.get("member_id") or "")
        care_gap_id = str(care_gap.get("care_gap_id") or "")
        measure_id = str(care_gap.get("measure_id") or "")
        status = str(care_gap.get("status") or "").strip().lower()

        if not member_id or not care_gap_id or not measure_id:
            error_list.append(
                {
                    "care_gap_id": care_gap_id or None,
                    "reason": "Care gap row is missing member_id, care_gap_id, or measure_id.",
                }
            )
            continue

        member = row_to_dict(repo.get_member(member_id))
        if not member:
            error_list.append(
                {"care_gap_id": care_gap_id, "reason": f"Member not found: {member_id}"}
            )
            continue

        measure = row_to_dict(repo.get_measure(measure_id))
        if not measure:
            error_list.append(
                {"care_gap_id": care_gap_id, "reason": f"Measure not found: {measure_id}"}
            )
            continue

        summary["processed"] = int(summary["processed"]) + 1

        # 1) Evidence generation (once per care gap for automation path).
        auto_evidence_fact_key = "auto_evidence_file_path:pdf"
        if not repo.synthea_record_exists(
            member_id=member_id,
            care_gap_id=care_gap_id,
            source_type="EvidenceDocument",
            fact_key=auto_evidence_fact_key,
            provenance="app:/automation/gap-processor",
        ):
            supporting_rows = [
                row_to_dict(r) or {}
                for r in repo.get_supporting_records(member_id, care_gap_id)
            ]
            evidence_data = prepare_evidence_data(member, care_gap, measure, supporting_rows)
            evidence_id = f"EVID-{uuid.uuid4().hex[:14].upper()}"
            file_name = f"{evidence_id}_pdf{extension_for_format('pdf')}"
            destination = cfg.evidence_storage_dir / file_name
            try:
                generate_document("pdf", destination, evidence_data)
                repo.add_synthea_record(
                    record_id=f"SRAUTOEVD-{uuid.uuid4().hex[:18].upper()}",
                    member_id=member_id,
                    care_gap_id=care_gap_id,
                    source_type="EvidenceDocument",
                    source_date=dt.date.today(),
                    fact_key=auto_evidence_fact_key,
                    fact_value=str(destination.resolve()),
                    provenance="app:/automation/gap-processor",
                )
                summary["evidence_created"] = int(summary["evidence_created"]) + 1
            except Exception as exc:
                if destination.exists():
                    destination.unlink()
                error_list.append(
                    {
                        "care_gap_id": care_gap_id,
                        "reason": f"Automatic evidence generation failed: {exc}",
                    }
                )

        measure_code = str(measure.get("measure_id") or "").upper()

        # 2) Baseline star summary (once per care gap).
        baseline_fact_key = f"auto_star_baseline:{measure_code}"
        if not repo.synthea_record_exists(
            member_id=member_id,
            care_gap_id=care_gap_id,
            source_type="StarRating",
            fact_key=baseline_fact_key,
            provenance="app:/automation/gap-processor",
        ):
            try:
                baseline_payload = compare_measure(measure_code, [])
            except Exception as exc:
                baseline_payload = {
                    "calculation_status": "unavailable",
                    "reason": str(exc),
                    "measure_code": measure_code,
                    "care_gap_id": care_gap_id,
                }
            repo.add_synthea_record(
                record_id=f"SRAUTOSTAR-{uuid.uuid4().hex[:18].upper()}",
                member_id=member_id,
                care_gap_id=care_gap_id,
                source_type="StarRating",
                source_date=dt.date.today(),
                fact_key=baseline_fact_key,
                fact_value=json.dumps(baseline_payload, separators=(",", ":"), default=str),
                provenance="app:/automation/gap-processor",
            )
            summary["star_baseline_created"] = int(summary["star_baseline_created"]) + 1

        # 3) Closed-gap before/after summary (once when gap is closed).
        if status == "closed":
            compare_fact_key = f"auto_star_before_after:{measure_code}:{care_gap_id}"
            if not repo.synthea_record_exists(
                member_id=member_id,
                care_gap_id=care_gap_id,
                source_type="StarRating",
                fact_key=compare_fact_key,
                provenance="app:/automation/gap-processor",
            ):
                try:
                    compare_payload = compare_measure(measure_code, [care_gap_id])
                except Exception as exc:
                    compare_payload = {
                        "calculation_status": "unavailable",
                        "reason": str(exc),
                        "measure_code": measure_code,
                        "care_gap_id": care_gap_id,
                    }
                repo.add_synthea_record(
                    record_id=f"SRAUTOCOMP-{uuid.uuid4().hex[:18].upper()}",
                    member_id=member_id,
                    care_gap_id=care_gap_id,
                    source_type="StarRating",
                    source_date=dt.date.today(),
                    fact_key=compare_fact_key,
                    fact_value=json.dumps(compare_payload, separators=(",", ":"), default=str),
                    provenance="app:/automation/gap-processor",
                )
                summary["star_closed_compare_created"] = int(
                    summary["star_closed_compare_created"]
                ) + 1

    return summary


def create_app(
    config: AppConfig | None = None,
    *,
    validate_database: bool = True,
) -> FastAPI:
    cfg = config or AppConfig.from_env()
    database = Database(cfg.database_url)
    auto_enabled = os.getenv("AUTO_GAP_AUTOMATION", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    auto_interval_seconds = max(
        5, int(os.getenv("AUTO_GAP_AUTOMATION_INTERVAL_SECONDS", "30"))
    )

    async def auto_gap_worker(stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                with database.session_factory.begin() as session:
                    repo = Repository(session)
                    _process_gap_automation(repo, cfg)
            except Exception:
                # Keep the worker alive and retry on the next interval.
                pass
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=auto_interval_seconds,
                )
            except TimeoutError:
                continue

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if validate_database:
            database.validate()
        cfg.evidence_storage_dir.mkdir(parents=True, exist_ok=True)
        stop_event = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if auto_enabled:
            worker_task = asyncio.create_task(auto_gap_worker(stop_event))
        try:
            yield
        finally:
            if worker_task is not None:
                stop_event.set()
                await worker_task
            database.dispose()

    app = FastAPI(
        title="HEDIS POC API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.database = database
    app.state.config = cfg

    @app.get("/", response_class=HTMLResponse)
    def measure_comparison_page():
        page_path = Path(__file__).parent / "ui" / "measure_comparison.html"
        return HTMLResponse(page_path.read_text(encoding="utf-8"))

    @app.get("/star-results", response_class=HTMLResponse)
    def star_results_page():
        page_path = Path(__file__).parent / "ui" / "star_results.html"
        return HTMLResponse(page_path.read_text(encoding="utf-8"))

    @app.get("/api/measure-performance/measures")
    def measure_performance_measures():
        return get_measure_catalog()

    @app.get("/api/measure-performance/{measure_code}/gaps")
    def measure_performance_gaps(measure_code: str):
        try:
            comparison = compare_measure(measure_code, [])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return comparison["care_gaps"]

    @app.post("/api/measure-performance/compare")
    def measure_performance_compare(request: MeasureComparisonRequest):
        try:
            return compare_measure(request.measure_code, request.selected_gap_ids)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/star-rating/calculate")
    def star_rating_calculate(
        request: StarRatingRequest,
        session: Session = Depends(database.get_session),
    ):
        rating = assign_star_rating(
            _finalized_result(request.result), request.cms_rating_year
        )
        response_payload = rating.to_dict()

        if request.member_id is not None:
            repo = Repository(session)
            member = row_to_dict(repo.get_member(request.member_id))
            if not member:
                raise HTTPException(status_code=404, detail="Unknown member ID")

            care_gap_id = request.care_gap_id
            if care_gap_id is not None:
                care_gap = row_to_dict(repo.get_care_gap(care_gap_id))
                if not care_gap:
                    raise HTTPException(status_code=404, detail="Unknown care-gap ID")
                if care_gap["member_id"] != request.member_id:
                    raise HTTPException(
                        status_code=400,
                        detail="Care gap does not belong to the requested member",
                    )

            repo.add_synthea_record(
                record_id=f"SRSTAR-{uuid.uuid4().hex[:20].upper()}",
                member_id=request.member_id,
                care_gap_id=care_gap_id,
                source_type="StarRating",
                source_date=dt.date.today(),
                fact_key=(
                    f"star_rating:{request.result.measure_code.upper()}:"
                    f"{request.cms_rating_year}"
                ),
                fact_value=json.dumps(response_payload, separators=(",", ":")),
                provenance="app:/api/star-rating/calculate",
            )

        return response_payload

    @app.post("/api/star-rating/compare")
    def star_rating_compare(request: StarRatingComparisonRequest):
        return compare_star_ratings(
            _finalized_result(request.before),
            _finalized_result(request.after),
            request.cms_rating_year,
        )

    @app.post("/api/evidence/generate")
    def generate_evidence(
        request: GenerateEvidenceRequest,
        session: Session = Depends(database.get_session),
    ):
        repo = Repository(session)
        try:
            requested_format = ensure_supported_formats([request.format])[0]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        member = row_to_dict(repo.get_member(request.member_id))
        if not member:
            raise HTTPException(status_code=404, detail="Unknown member ID")

        care_gap = row_to_dict(repo.get_care_gap(request.care_gap_id))
        if not care_gap:
            raise HTTPException(status_code=404, detail="Unknown care-gap ID")

        if care_gap["member_id"] != request.member_id:
            raise HTTPException(
                status_code=400,
                detail="Care gap does not belong to the requested member",
            )

        measure = row_to_dict(repo.get_measure(care_gap["measure_id"]))
        if not measure:
            raise HTTPException(
                status_code=422,
                detail="Related measure data is unavailable",
            )

        supporting_rows = [
            row_to_dict(r) or {}
            for r in repo.get_supporting_records(request.member_id, request.care_gap_id)
        ]
        evidence_data = prepare_evidence_data(member, care_gap, measure, supporting_rows)

        try:
            evidence_id = f"EVID-{uuid.uuid4().hex[:14].upper()}"
            extension = extension_for_format(requested_format)
            file_name = f"{evidence_id}_{requested_format}{extension}"
            destination = cfg.evidence_storage_dir / file_name

            generate_document(requested_format, destination, evidence_data)

            repo.add_synthea_record(
                record_id=f"SREVD-{uuid.uuid4().hex[:20].upper()}",
                member_id=request.member_id,
                care_gap_id=request.care_gap_id,
                source_type="EvidenceDocument",
                source_date=dt.date.today(),
                fact_key=f"evidence_file_path:{requested_format}",
                fact_value=str(destination.resolve()),
                provenance="app:/api/evidence/generate",
            )
        except Exception as exc:
            if destination.exists():
                destination.unlink()
            raise HTTPException(
                status_code=500,
                detail=f"Evidence generation failed: {exc}",
            ) from exc

        return {
            "evidence_id": evidence_id,
            "member_id": request.member_id,
            "care_gap_id": request.care_gap_id,
            "measure_id": measure["measure_id"],
            "document_id": evidence_data.document_id,
            "format": requested_format,
            "file_name": file_name,
            "file_path": str(destination.resolve()),
        }

    @app.post("/api/automation/process-gaps")
    def process_gaps(session: Session = Depends(database.get_session)):
        repo = Repository(session)
        return _process_gap_automation(repo, cfg)

    @app.post("/api/star-dashboard/calculate")
    def star_dashboard_calculate(
        request: StarDashboardRequest,
        session: Session = Depends(database.get_session),
    ):
        return _build_star_dashboard(
            session,
            measure_id=request.measure_id,
            selected_gap_ids=request.selected_gap_ids,
        )

    @app.post("/api/star-dashboard/seed-sample-data")
    def star_dashboard_seed_sample_data(session: Session = Depends(database.get_session)):
        with _seed_lock:
            try:
                inserted = _seed_dashboard_sample_data(session)
                # Commit inside the lock so follow-up seed calls observe inserted rows.
                session.commit()
            except IntegrityError:
                session.rollback()
                inserted = {
                    "inserted_members": 0,
                    "inserted_measures": 0,
                    "inserted_care_gaps": 0,
                }
        return {
            "status": "ok",
            **inserted,
        }

    return app


app = create_app()
