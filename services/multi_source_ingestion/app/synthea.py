"""
Backward compatibility module for Synthea parsing.

DEPRECATED: Use adapters.SyntheaCSVAdapter directly.
This module re-exports functions from the adapter for backward compatibility.

Example:
    Old code: from synthea import parse_dataset
    New code: from adapters import registry; adapter = registry.get_adapter('synthea_csv'); parsed = adapter.parse(input_dir)
    
    This file: from synthea import parse_dataset  # Still works!
"""

from __future__ import annotations

# Re-export data classes and functions from adapters for backward compatibility
from .adapters.base import (
    DatasetMetadata,
    ParsedDataset,
    RowIssue,
    SourceRecord,
)
from .adapters.synthea_adapter import SyntheaCSVAdapter, SYNTHEA_SUPPORTED_FILES


# Re-export SUPPORTED_FILES for backward compatibility
SUPPORTED_FILES = SYNTHEA_SUPPORTED_FILES


# Create singleton adapter instance for backward compatibility
_adapter = SyntheaCSVAdapter()


def discover_dataset(input_dir: str) -> DatasetMetadata:
    """
    Backward compatibility function.
    DEPRECATED: Use adapter.discover() instead.
    """
    return _adapter.discover(input_dir)


def parse_dataset(input_dir: str) -> ParsedDataset:
    """
    Backward compatibility function.
    DEPRECATED: Use adapter.parse() instead.
    """
    return _adapter.parse(input_dir)


def parse_file(file_path, source_file: str, spec: dict) -> tuple[list[SourceRecord], list[RowIssue]]:
    """
    Backward compatibility function.
    DEPRECATED: Use adapter._parse_file() instead.
    """
    return SyntheaCSVAdapter._parse_file(file_path, source_file, spec)


def count_rows(file_path) -> int:
    """
    Backward compatibility function.
    DEPRECATED: Use adapter._count_rows() instead.
    """
    return SyntheaCSVAdapter._count_rows(file_path)


__all__ = [
    # Data classes
    "RowIssue",
    "SourceRecord",
    "DatasetMetadata",
    "ParsedDataset",
    # Functions (deprecated, use adapter instead)
    "discover_dataset",
    "parse_dataset",
    "parse_file",
    "count_rows",
    # Constants
    "SUPPORTED_FILES",
]
