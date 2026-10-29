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

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from common.config import AppConfig
from common.database import Database
from common.evidence import (
    ensure_supported_formats,
    extension_for_format,
    generate_document,
    prepare_evidence_data,
)
from common.measure_performance import compare_measure
from common.repository import Repository, row_to_dict
from common.models import CareGap, Measure, Member
from services.data_ingestion.app import api as ingestion_api
from services.data_ingestion.app.db import session_scope as ingestion_session_scope
from services.data_ingestion.app.repository import as_job_dict as ingestion_as_job_dict, list_jobs as ingestion_list_jobs
from services.star_modeler.main import (
    CMS_MEASURE_SET_URL,
    CMS_RATING_YEAR,
    CMS_TECHNICAL_NOTES_URL,
    assign_star_from_sqlalchemy,
    has_cutpoints_sqlalchemy,
    load_cutpoints_sqlalchemy,
)


class GenerateEvidenceRequest(BaseModel):
    member_id: str = Field(min_length=1)
    care_gap_id: str = Field(min_length=1)
    format: str = Field(min_length=1)


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


def _ingestion_routes() -> APIRouter:
    """Expose ingestion endpoints under /api/ingestion for UI compatibility."""
    router = APIRouter()
    for route in ingestion_api.router.routes:
        if isinstance(route, APIRoute):
            router.add_api_route(
                route.path.removeprefix(ingestion_api.router.prefix),
                route.endpoint,
                methods=sorted(route.methods),
                name=f"ingestion_{route.name}",
            )
    return router


def _is_closed_status(status: str | None) -> bool:
    normalized = (status or "").strip().lower()
    return normalized in {"closed", "complete", "completed", "resolved"}


def _gap_rate(closed_count: int, total_count: int) -> float | None:
    if total_count <= 0:
        return None
    return round((closed_count / total_count) * 100.0, 2)


def _load_dashboard_rows(session: Session) -> list[dict[str, object]]:
    """Read gap rows from whichever schema is present in the connected database."""
    try:
        rows = session.execute(
            text(
                """SELECT g.care_gap_id,
                          g.member_id,
                          g.measure_id,
                          g.measurement_year,
                          g.status,
                          m.name AS measure_name,
                          mb.first_name,
                          mb.last_name
                     FROM care_gaps g
                     JOIN measures m ON g.measure_id = m.measure_id
                     JOIN members mb ON g.member_id = mb.member_id"""
            )
        ).mappings().all()
        return [dict(r) for r in rows]
    except SQLAlchemyError:
        session.rollback()

    rows = session.execute(
        text(
            """SELECT g.gap_id::text AS care_gap_id,
                      COALESCE(p.mrn, p.patient_id) AS member_id,
                      g.measure_id,
                      g.measurement_year,
                      g.status,
                      g.measure_id AS measure_name,
                      NULL::text AS first_name,
                      NULL::text AS last_name
                 FROM care_gap g
                 JOIN patient p ON g.patient_id = p.patient_id"""
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _load_engine_measure_counts(session: Session) -> dict[tuple[str, int], dict[str, int]]:
    """Use engine result populations when available, matching the integration star baseline logic."""
    try:
        rows = session.execute(
            text(
                """SELECT r.measure_id,
                          r.measurement_year,
                          count(*) FILTER (
                              WHERE r.in_denominator AND NOT coalesce(r.excluded, false)
                          ) AS denominator,
                          count(*) FILTER (
                              WHERE r.in_denominator
                                AND r.in_numerator
                                AND NOT coalesce(r.excluded, false)
                          ) AS numerator
                     FROM v_measure_result_latest r
                    GROUP BY r.measure_id, r.measurement_year"""
            )
        ).all()
    except SQLAlchemyError:
        session.rollback()
        return {}

    counts: dict[tuple[str, int], dict[str, int]] = {}
    for row in rows:
        measure_code = str(row[0])
        measurement_year = int(row[1])
        denominator = max(int(row[2] or 0), 0)
        numerator = max(int(row[3] or 0), 0)
        counts[(measure_code, measurement_year)] = {
            "denominator": denominator,
            "numerator": numerator,
        }
    return counts


def _build_star_dashboard(
    session: Session,
    *,
    measure_id: str | None,
    selected_gap_ids: list[str],
) -> dict[str, object]:
    selected_set = set(selected_gap_ids)

    rows = _load_dashboard_rows(session)
    engine_counts = _load_engine_measure_counts(session)

    measure_stats: dict[str, dict[str, object]] = {}
    patient_stats: dict[str, dict[str, object]] = {}
    gaps_by_measure: dict[str, list[dict[str, object]]] = defaultdict(list)
    measure_names_by_id: dict[str, str] = {}

    for row in rows:
        gap_id = str(row["care_gap_id"])
        m_id = str(row["measure_id"])
        measurement_year = int(row["measurement_year"])
        p_id = str(row["member_id"])
        is_closed = _is_closed_status(str(row.get("status") or ""))
        projected_closed = is_closed or (gap_id in selected_set)
        measure_name = str(row.get("measure_name") or m_id)
        if m_id not in measure_names_by_id:
            measure_names_by_id[m_id] = measure_name

        if m_id not in measure_stats:
            measure_stats[m_id] = {
                "measure_id": m_id,
                "measurement_year": measurement_year,
                "measure_name": measure_name,
                "total": 0,
                "baseline_closed": 0,
                "projected_closed": 0,
                "selected_open": 0,
            }

        measure_stats[m_id]["total"] = int(measure_stats[m_id]["total"]) + 1
        if is_closed:
            measure_stats[m_id]["baseline_closed"] = int(measure_stats[m_id]["baseline_closed"]) + 1
        elif gap_id in selected_set:
            measure_stats[m_id]["selected_open"] = int(measure_stats[m_id]["selected_open"]) + 1
        if projected_closed:
            measure_stats[m_id]["projected_closed"] = int(measure_stats[m_id]["projected_closed"]) + 1

        patient_key = f"{p_id}:{m_id}"
        if patient_key not in patient_stats:
            full_name = " ".join(
                part for part in [row.get("first_name"), row.get("last_name")] if part
            ).strip() or p_id
            patient_stats[patient_key] = {
                "member_id": p_id,
                "member_name": full_name,
                "measure_id": m_id,
                "measurement_year": measurement_year,
                "measure_name": str(row.get("measure_name") or m_id),
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
                "status": row.get("status"),
                "is_closed": is_closed,
                "selected_for_projection": gap_id in selected_set,
            }
        )

    for (m_id, measurement_year), counts in engine_counts.items():
        if m_id not in measure_stats:
            measure_stats[m_id] = {
                "measure_id": m_id,
                "measurement_year": measurement_year,
                "measure_name": measure_names_by_id.get(m_id, m_id),
                "total": 0,
                "baseline_closed": 0,
                "projected_closed": 0,
                "selected_open": 0,
            }
        measure_stats[m_id]["engine_denominator"] = counts["denominator"]
        measure_stats[m_id]["engine_numerator"] = counts["numerator"]

    measure_cards: list[dict[str, object]] = []
    for stat in measure_stats.values():
        measure_code = str(stat["measure_id"])
        measurement_year = int(stat["measurement_year"])
        fallback_total = int(stat["total"])
        fallback_baseline_closed = int(stat["baseline_closed"])
        selected_open = int(stat.get("selected_open") or 0)
        engine_denominator = stat.get("engine_denominator")
        engine_numerator = stat.get("engine_numerator")

        if isinstance(engine_denominator, int) and isinstance(engine_numerator, int) and engine_denominator > 0:
            total = engine_denominator
            baseline_closed = min(max(engine_numerator, 0), total)
        else:
            total = fallback_total
            baseline_closed = fallback_baseline_closed

        projected_closed = min(total, baseline_closed + max(selected_open, 0))
        baseline_rate = _gap_rate(baseline_closed, total)
        projected_rate = _gap_rate(projected_closed, total)
        star_eligible = has_cutpoints_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
        )
        baseline_result = assign_star_from_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
            scoring_value=baseline_rate,
            denominator=total,
        )
        projected_result = assign_star_from_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
            scoring_value=projected_rate,
            denominator=total,
        )
        baseline_star = baseline_result.assigned_star_rating
        projected_star = projected_result.assigned_star_rating
        measure_cards.append(
            {
                "measure_id": stat["measure_id"],
                "measurement_year": measurement_year,
                "measure_name": stat["measure_name"],
                "star_eligible": star_eligible,
                "rating_note": (
                    "Projected star based on current measure rate and selected closure projection."
                    if star_eligible
                    else "No verified CMS star mapping configured for this measure."
                ),
                "total_gaps": total,
                "baseline_closed": baseline_closed,
                "projected_closed": projected_closed,
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
        measure_code = str(stat["measure_id"])
        measurement_year = int(stat["measurement_year"])
        total = int(stat["total"])
        baseline_closed = int(stat["baseline_closed"])
        projected_closed = int(stat["projected_closed"])
        baseline_rate = _gap_rate(int(stat["baseline_closed"]), int(stat["total"]))
        projected_rate = _gap_rate(int(stat["projected_closed"]), int(stat["total"]))
        star_eligible = has_cutpoints_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
        )
        baseline_result = assign_star_from_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
            scoring_value=baseline_rate,
            denominator=total,
        )
        projected_result = assign_star_from_sqlalchemy(
            session,
            measure_code=measure_code,
            measurement_year=measurement_year,
            scoring_value=projected_rate,
            denominator=total,
        )
        baseline_star = baseline_result.assigned_star_rating
        projected_star = projected_result.assigned_star_rating
        patient_rows.append(
            {
                "member_id": stat["member_id"],
                "member_name": stat["member_name"],
                "measure_id": stat["measure_id"],
                "measurement_year": measurement_year,
                "measure_name": stat["measure_name"],
                "star_eligible": star_eligible,
                "total_gaps": total,
                "baseline_closed": baseline_closed,
                "projected_closed": projected_closed,
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
                    "two_star_min": next((v for s, v in load_cutpoints_sqlalchemy(
                        session,
                        measure_code=str(item["measure_id"]),
                        measurement_year=int(item["measurement_year"]),
                    ) if s == 2), None),
                    "three_star_min": next((v for s, v in load_cutpoints_sqlalchemy(
                        session,
                        measure_code=str(item["measure_id"]),
                        measurement_year=int(item["measurement_year"]),
                    ) if s == 3), None),
                    "four_star_min": next((v for s, v in load_cutpoints_sqlalchemy(
                        session,
                        measure_code=str(item["measure_id"]),
                        measurement_year=int(item["measurement_year"]),
                    ) if s == 4), None),
                    "five_star_min": next((v for s, v in load_cutpoints_sqlalchemy(
                        session,
                        measure_code=str(item["measure_id"]),
                        measurement_year=int(item["measurement_year"]),
                    ) if s == 5), None),
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

    missing_tables = [
        table_name
        for table_name in ("members", "synthea_records")
        if not repo.table_exists(table_name)
    ]
    if missing_tables:
        gap_rows = repo.list_care_gaps()
        summary["processed"] = len(gap_rows)
        summary["skipped"] = True
        summary["skip_reason"] = (
            "Automation writeback is unavailable for this database profile "
            f"(missing tables: {', '.join(missing_tables)})."
        )
        return summary

    for care_gap in repo.list_care_gaps():
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
                baseline_payload = compare_measure(repo.session, measure_code, [])
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
                    compare_payload = compare_measure(repo.session, measure_code, [care_gap_id])
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

    # Local frontend calls POST JSON endpoints, which triggers CORS preflight (OPTIONS).
    # Allow common localhost dev origins so preflight is accepted instead of 405.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.database = database
    app.state.config = cfg

    @app.get("/", response_class=HTMLResponse)
    def home_page():
        page_path = Path(__file__).parent / "ui" / "star_results.html"
        return HTMLResponse(page_path.read_text(encoding="utf-8"))

    @app.get("/star-results", response_class=HTMLResponse)
    def star_results_page():
        page_path = Path(__file__).parent / "ui" / "star_results.html"
        return HTMLResponse(page_path.read_text(encoding="utf-8"))

    @app.post("/api/star-rating/calculate")
    def star_rating_calculate(
        request: StarRatingRequest,
        session: Session = Depends(database.get_session),
    ):
        rating = assign_star_from_sqlalchemy(
            session,
            measure_code=request.result.measure_code,
            measurement_year=request.result.measurement_year,
            scoring_value=request.result.scoring_value,
            denominator=request.result.denominator,
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
    def star_rating_compare(
        request: StarRatingComparisonRequest,
        session: Session = Depends(database.get_session),
    ):
        before_result = assign_star_from_sqlalchemy(
            session,
            measure_code=request.before.measure_code,
            measurement_year=request.before.measurement_year,
            scoring_value=request.before.scoring_value,
            denominator=request.before.denominator,
        )
        after_result = assign_star_from_sqlalchemy(
            session,
            measure_code=request.after.measure_code,
            measurement_year=request.after.measurement_year,
            scoring_value=request.after.scoring_value,
            denominator=request.after.denominator,
        )
        return {
            "before": before_result.to_dict(),
            "after": after_result.to_dict(),
            "star_rating_changed": (
                before_result.rating_status == "assigned"
                and after_result.rating_status == "assigned"
                and before_result.assigned_star_rating != after_result.assigned_star_rating
            ),
        }

    @app.get("/api/star-rating/catalog")
    def star_rating_catalog(session: Session = Depends(database.get_session)):
        rows = session.execute(
            text(
                """SELECT d.measure_id,
                          d.measurement_year,
                          coalesce(r.name, d.measure_id) AS measure_name,
                          w.weight,
                          c.star_rating,
                          c.rate_low_pct
                     FROM measure_definition d
                     LEFT JOIN measure_rule r
                       ON r.measure_id = d.measure_id
                      AND r.measurement_year = d.measurement_year
                     LEFT JOIN measure_star_weight w
                       ON w.measure_id = d.measure_id
                      AND w.measurement_year = d.measurement_year
                      AND w.star_year = :star_year
                     LEFT JOIN star_cutpoint c
                       ON c.measure_id = d.measure_id
                      AND c.measurement_year = d.measurement_year
                      AND c.rate_low_pct IS NOT NULL
                    ORDER BY d.measure_id, d.measurement_year, c.star_rating"""
            ),
            {"star_year": CMS_RATING_YEAR},
        ).all()

        grouped: dict[tuple[str, int], dict[str, object]] = {}
        for row in rows:
            measure_id = str(row[0])
            measurement_year = int(row[1])
            key = (measure_id, measurement_year)
            if key not in grouped:
                weight = row[3]
                grouped[key] = {
                    "measure_code": measure_id,
                    "measurement_year": measurement_year,
                    "measure_name": str(row[2]) if row[2] else measure_id,
                    "weight": int(weight) if weight is not None else None,
                    "cut_points": [],
                }

            if row[4] is not None and row[5] is not None:
                grouped[key]["cut_points"].append(
                    {
                        "star": int(row[4]),
                        "minimum_rate": float(row[5]),
                    }
                )

        return {
            "cms_rating_year": CMS_RATING_YEAR,
            "cms_measure_set_url": CMS_MEASURE_SET_URL,
            "cms_technical_notes_url": CMS_TECHNICAL_NOTES_URL,
            "measures": list(grouped.values()),
        }

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
app.include_router(_ingestion_routes(), prefix="/api/ingestion")


@app.get("/api/flow")
def ingestion_flow_snapshot() -> dict[str, object]:
    """Return a lightweight flow snapshot compatible with the ingestion UI."""
    with ingestion_session_scope() as session:
        jobs = ingestion_list_jobs(session)
        if not jobs:
            return {"batch": None, "tasks": {}}

        job = ingestion_as_job_dict(jobs[0])

    raw_status = str(job.get("status") or "").lower()
    if raw_status in {"failed", "error"}:
        flow_status = "failed"
    elif raw_status in {"running", "pending"}:
        flow_status = "running"
    elif int(job.get("resources_persisted") or 0) > 0:
        flow_status = "prioritized"
    else:
        flow_status = "loaded"

    return {
        "batch": {
            "batch_id": str(job.get("id") or ""),
            "ingest_job_id": str(job.get("id") or ""),
            "status": flow_status,
            "step": "prioritizing" if flow_status == "prioritized" else "loading",
            "updated_at": job.get("completed_at") or job.get("started_at"),
            "counts": {
                "resources_generated": int(job.get("resources_generated") or 0),
                "resources_validated": int(job.get("resources_validated") or 0),
                "resources_persisted": int(job.get("resources_persisted") or 0),
                "warnings": int(job.get("warning_count") or 0),
                "errors": int(job.get("error_count") or 0),
            },
        },
        "tasks": {
            "ingestion": flow_status,
        },
    }
