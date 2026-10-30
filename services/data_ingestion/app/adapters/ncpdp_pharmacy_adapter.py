"""Adapter for normalized pipe-delimited NCPDP pharmacy exports."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..synthea import DatasetMetadata, ParsedDataset, RowIssue, SourceRecord
from . import Adapter


class NCPDPPharmacyAdapter(Adapter):
    """Normalize pharmacy transactions into request and dispense resources."""

    supported_formats = (".ncpdp", ".ncpdp.txt", ".pharmacy", ".rx")
    positional_fields = (
        "transaction_code",
        "service_date",
        "rx_number",
        "ndc",
        "quantity",
        "fill_number",
        "response_code",
        "patient_id",
        "member_id",
        "drug_name",
    )

    def __init__(self) -> None:
        super().__init__("NCPDP Pharmacy", "Pharmacy data in normalized NCPDP format", "ncpdp_pharmacy")

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
        seen_patient_ids: set[str] = set()
        for file_path in self._files(Path(input_source)):
            try:
                with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.reader(handle, delimiter="|"))
            except (OSError, csv.Error):
                self._report(collect_issue, file_path.name, 0, "ncpdp_read_error", "Pharmacy file could not be read.")
                continue
            if not rows:
                continue

            has_header = self._has_header(rows[0])
            headers = [value.strip().lower() for value in rows[0]] if has_header else list(self.positional_fields)
            start_row = 2 if has_header else 1
            for row_number, values in enumerate(rows[1:] if has_header else rows, start=start_row):
                if not values or not any(value.strip() for value in values):
                    continue
                fields = {header: values[index].strip() for index, header in enumerate(headers) if index < len(values)}
                yield from self._records_from_transaction(
                    fields,
                    file_path.name,
                    row_number,
                    collect_issue,
                    seen_patient_ids,
                )

    def _records_from_transaction(
        self,
        fields: dict[str, str],
        file_name: str,
        row_number: int,
        collect_issue: Callable[[RowIssue], None] | None,
        seen_patient_ids: set[str],
    ) -> Iterator[tuple[str, SourceRecord]]:
        prescription_id = self._first(fields, "prescription_id", "rx_number")
        source_patient_id = self._first(fields, "patient_id", "member_id")
        if not prescription_id:
            self._report(
                collect_issue,
                file_name,
                row_number,
                "missing_prescription_id",
                "Pharmacy transaction has no prescription identifier.",
            )
            return
        if not source_patient_id:
            self._report(
                collect_issue,
                file_name,
                row_number,
                "missing_patient_id",
                "Pharmacy transaction has no member or patient identifier.",
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
                row_number,
                "Patient",
                source_patient_id,
                patient_id,
                patient_payload,
            )
            seen_patient_ids.add(patient_id)

        medication_id = self._stable_id("MedicationRequest", prescription_id)
        approved = self._is_approved(fields)
        medication = {
            "coding": [{
                "system": "http://hl7.org/fhir/sid/ndc",
                "code": self._first(fields, "medication_code", "ndc") or "unknown",
            }],
            "text": self._first(fields, "medication_name", "drug_name") or "Pharmacy medication",
        }
        request_payload = {
            "resourceType": "MedicationRequest",
            "id": medication_id,
            "status": "active" if approved else "cancelled",
            "intent": "order",
            "subject": {"reference": f"Patient/{patient_id}"},
            "medication": {"concept": medication},
        }
        service_date = self._date(self._first(fields, "date_dispensed", "date_filled", "service_date"))
        if service_date:
            request_payload["authoredOn"] = service_date
        yield file_name, self._record(
            file_name,
            row_number,
            "MedicationRequest",
            prescription_id,
            patient_id,
            request_payload,
        )

        fill_number = fields.get("fill_number") or "0"
        dispense_source_id = f"{prescription_id}:{fill_number}"
        dispense_payload = {
            "resourceType": "MedicationDispense",
            "id": self._stable_id("MedicationDispense", dispense_source_id),
            "status": "completed" if approved else "cancelled",
            "medication": {"concept": medication},
            "subject": {"reference": f"Patient/{patient_id}"},
            "authorizingPrescription": [{"reference": f"MedicationRequest/{medication_id}"}],
        }
        quantity = self._number(self._first(fields, "medication_quantity", "quantity"))
        if quantity is not None:
            dispense_payload["quantity"] = {"value": quantity}
        if service_date:
            dispense_payload["whenHandedOver"] = service_date
        yield file_name, self._record(
            file_name,
            row_number,
            "MedicationDispense",
            dispense_source_id,
            patient_id,
            dispense_payload,
        )

    @classmethod
    def _files(cls, path: Path) -> list[Path]:
        if path.is_file():
            return [path] if any(path.name.lower().endswith(extension) for extension in cls.supported_formats) else []
        files = (file for extension in cls.supported_formats for file in path.glob(f"*{extension}"))
        return sorted(set(files))

    @classmethod
    def _has_header(cls, values: list[str]) -> bool:
        normalized = {value.strip().lower() for value in values}
        return bool(normalized.intersection({"prescription_id", "rx_number", "transaction_code", "patient_id"}))

    @staticmethod
    def _first(fields: dict[str, str], *keys: str) -> str | None:
        return next((fields[key] for key in keys if fields.get(key)), None)

    @staticmethod
    def _record(
        file_name: str,
        row_number: int,
        resource_type: str,
        source_id: str,
        patient_id: str,
        payload: dict[str, Any],
    ) -> SourceRecord:
        return SourceRecord(
            source_file=f"ncpdp:{file_name}",
            row_number=row_number,
            source_record_id=f"{resource_type}/{source_id}",
            patient_id=patient_id,
            encounter_id=None,
            data={"resourceType": resource_type, "resource_json": json.dumps(payload)},
        )

    @staticmethod
    def _is_approved(fields: dict[str, str]) -> bool:
        response_code = fields.get("response_code", "").upper()
        status = fields.get("status", "").lower()
        return response_code in {"", "0", "00", "P", "PA", "APPROVED"} and status not in {
            "cancelled",
            "denied",
            "rejected",
        }

    @staticmethod
    def _date(value: str | None) -> str | None:
        normalized = (value or "").replace("-", "")
        if len(normalized) >= 8 and normalized[:8].isdigit():
            return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"
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
    def _report(
        callback: Callable[[RowIssue], None] | None,
        file_name: str,
        row_number: int,
        code: str,
        message: str,
    ) -> None:
        if callback:
            callback(RowIssue(file_name, row_number, "error", code, message))