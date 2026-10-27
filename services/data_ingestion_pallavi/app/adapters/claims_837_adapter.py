"""Streaming X12 837 professional claim adapter."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base import DatasetMetadata, ParsedDataset, RowIssue, SourceAdapter, SourceRecord


class Claims837Adapter(SourceAdapter):
    """Parse common 837P loops into FHIR Claim-family resources."""

    @property
    def adapter_name(self) -> str:
        return "claims_837"

    @property
    def supported_formats(self) -> list[str]:
        return [".837", ".x12", ".edi", ".txt"]

    def discover(self, input_source: str) -> DatasetMetadata:
        path = Path(input_source)
        metadata = DatasetMetadata(input_dir=str(path))
        files = [path] if path.is_file() else sorted(
            file for extension in self.supported_formats for file in path.glob(f"*{extension}")
        )
        unique_files = list(dict.fromkeys(files))
        metadata.found_files = [file.name for file in unique_files]
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
        files = [path] if path.is_file() else [path / name for name in self.discover(input_source).found_files]
        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                yield from self._parse_transaction(text, file_path.name, issue_callback)
            except OSError as exc:
                self._report(issue_callback, file_path.name, 0, "claims_837_read_error", str(exc))

    def _parse_transaction(
        self,
        text: str,
        file_name: str,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        element_separator = text[3] if text.startswith("ISA") and len(text) > 3 else "*"
        segment_terminator = "~"
        if text.startswith("ISA") and len(text) > 105:
            segment_terminator = text[105]
        segments = [segment.strip() for segment in text.replace("\r", "").replace("\n", "").split(segment_terminator) if segment.strip()]
        claim_segments: list[list[str]] = []
        current: list[list[str]] | None = None
        preamble: list[list[str]] = []
        for raw_segment in segments:
            fields = raw_segment.split(element_separator)
            if fields[0] == "CLM":
                if current:
                    claim_segments.append(current)
                current = [*preamble, fields]
            elif current is not None:
                current.append(fields)
            else:
                preamble.append(fields)
        if current:
            claim_segments.append(current)

        for claim_index, claim in enumerate(claim_segments, start=1):
            yield from self._claim_records(claim, file_name, claim_index, issue_callback)

    def _claim_records(
        self,
        segments: list[list[str]],
        file_name: str,
        claim_index: int,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        claim = next((segment for segment in segments if segment[0] == "CLM"), None)
        if not claim or len(claim) < 2:
            self._report(issue_callback, file_name, claim_index, "invalid_claim", "837 claim is missing CLM01 claim ID")
            return
        claim_id = claim[1]
        patient_id = self._patient_id(segments)
        if not patient_id:
            self._report(issue_callback, file_name, claim_index, "missing_patient_id", f"Claim {claim_id} has no subscriber/member identifier")
            return
        claim_date = self._date(self._first_value(segments, "DTP", "472"))
        encounter_id = self._stable_id("Encounter", f"{claim_id}:encounter")
        claim_resource_id = self._stable_id("Claim", claim_id)
        claim_payload = {
            "resourceType": "Claim",
            "id": claim_resource_id,
            "status": "active",
            "use": "claim",
            "created": claim_date or "1970-01-01",
            "type": {"coding": [{"code": "professional"}]},
            "patient": {"reference": f"Patient/{patient_id}"},
            "provider": {"display": "837 billing provider"},
            "priority": {"coding": [{"code": "normal"}]},
            "insurance": [{"sequence": 1, "focal": True, "coverage": {"display": "837 payer"}}],
        }
        yield file_name, self._record(file_name, claim_index, "Claim", claim_id, patient_id, claim_payload)
        encounter_payload = {
            "resourceType": "Encounter",
            "id": encounter_id,
            "status": "finished",
            "class": {"code": "AMB"},
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        yield file_name, self._record(file_name, claim_index, "Encounter", f"{claim_id}:encounter", patient_id, encounter_payload, encounter_id)

        for line_index, segment in enumerate((item for item in segments if item[0] == "SV1"), start=1):
            procedure_code = self._composite_code(segment[1] if len(segment) > 1 else "")
            procedure_id = f"{claim_id}:procedure:{line_index}"
            procedure_payload = {
                "resourceType": "Procedure",
                "id": self._stable_id("Procedure", procedure_id),
                "status": "completed",
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": procedure_code or "unknown"}]},
            }
            yield file_name, self._record(file_name, claim_index, "Procedure", procedure_id, patient_id, procedure_payload, encounter_id)

        for diagnosis_index, segment in enumerate((item for item in segments if item[0] == "HI"), start=1):
            for diagnosis_value in segment[1:]:
                diagnosis_code = self._composite_code(diagnosis_value)
                if not diagnosis_code:
                    continue
                condition_id = f"{claim_id}:condition:{diagnosis_index}:{diagnosis_code}"
                condition_payload = {
                    "resourceType": "Condition",
                    "id": self._stable_id("Condition", condition_id),
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "encounter": {"reference": f"Encounter/{encounter_id}"},
                    "code": {"coding": [{"code": diagnosis_code}]},
                }
                yield file_name, self._record(file_name, claim_index, "Condition", condition_id, patient_id, condition_payload, encounter_id)

    @staticmethod
    def _record(file_name: str, row_number: int, resource_type: str, source_id: str, patient_id: str, payload: dict[str, Any], encounter_id: str | None = None) -> SourceRecord:
        return SourceRecord(
            source_file=f"claims837:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{source_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            data={"resourceType": resource_type, "resource_json": payload},
        )

    @staticmethod
    def _patient_id(segments: list[list[str]]) -> str | None:
        for segment in segments:
            if segment[0] == "NM1" and len(segment) > 9 and segment[1] in {"IL", "QC"}:
                return segment[9]
        return None

    @staticmethod
    def _first_value(segments: list[list[str]], segment_id: str, qualifier: str) -> str | None:
        for segment in segments:
            if segment[0] == segment_id and qualifier in segment:
                index = segment.index(qualifier) + 1
                if segment_id == "DTP" and index < len(segment) and segment[index] in {"D8", "DT", "RD8"}:
                    index += 1
                return segment[index] if index < len(segment) else None
        return None

    @staticmethod
    def _composite_code(value: str) -> str | None:
        return value.split(":", 1)[1] if ":" in value else value or None

    @staticmethod
    def _date(value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"(\d{8})", value)
        if not match:
            return None
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"

    @staticmethod
    def _stable_id(resource_type: str, value: str) -> str:
        return hashlib.sha1(f"{resource_type}:{value}".encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _report(callback: Callable[[RowIssue], None] | None, file_name: str, row_number: int, code: str, message: str) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", code, message))
