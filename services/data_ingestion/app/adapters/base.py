"""
Base adapter interface and common data classes for source data ingestion.

All adapters must implement SourceAdapter interface to produce normalized
SourceRecord objects that can be mapped to FHIR resources.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import Callable, Iterator
from typing import Any


@dataclass
class RowIssue:
    """Validation error or warning during parsing."""
    source_file: str
    row_number: int
    severity: str  # "error" | "warning" | "info"
    code: str  # Machine-readable error code
    message: str  # Human-readable message
    source_record_id: str | None = None


@dataclass
class SourceRecord:
    """Normalized record from any source adapter."""
    source_file: str  # e.g., "patients.csv", "claims.837", "pharmacy.ncpdp"
    row_number: int  # Line number in source (1-indexed)
    source_record_id: str  # Unique ID from source system
    patient_id: str | None  # Link to patient (FK)
    encounter_id: str | None  # Link to encounter (FK)
    data: dict[str, str]  # Raw normalized fields from source

    @property
    def fingerprint(self) -> str:
        """SHA1 hash of source location for deduplication."""
        raw = f"{self.source_file}:{self.source_record_id}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class DatasetMetadata:
    """Metadata about discovered and parsed dataset."""
    input_dir: str  # Input path/URL/connection string
    found_files: list[str] = field(default_factory=list)  # Files found
    missing_required_files: list[str] = field(default_factory=list)  # Required files not found
    row_counts: dict[str, int] = field(default_factory=dict)  # Rows per file
    malformed_rows: dict[str, int] = field(default_factory=dict)  # Malformed rows per file


@dataclass
class ParsedDataset:
    """Complete result of adapter parsing - normalized records ready for mapping."""
    metadata: DatasetMetadata
    records_by_file: dict[str, list[SourceRecord]]  # SourceRecords grouped by source file
    issues: list[RowIssue]  # Parsing errors/warnings

    @property
    def total_rows(self) -> int:
        """Total rows across all files."""
        return sum(self.metadata.row_counts.values())

    @property
    def total_records(self) -> int:
        """Total successfully parsed records."""
        return sum(len(records) for records in self.records_by_file.values())


class SourceAdapter(ABC):
    """
    Abstract base class for all data source adapters.
    
    Every adapter must:
    1. Implement discover() to scan input source
    2. Implement parse() to normalize records
    3. Return ParsedDataset with SourceRecord objects
    
    The mapping.py and validator.py layers consume ParsedDataset,
    so any adapter can work with the entire pipeline.
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """
        Unique identifier for this adapter.
        Examples: 'synthea_csv', 'claims_837', 'pharmacy_ncpdp'
        """
        pass

    @property
    @abstractmethod
    def supported_formats(self) -> list[str]:
        """
        File/format extensions this adapter handles.
        Examples: ['.csv'], ['.837', '.txt'], ['*.ncpdp']
        """
        pass

    @abstractmethod
    def discover(self, input_source: str) -> DatasetMetadata:
        """
        Scan input source and discover what's available.
        
        Args:
            input_source: File path, directory, URL, or connection string
        
        Returns:
            DatasetMetadata with found files, counts, missing files
        
        Raises:
            ValueError: If input_source format is invalid for this adapter
            IOError: If input_source cannot be accessed
        """
        pass

    @abstractmethod
    def parse(self, input_source: str) -> ParsedDataset:
        """
        Parse input source into normalized records.
        
        Args:
            input_source: File path, directory, URL, or connection string
        
        Returns:
            ParsedDataset with records_by_file dict and any parsing issues
        
        Raises:
            ValueError: If input_source format is invalid for this adapter
            IOError: If input_source cannot be accessed
        """
        pass

    def validate_input(self, input_source: str) -> tuple[bool, str]:
        """
        Optional: Pre-flight validation before parse.
        
        Args:
            input_source: File path, directory, URL, or connection string
        
        Returns:
            (is_valid, error_message) tuple
        """
        return True, "OK"

    def stream(
        self,
        input_source: str,
        issue_callback: Callable[[RowIssue], None] | None = None,
    ) -> tuple[DatasetMetadata, Iterator[SourceRecord]]:
        """Stream normalized records while retaining the legacy parse API."""
        parsed = self.parse(input_source)

        def records() -> Iterator[SourceRecord]:
            for issue in parsed.issues:
                if issue_callback:
                    issue_callback(issue)
            for file_records in parsed.records_by_file.values():
                yield from file_records

        return parsed.metadata, records()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} '{self.adapter_name}'>"
