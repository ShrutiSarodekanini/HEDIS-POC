"""Adapter registry for data ingestion."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from ..synthea import DatasetMetadata, ParsedDataset, RowIssue, SourceRecord, discover_dataset, parse_dataset


class Adapter:
    """Base adapter class for data import formats."""

    def __init__(self, name: str, description: str, adapter_name: str) -> None:
        self.name = name
        self.adapter_name = adapter_name
        self.description = description

    def discover(self, input_dir: str) -> DatasetMetadata:
        """Discover files and metadata in the input directory."""
        return discover_dataset(input_dir)

    def stream(
        self,
        input_dir: str,
        collect_issue: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterable[SourceRecord]]:
        """Stream records from the input directory."""
        metadata = DatasetMetadata(input_dir=input_dir)
        return metadata, []

    def parse(self, input_dir: str) -> ParsedDataset:
        """Parse records from the input directory."""
        issues: list[RowIssue] = []
        metadata, records = self.stream(input_dir, issues.append)
        records_by_file: dict[str, list[SourceRecord]] = {}
        for record in records:
            source_file = record.source_file.split(":", 1)[-1]
            records_by_file.setdefault(source_file, []).append(record)
        for source_file, source_records in records_by_file.items():
            metadata.row_counts[source_file] = len(source_records)
        return ParsedDataset(metadata=metadata, records_by_file=records_by_file, issues=issues)


class SyntheaCSVAdapter(Adapter):
    """Adapter for Synthea CSV format."""

    def __init__(self) -> None:
        super().__init__("Synthea CSV", "Synthetic healthcare data from Synthea", "synthea_csv")

    def discover(self, input_dir: str) -> DatasetMetadata:
        return discover_dataset(input_dir)

    def stream(
        self,
        input_dir: str,
        collect_issue: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterable[SourceRecord]]:
        """Stream CSV records - delegate to synthea module."""
        parsed = parse_dataset(input_dir)
        if collect_issue:
            for issue in parsed.issues:
                collect_issue(issue)
        all_records: list[SourceRecord] = []
        for records in parsed.records_by_file.values():
            all_records.extend(records)
        return parsed.metadata, all_records


class FhirR4Adapter(Adapter):
    """Adapter for EHR FHIR R4 format."""

    def __init__(self) -> None:
        super().__init__("EHR FHIR R4", "EHR system FHIR R4 JSON export", "ehr_fhir_r4")

    def discover(self, input_dir: str) -> DatasetMetadata:
        """Discover FHIR JSON files."""
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))

        json_files = sorted((*base.glob("*.json"), *base.glob("*.fhir")))
        for json_file in json_files:
            metadata.found_files.append(json_file.name)
            try:
                with json_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "entry" in data:
                        metadata.row_counts[json_file.name] = len(data["entry"])
                    else:
                        metadata.row_counts[json_file.name] = 1
            except (OSError, json.JSONDecodeError, TypeError):
                metadata.row_counts[json_file.name] = 0

        return metadata

    def stream(
        self,
        input_dir: str,
        collect_issue: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterable[SourceRecord]]:
        """Stream FHIR bundles from JSON files."""
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        records_by_file: dict[str, list[SourceRecord]] = {}

        for json_file in sorted((*base.glob("*.json"), *base.glob("*.fhir"))):
            try:
                with json_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                    entries: list[object] = []
                    if isinstance(data, dict):
                        if "entry" in data:
                            entries = data["entry"]
                        else:
                            entries = [{"resource": data}]

                    records: list[SourceRecord] = []
                    for i, entry in enumerate(entries):
                        resource = entry.get("resource", {}) if isinstance(entry, dict) else entry
                        if not isinstance(resource, dict):
                            raise TypeError("FHIR resource must be a JSON object")
                        resource_id = resource.get("id", f"resource_{i}")
                        records.append(SourceRecord(
                            source_file=f"fhir:{json_file.name}",
                            row_number=i,
                            source_record_id=str(resource_id),
                            patient_id=None,
                            encounter_id=None,
                            data={"resource_json": json.dumps(resource)},
                        ))

                    if records:
                        records_by_file[json_file.name] = records
                        metadata.found_files.append(json_file.name)
                        metadata.row_counts[json_file.name] = len(records)
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                if collect_issue:
                    collect_issue(RowIssue(
                        source_file=json_file.name,
                        row_number=0,
                        severity="error",
                        code="JSON_PARSE_ERROR",
                        message="FHIR JSON could not be parsed.",
                        source_record_id=None,
                    ))

        all_records: list[SourceRecord] = []
        for records in records_by_file.values():
            all_records.extend(records)

        return metadata, all_records


class AdapterRegistry:
    """Registry for available data adapters."""

    def __init__(self) -> None:
        from .ccda_hie_adapter import CCDAdapter
        from .claims_837_adapter import Claims837Adapter
        from .ncpdp_pharmacy_adapter import NCPDPPharmacyAdapter

        self._adapters: dict[str, Adapter] = {
            "synthea_csv": SyntheaCSVAdapter(),
            "ehr_fhir_r4": FhirR4Adapter(),
            "ccda_hie": CCDAdapter(),
            "claims_837": Claims837Adapter(),
            "ncpdp_pharmacy": NCPDPPharmacyAdapter(),
        }
    
    def list_adapters(self) -> dict[str, dict[str, str]]:
        """Return list of available adapters."""
        return {
            key: {"name": adapter.name, "description": adapter.description}
            for key, adapter in self._adapters.items()
        }

    def get_adapter(self, adapter_name: str) -> Adapter | None:
        """Get adapter by name."""
        return self._adapters.get(adapter_name)
    
    def has_adapter(self, adapter_name: str) -> bool:
        """Check if adapter exists."""
        return adapter_name in self._adapters


# Global registry instance
registry = AdapterRegistry()

