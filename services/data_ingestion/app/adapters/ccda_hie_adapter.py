"""C-CDA adapter for clinical documents received from HIE feeds."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..synthea import DatasetMetadata, ParsedDataset, RowIssue, SourceRecord
from . import Adapter

SECTION_RESOURCE_TYPES = {
    "48765-2": "AllergyIntolerance",
    "11348-0": "Condition",
    "11450-4": "Condition",
    "46240-8": "Encounter",
    "10160-0": "MedicationRequest",
    "30954-2": "Observation",
    "29549-3": "Procedure",
    "47519-4": "Procedure",
    "8716-3": "Observation",
    "11369-6": "Immunization",
}

CODE_SYSTEMS = {
    "2.16.840.1.113883.6.1": "http://loinc.org",
    "2.16.840.1.113883.6.88": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "2.16.840.1.113883.6.96": "http://snomed.info/sct",
    "2.16.840.1.113883.12.292": "http://hl7.org/fhir/sid/cvx",
}


class CCDAdapter(Adapter):
    """Normalize common C-CDA sections into FHIR-shaped source records."""

    def __init__(self) -> None:
        super().__init__("C-CDA HIE", "Healthcare Information Exchange C-CDA documents", "ccda_hie")

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
                root = ET.parse(file_path).getroot()
            except ET.ParseError:
                self._report(collect_issue, file_path.name, 0, "error", "invalid_ccda_xml", "C-CDA XML is invalid.")
                continue
            except OSError:
                self._report(collect_issue, file_path.name, 0, "error", "ccda_read_error", "C-CDA file could not be read.")
                continue
            yield from self._records_from_document(root, file_path.name, collect_issue)

    def _records_from_document(
        self,
        root: ET.Element,
        file_name: str,
        collect_issue: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        patient_id = self._patient_id(root)
        if not patient_id:
            self._report(collect_issue, file_name, 0, "error", "missing_patient_id", "C-CDA patient identifier is missing.")
            return

        document_id = self._identifier(root) or hashlib.sha1(file_name.encode("utf-8")).hexdigest()
        patient = {
            "resourceType": "Patient",
            "id": patient_id,
            "identifier": [{"value": patient_id}],
        }
        yield file_name, self._record(file_name, 1, "Patient", document_id, patient_id, patient)

        seen_entry_ids: set[str] = set()
        row_number = 2
        for section in self._elements_named(root, "section"):
            section_code = self._code_value(section.find("{*}code"))
            resource_type = SECTION_RESOURCE_TYPES.get(section_code or "")
            if resource_type is None:
                continue
            for entry_node, code_node in self._section_entries(section, resource_type):
                record_id = self._identifier(entry_node) or f"{document_id}-{row_number}"
                if record_id in seen_entry_ids:
                    self._report(
                        collect_issue,
                        file_name,
                        row_number,
                        "warning",
                        "duplicate_ccda_entry_id",
                        "A repeated C-CDA entry identifier was skipped.",
                    )
                    continue
                seen_entry_ids.add(record_id)
                payload = self._fhir_payload(resource_type, record_id, patient_id, code_node)
                yield file_name, self._record(file_name, row_number, resource_type, record_id, patient_id, payload)
                row_number += 1

    @staticmethod
    def _files(path: Path) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in {".xml", ".ccda"} else []
        return sorted((*path.glob("*.xml"), *path.glob("*.ccda")))

    @staticmethod
    def _elements_named(root: ET.Element, local_name: str) -> Iterator[ET.Element]:
        return (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == local_name)

    @staticmethod
    def _section_entries(section: ET.Element, resource_type: str) -> Iterator[tuple[ET.Element, ET.Element | None]]:
        entry_names = {
            "Encounter": "encounter",
            "MedicationRequest": "substanceAdministration",
            "Immunization": "substanceAdministration",
            "Procedure": "procedure",
        }
        entry_name = entry_names.get(resource_type, "observation")
        for entry in section.findall("./{*}entry"):
            for clinical_entry in entry.findall(f".//{{*}}{entry_name}"):
                if resource_type in {"MedicationRequest", "Immunization"}:
                    code = clinical_entry.find(".//{*}manufacturedMaterial/{*}code")
                elif resource_type == "Condition":
                    code = clinical_entry.find("./{*}value") or clinical_entry.find("./{*}code")
                else:
                    code = clinical_entry.find("./{*}code")
                yield clinical_entry, code

    @staticmethod
    def _record(
        file_name: str,
        row_number: int,
        resource_type: str,
        record_id: str,
        patient_id: str,
        payload: dict[str, Any],
    ) -> SourceRecord:
        return SourceRecord(
            source_file=f"ccda:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{record_id}",
            patient_id=patient_id,
            encounter_id=None,
            data={"resourceType": resource_type, "resource_json": json.dumps(payload)},
        )

    @staticmethod
    def _fhir_payload(
        resource_type: str,
        record_id: str,
        patient_id: str,
        code_node: ET.Element | None,
    ) -> dict[str, Any]:
        patient_reference = {"reference": f"Patient/{patient_id}"}
        concept = CCDAdapter._concept(code_node)
        common = {"resourceType": resource_type, "id": record_id}
        if resource_type == "AllergyIntolerance":
            return {**common, "patient": patient_reference, "code": concept}
        if resource_type == "Encounter":
            return {
                **common,
                "status": "finished",
                "class": [{"coding": [{"code": "AMB"}]}],
                "subject": patient_reference,
            }
        if resource_type == "MedicationRequest":
            return {
                **common,
                "status": "active",
                "intent": "order",
                "subject": patient_reference,
                "medication": {"concept": concept},
            }
        if resource_type == "Observation":
            return {**common, "status": "final", "subject": patient_reference, "code": concept}
        if resource_type == "Procedure":
            return {**common, "status": "completed", "subject": patient_reference, "code": concept}
        if resource_type == "Immunization":
            return {**common, "status": "completed", "patient": patient_reference, "vaccineCode": concept}
        return {**common, "subject": patient_reference, "code": concept}

    @staticmethod
    def _concept(code_node: ET.Element | None) -> dict[str, Any]:
        coding: dict[str, str] = {"code": CCDAdapter._code_value(code_node) or "unknown"}
        if code_node is not None:
            if system := CODE_SYSTEMS.get(code_node.get("codeSystem", "")):
                coding["system"] = system
            if display := code_node.get("displayName"):
                coding["display"] = display
        return {"coding": [coding]}

    @staticmethod
    def _identifier(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        identifier = node if node.tag.rsplit("}", 1)[-1] == "id" else node.find(".//{*}id")
        if identifier is None:
            return None
        return identifier.get("extension") or identifier.get("root")

    @staticmethod
    def _patient_id(root: ET.Element) -> str | None:
        return CCDAdapter._identifier(root.find(".//{*}patientRole"))

    @staticmethod
    def _code_value(node: ET.Element | None) -> str | None:
        return node.get("code") if node is not None else None

    @staticmethod
    def _report(
        callback: Callable[[RowIssue], None] | None,
        file_name: str,
        row_number: int,
        severity: str,
        code: str,
        message: str,
    ) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, severity, code, message))