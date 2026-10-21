from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_FILES: dict[str, dict[str, Any]] = {
    "patients.csv": {"required": True, "id_col": "Id", "patient_col": "Id"},
    "encounters.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT"},
    "conditions.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "observations.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "procedures.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "medications.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "immunizations.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "careplans.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "claims.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENTID"},
    "coverages.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT"},
    # Synthea commonly emits payer_transitions.csv instead of coverages.csv.
    # Use row fallback IDs to avoid collapsing multiple transitions under one member id.
    "payer_transitions.csv": {"required": False, "id_col": "__rowid__", "patient_col": "PATIENT"},
}


@dataclass
class RowIssue:
    source_file: str
    row_number: int
    severity: str
    code: str
    message: str
    source_record_id: str | None = None


@dataclass
class SourceRecord:
    source_file: str
    row_number: int
    source_record_id: str
    patient_id: str | None
    encounter_id: str | None
    data: dict[str, str]

    @property
    def fingerprint(self) -> str:
        raw = f"{self.source_file}:{self.source_record_id}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class DatasetMetadata:
    input_dir: str
    found_files: list[str] = field(default_factory=list)
    missing_required_files: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    malformed_rows: dict[str, int] = field(default_factory=dict)


@dataclass
class ParsedDataset:
    metadata: DatasetMetadata
    records_by_file: dict[str, list[SourceRecord]]
    issues: list[RowIssue]

    @property
    def total_rows(self) -> int:
        return sum(self.metadata.row_counts.values())


def discover_dataset(input_dir: str) -> DatasetMetadata:
    base = Path(input_dir)
    metadata = DatasetMetadata(input_dir=str(base))

    for filename, spec in SUPPORTED_FILES.items():
        file_path = base / filename
        if file_path.exists():
            metadata.found_files.append(filename)
            metadata.row_counts[filename] = count_rows(file_path)
        elif spec.get("required"):
            metadata.missing_required_files.append(filename)

    return metadata


def count_rows(file_path: Path) -> int:
    with file_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def parse_dataset(input_dir: str) -> ParsedDataset:
    base = Path(input_dir)
    metadata = discover_dataset(input_dir)
    issues: list[RowIssue] = []
    records_by_file: dict[str, list[SourceRecord]] = {}

    for filename in metadata.found_files:
        spec = SUPPORTED_FILES[filename]
        file_path = base / filename
        parsed_rows, row_issues = parse_file(file_path, filename, spec)
        records_by_file[filename] = parsed_rows
        issues.extend(row_issues)

    return ParsedDataset(metadata=metadata, records_by_file=records_by_file, issues=issues)


def parse_file(file_path: Path, source_file: str, spec: dict[str, Any]) -> tuple[list[SourceRecord], list[RowIssue]]:
    id_col = spec.get("id_col", "Id")
    patient_col = spec.get("patient_col")
    encounter_col = spec.get("encounter_col")

    issues: list[RowIssue] = []
    records: list[SourceRecord] = []
    seen_ids: set[str] = set()

    with file_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            issues.append(
                RowIssue(
                    source_file=source_file,
                    row_number=0,
                    severity="error",
                    code="missing_header",
                    message="CSV file is missing a header row.",
                )
            )
            return records, issues

        for row_number, row in enumerate(reader, start=2):
            normalized = {k.strip(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k is not None}

            source_record_id = normalized.get(id_col) or f"row-{row_number}"
            patient_id = normalized.get(patient_col) if patient_col else None
            encounter_id = normalized.get(encounter_col) if encounter_col else None

            if source_record_id in seen_ids:
                issues.append(
                    RowIssue(
                        source_file=source_file,
                        row_number=row_number,
                        severity="warning",
                        code="duplicate_source_id",
                        message="Duplicate source record ID encountered; duplicate row retained for troubleshooting and ignored for conversion.",
                        source_record_id=source_record_id,
                    )
                )
                continue

            seen_ids.add(source_record_id)

            if not source_record_id:
                issues.append(
                    RowIssue(
                        source_file=source_file,
                        row_number=row_number,
                        severity="error",
                        code="missing_source_id",
                        message="Unable to determine source ID for row.",
                    )
                )
                continue

            if source_file != "patients.csv" and not patient_id:
                issues.append(
                    RowIssue(
                        source_file=source_file,
                        row_number=row_number,
                        severity="error",
                        code="missing_patient_id",
                        message="Missing patient foreign key in source row.",
                        source_record_id=source_record_id,
                    )
                )
                continue

            records.append(
                SourceRecord(
                    source_file=source_file,
                    row_number=row_number,
                    source_record_id=source_record_id,
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    data=normalized,
                )
            )

    return records, issues
