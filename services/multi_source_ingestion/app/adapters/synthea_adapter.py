"""
Synthea CSV source adapter.

Implements SourceAdapter interface for Synthea-generated CSV files.
Wraps the existing synthea.py parsing logic.
"""

from __future__ import annotations

import csv
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Any

from .base import DatasetMetadata, ParsedDataset, RowIssue, SourceAdapter, SourceRecord


SYNTHEA_SUPPORTED_FILES: dict[str, dict[str, Any]] = {
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
    "allergies.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "claims_transactions.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENTID"},
    "devices.csv": {"required": False, "id_col": "UDI", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "imaging_studies.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
    "organizations.csv": {"required": False, "id_col": "Id"},
    "payer_transitions.csv": {"required": False, "id_col": "__row__", "patient_col": "PATIENT"},
    "payers.csv": {"required": False, "id_col": "Id", "patient_required": False},
    "providers.csv": {"required": False, "id_col": "Id", "patient_required": False},
    "organizations.csv": {"required": False, "id_col": "Id", "patient_required": False},
    "supplies.csv": {"required": False, "id_col": "Id", "patient_col": "PATIENT", "encounter_col": "ENCOUNTER"},
}


class SyntheaCSVAdapter(SourceAdapter):
    """
    Adapter for Synthea-generated CSV files.
    
    Discovers and parses standard Synthea output directory containing:
    - patients.csv (required)
    - encounters.csv, conditions.csv, observations.csv, etc. (optional)
    
    Returns normalized SourceRecord objects with:
    - source_file: CSV filename
    - source_record_id: Id column value
    - patient_id: Patient FK (varies by file)
    - encounter_id: Encounter FK (if applicable)
    - data: Dict of all CSV columns
    """

    @property
    def adapter_name(self) -> str:
        return "synthea_csv"

    @property
    def supported_formats(self) -> list[str]:
        return [".csv"]

    def discover(self, input_dir: str) -> DatasetMetadata:
        """
        Scan directory for Synthea CSV files.
        
        Args:
            input_dir: Path to directory containing Synthea CSVs
        
        Returns:
            DatasetMetadata with found files and row counts
        """
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))

        for filename, spec in SYNTHEA_SUPPORTED_FILES.items():
            file_path = base / filename
            if file_path.exists():
                metadata.found_files.append(filename)
                metadata.row_counts[filename] = self._count_rows(file_path)
            elif spec.get("required"):
                metadata.missing_required_files.append(filename)

        return metadata

    def parse(self, input_dir: str) -> ParsedDataset:
        """
        Parse Synthea CSV directory into normalized records.
        
        Args:
            input_dir: Path to directory containing Synthea CSVs
        
        Returns:
            ParsedDataset with records_by_file and any parsing issues
        """
        base = Path(input_dir)
        metadata = self.discover(input_dir)
        issues: list[RowIssue] = []
        records_by_file: dict[str, list[SourceRecord]] = {}

        for filename in metadata.found_files:
            spec = SYNTHEA_SUPPORTED_FILES[filename]
            file_path = base / filename
            parsed_rows, row_issues = self._parse_file(file_path, filename, spec)
            records_by_file[filename] = parsed_rows
            issues.extend(row_issues)

        return ParsedDataset(metadata=metadata, records_by_file=records_by_file, issues=issues)

    def stream(
        self,
        input_dir: str,
        issue_callback: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterator[SourceRecord]]:
        metadata = self.discover(input_dir)
        base = Path(input_dir)

        def records() -> Iterator[SourceRecord]:
            for filename in metadata.found_files:
                spec = SYNTHEA_SUPPORTED_FILES[filename]
                yield from self._iter_file(
                    base / filename,
                    filename,
                    spec,
                    issue_callback,
                )

        return metadata, records()

    @staticmethod
    def _count_rows(file_path: Path) -> int:
        """Count rows in CSV file (excluding header)."""
        with file_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                next(reader)
            except StopIteration:
                return 0
            return sum(1 for _ in reader)

    @staticmethod
    def _parse_file(
        file_path: Path, source_file: str, spec: dict[str, Any]
    ) -> tuple[list[SourceRecord], list[RowIssue]]:
        """Parse single Synthea CSV file into SourceRecord objects."""
        issues: list[RowIssue] = []
        records = list(SyntheaCSVAdapter._iter_file(file_path, source_file, spec, issues.append))
        return records, issues

    @staticmethod
    def _iter_file(
        file_path: Path,
        source_file: str,
        spec: dict[str, Any],
        issue_callback: Callable[[RowIssue], None] | None = None,
    ) -> Iterator[SourceRecord]:
        id_col = spec.get("id_col", "Id")
        patient_col = spec.get("patient_col")
        encounter_col = spec.get("encounter_col")
        seen_ids: set[str] = set()

        def report(issue: RowIssue) -> None:
            if issue_callback:
                issue_callback(issue)

        with file_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                report(
                    RowIssue(
                        source_file=source_file,
                        row_number=0,
                        severity="error",
                        code="missing_header",
                        message="CSV file is missing a header row.",
                    )
                )
                return

            for row_number, row in enumerate(reader, start=2):
                normalized = {
                    k.strip(): (v.strip() if isinstance(v, str) else "")
                    for k, v in row.items()
                    if k is not None
                }

                source_record_id = normalized.get(id_col) or f"row-{row_number}"
                patient_id = normalized.get(patient_col) if patient_col else None
                encounter_id = normalized.get(encounter_col) if encounter_col else None

                if source_record_id in seen_ids:
                    report(
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
                    report(
                        RowIssue(
                            source_file=source_file,
                            row_number=row_number,
                            severity="error",
                            code="missing_source_id",
                            message="Unable to determine source ID for row.",
                        )
                    )
                    continue

                if source_file != "patients.csv" and spec.get("patient_required", True) and not patient_id:
                    report(
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

                yield SourceRecord(
                    source_file=source_file,
                    row_number=row_number,
                    source_record_id=source_record_id,
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    data=normalized,
                )
