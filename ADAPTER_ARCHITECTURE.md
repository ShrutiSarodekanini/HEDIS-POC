# Data Source Adapter Architecture Analysis

## Current State: Synthea CSV Only

### What's Already Implemented ✅

#### 1. **CSV Parsing Layer** (`synthea.py`)
- **Purpose**: Read Synthea CSV files and normalize into records
- **Classes**:
  - `SourceRecord`: Normalized single row (source_file, source_record_id, patient_id, encounter_id, data dict)
  - `ParsedDataset`: Collection of records by file (metadata + records_by_file dict)
  - `DatasetMetadata`: Tracks found files, missing files, row counts
  - `RowIssue`: Validation errors during parsing

- **Functions**:
  - `discover_dataset(input_dir)`: Scans for Synthea CSV files
  - `parse_dataset(input_dir)`: Parse all files → `ParsedDataset`
  - `parse_file(file_path, source_file, spec)`: Parse single CSV → `list[SourceRecord]`

- **Supported Files**: 10 Synthea CSVs (patients, encounters, conditions, observations, procedures, medications, immunizations, careplans, claims, coverages)

```python
@dataclass
class SourceRecord:
    source_file: str           # e.g., "patients.csv"
    row_number: int            # CSV line number (1-indexed)
    source_record_id: str      # Unique ID from CSV (e.g., patient UUID)
    patient_id: str | None     # Foreign key to patient
    encounter_id: str | None   # Foreign key to encounter
    data: dict[str, str]       # Raw CSV column data
```

---

#### 2. **Mapping Layer** (`mapping.py`)
- **Purpose**: Transform normalized records → FHIR R4 resources
- **Classes**:
  - `ResourceEnvelope`: Single FHIR resource (resource_type, resource_id, patient_id, fhir dict, references)
  - `MappingResult`: Collection of resources + issues

- **Functions**:
  - `map_dataset(parsed)`: Main orchestrator - iterates records_by_file, calls per-type mappers
  - `map_patient()`, `map_encounter()`, `map_condition()`, etc.: Type-specific mapping logic
  - `codeable_concept()`: Build FHIR CodeableConcept objects
  - `deterministic_id()`: Generate reproducible UUIDs using source data

- **Current Mappers**: 10 functions (one per Synthea file type)

```python
@dataclass
class ResourceEnvelope:
    resource_type: str           # e.g., "Patient", "Encounter"
    resource_id: str             # FHIR logical ID
    patient_id: str | None       # Link to patient
    source_file: str             # Where it came from
    source_record_id: str        # Source system ID
    fhir: dict[str, Any]         # Validated FHIR JSON
    references: list[str]        # Links to other resources
```

---

#### 3. **Validation Layer** (`validator.py`)
- **Purpose**: Validate FHIR resources against pydantic models
- **Functions**:
  - `validate_resources(resources, job_id)`: Structural validation
  - `validate_with_external_jar_batch()`: Optional external HL7 validator

- **Output**: Marks resources as "validated", "partial", "failed"

---

#### 4. **Pipeline Orchestration** (`pipeline.py`)
- **Purpose**: Coordinate the 5 stages
- **Stages**:
  1. `csv_input` → Call `synthea.parse_dataset()`
  2. `mapping` → Call `mapping.map_dataset()`
  3. `fhir_generation` → Build resource envelopes
  4. `validation` → Call `validator.validate_resources()`
  5. `postgresql_storage` → Call `repository.mark_validated_resources_persisted()`

- **Process**:
  ```
  run_conversion()
  ├─ Stage 1: parse_dataset() → ParsedDataset
  ├─ Stage 2: map_dataset(ParsedDataset) → list[ResourceEnvelope]
  ├─ Stage 3: Create bundles
  ├─ Stage 4: validate_resources(resources) → marked as validated
  └─ Stage 5: upsert_resource() → Save to DB
  ```

---

### Data Flow Diagram (Current)

```
┌─────────────────────┐
│  Synthea CSV Files  │
│  (10 files in dir)  │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────┐
│  synthea.parse_dataset() │  ← Only source adapter
│                          │
│  discover_dataset()      │
│  parse_file()            │
│                          │
│  Returns: ParsedDataset  │
│  {                       │
│    metadata: {...}       │
│    records_by_file: {    │
│      "patients.csv": [   │
│        SourceRecord(...) │
│      ],                  │
│      ...                 │
│    }                     │
│  }                       │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ mapping.map_dataset()    │
│                          │
│ For each SourceRecord:   │
│   → map_patient()        │
│   → map_encounter()      │
│   → map_condition()      │
│   ...                    │
│                          │
│ Returns: MappingResult   │
│ {                        │
│   resources: [           │
│     ResourceEnvelope     │
│   ],                     │
│   issues: [...]          │
│ }                        │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ validator.validate()     │
│                          │
│ Structural + optional    │
│ external validation      │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ DB: FhirResource table   │
│ status = "validated"     │
└──────────────────────────┘
```

---

## What's Missing: Pluggable Adapter Pattern

### Gap Analysis

| Component | Current | Needed |
|-----------|---------|--------|
| **Input Interfaces** | Only Synthea CSV | Claims (837), Pharmacy (NCPDP), Lab (LOINC), EHR/HIE (C-CDA), APIs (CMS-0057-F) |
| **Adapter Pattern** | Hard-coded `synthea.parse_dataset()` | Abstract interface all adapters implement |
| **Normalization Contract** | `SourceRecord` class | Formal adapter interface/protocol |
| **Adapter Discovery** | None | Registry pattern - auto-discover adapters |
| **Pipeline Entry Point** | Calls `synthea.parse_dataset()` directly | Call adapter registry based on input type |
| **Error Handling** | Per-adapter | Unified error model across adapters |
| **Adapter Composition** | Single source only | Multiple adapters in sequence (e.g., Claims + Pharmacy in one job) |

---

## Implementation Roadmap

### Phase 1: Define Common Adapter Interface

Create `adapters/base.py`:

```python
from abc import ABC, abstractmethod
from typing import Protocol
from dataclasses import dataclass

# ✅ Already have this in synthea.py
@dataclass
class SourceRecord:
    source_file: str
    row_number: int
    source_record_id: str
    patient_id: str | None
    encounter_id: str | None
    data: dict[str, str]

# ✅ Already have this in synthea.py
@dataclass
class ParsedDataset:
    metadata: DatasetMetadata
    records_by_file: dict[str, list[SourceRecord]]
    issues: list[RowIssue]

# ❌ NEW: Abstract adapter interface
class SourceAdapter(ABC):
    """Base class for all data source adapters."""
    
    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """e.g., 'synthea_csv', 'claims_837', 'pharmacy_ncpdp'"""
        pass
    
    @property
    @abstractmethod
    def supported_formats(self) -> list[str]:
        """e.g., ['.csv'], ['*.837', '*.txt'], ['http://']"""
        pass
    
    @abstractmethod
    def discover(self, input_source: str) -> DatasetMetadata:
        """
        Scan input_source and return what was found.
        input_source = file path, directory, URL, or connection string
        Returns: DatasetMetadata with found files, counts, etc.
        """
        pass
    
    @abstractmethod
    def parse(self, input_source: str) -> ParsedDataset:
        """
        Parse input_source into normalized records.
        Returns: ParsedDataset with records_by_file dict
        """
        pass
    
    def validate_input(self, input_source: str) -> tuple[bool, str]:
        """Optional: Pre-flight validation before parse."""
        return True, "OK"
```

---

### Phase 2: Refactor Synthea as Adapter

Rename/restructure: `synthea.py` → `adapters/synthea_adapter.py`

```python
from adapters.base import SourceAdapter, ParsedDataset, SourceRecord

class SyntheaCSVAdapter(SourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "synthea_csv"
    
    @property
    def supported_formats(self) -> list[str]:
        return [".csv"]
    
    def discover(self, input_dir: str) -> DatasetMetadata:
        """Existing discover_dataset() logic"""
        # ... existing code ...
    
    def parse(self, input_dir: str) -> ParsedDataset:
        """Existing parse_dataset() logic"""
        # ... existing code ...
```

**No changes to existing logic** - just wrapped in adapter class.

---

### Phase 3: Create Adapter Registry

New file: `adapters/registry.py`

```python
from typing import Type
from .base import SourceAdapter
from .synthea_adapter import SyntheaCSVAdapter

class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, Type[SourceAdapter]] = {}
    
    def register(self, adapter_class: Type[SourceAdapter]) -> None:
        instance = adapter_class()
        self._adapters[instance.adapter_name] = adapter_class
    
    def get_adapter(self, adapter_name: str) -> SourceAdapter | None:
        adapter_class = self._adapters.get(adapter_name)
        return adapter_class() if adapter_class else None
    
    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())

# Global registry
registry = AdapterRegistry()
registry.register(SyntheaCSVAdapter)
# Later: registry.register(ClaimsAdapter)
# Later: registry.register(PharmacyAdapter)
```

---

### Phase 4: Update Pipeline to Use Adapter Registry

Modify `pipeline.py`:

**Before** (hard-coded):
```python
def run_conversion(job_id: str, input_dir: str) -> None:
    stage = job_manager.set_stage
    
    stage(job_id, "csv_input", "running")
    parsed = parse_dataset(input_dir)  # ← Hard-coded to Synthea
    stage(job_id, "csv_input", "completed")
```

**After** (adapter-driven):
```python
from adapters.registry import registry

def run_conversion(job_id: str, input_source: str, adapter_name: str = "synthea_csv") -> None:
    stage = job_manager.set_stage
    
    # Get adapter from registry
    adapter = registry.get_adapter(adapter_name)
    if not adapter:
        stage(job_id, "csv_input", "failed", f"Adapter '{adapter_name}' not found")
        return
    
    # Use adapter's parse() method (same interface for all)
    stage(job_id, "csv_input", "running")
    parsed = adapter.parse(input_source)
    stage(job_id, "csv_input", "completed")
    
    # Rest of pipeline is unchanged!
    # (mapping.map_dataset() still takes ParsedDataset)
    stage(job_id, "mapping", "running")
    mapping_result = map_dataset(parsed)
    stage(job_id, "mapping", "completed")
    
    # ... validation, storage, etc. ...
```

---

### Phase 5: Add New Adapters (Claims, Pharmacy, etc.)

Example: `adapters/claims_adapter.py`

```python
from .base import SourceAdapter, SourceRecord, ParsedDataset, DatasetMetadata

class ClaimsAdapter(SourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "claims_837"
    
    @property
    def supported_formats(self) -> list[str]:
        return [".837", ".txt"]  # EDI format
    
    def discover(self, input_dir: str) -> DatasetMetadata:
        # Scan for *.837 files
        # Count records per file
        # Return metadata
        pass
    
    def parse(self, input_dir: str) -> ParsedDataset:
        # Parse EDI 837 format
        # Normalize into SourceRecord objects
        # Return ParsedDataset with records_by_file = {
        #   "claims_837.txt": [
        #     SourceRecord(
        #       source_file="claims_837.txt",
        #       data={"SERVICE_DATE": "2026-01-15", "CPT_CODE": "45378", ...}
        #     ),
        #     ...
        #   ]
        # }
        pass

# Register with registry
registry.register(ClaimsAdapter)
```

Then `mapping.py` adds a `map_claim_837()` function to handle CPT codes, service dates, etc.

**Pipeline stays the same!** - just pass different adapter_name.

---

## Summary: What's There vs. What's Needed

### ✅ Already Implemented
1. **SourceRecord** class - normalized record format
2. **ParsedDataset** class - collection of records
3. **Synthea CSV adapter** - file discovery + parsing logic
4. **Mapping layer** - transforms records → FHIR
5. **Validation layer** - validates FHIR
6. **Pipeline orchestration** - 5-stage flow

### ❌ Still Needed
1. **Abstract adapter interface** (`SourceAdapter` base class)
2. **Adapter registry** - registry to discover adapters
3. **Refactor Synthea** - wrap in adapter class
4. **Pipeline adapter integration** - call registry instead of hard-coded `parse_dataset()`
5. **New adapters** - Claims, Pharmacy, Lab, EHR/HIE, APIs

### 📊 Architecture Benefit
- **Extensible**: Add Claims adapter → Just create new class, implement 3 methods, register
- **Testable**: Each adapter tested independently
- **Reusable**: Mapping + validation layers work with ANY adapter output (ParsedDataset)
- **Flexible**: Compose multiple adapters in one pipeline (e.g., Synthea + Claims + Pharmacy)

---

## Next Steps

1. **Create `services/data_ingestion/app/adapters/` directory**
2. **Create base adapter interface** (`base.py`)
3. **Refactor Synthea as adapter** (`synthea_adapter.py`)
4. **Create adapter registry** (`registry.py`)
5. **Update pipeline** to use registry
6. **Verify backward compatibility** - existing jobs still work
7. **Add first new adapter** (Claims, Pharmacy, or Lab)

**Effort**: ~4 hours total
**Risk**: Low (no breaking changes to existing logic, just restructure)
**ROI**: High (enables 5+ new data sources)

---
