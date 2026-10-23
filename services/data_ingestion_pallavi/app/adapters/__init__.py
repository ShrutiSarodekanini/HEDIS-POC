"""
Data source adapters for HEDIS POC.

Provides pluggable adapters for different data sources:
- Synthea CSV
- Claims (837 EDI format)
- Pharmacy (NCPDP)
- Lab (LOINC)
- EHR/HIE (C-CDA, FHIR)
- CMS APIs (0057-F Provider Access)
"""

from .base import SourceAdapter, SourceRecord, ParsedDataset, DatasetMetadata, RowIssue
from .registry import registry
from .synthea_adapter import SyntheaCSVAdapter

__all__ = [
    "SourceAdapter",
    "SourceRecord",
    "ParsedDataset",
    "DatasetMetadata",
    "RowIssue",
    "registry",
    "SyntheaCSVAdapter",
]
