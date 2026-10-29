# HEDIS POC - Database Schema Design

## Data Flow Overview

```
CSV Files (Synthea)
    ↓
[CSV Parser + Mapping]
    ↓
[FHIR Generation & Validation]
    ↓
[Extract & Denormalize]
    ↓
Database Tables
```

---

## Database Tables Structure

### LAYER 1: INGESTION TRACKING

#### `source_batch` (Tracks each ingestion run)
```
source_batch
├── batch_id (UUID, PRIMARY KEY)
├── source_system (VARCHAR) → "Synthea"
├── status (VARCHAR) → "running" | "completed" | "failed"
├── resource_count (INTEGER) → Total resources processed
├── started_at (TIMESTAMP)
└── completed_at (TIMESTAMP, nullable)
```

**Purpose**: Track which data came in from which batch run

---

### LAYER 2: FHIR RESOURCES (Standards-based storage)

#### `fhir_resource` (Current - Keep as is)
```
fhir_resource
├── id (INTEGER, PRIMARY KEY, auto-increment)
├── job_id (UUID, FOREIGN KEY → conversion_job)
├── resource_type (VARCHAR) → "Patient" | "Encounter" | "Condition" | etc
├── resource_id (VARCHAR) → FHIR resource unique ID
├── patient_id (VARCHAR) → Link to patient
├── source_file (VARCHAR) → Which CSV file it came from
├── source_record_id (VARCHAR) → Original record ID in CSV
├── resource_json (JSON) → Full FHIR R4 resource
├── validation_status (VARCHAR) → "validated" | "quarantined" | "rejected"
├── lifecycle_status (VARCHAR) → "generated" | "validated" | "persisted"
├── persisted (BOOLEAN)
└── batch_id (UUID, FOREIGN KEY → source_batch) [ADD THIS]
```

**Purpose**: Single source of truth - interoperable FHIR standard format

---

### LAYER 3: DENORMALIZED HEALTHCARE DATA (Optimized for reporting)

#### `patient` (Extracted from FHIR Patient resources)
```
patient
├── patient_id (VARCHAR, PRIMARY KEY) → matches resource_id
├── mrn (VARCHAR, UNIQUE) → Medical Record Number
├── gender (VARCHAR) → "male" | "female" | "other"
├── birth_date (DATE)
├── race (VARCHAR, nullable)
├── ethnicity (VARCHAR, nullable)
├── birth_sex (VARCHAR, nullable)
├── source_file (VARCHAR) → "patients.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional, for audit trail]
└── created_at (TIMESTAMP)
```

**Purpose**: Patient demographics - needed for HEDIS measure definitions

---

#### `encounter` (Extracted from FHIR Encounter resources)
```
encounter
├── encounter_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient) [IMPORTANT: Links to patient]
├── encounter_type (VARCHAR) → "office-visit" | "inpatient" | "ER"
├── encounter_date (DATETIME)
├── provider_id (VARCHAR, nullable) → Practitioner reference
├── location_id (VARCHAR, nullable) → Organization/Location reference
├── source_file (VARCHAR) → "encounters.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Visit history - core to almost ALL HEDIS measures

**HEDIS Use**: 
- Identify office visits, inpatient stays
- Calculate visit frequencies
- Determine measurement year encounters

---

#### `condition` (Extracted from FHIR Condition resources)
```
condition
├── condition_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient) [IMPORTANT: Links to patient]
├── code (VARCHAR) → ICD-10 or SNOMED code (e.g., "E11.9")
├── description (VARCHAR) → Human readable (e.g., "Type 2 diabetes")
├── status (VARCHAR) → "active" | "resolved" | "inactive"
├── onset_date (DATE, nullable) → When condition started
├── source_file (VARCHAR) → "conditions.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Diagnoses - used for gap identification and measure inclusion/exclusion

**HEDIS Use**:
- Diabetes: E11 (Type 2), E10 (Type 1)
- Hypertension: I10
- CAD: I25
- etc.

---

#### `observation` (Extracted from FHIR Observation resources)
```
observation
├── observation_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient) [IMPORTANT: Links to patient]
├── code (VARCHAR) → LOINC code (e.g., "8480-6" for systolic BP)
├── code_description (VARCHAR) → "Systolic Blood Pressure"
├── value (VARCHAR) → Numeric or text value
├── unit (VARCHAR, nullable) → "mmHg" | "mg/dL" | etc
├── observation_date (DATETIME)
├── reference_low (VARCHAR, nullable) → Normal range low
├── reference_high (VARCHAR, nullable) → Normal range high
├── source_file (VARCHAR) → "observations.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Lab results, vital signs - measurements for quality assessment

**HEDIS Use**:
- Blood pressure readings → Hypertension control
- HbA1c results → Diabetes control
- LDL cholesterol → CAD treatment effectiveness

---

#### `procedure` (Extracted from FHIR Procedure resources)
```
procedure
├── procedure_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient)
├── code (VARCHAR) → CPT or ICD-10-PCS code
├── description (VARCHAR) → "Colonoscopy" | "Blood glucose test"
├── procedure_date (DATETIME)
├── encounter_id (VARCHAR, FOREIGN KEY → encounter, nullable)
├── status (VARCHAR) → "completed" | "in-progress"
├── source_file (VARCHAR) → "procedures.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Medical procedures, screenings, tests

**HEDIS Use**:
- Colonoscopy screening rates
- Mammography screening
- Preventive care procedures

---

#### `medication` (Extracted from FHIR Medication/MedicationRequest resources)
```
medication
├── medication_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient)
├── medication_code (VARCHAR) → NDC or RxNorm code
├── medication_name (VARCHAR) → "Metformin" | "Lisinopril"
├── start_date (DATE)
├── end_date (DATE, nullable) → If medication stopped
├── source_file (VARCHAR) → "medications.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Medication history - compliance with treatment guidelines

**HEDIS Use**:
- ACE inhibitor use in diabetes/hypertension
- Statin use in CAD
- Medication adherence rates

---

#### `immunization` (Extracted from FHIR Immunization resources)
```
immunization
├── immunization_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient)
├── vaccine_code (VARCHAR) → CVX code
├── vaccine_name (VARCHAR) → "Influenza" | "COVID-19"
├── administration_date (DATE)
├── status (VARCHAR) → "completed" | "not-done"
├── source_file (VARCHAR) → "immunizations.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Vaccination records

**HEDIS Use**:
- Annual flu shot rates
- COVID-19 vaccination status
- Immunization compliance

---

#### `coverage` (Extracted from FHIR Coverage resources)
```
coverage
├── coverage_id (VARCHAR, PRIMARY KEY)
├── patient_id (VARCHAR, FOREIGN KEY → patient)
├── coverage_type (VARCHAR) → "medical" | "pharmacy"
├── payer (VARCHAR, nullable) → Insurance company name
├── start_date (DATE)
├── end_date (DATE, nullable)
├── source_file (VARCHAR) → "coverages.csv"
├── batch_id (UUID, FOREIGN KEY → source_batch)
├── fhir_resource_pk (INTEGER, FOREIGN KEY → fhir_resource) [optional]
└── created_at (TIMESTAMP)
```

**Purpose**: Insurance coverage periods

**HEDIS Use**:
- Enrollment determination
- Continuous enrollment verification
- Benefit period calculations

---

### LAYER 4: EXISTING CONVERSION TRACKING (Keep as is)

#### `conversion_job` (Existing)
```
conversion_job
├── id (UUID, PRIMARY KEY)
├── status (VARCHAR) → "running" | "completed" | "failed"
├── started_at (TIMESTAMP)
├── completed_at (TIMESTAMP, nullable)
├── resources_generated (INTEGER)
├── resources_validated (INTEGER)
├── resources_persisted (INTEGER)
└── summary (JSON)
```

**Purpose**: Track conversion pipeline status

---

#### `validation_issue` (Existing)
```
validation_issue
├── id (INTEGER, PRIMARY KEY, auto-increment)
├── job_id (UUID, FOREIGN KEY → conversion_job)
├── resource_pk (INTEGER, FOREIGN KEY → fhir_resource)
├── severity (VARCHAR) → "error" | "warning"
├── error_code (VARCHAR)
└── diagnostic (TEXT)
```

**Purpose**: Track validation errors

---

## Relationships Diagram

```
source_batch (master)
    ├── fhir_resource (1:many) ← batch_id
    │   ├── patient (1:many) ← patient_id
    │   ├── encounter (1:many) ← patient_id + fhir_resource_pk
    │   ├── condition (1:many) ← patient_id + fhir_resource_pk
    │   ├── observation (1:many) ← patient_id + fhir_resource_pk
    │   ├── procedure (1:many) ← patient_id + fhir_resource_pk
    │   ├── medication (1:many) ← patient_id + fhir_resource_pk
    │   ├── immunization (1:many) ← patient_id + fhir_resource_pk
    │   └── coverage (1:many) ← patient_id + fhir_resource_pk
    └── patient (1:many) ← batch_id
        ├── encounter (1:many)
        ├── condition (1:many)
        ├── observation (1:many)
        ├── procedure (1:many)
        ├── medication (1:many)
        ├── immunization (1:many)
        └── coverage (1:many)

conversion_job (existing, parallel tracking)
    ├── fhir_resource (1:many)
    └── validation_issue (1:many)
```

---

## Data Flow: CSV → Database

```
Step 1: Parse CSV
  patients.csv
  encounters.csv
  conditions.csv
  observations.csv
  procedures.csv
  medications.csv
  immunizations.csv
  coverages.csv
       ↓
Step 2: Generate FHIR Resources
  Create Patient, Encounter, Condition, Observation, etc. FHIR objects
       ↓
Step 3: Store in fhir_resource table
  INSERT INTO fhir_resource (resource_type, resource_id, resource_json, ...)
       ↓
Step 4: Validate
  Check FHIR compliance, business rules
       ↓
Step 5: Extract & Denormalize [NEW]
  For each validated FHIR resource:
    IF resource_type == "Patient":
      INSERT INTO patient (patient_id, mrn, gender, birth_date, ...)
    ELIF resource_type == "Encounter":
      INSERT INTO encounter (encounter_id, patient_id, encounter_date, ...)
    ELIF resource_type == "Condition":
      INSERT INTO condition (condition_id, patient_id, code, ...)
    ... etc
       ↓
Step 6: Mark as Persisted
  UPDATE fhir_resource SET lifecycle_status = "persisted"
       ↓
✓ Data Ready for HEDIS Measure Calculation
```

---

## SQL Indexes (Performance)

```sql
-- Core lookups
CREATE INDEX idx_patient_mrn ON patient(mrn);
CREATE INDEX idx_encounter_patient_date ON encounter(patient_id, encounter_date);
CREATE INDEX idx_condition_patient_code ON condition(patient_id, code);
CREATE INDEX idx_observation_patient_code ON observation(patient_id, code, observation_date);
CREATE INDEX idx_procedure_patient_date ON procedure(patient_id, procedure_date);
CREATE INDEX idx_medication_patient_date ON medication(patient_id, start_date);
CREATE INDEX idx_immunization_patient_date ON immunization(patient_id, administration_date);
CREATE INDEX idx_coverage_patient_date ON coverage(patient_id, start_date, end_date);

-- Batch tracking
CREATE INDEX idx_source_batch_status ON source_batch(status);
CREATE INDEX idx_fhir_resource_batch ON fhir_resource(batch_id);

-- Audit trail
CREATE INDEX idx_fhir_resource_pk_lookup ON patient(fhir_resource_pk);
```

---

## Implementation Priority

### Phase 1: Core Tables (Week 1)
- [x] `fhir_resource` (existing)
- [ ] `source_batch` (ADD)
- [ ] `patient` (ADD)
- [ ] `encounter` (ADD)
- [ ] `condition` (ADD)

### Phase 2: Supporting Tables (Week 2)
- [ ] `observation` (ADD)
- [ ] `procedure` (ADD)
- [ ] `medication` (ADD)
- [ ] `immunization` (ADD)

### Phase 3: Compliance Tables (Week 3)
- [ ] `coverage` (ADD)
- [ ] Indexes and query optimization

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Keep FHIR resources** | Standards compliance, interoperability, audit trail |
| **Add denormalized tables** | Fast HEDIS queries, no repeated JSON parsing |
| **Link via patient_id** | HEDIS measures are patient-centric |
| **Batch tracking** | Know which data came from which ingestion run |
| **Optional fhir_resource_pk** | Can trace any denormalized row back to source FHIR |
| **Source_file tracking** | Debug: know which CSV row each record came from |

---

## Next Steps

1. Add these models to `models.py`
2. Create extraction functions in `repository.py`
3. Call extraction after validation in `pipeline.py`
4. Run migrations to create tables
5. Test with Synthea dataset
6. Build HEDIS measure queries

Ready to implement? 🚀
