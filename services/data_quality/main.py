from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import socket
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from services.data_ingestion.app.db import session_scope
from services.data_ingestion.app.models import (
    Condition,
    ConversionJob,
    Coverage,
    DataAnomaly,
    DomainEvent,
    DqCheckResult,
    DqRun,
    Encounter,
    FeedCompletenessReport,
    FhirResource,
    Immunization,
    Medication,
    Observation,
    Patient,
    Procedure,
    SourceBatch,
    ValidationIssue,
)


LOGGER = logging.getLogger("dq_worker")
DEFAULT_EVENT_TYPE = "ingestion.completed"
REQUIRED_RESOURCE_TYPES = ("Patient", "Encounter", "Condition", "Observation", "Coverage")
MEASURE_RELEVANT_TYPES = {"Patient", "Encounter", "Condition", "Observation", "Procedure", "MedicationRequest", "Coverage"}
VOLUME_DROP_THRESHOLD_PCT = 40.0
VOLUME_SPIKE_THRESHOLD_PCT = 100.0
MAPPING_ERROR_RATE_THRESHOLD = 0.02
RULES_PATH = Path(__file__).resolve().parent / "config" / "dq_rules.json"

# Normalize source-specific measure IDs to canonical DQ rule IDs.
MEASURE_ID_ALIASES = {
    "COL-E": "COL",
    "COLE": "COL",
    "BCS-E": "BCS",
    "BCSE": "BCS",
    "COA-FSA": "COA",
    "COA-MDR": "COA",
    "GSD-HBAOPOR": "GSD",
}


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _summarize_batch(session: Any, batch_id: str) -> dict[str, int]:
    return {
        "fhir_resources": session.execute(
            select(func.count()).select_from(FhirResource).where(FhirResource.batch_id == batch_id)
        ).scalar_one(),
        "patients": session.execute(
            select(func.count()).select_from(Patient).where(Patient.batch_id == batch_id)
        ).scalar_one(),
        "encounters": session.execute(
            select(func.count()).select_from(Encounter).where(Encounter.batch_id == batch_id)
        ).scalar_one(),
        "conditions": session.execute(
            select(func.count()).select_from(Condition).where(Condition.batch_id == batch_id)
        ).scalar_one(),
        "observations": session.execute(
            select(func.count()).select_from(Observation).where(Observation.batch_id == batch_id)
        ).scalar_one(),
        "procedures": session.execute(
            select(func.count()).select_from(Procedure).where(Procedure.batch_id == batch_id)
        ).scalar_one(),
        "medications": session.execute(
            select(func.count()).select_from(Medication).where(Medication.batch_id == batch_id)
        ).scalar_one(),
        "immunizations": session.execute(
            select(func.count()).select_from(Immunization).where(Immunization.batch_id == batch_id)
        ).scalar_one(),
        "coverages": session.execute(
            select(func.count()).select_from(Coverage).where(Coverage.batch_id == batch_id)
        ).scalar_one(),
    }


def _resource_counts_by_type(session: Any, batch_id: str) -> dict[str, int]:
    rows = session.execute(
        select(FhirResource.resource_type, func.count(FhirResource.id))
        .where(FhirResource.batch_id == batch_id)
        .group_by(FhirResource.resource_type)
    ).all()
    return {resource_type: count for resource_type, count in rows}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _load_measure_rules(rules_path: Path = RULES_PATH) -> dict[str, dict[str, Any]]:
    if not rules_path.exists():
        return {}
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _rule_phase(rule: dict[str, Any]) -> int:
    phase = _to_float(rule.get("phase"))
    if phase is None:
        return 99
    return int(phase)


def _normalize_measure_token(measure_id: str) -> str:
    token = measure_id.strip().upper().replace("_", "-")
    if " " in token:
        token = token.replace(" ", "")
    return token


def _resolve_measure_id(selected_measure: str | None, measure_rules: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    if not selected_measure:
        return {
            "input_measure_id": None,
            "normalized_measure_id": None,
            "resolved_measure_id": None,
            "normalization_status": "none",
        }

    normalized = _normalize_measure_token(selected_measure)
    if normalized in measure_rules:
        return {
            "input_measure_id": selected_measure,
            "normalized_measure_id": normalized,
            "resolved_measure_id": normalized,
            "normalization_status": "exact",
        }

    aliased = MEASURE_ID_ALIASES.get(normalized)
    if aliased and aliased in measure_rules:
        return {
            "input_measure_id": selected_measure,
            "normalized_measure_id": normalized,
            "resolved_measure_id": aliased,
            "normalization_status": "aliased",
        }

    return {
        "input_measure_id": selected_measure,
        "normalized_measure_id": normalized,
        "resolved_measure_id": None,
        "normalization_status": "unknown",
    }


def _count_rows_for_codes(
    session: Any,
    *,
    model: Any,
    batch_id: str,
    code_column: str,
    codes: set[str],
) -> int:
    if not codes:
        return 0
    column = getattr(model, code_column)
    return session.execute(
        select(func.count(model.id))
        .where(model.batch_id == batch_id)
        .where(column.in_(sorted(codes)))
    ).scalar_one()


def _count_distinct_patients_for_codes(
    session: Any,
    *,
    model: Any,
    batch_id: str,
    code_column: str,
    codes: set[str],
) -> int:
    if not codes:
        return 0
    column = getattr(model, code_column)
    return session.execute(
        select(func.count(func.distinct(model.patient_id)))
        .join(
            Patient,
            (Patient.patient_id == model.patient_id) & (Patient.batch_id == model.batch_id),
        )
        .where(model.batch_id == batch_id)
        .where(model.patient_id.is_not(None))
        .where(column.in_(sorted(codes)))
    ).scalar_one()


def _insert_anomaly(
    session: Any,
    *,
    dq_run_id: int,
    batch_id: str,
    job_id: str,
    resource_type: str | None,
    anomaly_type: str,
    severity: str,
    details_json: dict[str, Any],
    patient_id: str | None = None,
) -> None:
    session.add(
        DataAnomaly(
            dq_run_id=dq_run_id,
            batch_id=batch_id,
            job_id=job_id,
            patient_id=patient_id,
            resource_type=resource_type,
            anomaly_type=anomaly_type,
            severity=severity,
            status="open",
            source_component="dq_worker",
            details_json=details_json,
        )
    )


def _insert_check_result(
    session: Any,
    *,
    dq_run_id: int,
    batch_id: str,
    check_name: str,
    resource_type: str | None,
    status: str,
    metric_name: str,
    metric_value: float,
    threshold_value: float | None,
    details_json: dict[str, Any],
) -> None:
    session.add(
        DqCheckResult(
            dq_run_id=dq_run_id,
            batch_id=batch_id,
            check_name=check_name,
            resource_type=resource_type,
            status=status,
            metric_name=metric_name,
            metric_value=metric_value,
            threshold_value=threshold_value,
            details_json=details_json,
        )
    )


def _create_feed_reports(session: Any, *, dq_run_id: int, batch_id: str, resource_counts: dict[str, int]) -> None:
    for resource_type, count in resource_counts.items():
        session.add(
            FeedCompletenessReport(
                dq_run_id=dq_run_id,
                batch_id=batch_id,
                resource_type=resource_type,
                record_count=count,
                measure_relevant=resource_type in MEASURE_RELEVANT_TYPES,
            )
        )


def _baseline_resource_counts(session: Any, *, source_system: str, batch_id: str) -> dict[str, float]:
    rows = session.execute(
        select(FeedCompletenessReport.resource_type, func.avg(FeedCompletenessReport.record_count))
        .join(SourceBatch, SourceBatch.id == FeedCompletenessReport.batch_id)
        .where(SourceBatch.source_system == source_system)
        .where(FeedCompletenessReport.batch_id != batch_id)
        .group_by(FeedCompletenessReport.resource_type)
    ).all()
    return {resource_type: float(avg_count) for resource_type, avg_count in rows if avg_count is not None}


def _run_dq_checks(
    session: Any,
    *,
    dq_run: DqRun,
    job: ConversionJob,
    event: DomainEvent,
    measure_rules: dict[str, dict[str, Any]],
    selected_measure: str | None,
    measure_resolution: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    session.execute(delete(DataAnomaly).where(DataAnomaly.dq_run_id == dq_run.id))
    session.execute(delete(DqCheckResult).where(DqCheckResult.dq_run_id == dq_run.id))
    session.execute(delete(FeedCompletenessReport).where(FeedCompletenessReport.dq_run_id == dq_run.id))

    resource_counts = _resource_counts_by_type(session, dq_run.batch_id)
    _create_feed_reports(session, dq_run_id=dq_run.id, batch_id=dq_run.batch_id, resource_counts=resource_counts)

    anomaly_count = 0
    failed_checks = 0
    total_checks = 0

    batch = session.get(SourceBatch, dq_run.batch_id)
    source_system = batch.source_system if batch else "unknown"
    baseline_counts = _baseline_resource_counts(session, source_system=source_system, batch_id=dq_run.batch_id)

    active_measure_ids = (
        [selected_measure]
        if selected_measure and selected_measure in measure_rules
        else sorted(measure_rules.keys(), key=lambda measure_id: (_rule_phase(measure_rules[measure_id]), measure_id))
    )
    if not active_measure_ids:
        active_measure_ids = ["_default"]

    per_measure_summary: dict[str, dict[str, Any]] = {}
    required_types = set(REQUIRED_RESOURCE_TYPES)
    if active_measure_ids != ["_default"]:
        for measure_id in active_measure_ids:
            required_types.update(measure_rules.get(measure_id, {}).get("required_resource_types", []))

    for resource_type in sorted(set(resource_counts.keys()) | required_types):
        current_count = float(resource_counts.get(resource_type, 0))
        baseline_count = baseline_counts.get(resource_type)
        matching_rules = [
            rule
            for measure_id, rule in measure_rules.items()
            if measure_id in active_measure_ids and resource_type in rule.get("required_resource_types", [])
        ]
        drop_threshold = min([_to_float(rule.get("volume_drop_threshold_pct")) or VOLUME_DROP_THRESHOLD_PCT for rule in matching_rules], default=VOLUME_DROP_THRESHOLD_PCT)
        spike_threshold = min([_to_float(rule.get("volume_spike_threshold_pct")) or VOLUME_SPIKE_THRESHOLD_PCT for rule in matching_rules], default=VOLUME_SPIKE_THRESHOLD_PCT)
        if baseline_count is None or baseline_count <= 0:
            status = "passed"
            details = {"current_count": int(current_count), "baseline_avg_count": None, "note": "insufficient_baseline"}
        else:
            drop_pct = ((baseline_count - current_count) / baseline_count) * 100 if current_count < baseline_count else 0.0
            spike_pct = ((current_count - baseline_count) / baseline_count) * 100 if current_count > baseline_count else 0.0
            details = {
                "current_count": int(current_count),
                "baseline_avg_count": round(baseline_count, 2),
                "drop_pct": round(drop_pct, 2),
                "spike_pct": round(spike_pct, 2),
                "drop_threshold_pct": drop_threshold,
                "spike_threshold_pct": spike_threshold,
            }
            status = "failed" if drop_pct >= drop_threshold or spike_pct >= spike_threshold else "passed"
            if current_count == 0 and baseline_count > 0:
                _insert_anomaly(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    job_id=dq_run.job_id,
                    resource_type=resource_type,
                    anomaly_type="feed_dropout",
                    severity="critical",
                    details_json=details,
                )
                anomaly_count += 1
            elif drop_pct >= drop_threshold:
                _insert_anomaly(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    job_id=dq_run.job_id,
                    resource_type=resource_type,
                    anomaly_type="volume_drop",
                    severity="high" if drop_pct < 70 else "critical",
                    details_json=details,
                )
                anomaly_count += 1
            elif spike_pct >= spike_threshold:
                _insert_anomaly(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    job_id=dq_run.job_id,
                    resource_type=resource_type,
                    anomaly_type="volume_spike",
                    severity="medium" if spike_pct < 150 else "high",
                    details_json=details,
                )
                anomaly_count += 1

        total_checks += 1
        if status == "failed":
            failed_checks += 1
        _insert_check_result(
            session,
            dq_run_id=dq_run.id,
            batch_id=dq_run.batch_id,
            check_name="feed_volume",
            resource_type=resource_type,
            status=status,
            metric_name="record_count",
            metric_value=current_count,
            threshold_value=baseline_count,
            details_json=details,
        )

    for required_type in sorted(required_types):
        current = resource_counts.get(required_type, 0)
        status = "passed" if current > 0 else "failed"
        total_checks += 1
        if status == "failed":
            failed_checks += 1
            _insert_anomaly(
                session,
                dq_run_id=dq_run.id,
                batch_id=dq_run.batch_id,
                job_id=dq_run.job_id,
                resource_type=required_type,
                anomaly_type="required_feed_missing",
                severity="high",
                details_json={"resource_type": required_type, "record_count": current},
            )
            anomaly_count += 1
        _insert_check_result(
            session,
            dq_run_id=dq_run.id,
            batch_id=dq_run.batch_id,
            check_name="required_resource_presence",
            resource_type=required_type,
            status=status,
            metric_name="record_count",
            metric_value=float(current),
            threshold_value=1.0,
            details_json={"resource_type": required_type},
        )

    if active_measure_ids != ["_default"]:
        for measure_id in active_measure_ids:
            rule = measure_rules[measure_id]
            observation_codes = set(rule.get("observation_codes", []))
            condition_codes = set(rule.get("required_condition_codes", []))
            procedure_codes = set(rule.get("required_procedure_codes", []))
            medication_codes = set(rule.get("required_medication_codes", []))
            immunization_codes = set(rule.get("required_immunization_codes", []))
            required_unit = rule.get("requires_unit")
            value_range = rule.get("value_range", {})
            min_value = _to_float(value_range.get("min"))
            max_value = _to_float(value_range.get("max"))
            clinical_evidence_mode = str(rule.get("clinical_evidence_mode", "any")).lower()
            min_distinct_patients = _to_float(rule.get("min_distinct_patients_with_evidence"))
            if min_distinct_patients is None:
                min_distinct_patients = 1.0

            rows: list[tuple[int, str | None, str | None, str | None, str | None]] = []
            if observation_codes:
                rows = session.execute(
                    select(Observation.id, Observation.code, Observation.value, Observation.unit, Observation.patient_id)
                    .where(Observation.batch_id == dq_run.batch_id)
                    .where(Observation.code.in_(observation_codes))
                ).all()

            evidence_checks: list[dict[str, Any]] = []
            if observation_codes:
                evidence_checks.append(
                    {
                        "resource_type": "Observation",
                        "codes": sorted(observation_codes),
                        "row_count": len(rows),
                        "patient_count": _count_distinct_patients_for_codes(
                            session,
                            model=Observation,
                            batch_id=dq_run.batch_id,
                            code_column="code",
                            codes=observation_codes,
                        ),
                    }
                )
            if condition_codes:
                evidence_checks.append(
                    {
                        "resource_type": "Condition",
                        "codes": sorted(condition_codes),
                        "row_count": _count_rows_for_codes(
                            session,
                            model=Condition,
                            batch_id=dq_run.batch_id,
                            code_column="code",
                            codes=condition_codes,
                        ),
                        "patient_count": _count_distinct_patients_for_codes(
                            session,
                            model=Condition,
                            batch_id=dq_run.batch_id,
                            code_column="code",
                            codes=condition_codes,
                        ),
                    }
                )
            if procedure_codes:
                evidence_checks.append(
                    {
                        "resource_type": "Procedure",
                        "codes": sorted(procedure_codes),
                        "row_count": _count_rows_for_codes(
                            session,
                            model=Procedure,
                            batch_id=dq_run.batch_id,
                            code_column="code",
                            codes=procedure_codes,
                        ),
                        "patient_count": _count_distinct_patients_for_codes(
                            session,
                            model=Procedure,
                            batch_id=dq_run.batch_id,
                            code_column="code",
                            codes=procedure_codes,
                        ),
                    }
                )
            if medication_codes:
                evidence_checks.append(
                    {
                        "resource_type": "MedicationRequest",
                        "codes": sorted(medication_codes),
                        "row_count": _count_rows_for_codes(
                            session,
                            model=Medication,
                            batch_id=dq_run.batch_id,
                            code_column="medication_code",
                            codes=medication_codes,
                        ),
                        "patient_count": _count_distinct_patients_for_codes(
                            session,
                            model=Medication,
                            batch_id=dq_run.batch_id,
                            code_column="medication_code",
                            codes=medication_codes,
                        ),
                    }
                )
            if immunization_codes:
                evidence_checks.append(
                    {
                        "resource_type": "Immunization",
                        "codes": sorted(immunization_codes),
                        "row_count": _count_rows_for_codes(
                            session,
                            model=Immunization,
                            batch_id=dq_run.batch_id,
                            code_column="vaccine_code",
                            codes=immunization_codes,
                        ),
                        "patient_count": _count_distinct_patients_for_codes(
                            session,
                            model=Immunization,
                            batch_id=dq_run.batch_id,
                            code_column="vaccine_code",
                            codes=immunization_codes,
                        ),
                    }
                )

            if evidence_checks:
                evidence_found = [item for item in evidence_checks if item["row_count"] > 0]
                evidence_status = "passed"
                if clinical_evidence_mode == "all":
                    evidence_status = "passed" if len(evidence_found) == len(evidence_checks) else "failed"
                else:
                    evidence_status = "passed" if evidence_found else "failed"

                max_patient_count = max(item["patient_count"] for item in evidence_checks)
                min_patients_status = "passed" if max_patient_count >= min_distinct_patients else "failed"
                if evidence_status == "failed":
                    _insert_anomaly(
                        session,
                        dq_run_id=dq_run.id,
                        batch_id=dq_run.batch_id,
                        job_id=dq_run.job_id,
                        resource_type=None,
                        anomaly_type="measure_evidence_missing",
                        severity="high",
                        details_json={
                            "measure_id": measure_id,
                            "clinical_evidence_mode": clinical_evidence_mode,
                            "evidence_checks": evidence_checks,
                        },
                    )
                    anomaly_count += 1
                if min_patients_status == "failed":
                    _insert_anomaly(
                        session,
                        dq_run_id=dq_run.id,
                        batch_id=dq_run.batch_id,
                        job_id=dq_run.job_id,
                        resource_type=None,
                        anomaly_type="measure_low_patient_coverage",
                        severity="medium",
                        details_json={
                            "measure_id": measure_id,
                            "max_patient_count": max_patient_count,
                            "min_distinct_patients_with_evidence": min_distinct_patients,
                            "evidence_checks": evidence_checks,
                        },
                    )
                    anomaly_count += 1

                total_checks += 1
                if evidence_status == "failed":
                    failed_checks += 1
                _insert_check_result(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    check_name="measure_clinical_evidence",
                    resource_type=None,
                    status=evidence_status,
                    metric_name=f"{measure_id}_evidence_rows",
                    metric_value=float(sum(item["row_count"] for item in evidence_checks)),
                    threshold_value=1.0,
                    details_json={
                        "measure_id": measure_id,
                        "phase": _rule_phase(rule),
                        "clinical_evidence_mode": clinical_evidence_mode,
                        "evidence_checks": evidence_checks,
                    },
                )

                total_checks += 1
                if min_patients_status == "failed":
                    failed_checks += 1
                _insert_check_result(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    check_name="measure_patient_coverage",
                    resource_type=None,
                    status=min_patients_status,
                    metric_name=f"{measure_id}_distinct_patients",
                    metric_value=float(max_patient_count),
                    threshold_value=float(min_distinct_patients),
                    details_json={
                        "measure_id": measure_id,
                        "phase": _rule_phase(rule),
                        "max_patient_count": max_patient_count,
                        "min_distinct_patients_with_evidence": min_distinct_patients,
                    },
                )

            unit_failures = 0
            range_failures = 0
            observations_evaluated = len(rows)
            if observation_codes:
                total_checks += 1
                presence_status = "passed" if observations_evaluated > 0 else "failed"
                if presence_status == "failed":
                    failed_checks += 1
                    _insert_anomaly(
                        session,
                        dq_run_id=dq_run.id,
                        batch_id=dq_run.batch_id,
                        job_id=dq_run.job_id,
                        resource_type="Observation",
                        anomaly_type="measure_observation_missing",
                        severity="high",
                        details_json={
                            "measure_id": measure_id,
                            "observation_codes": sorted(observation_codes),
                        },
                    )
                    anomaly_count += 1
                _insert_check_result(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    check_name="measure_observation_presence",
                    resource_type="Observation",
                    status=presence_status,
                    metric_name=f"{measure_id}_observations_evaluated",
                    metric_value=float(observations_evaluated),
                    threshold_value=1.0,
                    details_json={"measure_id": measure_id, "observation_codes": sorted(observation_codes)},
                )

                for row_id, code, value, unit, patient_id in rows:
                    if required_unit and (unit is None or unit != required_unit):
                        unit_failures += 1
                        _insert_anomaly(
                            session,
                            dq_run_id=dq_run.id,
                            batch_id=dq_run.batch_id,
                            job_id=dq_run.job_id,
                            patient_id=patient_id,
                            resource_type="Observation",
                            anomaly_type="mapping_error",
                            severity="high",
                            details_json={
                                "measure_id": measure_id,
                                "observation_pk": row_id,
                                "code": code,
                                "reason": "missing_or_invalid_unit",
                                "expected_unit": required_unit,
                                "actual_unit": unit,
                            },
                        )
                        anomaly_count += 1

                    numeric_value = _to_float(value)
                    out_of_range = (
                        numeric_value is None
                        or (min_value is not None and numeric_value < min_value)
                        or (max_value is not None and numeric_value > max_value)
                    )
                    if out_of_range:
                        range_failures += 1
                        _insert_anomaly(
                            session,
                            dq_run_id=dq_run.id,
                            batch_id=dq_run.batch_id,
                            job_id=dq_run.job_id,
                            patient_id=patient_id,
                            resource_type="Observation",
                            anomaly_type="mapping_error",
                            severity="high",
                            details_json={
                                "measure_id": measure_id,
                                "observation_pk": row_id,
                                "code": code,
                                "reason": "value_out_of_expected_range",
                                "min_expected": min_value,
                                "max_expected": max_value,
                                "actual_value": numeric_value,
                                "raw_value": value,
                            },
                        )
                        anomaly_count += 1

                total_checks += 1
                status_unit = "failed" if unit_failures > 0 else "passed"
                if status_unit == "failed":
                    failed_checks += 1
                _insert_check_result(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    check_name="measure_required_unit",
                    resource_type="Observation",
                    status=status_unit,
                    metric_name=f"{measure_id}_unit_failures",
                    metric_value=float(unit_failures),
                    threshold_value=0.0,
                    details_json={"measure_id": measure_id, "required_unit": required_unit, "observation_codes": sorted(observation_codes)},
                )

                total_checks += 1
                status_range = "failed" if range_failures > 0 else "passed"
                if status_range == "failed":
                    failed_checks += 1
                _insert_check_result(
                    session,
                    dq_run_id=dq_run.id,
                    batch_id=dq_run.batch_id,
                    check_name="measure_value_range",
                    resource_type="Observation",
                    status=status_range,
                    metric_name=f"{measure_id}_value_range_failures",
                    metric_value=float(range_failures),
                    threshold_value=0.0,
                    details_json={"measure_id": measure_id, "min": min_value, "max": max_value, "observation_codes": sorted(observation_codes)},
                )

            per_measure_summary[measure_id] = {
                "phase": _rule_phase(rule),
                "observations_evaluated": observations_evaluated,
                "unit_failures": unit_failures,
                "value_range_failures": range_failures,
            }

    severity_rows = session.execute(
        select(ValidationIssue.severity, func.count(ValidationIssue.id))
        .where(ValidationIssue.job_id == job.id)
        .group_by(ValidationIssue.severity)
    ).all()
    severity_counts = {severity: count for severity, count in severity_rows}
    error_count = severity_counts.get("error", 0)
    total_resources = resource_counts.get("Patient", 0) + resource_counts.get("Encounter", 0) + resource_counts.get("Observation", 0)
    if total_resources <= 0:
        total_resources = max(job.resources_generated, 1)
    error_rate = error_count / total_resources
    mapping_status = "failed" if error_rate > MAPPING_ERROR_RATE_THRESHOLD else "passed"
    total_checks += 1
    if mapping_status == "failed":
        failed_checks += 1
        _insert_anomaly(
            session,
            dq_run_id=dq_run.id,
            batch_id=dq_run.batch_id,
            job_id=dq_run.job_id,
            resource_type=None,
            anomaly_type="mapping_error_rate",
            severity="high",
            details_json={
                "error_count": error_count,
                "warning_count": severity_counts.get("warning", 0),
                "total_resources": total_resources,
                "error_rate": round(error_rate, 4),
            },
        )
        anomaly_count += 1
    _insert_check_result(
        session,
        dq_run_id=dq_run.id,
        batch_id=dq_run.batch_id,
        check_name="mapping_error_rate",
        resource_type=None,
        status=mapping_status,
        metric_name="error_rate",
        metric_value=error_rate,
        threshold_value=MAPPING_ERROR_RATE_THRESHOLD,
        details_json={
            "error_count": error_count,
            "warning_count": severity_counts.get("warning", 0),
            "total_resources": total_resources,
        },
    )

    coverage_violations = session.execute(
        select(func.count(Coverage.id)).where(
            Coverage.batch_id == dq_run.batch_id,
            Coverage.gap_detected == True,
            Coverage.gap_days.is_not(None),
            Coverage.gap_days > 45,
        )
    ).scalar_one()
    coverage_status = "failed" if coverage_violations > 0 else "passed"
    total_checks += 1
    if coverage_status == "failed":
        failed_checks += 1
        _insert_anomaly(
            session,
            dq_run_id=dq_run.id,
            batch_id=dq_run.batch_id,
            job_id=dq_run.job_id,
            resource_type="Coverage",
            anomaly_type="coverage_gap_violation",
            severity="high",
            details_json={"violation_count": coverage_violations, "threshold_gap_days": 45},
        )
        anomaly_count += 1
    _insert_check_result(
        session,
        dq_run_id=dq_run.id,
        batch_id=dq_run.batch_id,
        check_name="coverage_gap_45_days",
        resource_type="Coverage",
        status=coverage_status,
        metric_name="violation_count",
        metric_value=float(coverage_violations),
        threshold_value=0.0,
        details_json={"threshold_gap_days": 45},
    )

    return {
        "event_id": event.id,
        "source_system": source_system,
        "selected_measure": selected_measure,
        "measure_resolution": measure_resolution or {},
        "active_measure_ids": active_measure_ids if active_measure_ids != ["_default"] else [],
        "resource_counts": resource_counts,
        "baseline_counts": {k: round(v, 2) for k, v in baseline_counts.items()},
        "anomalies_created": anomaly_count,
        "checks_total": total_checks,
        "checks_failed": failed_checks,
        "measure_summary": per_measure_summary,
    }


def _claim_next_event(worker_id: str, event_type: str) -> DomainEvent | None:
    with session_scope() as session:
        event = session.execute(
            select(DomainEvent)
            .where(DomainEvent.event_type == event_type)
            .where(DomainEvent.status.in_(["pending", "failed"]))
            .order_by(DomainEvent.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if event is None:
            return None

        event.status = "running"
        event.attempt_count = (event.attempt_count or 0) + 1
        event.locked_by = worker_id
        event.locked_at = _utcnow()
        event.last_error = None
        session.flush()
        session.expunge(event)
        return event


def _process_event(event: DomainEvent) -> None:
    if not event.batch_id or not event.job_id:
        raise ValueError("Event is missing batch_id or job_id")

    with session_scope() as session:
        job = session.get(ConversionJob, event.job_id)
        if job is None:
            raise ValueError(f"Referenced job not found: {event.job_id}")

        dq_run = session.execute(
            select(DqRun).where(DqRun.batch_id == event.batch_id, DqRun.job_id == event.job_id)
        ).scalar_one_or_none()

        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        force_rerun = bool(payload.get("force_rerun"))
        input_measure = payload.get("measure_id") if isinstance(payload.get("measure_id"), str) else None
        rules = _load_measure_rules()
        measure_resolution = _resolve_measure_id(input_measure, rules)
        selected_measure = measure_resolution.get("resolved_measure_id")

        if dq_run is None:
            dq_run = DqRun(
                batch_id=event.batch_id,
                job_id=event.job_id,
                trigger_event_id=event.id,
                status="running",
                started_at=_utcnow(),
                summary_json={},
            )
            session.add(dq_run)
            session.flush()
        elif dq_run.status == "completed" and not force_rerun:
            tracked_event = session.get(DomainEvent, event.id)
            if tracked_event:
                tracked_event.status = "completed"
                tracked_event.processed_at = _utcnow()
                tracked_event.locked_by = None
                tracked_event.locked_at = None
            return
        else:
            dq_run.status = "running"
            dq_run.error_text = None
            dq_run.trigger_event_id = event.id

        counts = _summarize_batch(session, event.batch_id)
        dq_summary = _run_dq_checks(
            session,
            dq_run=dq_run,
            job=job,
            event=event,
            measure_rules=rules,
            selected_measure=selected_measure if isinstance(selected_measure, str) else None,
            measure_resolution=measure_resolution,
        )
        dq_run.summary_json = {
            "trigger": {
                "event_id": event.id,
                "event_type": event.event_type,
                "attempt_count": event.attempt_count,
                "force_rerun": force_rerun,
                "measure_id": selected_measure if isinstance(selected_measure, str) else None,
                "input_measure_id": measure_resolution.get("input_measure_id"),
                "normalized_measure_id": measure_resolution.get("normalized_measure_id"),
                "resolved_measure_id": measure_resolution.get("resolved_measure_id"),
                "normalization_status": measure_resolution.get("normalization_status"),
            },
            "batch_id": event.batch_id,
            "job_id": event.job_id,
            "source_job_status": job.status,
            "resource_table_counts": counts,
            **dq_summary,
        }
        dq_run.status = "completed"
        dq_run.completed_at = _utcnow()

        tracked_event = session.get(DomainEvent, event.id)
        if tracked_event:
            tracked_event.status = "completed"
            tracked_event.processed_at = _utcnow()
            tracked_event.locked_by = None
            tracked_event.locked_at = None


def _mark_event_failed(event_id: int, error_text: str) -> None:
    with session_scope() as session:
        event = session.get(DomainEvent, event_id)
        if event is None:
            return
        event.status = "failed"
        event.last_error = error_text
        event.locked_by = None
        event.locked_at = None


def process_event_by_id(event_id: int) -> bool:
    with session_scope() as session:
        event = session.get(DomainEvent, event_id)
        if event is None:
            return False
        session.expunge(event)

    try:
        _process_event(event)
        return True
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        LOGGER.exception("Failed to process event id=%s: %s", event_id, message)
        _mark_event_failed(event_id, message)
        return False


def run_worker(*, poll_seconds: int, once: bool = False, event_type: str = DEFAULT_EVENT_TYPE) -> int:
    worker_id = f"dq-worker:{socket.gethostname()}"
    LOGGER.info("Starting DQ worker id=%s event_type=%s poll=%ss once=%s", worker_id, event_type, poll_seconds, once)
    processed = 0
    while True:
        event = _claim_next_event(worker_id, event_type)
        if event is None:
            if once:
                return processed
            time.sleep(poll_seconds)
            continue

        try:
            _process_event(event)
            processed += 1
            LOGGER.info("Processed event id=%s batch_id=%s job_id=%s", event.id, event.batch_id, event.job_id)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Failed to process event id=%s: %s", event.id, message)
            _mark_event_failed(event.id, message)

        if once:
            return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="DQ/AD event worker")
    parser.add_argument("--poll-seconds", type=int, default=5, help="Polling interval for domain events")
    parser.add_argument("--once", action="store_true", help="Process at most one available event and exit")
    parser.add_argument("--event-type", default=DEFAULT_EVENT_TYPE, help="Domain event type to consume")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    run_worker(poll_seconds=max(args.poll_seconds, 1), once=args.once, event_type=args.event_type)


if __name__ == "__main__":
    main()