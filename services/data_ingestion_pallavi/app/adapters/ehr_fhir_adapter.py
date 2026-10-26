"""Adapter for local EHR-style FHIR R4 JSON, NDJSON, and Bundle exports."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base import DatasetMetadata, ParsedDataset, RowIssue, SourceAdapter, SourceRecord


class EHRFHIRAdapter(SourceAdapter):
    """Read FHIR resources from a file or directory without flattening them."""

    @property
    def adapter_name(self) -> str:
        return "ehr_fhir_r4"

    @property
    def supported_formats(self) -> list[str]:
        return [".json", ".ndjson"]

    def discover(self, input_source: str) -> DatasetMetadata:
        path = Path(input_source)
        metadata = DatasetMetadata(input_dir=str(path))
        files = [path] if path.is_file() else sorted(path.glob("*.json")) + sorted(path.glob("*.ndjson"))
        metadata.found_files = [file.name for file in files]
        return metadata

    def parse(self, input_source: str) -> ParsedDataset:
        metadata = self.discover(input_source)
        issues: list[RowIssue] = []
        records_by_file: dict[str, list[SourceRecord]] = {}
        for file_name, record in self._iter_records(input_source, issues.append):
            records_by_file.setdefault(file_name, []).append(record)
        for file_name, records in records_by_file.items():
            metadata.row_counts[file_name] = len(records)
        return ParsedDataset(metadata=metadata, records_by_file=records_by_file, issues=issues)

    def stream(
        self,
        input_source: str,
        issue_callback: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterator[SourceRecord]]:
        metadata = self.discover(input_source)

        def records() -> Iterator[SourceRecord]:
            counts: dict[str, int] = {}
            # Counts update progressively while the lazy records generator is consumed.
            for file_name, record in self._iter_records(input_source, issue_callback):
                counts[file_name] = counts.get(file_name, 0) + 1
                metadata.row_counts[file_name] = counts[file_name]
                yield record

        return metadata, records()

    def _iter_records(
        self,
        input_source: str,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        path = Path(input_source)
        files = [path] if path.is_file() else sorted(path.glob("*.json")) + sorted(path.glob("*.ndjson"))
        for file_path in files:
            if file_path.suffix.lower() == ".ndjson":
                yield from self._iter_ndjson(file_path, issue_callback)
            else:
                yield from self._iter_json(file_path, issue_callback)

    def _iter_ndjson(
        self,
        file_path: Path,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        with file_path.open("r", encoding="utf-8") as handle:
            for row_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    document = json.loads(line)
                    if not self._is_fhir_resource(document):
                        self._report(issue_callback, file_path.name, row_number, "Skipping JSON value without a FHIR resourceType")
                        continue
                    yield file_path.name, self._to_record(document, file_path.name, row_number)
                except Exception as exc:
                    self._report(issue_callback, file_path.name, row_number, str(exc))

    def _iter_json(
        self,
        file_path: Path,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
            if not self._is_fhir_resource(document):
                self._report(issue_callback, file_path.name, 0, "Skipping JSON value without a FHIR resourceType")
                return
            if document.get("resourceType") == "Bundle":
                resources = document.get("entry") or []
            else:
                resources = [{"resource": document}]
            for row_number, entry in enumerate(resources, start=1):
                if not isinstance(entry, dict) or not isinstance(entry.get("resource"), dict):
                    self._report(issue_callback, file_path.name, row_number, "Skipping Bundle entry without a resource object")
                    continue
                resource = entry["resource"]
                if not self._is_fhir_resource(resource):
                    self._report(issue_callback, file_path.name, row_number, "Skipping Bundle entry without a FHIR resourceType")
                    continue
                yield file_path.name, self._to_record(resource, file_path.name, row_number)
        except Exception as exc:
            self._report(issue_callback, file_path.name, 0, str(exc))

    @staticmethod
    def _is_fhir_resource(value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("resourceType"), str) and bool(value["resourceType"])

    @staticmethod
    def _extract_id_from_reference(reference_node: Any) -> str | None:
        if isinstance(reference_node, list):
            reference_node = next((item for item in reference_node if item is not None), None)
        if not isinstance(reference_node, dict):
            return None
        reference = reference_node.get("reference")
        if not isinstance(reference, str) or not reference:
            return None
        if reference.startswith("#"):
            reference = reference[1:]
        parts = [part for part in reference.split("/") if part]
        return parts[-1] if parts else None

    @staticmethod
    def _to_record(resource: dict[str, Any], file_name: str, row_number: int) -> SourceRecord:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        if not resource_type or not resource_id:
            raise ValueError("FHIR resourceType and id are required")
        subject = resource.get("subject") or resource.get("patient") or resource.get("beneficiary") or {}
        patient_id = EHRFHIRAdapter._extract_id_from_reference(subject)
        encounter = resource.get("encounter") or {}
        encounter_id = EHRFHIRAdapter._extract_id_from_reference(encounter)
        return SourceRecord(
            source_file=f"fhir:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{resource_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            data={"resourceType": resource_type, "resource_json": json.dumps(resource)},
        )

    @staticmethod
    def _report(callback: Callable[[RowIssue], None] | None, file_name: str, row_number: int, message: str) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", "invalid_fhir_json", message))
