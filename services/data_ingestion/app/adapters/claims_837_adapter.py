"""Streaming adapter for common X12 837 professional claim loops."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..synthea import DatasetMetadata, ParsedDataset, RowIssue, SourceRecord
from . import Adapter


class Claims837Adapter(Adapter):
    """Normalize common 837P claim loops into FHIR-shaped records."""

    supported_formats = (".837", ".x12", ".edi", ".txt")

    def __init__(self) -> None:
        super().__init__("Claims X12 837", "Medical insurance claims in X12 837 format", "claims_837")

    def discover(self, input_source: str) -> DatasetMetadata:
        path = Path(input_source)
        metadata = DatasetMetadata(input_dir=str(path))
        metadata.found_files = [file.name for file in self._files(path)]
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
        collect_issue: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterator[SourceRecord]]:
        metadata = self.discover(input_source)

        def records() -> Iterator[SourceRecord]:
            counts: dict[str, int] = {}
            for file_name, record in self._iter_records(input_source, collect_issue):
                counts[file_name] = counts.get(file_name, 0) + 1
                metadata.row_counts[file_name] = counts[file_name]
                yield record

        return metadata, records()

    def _iter_records(
        self,
        input_source: str,
        collect_issue: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        for file_path in self._files(Path(input_source)):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._report(collect_issue, file_path.name, 0, "claims_837_read_error", "Claims file could not be read.")
                continue
            yield from self._parse_transaction(text, file_path.name, collect_issue)

    def _parse_transaction(
        self,
        text: str,
        file_name: str,
        collect_issue: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        element_separator = text[3] if text.startswith("ISA") and len(text) > 3 else "*"
        first_line = text.splitlines()[0].strip() if text else ""
        segment_terminator = first_line[-1] if first_line.startswith("ISA") else "~"
        segments = [
            segment.strip().split(element_separator)
            for segment in text.replace("\r", "").replace("\n", "").split(segment_terminator)
            if segment.strip()
        ]
        preamble: list[list[str]] = []
        current_claim: list[list[str]] | None = None
        claims: list[list[list[str]]] = []
        for segment in segments:
            if segment[0] == "CLM":
                if current_claim:
                    claims.append(current_claim)
                current_claim = [*preamble, segment]
            elif current_claim is None:
                preamble.append(segment)
            else:
                current_claim.append(segment)
        if current_claim:
            claims.append(current_claim)
        if not claims:
            self._report(
                collect_issue,
                file_name,
                0,
                "invalid_claim",
                "837 transaction contains no claim segments.",
            )
            return

        seen_patient_ids: set[str] = set()
        for claim_index, claim_segments in enumerate(claims, start=1):
            yield from self._claim_records(
                claim_segments,
                file_name,
                claim_index,
                collect_issue,
                seen_patient_ids,
            )

    def _claim_records(
        self,
        segments: list[list[str]],
        file_name: str,
        claim_index: int,
        collect_issue: Callable[[RowIssue], None] | None,
        seen_patient_ids: set[str],
    ) -> Iterator[tuple[str, SourceRecord]]:
        claim = next((segment for segment in segments if segment[0] == "CLM"), None)
        if not claim or len(claim) < 2:
            self._report(collect_issue, file_name, claim_index, "invalid_claim", "837 claim is missing its claim ID.")
            return
        claim_id = claim[1]
        source_patient_id = self._patient_id(segments)
        if not source_patient_id:
            self._report(
                collect_issue,
                file_name,
                claim_index,
                "missing_patient_id",
                "837 claim has no subscriber or member identifier.",
            )
            return

        patient_id = self._stable_id("Patient", source_patient_id)
        if patient_id not in seen_patient_ids:
            patient_payload = {
                "resourceType": "Patient",
                "id": patient_id,
                "identifier": [{"value": source_patient_id}],
            }
            yield file_name, self._record(
                file_name,
                claim_index,
                "Patient",
                source_patient_id,
                patient_id,
                patient_payload,
            )
            seen_patient_ids.add(patient_id)

        encounter_source_id = f"{claim_id}:encounter"
        encounter_id = self._stable_id("Encounter", encounter_source_id)
        claim_payload = {
            "resourceType": "Claim",
            "id": self._stable_id("Claim", claim_id),
            "status": "active",
            "use": "claim",
            "created": self._date(self._first_value(segments, "DTP", "472")) or "1970-01-01",
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
            "class": [{"coding": [{"code": "AMB"}]}],
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        yield file_name, self._record(
            file_name,
            claim_index,
            "Encounter",
            encounter_source_id,
            patient_id,
            encounter_payload,
            encounter_id,
        )

        for line_index, segment in enumerate((item for item in segments if item[0] == "SV1"), start=1):
            procedure_code = self._composite_code(segment[1] if len(segment) > 1 else "")
            procedure_source_id = f"{claim_id}:procedure:{line_index}"
            procedure_payload = {
                "resourceType": "Procedure",
                "id": self._stable_id("Procedure", procedure_source_id),
                "status": "completed",
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "code": {
                    "coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": procedure_code or "unknown"}]
                },
            }
            yield file_name, self._record(
                file_name,
                claim_index,
                "Procedure",
                procedure_source_id,
                patient_id,
                procedure_payload,
                encounter_id,
            )

        for diagnosis_index, segment in enumerate((item for item in segments if item[0] == "HI"), start=1):
            for diagnosis_value in segment[1:]:
                diagnosis_code = self._composite_code(diagnosis_value)
                if not diagnosis_code:
                    continue
                condition_source_id = f"{claim_id}:condition:{diagnosis_index}:{diagnosis_code}"
                condition_payload = {
                    "resourceType": "Condition",
                    "id": self._stable_id("Condition", condition_source_id),
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "encounter": {"reference": f"Encounter/{encounter_id}"},
                    "code": {"coding": [{"code": diagnosis_code}]},
                }
                yield file_name, self._record(
                    file_name,
                    claim_index,
                    "Condition",
                    condition_source_id,
                    patient_id,
                    condition_payload,
                    encounter_id,
                )

    @classmethod
    def _files(cls, path: Path) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in cls.supported_formats else []
        files = (file for extension in cls.supported_formats for file in path.glob(f"*{extension}"))
        return sorted(set(files))

    @staticmethod
    def _record(
        file_name: str,
        row_number: int,
        resource_type: str,
        source_id: str,
        patient_id: str,
        payload: dict[str, Any],
        encounter_id: str | None = None,
    ) -> SourceRecord:
        return SourceRecord(
            source_file=f"claims837:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{source_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            data={"resourceType": resource_type, "resource_json": json.dumps(payload)},
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
            if segment[0] != segment_id or qualifier not in segment:
                continue
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
        match = re.search(r"(\d{8})", value or "")
        if not match:
            return None
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"

    @staticmethod
    def _stable_id(resource_type: str, value: str) -> str:
        return hashlib.sha1(f"{resource_type}:{value}".encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _report(
        callback: Callable[[RowIssue], None] | None,
        file_name: str,
        row_number: int,
        code: str,
        message: str,
    ) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", code, message))