"""Stream a denormalized CSV into a FHIR R4 transaction Bundle.

The pipeline is intentionally split into four stages:

1. The main process reads CSV chunks and maps rows to plain dictionaries.
2. Worker processes validate dictionaries with fhir.resources.
3. Workers serialize validated resources to JSON strings.
4. The main process writes Bundle entries sequentially.

Example:
    python scripts/csv_to_fhir_bundle.py \
        --input observations.csv \
        --output observations-bundle.json \
        --resource-type Observation \
        --id-column Id \
        --patient-column PATIENT \
        --code-column CODE \
        --description-column DESCRIPTION \
        --date-column DATE \
        --value-column VALUE \
        --unit-column UNITS
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
from fhir.resources.allergyintolerance import AllergyIntolerance
from fhir.resources.basic import Basic
from fhir.resources.claim import Claim
from fhir.resources.condition import Condition
from fhir.resources.coverage import Coverage
from fhir.resources.device import Device
from fhir.resources.encounter import Encounter
from fhir.resources.imagingstudy import ImagingStudy
from fhir.resources.immunization import Immunization
from fhir.resources.medicationrequest import MedicationRequest
from fhir.resources.observation import Observation
from fhir.resources.organization import Organization
from fhir.resources.patient import Patient
from fhir.resources.practitioner import Practitioner
from fhir.resources.procedure import Procedure
from fhir.resources.supplydelivery import SupplyDelivery

LOGGER = logging.getLogger("csv_to_fhir_bundle")
FHIR_NAMESPACE = uuid.UUID("44f46614-c2f4-42d0-8d47-f4f57a0535e0")

MODEL_BY_RESOURCE_TYPE: dict[str, type] = {
    "AllergyIntolerance": AllergyIntolerance,
    "Basic": Basic,
    "Claim": Claim,
    "Condition": Condition,
    "Coverage": Coverage,
    "Device": Device,
    "Encounter": Encounter,
    "ImagingStudy": ImagingStudy,
    "Immunization": Immunization,
    "MedicationRequest": MedicationRequest,
    "Observation": Observation,
    "Organization": Organization,
    "Patient": Patient,
    "Practitioner": Practitioner,
    "Procedure": Procedure,
    "SupplyDelivery": SupplyDelivery,
}


@dataclass(frozen=True)
class ColumnMapping:
    resource_type: str
    id_column: str
    patient_column: str | None = None
    encounter_column: str | None = None
    code_column: str | None = None
    description_column: str | None = None
    date_column: str | None = None
    value_column: str | None = None
    unit_column: str | None = None
    status_column: str | None = None


@dataclass
class PipelineMetrics:
    rows_read: int = 0
    rows_mapped: int = 0
    resources_validated: int = 0
    rows_rejected: int = 0
    extraction_seconds: float = 0.0
    compute_seconds: float = 0.0
    write_seconds: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class WorkerResult:
    row_number: int
    resource_json: str | None
    error: str | None


def deterministic_resource_id(resource_type: str, source_id: str) -> str:
    return str(uuid.uuid5(FHIR_NAMESPACE, f"{resource_type}:{source_id}"))


def _clean(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def _reference(resource_type: str, value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    return {"reference": f"{resource_type}/{deterministic_resource_id(resource_type, value)}", "display": value}


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return pd.to_datetime(value, utc=True).date().isoformat()
    except (TypeError, ValueError):
        return None


def _datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = pd.to_datetime(value, utc=True)
        return parsed.isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


def _concept(code: str | None, display: str | None, system: str = "http://snomed.info/sct") -> dict[str, Any] | None:
    if not code and not display:
        return None
    coding: dict[str, str] = {"system": system}
    if code:
        coding["code"] = code
    if display:
        coding["display"] = display
    return {"coding": [coding], "text": display or code}


def map_row(row: dict[str, Any], row_number: int, mapping: ColumnMapping) -> dict[str, Any]:
    """Map one flat row to a structural FHIR dictionary; no FHIR validation occurs here."""
    source_id = _clean(row.get(mapping.id_column))
    if not source_id:
        raise ValueError(f"missing {mapping.id_column}")

    resource_id = deterministic_resource_id(mapping.resource_type, source_id)
    patient_id = _clean(row.get(mapping.patient_column)) if mapping.patient_column else None
    encounter_id = _clean(row.get(mapping.encounter_column)) if mapping.encounter_column else None
    code = _clean(row.get(mapping.code_column)) if mapping.code_column else None
    description = _clean(row.get(mapping.description_column)) if mapping.description_column else None
    status = _clean(row.get(mapping.status_column)) if mapping.status_column else None
    date_value = _clean(row.get(mapping.date_column)) if mapping.date_column else None

    common: dict[str, Any] = {"resourceType": mapping.resource_type, "id": resource_id}
    if mapping.resource_type == "Patient":
        common.update({"identifier": [{"value": source_id}], "name": [{"text": description or source_id}], "gender": status or "unknown"})
        if date_value and _date(date_value):
            common["birthDate"] = _date(date_value)
    elif mapping.resource_type == "Observation":
        common.update({"status": status or "final", "subject": _reference("Patient", patient_id), "code": _concept(code, description, "http://loinc.org") or {"text": "Unknown"}})
        if encounter_id:
            common["encounter"] = _reference("Encounter", encounter_id)
        if date_value and _datetime(date_value):
            common["effectiveDateTime"] = _datetime(date_value)
        value = _clean(row.get(mapping.value_column)) if mapping.value_column else None
        unit = _clean(row.get(mapping.unit_column)) if mapping.unit_column else None
        if value:
            try:
                common["valueQuantity"] = {"value": float(value), "unit": unit or "1"}
            except ValueError:
                common["valueString"] = value
    elif mapping.resource_type in {"Condition", "Procedure"}:
        common.update({"subject": _reference("Patient", patient_id), "code": _concept(code, description) or {"text": "Unknown"}})
        if mapping.resource_type == "Condition":
            common["clinicalStatus"] = {"coding": [{"code": "active"}]}
            if date_value and _datetime(date_value):
                common["onsetDateTime"] = _datetime(date_value)
        else:
            common["status"] = status or "completed"
            if date_value and _datetime(date_value):
                common["performedDateTime"] = _datetime(date_value)
    elif mapping.resource_type == "Encounter":
        common.update({"status": status or "finished", "class": {"code": "AMB"}, "subject": _reference("Patient", patient_id)})
        if date_value and _datetime(date_value):
            common["period"] = {"start": _datetime(date_value)}
    elif mapping.resource_type == "MedicationRequest":
        common.update({"status": status or "active", "intent": "order", "subject": _reference("Patient", patient_id), "medicationCodeableConcept": _concept(code, description, "http://www.nlm.nih.gov/research/umls/rxnorm") or {"text": "Unknown"}})
        if date_value and _datetime(date_value):
            common["authoredOn"] = _datetime(date_value)
    elif mapping.resource_type == "Immunization":
        common.update({"status": status or "completed", "patient": _reference("Patient", patient_id), "vaccineCode": _concept(code, description, "http://hl7.org/fhir/sid/cvx") or {"text": "Unknown"}, "occurrenceDateTime": _datetime(date_value) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
    elif mapping.resource_type == "Claim":
        common.update({"status": status or "active", "use": "claim", "created": _date(date_value) or datetime.now(timezone.utc).date().isoformat(), "type": {"coding": [{"code": "professional"}]}, "patient": _reference("Patient", patient_id), "priority": {"coding": [{"code": "normal"}]}, "insurance": [{"sequence": 1, "focal": True, "coverage": {"display": description or "Source coverage"}}]})
    elif mapping.resource_type == "Coverage":
        common.update({"status": status or "active", "beneficiary": _reference("Patient", patient_id), "payor": [{"display": description or "Source payer"}]})
    elif mapping.resource_type == "AllergyIntolerance":
        common.update({"clinicalStatus": {"coding": [{"code": "active"}]}, "verificationStatus": {"coding": [{"code": "confirmed"}]}, "patient": _reference("Patient", patient_id), "code": _concept(code, description) or {"text": "Unknown allergy"}})
    elif mapping.resource_type == "Device":
        common.update({"status": status or "active", "patient": _reference("Patient", patient_id), "type": _concept(code, description) or {"text": "Unknown device"}})
    elif mapping.resource_type == "ImagingStudy":
        common.update({"status": status or "available", "subject": _reference("Patient", patient_id), "started": _datetime(date_value) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "numberOfSeries": 0, "numberOfInstances": 0})
    elif mapping.resource_type == "Organization":
        common["name"] = description or source_id
    elif mapping.resource_type == "Practitioner":
        common["name"] = [{"text": description or source_id}]
    elif mapping.resource_type == "SupplyDelivery":
        common.update({"status": status or "completed", "patient": _reference("Patient", patient_id), "suppliedItem": {"quantity": {"value": 1}, "itemCodeableConcept": _concept(code, description) or {"text": "Supply"}}})
    elif mapping.resource_type == "Basic":
        common["code"] = {"text": description or "Source record"}
    else:
        raise ValueError(f"unsupported resource type: {mapping.resource_type}")

    return common


def validate_and_serialize(batch: list[tuple[int, dict[str, Any], str]]) -> list[WorkerResult]:
    """Worker entry point. It must remain top-level for Windows spawn support."""
    results: list[WorkerResult] = []
    for row_number, resource_dict, resource_type in batch:
        try:
            model = MODEL_BY_RESOURCE_TYPE[resource_type]
            resource = model.parse_obj(resource_dict)
            results.append(WorkerResult(row_number, resource.json(exclude_none=True), None))
        except Exception as exc:  # A bad row must not abort the worker batch.
            results.append(WorkerResult(row_number, None, f"{type(exc).__name__}: {exc}"))
    return results


def iter_batches(rows: Iterator[tuple[int, dict[str, Any], str]], batch_size: int) -> Iterator[list[tuple[int, dict[str, Any], str]]]:
    batch: list[tuple[int, dict[str, Any], str]] = []
    for item in rows:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def write_bundle_header(handle: Any) -> None:
    handle.write('{"resourceType":"Bundle","type":"transaction","entry":[')


def write_bundle_entry(handle: Any, resource_json: str, resource_type: str, first: bool) -> None:
    if not first:
        handle.write(",")
    resource = json.loads(resource_json)
    entry = {"resource": resource, "request": {"method": "PUT", "url": f"{resource_type}/{resource['id']}"}}
    handle.write(json.dumps(entry, separators=(",", ":")))


def write_bundle_footer(handle: Any) -> None:
    handle.write("]}")


def run_pipeline(input_path: Path, output_path: Path, mapping: ColumnMapping, chunk_size: int, batch_size: int, use_parallel: bool, workers: int | None) -> PipelineMetrics:
    metrics = PipelineMetrics()
    row_number = 1

    def mapped_rows() -> Iterator[tuple[int, dict[str, Any], str]]:
        nonlocal row_number
        for chunk in pd.read_csv(input_path, chunksize=chunk_size, dtype=str, keep_default_na=False):
            metrics.rows_read += len(chunk)
            for row in chunk.to_dict(orient="records"):
                current_row = row_number
                row_number += 1
                try:
                    yield current_row, map_row(row, current_row, mapping), mapping.resource_type
                    metrics.rows_mapped += 1
                except Exception as exc:
                    metrics.rows_rejected += 1
                    metrics.errors.append({"row": current_row, "stage": "mapping", "error": str(exc)})
                    LOGGER.warning("row=%s mapping failed: %s", current_row, exc)

    pool = ProcessPoolExecutor(max_workers=workers) if use_parallel else None
    first_entry = True

    def write_results(results: list[WorkerResult], output: Any) -> None:
        nonlocal first_entry
        write_start = time.perf_counter()
        for result in results:
            if result.error:
                metrics.rows_rejected += 1
                metrics.errors.append({"row": result.row_number, "stage": "validation", "error": result.error})
                LOGGER.warning("row=%s validation failed: %s", result.row_number, result.error)
                continue
            write_bundle_entry(output, result.resource_json or "{}", mapping.resource_type, first_entry)
            first_entry = False
            metrics.resources_validated += 1
        output.flush()
        metrics.write_seconds += time.perf_counter() - write_start

    try:
        with output_path.open("w", encoding="utf-8", newline="") as output:
            write_bundle_header(output)
            extraction_start = time.perf_counter()
            if pool:
                in_flight: dict[Any, float] = {}
                max_in_flight = max(1, (workers or 1) * 2)
                for batch in iter_batches(mapped_rows(), batch_size):
                    while len(in_flight) >= max_in_flight:
                        done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                        for future in done:
                            compute_start = in_flight.pop(future)
                            write_results(future.result(), output)
                            metrics.compute_seconds += time.perf_counter() - compute_start
                    future = pool.submit(validate_and_serialize, batch)
                    in_flight[future] = time.perf_counter()
                while in_flight:
                    done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in done:
                        compute_start = in_flight.pop(future)
                        write_results(future.result(), output)
                        metrics.compute_seconds += time.perf_counter() - compute_start
            else:
                for batch in iter_batches(mapped_rows(), batch_size):
                    compute_start = time.perf_counter()
                    write_results(validate_and_serialize(batch), output)
                    metrics.compute_seconds += time.perf_counter() - compute_start
            metrics.extraction_seconds = time.perf_counter() - extraction_start
            write_bundle_footer(output)
    finally:
        if pool:
            pool.shutdown(wait=True)
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resource-type", choices=sorted(MODEL_BY_RESOURCE_TYPE), required=True)
    parser.add_argument("--id-column", required=True)
    parser.add_argument("--patient-column")
    parser.add_argument("--encounter-column")
    parser.add_argument("--code-column")
    parser.add_argument("--description-column")
    parser.add_argument("--date-column")
    parser.add_argument("--value-column")
    parser.add_argument("--unit-column")
    parser.add_argument("--status-column")
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--use-parallel", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--error-log", type=Path, default=Path("fhir-conversion-errors.jsonl"))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.chunk_size < 1 or args.batch_size < 1:
        raise SystemExit("--chunk-size and --batch-size must be positive")
    mapping = ColumnMapping(
        resource_type=args.resource_type,
        id_column=args.id_column,
        patient_column=args.patient_column,
        encounter_column=args.encounter_column,
        code_column=args.code_column,
        description_column=args.description_column,
        date_column=args.date_column,
        value_column=args.value_column,
        unit_column=args.unit_column,
        status_column=args.status_column,
    )
    started = time.perf_counter()
    metrics = run_pipeline(args.input, args.output, mapping, args.chunk_size, args.batch_size, args.use_parallel, args.workers)
    if metrics.errors:
        with args.error_log.open("w", encoding="utf-8") as errors:
            for error in metrics.errors:
                errors.write(json.dumps(error) + "\n")
    elapsed = time.perf_counter() - started
    print(json.dumps({"rows_read": metrics.rows_read, "rows_mapped": metrics.rows_mapped, "resources_validated": metrics.resources_validated, "rows_rejected": metrics.rows_rejected, "extraction_read_seconds": round(metrics.extraction_seconds, 3), "compute_mapping_validation_seconds": round(metrics.compute_seconds, 3), "write_io_seconds": round(metrics.write_seconds, 3), "total_seconds": round(elapsed, 3), "parallel": args.use_parallel, "workers": args.workers if args.use_parallel else 0, "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
