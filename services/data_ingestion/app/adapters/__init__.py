"""Adapter registry for data ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class Adapter:
    """Base adapter class for data import formats."""
    
    def __init__(self, name: str, description: str, adapter_name: str):
        self.name = name
        self.adapter_name = adapter_name  # Used in pipeline
        self.description = description
    
    def discover(self, input_dir: str) -> Any:
        """Discover files and metadata in the input directory."""
        from ..synthea import DatasetMetadata, discover_dataset
        # Default behavior uses synthea discovery
        return discover_dataset(input_dir)
    
    def stream(self, input_dir: str, collect_issue = None):
        """Stream records from the input directory."""
        # This will be overridden by subclasses
        from ..synthea import DatasetMetadata
        metadata = DatasetMetadata(input_dir=input_dir)
        return metadata, {}
    
    def parse(self, input_dir: str):
        """Parse records from the input directory."""
        # This will be overridden by subclasses
        from ..synthea import DatasetMetadata, ParsedDataset
        metadata = DatasetMetadata(input_dir=input_dir)
        return ParsedDataset(metadata=metadata, records_by_file={}, issues=[])


class SyntheaCSVAdapter(Adapter):
    """Adapter for Synthea CSV format."""
    
    def __init__(self):
        super().__init__("Synthea CSV", "Synthetic healthcare data from Synthea", "synthea_csv")
    
    def discover(self, input_dir: str) -> Any:
        from ..synthea import discover_dataset
        return discover_dataset(input_dir)
    
    def stream(self, input_dir: str, collect_issue = None):
        """Stream CSV records - delegate to synthea module."""
        from ..synthea import parse_dataset
        parsed = parse_dataset(input_dir)
        # Collect any issues if a callback was provided
        if collect_issue:
            for issue in parsed.issues:
                collect_issue(issue)
        # Flatten records_by_file to a single list
        all_records = []
        for records in parsed.records_by_file.values():
            all_records.extend(records)
        return parsed.metadata, all_records


class FhirR4Adapter(Adapter):
    """Adapter for EHR FHIR R4 format."""
    
    def __init__(self):
        super().__init__("EHR FHIR R4", "EHR system FHIR R4 JSON export", "ehr_fhir_r4")
    
    def discover(self, input_dir: str) -> Any:
        """Discover FHIR JSON files."""
        from ..synthea import DatasetMetadata
        import json
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        
        # Look for JSON files (FHIR bundles)
        json_files = list(base.glob("*.json"))
        for json_file in json_files:
            metadata.found_files.append(json_file.name)
            # Try to count JSON entries (rough estimate)
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "entry" in data:
                        metadata.row_counts[json_file.name] = len(data["entry"])
                    else:
                        metadata.row_counts[json_file.name] = 1
            except:
                metadata.row_counts[json_file.name] = 0
        
        return metadata
    
    def stream(self, input_dir: str, collect_issue = None):
        """Stream FHIR bundles from JSON files."""
        from ..synthea import DatasetMetadata, SourceRecord
        import json
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        records_by_file = {}
        
        # Load all FHIR bundles from JSON files
        for json_file in base.glob("*.json"):
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)
                    
                    # Handle both single resource and bundle formats
                    entries = []
                    if isinstance(data, dict):
                        if "entry" in data:
                            # It's a Bundle
                            entries = data["entry"]
                        else:
                            # It's a single resource
                            entries = [{"resource": data}]
                    
                    # Convert to SourceRecord objects
                    records = []
                    for i, entry in enumerate(entries):
                        resource = entry.get("resource", {}) if isinstance(entry, dict) else entry
                        resource_id = resource.get("id", f"resource_{i}")
                        
                        # Store resource as JSON string in resource_json key
                        records.append(SourceRecord(
                            source_file=f"fhir:{json_file.name}",
                            row_number=i,
                            source_record_id=resource_id,
                            patient_id=None,
                            encounter_id=None,
                            data={"resource_json": json.dumps(resource)}
                        ))
                    
                    if records:
                        records_by_file[json_file.name] = records
                        metadata.found_files.append(json_file.name)
                        metadata.row_counts[json_file.name] = len(records)
            except Exception as e:
                if collect_issue:
                    from ..synthea import RowIssue
                    collect_issue(RowIssue(
                        source_file=json_file.name,
                        row_number=0,
                        severity="error",
                        code="JSON_PARSE_ERROR",
                        message=str(e),
                        source_record_id=None
                    ))
        
        # Flatten records_by_file to a flat list
        all_records = []
        for records in records_by_file.values():
            all_records.extend(records)
        
        return metadata, all_records


class CCDAHIEAdapter(Adapter):
    """Adapter for C-CDA HIE format."""
    
    def __init__(self):
        super().__init__("C-CDA HIE", "Healthcare Information Exchange C-CDA documents", "ccda_hie")
    
    def discover(self, input_dir: str) -> Any:
        """Discover C-CDA XML files."""
        from ..synthea import DatasetMetadata
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        
        # Look for XML files (C-CDA documents)
        xml_files = list(base.glob("*.xml"))
        for xml_file in xml_files:
            metadata.found_files.append(xml_file.name)
            metadata.row_counts[xml_file.name] = 1
        
        return metadata
    
    def stream(self, input_dir: str, collect_issue = None):
        """Stream C-CDA documents from XML files."""
        from ..synthea import DatasetMetadata, SourceRecord
        import json
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        records_by_file = {}
        
        # Load all C-CDA documents from XML files
        for xml_file in base.glob("*.xml"):
            try:
                with open(xml_file, "r") as f:
                    xml_content = f.read()
                    
                    records = [SourceRecord(
                        source_file=f"ccda:{xml_file.name}",
                        row_number=0,
                        source_record_id=xml_file.stem,
                        patient_id=None,
                        encounter_id=None,
                        data={"resource_xml": xml_content}
                    )]
                    
                    if records:
                        records_by_file[xml_file.name] = records
                        metadata.found_files.append(xml_file.name)
                        metadata.row_counts[xml_file.name] = 1
            except Exception as e:
                if collect_issue:
                    from ..synthea import RowIssue
                    collect_issue(RowIssue(
                        source_file=xml_file.name,
                        row_number=0,
                        severity="error",
                        code="XML_READ_ERROR",
                        message=str(e),
                        source_record_id=None
                    ))
        
        # Flatten records_by_file to a flat list
        all_records = []
        for records in records_by_file.values():
            all_records.extend(records)
        
        return metadata, all_records


class ClaimsX12Adapter(Adapter):
    """Adapter for X12 837 claims format."""
    
    def __init__(self):
        super().__init__("Claims X12 837", "Medical insurance claims in X12 837 format", "claims_837")
    
    def discover(self, input_dir: str) -> Any:
        """Discover X12 files."""
        from ..synthea import DatasetMetadata
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        
        # Look for X12 files
        x12_files = list(base.glob("*.x12")) + list(base.glob("*.837"))
        for x12_file in x12_files:
            metadata.found_files.append(x12_file.name)
            metadata.row_counts[x12_file.name] = 1
        
        return metadata


class NCDPPPharmacyAdapter(Adapter):
    """Adapter for NCPDP pharmacy format."""
    
    def __init__(self):
        super().__init__("NCPDP Pharmacy", "Pharmacy data in NCPDP format", "ncpdp_pharmacy")
    
    def discover(self, input_dir: str) -> Any:
        """Discover NCPDP files."""
        from ..synthea import DatasetMetadata
        
        base = Path(input_dir)
        metadata = DatasetMetadata(input_dir=str(base))
        
        # Look for NCPDP files
        ncpdp_files = list(base.glob("*.ncpdp"))
        for ncpdp_file in ncpdp_files:
            metadata.found_files.append(ncpdp_file.name)
            metadata.row_counts[ncpdp_file.name] = 1
        
        return metadata


class AdapterRegistry:
    """Registry for available data adapters."""
    
    def __init__(self):
        self._adapters = {
            "synthea_csv": SyntheaCSVAdapter(),
            "ehr_fhir_r4": FhirR4Adapter(),
            "ccda_hie": CCDAHIEAdapter(),
            "claims_837": ClaimsX12Adapter(),
            "ncpdp_pharmacy": NCDPPPharmacyAdapter(),
        }
    
    def list_adapters(self) -> dict:
        """Return list of available adapters."""
        return {key: {"name": adapter.name, "description": adapter.description} 
                for key, adapter in self._adapters.items()}
    
    def get_adapter(self, adapter_name: str) -> Adapter | None:
        """Get adapter by name."""
        return self._adapters.get(adapter_name)
    
    def has_adapter(self, adapter_name: str) -> bool:
        """Check if adapter exists."""
        return adapter_name in self._adapters


# Global registry instance
registry = AdapterRegistry()

