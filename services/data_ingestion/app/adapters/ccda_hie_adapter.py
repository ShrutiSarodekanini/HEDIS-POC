"""Adapter for C-CDA clinical documents exchanged through EHR/HIE feeds."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base import DatasetMetadata, ParsedDataset, RowIssue, SourceAdapter, SourceRecord


FHIR_NS = "http://hl7.org/fhir"

SECTION_CODES = {
    "48765-2": "AllergyIntolerance",
    "11348-0": "Condition",
    "46240-8": "Encounter",
    "10160-0": "MedicationRequest",
    "30954-2": "Observation",
    "29549-3": "Procedure",
    "29762-2": "Basic",
}


class CCDAdapter(SourceAdapter):
    """Stream C-CDA XML files and normalize clinical entries into FHIR-shaped records."""

    @property
    def adapter_name(self) -> str:
        return "ccda_hie"

    @property
    def supported_formats(self) -> list[str]:
        return [".xml", ".ccda"]

    def discover(self, input_source: str) -> DatasetMetadata:
        path = Path(input_source)
        metadata = DatasetMetadata(input_dir=str(path))
        files = [path] if path.is_file() else sorted(path.glob("*.xml")) + sorted(path.glob("*.ccda"))
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
            # C-CDA documents are parsed one document at a time; counts update as records are yielded.
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
        files = [path] if path.is_file() else sorted(path.glob("*.xml")) + sorted(path.glob("*.ccda"))
        for file_path in files:
            try:
                root = ET.parse(file_path).getroot()
                yield from self._records_from_document(root, file_path.name, issue_callback)
            except ET.ParseError as exc:
                self._report(issue_callback, file_path.name, 0, "invalid_ccda_xml", str(exc))
            except OSError as exc:
                self._report(issue_callback, file_path.name, 0, "ccda_read_error", str(exc))

    def _records_from_document(
        self,
        root: ET.Element,
        file_name: str,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        patient_id = self._patient_id(root)
        if not patient_id:
            self._report(issue_callback, file_name, 0, "missing_patient_id", "C-CDA patient identifier is missing")
            return

        document_id = self._identifier(root) or hashlib.sha1(file_name.encode("utf-8")).hexdigest()
        yield file_name, self._record(
            file_name,
            1,
            "Patient",
            document_id,
            patient_id,
            {"resourceType": "Patient", "id": patient_id, "identifier": [{"value": patient_id}]},
        )

        row_number = 2
        for section in root.iter():
            if section.tag.rsplit("}", 1)[-1] != "section":
                continue
            template_code = self._code_value(section.find("{*}templateId")) or self._code_value(section.find(".//{*}templateId"))
            section_code = self._code_value(section.find("{*}code")) or self._code_value(section.find(".//{*}code"))
            resource_type = SECTION_CODES.get(section_code or "")
            if resource_type is None:
                continue
            for entry in section.findall(".//{*}entry"):
                observation = entry.find(".//{*}observation")
                if observation is None:
                    observation = entry.find(".//{*}act")
                if observation is None:
                    observation = entry.find(".//{*}procedure")
                if observation is None:
                    continue
                code_node = observation.find(".//{*}code")
                code = self._code_value(code_node)
                display = code_node.get("display") if code_node is not None else None
                record_id = self._identifier(observation) or f"{document_id}-{row_number}"
                payload = self._fhir_payload(resource_type, record_id, patient_id, code, display)
                yield file_name, self._record(file_name, row_number, resource_type, record_id, patient_id, payload)
                row_number += 1

    @staticmethod
    def _record(
        file_name: str,
        row_number: int,
        resource_type: str,
        record_id: str,
        patient_id: str,
        data: dict[str, Any],
    ) -> SourceRecord:
        return SourceRecord(
            source_file=f"ccda:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{record_id}",
            patient_id=patient_id,
            encounter_id=None,
            data={"resourceType": resource_type, "resource_json": data},
        )

    @staticmethod
    def _fhir_payload(
        resource_type: str,
        record_id: str,
        patient_id: str,
        code: str | None,
        display: str | None,
    ) -> dict[str, Any]:
        reference = {"reference": f"Patient/{patient_id}"}
        concept = {"coding": [{"code": code or "unknown", "display": display or "C-CDA entry"}], "text": display or "C-CDA entry"}
        if resource_type == "AllergyIntolerance":
            return {"resourceType": resource_type, "id": record_id, "patient": reference, "clinicalStatus": {"coding": [{"code": "active"}]}, "verificationStatus": {"coding": [{"code": "confirmed"}]}, "code": concept}
        if resource_type == "Condition":
            return {"resourceType": resource_type, "id": record_id, "subject": reference, "clinicalStatus": {"coding": [{"code": "active"}]}, "code": concept}
        if resource_type == "Encounter":
            return {"resourceType": resource_type, "id": record_id, "status": "finished", "class": {"code": "AMB"}, "subject": reference}
        if resource_type == "MedicationRequest":
            return {"resourceType": resource_type, "id": record_id, "status": "active", "intent": "order", "subject": reference, "medicationCodeableConcept": concept}
        if resource_type == "Observation":
            return {"resourceType": resource_type, "id": record_id, "status": "final", "subject": reference, "code": concept}
        if resource_type == "Procedure":
            return {"resourceType": resource_type, "id": record_id, "status": "completed", "subject": reference, "code": concept}
        return {"resourceType": "Basic", "id": record_id, "subject": reference, "code": concept}

    @staticmethod
    def _identifier(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        identifier = node if node.tag.rsplit("}", 1)[-1] == "id" else node.find(".//{*}id")
        return identifier.get("extension") or identifier.get("root") if identifier is not None else None

    @staticmethod
    def _patient_id(root: ET.Element) -> str | None:
        patient_role = root.find(".//{*}patientRole")
        return CCDAdapter._identifier(patient_role)

    @staticmethod
    def _code_value(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        return node.get("code")

    @staticmethod
    def _report(callback: Callable[[RowIssue], None] | None, file_name: str, row_number: int, code: str, message: str) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", code, message))
