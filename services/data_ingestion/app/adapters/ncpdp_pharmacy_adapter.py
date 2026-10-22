"""Streaming adapter for common pipe-delimited NCPDP pharmacy transactions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base import DatasetMetadata, ParsedDataset, RowIssue, SourceAdapter, SourceRecord


class NCPDPPharmacyAdapter(SourceAdapter):
    """Parse normalized NCPDP extracts into MedicationRequest/MedicationDispense records."""

    @property
    def adapter_name(self) -> str:
        return "ncpdp_pharmacy"

    @property
    def supported_formats(self) -> list[str]:
        return [".ncpdp", ".ncpdp.txt", ".pharmacy", ".rx"]

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
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for row_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        fields = self._parse_line(line)
                        if not fields:
                            continue
                        yield from self._records_from_transaction(fields, file_path.name, row_number, issue_callback)
            except OSError as exc:
                self._report(issue_callback, file_path.name, 0, "ncpdp_read_error", str(exc))

    def _records_from_transaction(
        self,
        fields: dict[str, str],
        file_name: str,
        row_number: int,
        issue_callback: Callable[[RowIssue], None] | None,
    ) -> Iterator[tuple[str, SourceRecord]]:
        prescription_id = fields.get("prescription_id") or fields.get("rx_number")
        patient_id = fields.get("patient_id") or fields.get("member_id")
        if not prescription_id:
            self._report(issue_callback, file_name, row_number, "missing_prescription_id", "Pharmacy transaction has no prescription ID")
            return
        if not patient_id:
            self._report(issue_callback, file_name, row_number, "missing_patient_id", "Pharmacy transaction has no member/patient ID")
            return

        medication_id = self._stable_id("MedicationRequest", prescription_id)
        request_payload = {
            "resourceType": "MedicationRequest",
            "id": medication_id,
            "status": self._request_status(fields.get("response_code")),
            "intent": "order",
            "subject": {"reference": f"Patient/{patient_id}"},
            "medicationCodeableConcept": {"coding": [{"system": "http://hl7.org/fhir/sid/ndc", "code": fields.get("ndc") or "unknown"}], "text": fields.get("drug_name") or "Pharmacy medication"},
        }
        authored = self._date(fields.get("service_date"))
        if authored:
            request_payload["authoredOn"] = authored
        yield file_name, self._record(file_name, row_number, "MedicationRequest", prescription_id, patient_id, request_payload)

        dispense_payload = {
            "resourceType": "MedicationDispense",
            "id": self._stable_id("MedicationDispense", f"{prescription_id}:{fields.get('fill_number', '0')}"),
            "status": "completed" if self._request_status(fields.get("response_code")) == "active" else "cancelled",
            "medicationCodeableConcept": request_payload["medicationCodeableConcept"],
            "subject": {"reference": f"Patient/{patient_id}"},
            "authorizingPrescription": [{"reference": f"MedicationRequest/{medication_id}"}],
        }
        quantity = self._number(fields.get("quantity"))
        if quantity is not None:
            dispense_payload["quantity"] = {"value": quantity}
        when = self._date(fields.get("service_date"))
        if when:
            dispense_payload["whenHandedOver"] = when
        yield file_name, self._record(file_name, row_number, "MedicationDispense", f"{prescription_id}:{fields.get('fill_number', '0')}", patient_id, dispense_payload)

    @staticmethod
    def _parse_line(line: str) -> dict[str, str]:
        values = [value.strip() for value in line.rstrip("\r\n").split("|")]
        if values and values[0].lower() in {"transaction_code", "patient_id", "member_id"}:
            return {}
        keys = [
            "transaction_code", "service_date", "rx_number", "ndc", "quantity",
            "fill_number", "response_code", "patient_id", "member_id", "drug_name",
        ]
        return {key: values[index] for index, key in enumerate(keys) if index < len(values)}

    @staticmethod
    def _record(file_name: str, row_number: int, resource_type: str, source_id: str, patient_id: str, payload: dict[str, Any]) -> SourceRecord:
        return SourceRecord(
            source_file=f"ncpdp:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{source_id}",
            patient_id=patient_id,
            encounter_id=None,
            data={"resourceType": resource_type, "resource_json": payload},
        )

    @staticmethod
    def _request_status(response_code: str | None) -> str:
        return "active" if (response_code or "P").upper() in {"P", "PA", "APPROVED"} else "cancelled"

    @staticmethod
    def _date(value: str | None) -> str | None:
        if not value:
            return None
        value = value.replace("-", "")
        if len(value) >= 8 and value[:8].isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return None

    @staticmethod
    def _number(value: str | None) -> float | None:
        try:
            return float(value) if value else None
        except ValueError:
            return None

    @staticmethod
    def _stable_id(resource_type: str, value: str) -> str:
        return hashlib.sha1(f"{resource_type}:{value}".encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _report(callback: Callable[[RowIssue], None] | None, file_name: str, row_number: int, code: str, message: str) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", code, message))
