# HEDIS POC - Complete User Interaction Flow

## **STEP-BY-STEP USER JOURNEY**

---

## **PHASE 1: DATA UPLOAD** 📤

### Step 1: User Opens Web Interface
```
User Action: Open browser
URL: http://localhost:8000/ui/
Browser loads: index.html → app.js → styles.css
```

**UI Appears With:**
- 📁 Input Dir field (text box)
- 📂 Pick Folder button (file browser)
- 🔍 Inspect button
- ▶️ Start button
- 📊 Display area for results

---

### Step 2: User Uploads Dataset
**Option A: Pick Local Folder**
```
User Click: "Pick Folder" button
  → Browser file picker opens
  → User selects folder with CSVs
  → Folder selected (e.g., ~/Synthea/output/)
  → app.js extracts CSVs matching SUPPORTED_CSV
  → Form created with CSV files
  → XMLHttpRequest uploads to /api/datasets/upload
  
Response:
{
  "input_dir": "./.uploaded_datasets/uuid-xxx/",
  "saved_files": ["patients.csv", "encounters.csv", ...],
  "found_files": [10 files],
  "missing_required_files": [],
  "row_counts": {
    "patients.csv": 1000,
    "encounters.csv": 5000,
    ...
  },
  "total_rows": 630000
}
```

**Option B: Enter Local Path Directly**
```
User Action: Type path in "Input Dir" field
  e.g.: /data/synthea/output/
  
User Click: "Inspect" button
  → Frontend calls GET /api/datasets/inspect?input_dir=/data/synthea/output/
  → Backend discovers files in directory
  → Shows metadata
```

---

## **PHASE 2: DATA INSPECTION** 🔍

### Step 3: User Inspects Dataset
```
User Click: "Inspect" button
  → Sends: GET /api/datasets/inspect?input_dir=<path>
  → Backend:
    ├─ Calls adapter.discover(input_dir)
    ├─ Scans for supported files
    ├─ Counts rows in each file
    └─ Returns metadata
    
Response:
{
  "input_dir": "/path/to/dataset/",
  "found_files": ["patients.csv", "encounters.csv", ...],
  "missing_required_files": [],
  "row_counts": {
    "patients.csv": 1001,
    "encounters.csv": 5342,
    "conditions.csv": 8901,
    ...
  },
  "total_rows": 631234
}
```

**Frontend Displays:**
```
✓ Found 10 files
✓ No missing required files
  - patients.csv: 1,001 rows
  - encounters.csv: 5,342 rows
  - conditions.csv: 8,901 rows
  ...
  TOTAL: 631,234 rows
```

---

## **PHASE 3: DATA CONVERSION & VALIDATION** ⚙️

### Step 4: User Starts Conversion Job
```
User Click: "Start" button
  → Sends: POST /api/jobs/convert?input_dir=<path>
  → Backend:
    ├─ Generates job_id (UUID)
    ├─ Saves to DB: ConversionJob table
    └─ Calls launch_job(job_id, input_dir)
       └─ Thread starts → run_conversion()

Response:
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Frontend Updates:**
```
Job ID: 550e8400-e29b-41d4-a716-446655440000
Status: RUNNING
```

---

### Step 5: Pipeline Executes (5 Stages)

**STAGE 1: CSV Input** 📥
```
Backend Action:
  adapter = registry.get_adapter("synthea_csv")
  parsed = adapter.parse(input_dir)
  
Process:
  ├─ Read patients.csv → Parse rows
  ├─ Read encounters.csv → Parse rows
  ├─ Read conditions.csv → Parse rows
  ├─ ... (all 10 files)
  └─ Create SourceRecord objects
  
Output: ParsedDataset {
  metadata: {...},
  records_by_file: {
    "patients.csv": [SourceRecord, SourceRecord, ...],
    "encounters.csv": [SourceRecord, ...],
    ...
  },
  issues: [...]  # Parse errors if any
}

Time: ~5-30 seconds (depending on size)
```

**Frontend Shows:**
```
✓ CSV Input: COMPLETED (5.234 seconds)
  Discovered 10 supported files
```

---

**STAGE 2: Mapping** 🗺️
```
Backend Action:
  mapping_result = map_dataset(parsed)
  
Process per Record:
  SourceRecord {
    source_file: "patients.csv",
    source_record_id: "patient-123",
    data: {"FIRST": "John", "BIRTHDATE": "1990-01-15", ...}
  }
  
  ↓ Call map_patient() ↓
  
  ResourceEnvelope {
    resource_type: "Patient",
    fhir: {
      "resourceType": "Patient",
      "id": "uuid-xxx",
      "name": [{"text": "John"}],
      "birthDate": "1990-01-15",
      ...
    }
  }

Mapping Functions:
  - map_patient() → Patient resource
  - map_encounter() → Encounter resource
  - map_condition() → Condition resource
  - map_observation() → Observation resource
  - map_procedure() → Procedure resource
  - map_medication_request() → MedicationRequest resource
  - map_immunization() → Immunization resource
  - map_careplan() → CarePlan resource
  - map_claim() → Claim resource
  - map_coverage() → Coverage resource

Output: MappingResult {
  resources: [ResourceEnvelope, ResourceEnvelope, ...],  # 630k resources
  issues: [...]  # Mapping errors if any
}

Time: ~30-90 seconds
```

**Frontend Shows:**
```
✓ Mapping: COMPLETED (45.123 seconds)
  Mapped 630,234 resources
  
Progress Indicators:
  - Patient: 1,001/1,001 (100%)
  - Encounter: 5,342/5,342 (100%)
  - Condition: 8,901/8,901 (100%)
  ...
```

---

**STAGE 3: FHIR Generation** 📦
```
Backend Action:
  bundles = bundles_by_patient(resources)
  
Process:
  Group 630k resources by patient
  Create FHIR Transaction Bundle per patient
  
Output: {
  "patient-1": {
    "resourceType": "Bundle",
    "type": "transaction",
    "entry": [
      {
        "resource": {Patient FHIR},
        "request": {"method": "PUT", "url": "Patient/..."}
      },
      {
        "resource": {Encounter FHIR},
        ...
      },
      ...
    ]
  },
  "patient-2": {...},
  ...
}

Time: ~5-10 seconds
```

**Frontend Shows:**
```
✓ FHIR Generation: COMPLETED (8.234 seconds)
  Generated 1,001 patient bundles
```

---

**STAGE 4: Validation** ✅
```
Backend Action (Two Options):

OPTION A: No External Validator (Default)
  For each ResourceEnvelope:
    ├─ Try: model_instance = model.model_validate(fhir_dict)
    ├─ If Success: lifecycle_status = "validated"
    └─ If Fail: lifecycle_status = "quarantined" or "rejected"
  
  Time: ~20-60 seconds

OPTION B: With External HL7 Validator
  1. Write all FHIR resources to temp XML
  2. Call external JAR: validator.jar -file resources.xml
  3. Parse validation results
  4. Mark resources with validation status
  
  Time: ~2-5 minutes

Output: ValidationResult[] {
  resource_id: "...",
  status: "validated" | "partial" | "failed",
  messages: [
    {"severity": "error", "message": "..."},
    {"severity": "warning", "message": "..."}
  ]
}

Time: ~20-60 seconds (internal) or ~2-5 min (external)
```

**Frontend Shows (Real-time Progress):**
```
⏳ Validation: RUNNING (23 seconds elapsed)
  Patient: 750/1,001 (75%)
  Encounter: 3,200/5,342 (60%)
  Condition: 6,500/8,901 (73%)
  ...

✓ Validation: COMPLETED (58.234 seconds)
  ✓ Validated: 615,234 resources
  ⚠️  Warnings: 12,543 resources
  ✗ Rejected: 2,457 resources
```

---

**STAGE 5: PostgreSQL Storage (PENDING)** 💾
```
Backend Action:
  Resources NOT auto-persisted to database
  Status set to: "pending" / "partially_completed"
  
Message: "Run persistence stage explicitly"
  (Requires manual "Store validated" button)

Time: 0 seconds (skipped for now)
```

**Frontend Shows:**
```
⏹ PostgreSQL Storage: PENDING
  Click "Store validated" button to persist resources
```

---

## **PHASE 4: PERSISTENCE** 💾

### Step 6: User Reviews Results
```
Frontend Displays Summary:
╔════════════════════════════════════════╗
║ CONVERSION JOB SUMMARY                 ║
├────────────────────────────────────────┤
║ Job ID: 550e8400-e29b-41d4-a716-...   ║
║ Status: PARTIALLY_COMPLETED            ║
║ Duration: 2 minutes 15 seconds         ║
├────────────────────────────────────────┤
║ Source Records: 631,234                ║
║ Resources Generated: 630,234           ║
║ Resources Validated: 615,234           ║
║ Resources Persisted: 0 (not yet)       ║
│                                        │
║ Warnings: 12,543                       ║
║ Errors: 2,457                          ║
├────────────────────────────────────────┤
║ Stages:                                ║
║   ✓ CSV Input (5.234s)                 ║
║   ✓ Mapping (45.123s)                  ║
║   ✓ FHIR Generation (8.234s)           ║
║   ✓ Validation (58.234s)               ║
║   ⏹ PostgreSQL Storage (PENDING)       ║
╚════════════════════════════════════════╝
```

---

### Step 7: User Persists Resources (Optional)
```
User Click: "Store validated" button
  → Sends: POST /api/jobs/{jobId}/persist
  → Backend:
    ├─ Query FhirResource table for this job
    ├─ Filter: status = "validated" OR status = "partial"
    ├─ Batch UPDATE query:
    │   UPDATE FhirResource
    │   SET lifecycle_status = 'persisted'
    │   WHERE job_id = '{jobId}' AND lifecycle_status IN ('validated', 'partial')
    ├─ Mark extraction complete
    └─ Return count

Response:
{
  "job_id": "550e8400-...",
  "persisted": 615,234,
  "status": "completed"
}
```

**Frontend Shows:**
```
✓ Store Validated: COMPLETED
  Persisted 615,234 resources to database
  
Updated Summary:
  Resources Persisted: 615,234
  Status: COMPLETED
```

---

## **PHASE 5: DATA EXPLORATION** 🔎

### Step 8: User Views Jobs List
```
User Click: "Jobs" button
  → Sends: GET /api/jobs
  → Backend: Query ConversionJob table, order by created DESC
  
Response:
{
  "jobs": [
    {
      "id": "550e8400-...",
      "input_dir": "./.uploaded_datasets/uuid-xxx/",
      "status": "completed",
      "created_at": "2026-10-16T14:23:45Z",
      "resources_generated": 630234,
      "resources_validated": 615234,
      "resources_persisted": 615234
    },
    {
      "id": "440e8400-...",
      ...
    }
  ]
}
```

**Frontend Shows Table:**
```
Job ID                          Status      Created      Generated  Validated  Persisted
550e8400-e29b-41d4-a716-...    COMPLETED   2026-10-16   630,234    615,234    615,234
440e8400-e29b-41d4-a716-...    COMPLETED   2026-10-15   500,000    498,000    498,000
...
```

---

### Step 9: User Views Validation Issues
```
User Click: "Issues" button
  → Sends: GET /api/jobs/{jobId}/issues
  → Backend: Query ValidationIssue table
  
Response:
{
  "issues": [
    {
      "source_file": "conditions.csv",
      "row_number": 234,
      "severity": "error",
      "code": "missing_patient_id",
      "message": "Missing patient foreign key in source row.",
      "source_record_id": "condition-5678"
    },
    {
      "source_file": "observations.csv",
      "row_number": 891,
      "severity": "warning",
      "code": "invalid_value",
      "message": "Observation value does not match expected format.",
      "source_record_id": "obs-1234"
    },
    ...
  ]
}
```

**Frontend Shows Table:**
```
File           Row    Severity  Code                 Message
conditions.csv 234    ERROR     missing_patient_id   Missing patient foreign key...
observations.csv 891   WARNING   invalid_value        Observation value does not...
procedures.csv 1045   ERROR     missing_code         Procedure row requires CODE...
...
```

---

### Step 10: User Inspects Patient Resources
```
User Action: Select patient from "Load Patient" dropdown
  e.g.: Patient "a3f5c2b1-..."
  
User Click: "Load Patient" button
  → Sends: GET /api/jobs/{jobId}/resources?patient_id=a3f5c2b1-...
  → Backend: Query FhirResource table filtered by patient_id
  
Response:
{
  "patient_resources": [
    {
      "resource_type": "Patient",
      "resource_id": "a3f5c2b1-...",
      "validation_status": "validated",
      "lifecycle_status": "persisted",
      "resource_json": {
        "resourceType": "Patient",
        "id": "a3f5c2b1-...",
        "name": [{"text": "John Doe"}],
        "birthDate": "1990-01-15",
        ...
      }
    },
    {
      "resource_type": "Encounter",
      "resource_id": "e7d9f4c2-...",
      "validation_status": "validated",
      "lifecycle_status": "persisted",
      "resource_json": {...}
    },
    {
      "resource_type": "Condition",
      "resource_id": "c3a1b5d7-...",
      "validation_status": "partial",
      "lifecycle_status": "persisted",
      "resource_json": {...}
    },
    ...
  ]
}
```

**Frontend Shows (JSON Tree View):**
```
📋 Patient Resources for: a3f5c2b1-...

📄 Patient (a3f5c2b1-...)
  - Name: John Doe
  - DOB: 1990-01-15
  - Gender: Male
  - Status: Persisted ✓

📅 Encounter (e7d9f4c2-...)
  - Type: Outpatient
  - Date: 2024-01-15
  - Status: Persisted ✓

💊 Condition (c3a1b5d7-...)
  - Code: E11 (Type 2 Diabetes)
  - Status: Persisted ✓
  - Warnings: ⚠️ (1)

... (more resources)
```

---

## **DATABASE STATE AT END** 🗄️

### ConversionJob Table
```
id                  input_dir                          status        created_at           resources_generated resources_validated resources_persisted
550e8400-...        ./.uploaded_datasets/uuid-xxx/    completed     2026-10-16 14:23:45  630234               615234               615234
```

### FhirResource Table
```
id  job_id           resource_type    resource_id       patient_id    lifecycle_status  validation_status  resource_json
1   550e8400-...     Patient          a3f5c2b1-...      a3f5c2b1-...  persisted         validated         {...}
2   550e8400-...     Encounter        e7d9f4c2-...      a3f5c2b1-...  persisted         validated         {...}
3   550e8400-...     Condition        c3a1b5d7-...      a3f5c2b1-...  persisted         partial           {...}
...
```

### ValidationIssue Table
```
id  job_id           source_file      row_number  severity  code                 message
1   550e8400-...     conditions.csv   234         error     missing_patient_id   Missing patient foreign key...
2   550e8400-...     observations.csv 891         warning   invalid_value        Observation value does not...
...
```

---

## **SEQUENCE DIAGRAM (Text Flow)**

```
User Browser          FastAPI Backend        Database           File System
    |                      |                    |                    |
    |--[1] Open UI-------->|                    |                    |
    |                      |                    |                    |
    |--[2] Upload CSVs---->|                    |                    |
    |                      |--[3] Save Temp------->|                    |
    |                      |<--[4] Path------------|                    |
    |<--[5] input_dir------|                    |                    |
    |                      |                    |                    |
    |--[6] Click Start---->|                    |                    |
    |                      |--[7] Create Job------>|                    |
    |                      |<--[8] job_id---------|                    |
    |<--[9] job_id--------|                    |                    |
    |                      |                    |                    |
    |                 [Thread Spawns]           |                    |
    |                      |                    |                    |
    |              [10] run_conversion()        |                    |
    |                      |                    |                    |
    |                [10a] CSV Parse------------|                    |
    |                      |<--[10b] SourceRecord--|                  |
    |                      |                    |                    |
    |                [10c] Mapping              |                    |
    |                [10d] Validation           |                    |
    |                [10e] upsert_resource()---->|                    |
    |                      |                    |<--[10f] Persisted--|
    |                      |                    |                    |
    |--[11] Poll Status--->|                    |                    |
    |<--[12] Progress------|                    |                    |
    | (repeat until done)  |                    |                    |
    |                      |                    |                    |
    |--[13] Click Persist->|                    |                    |
    |                [14] mark_validated_persisted()                 |
    |                      |--[15] UPDATE-------->|                    |
    |                      |<--[16] Rows Updated--|                    |
    |<--[17] Persisted-----|                    |                    |
    |                      |                    |                    |
    |--[18] View Issues--->|                    |                    |
    |                      |--[19] Query Issues--->|                  |
    |                      |<--[20] Issues List----|                  |
    |<--[21] Display-------|                    |                    |
    |                      |                    |                    |
    |--[22] Load Patient-->|                    |                    |
    |                      |--[23] Query FHIR---->|                  |
    |                      |<--[24] Resources-----|                  |
    |<--[25] Display-------|                    |                    |
    |                      |                    |                    |
```

---

## **SUMMARY: User's Experience Timeline**

```
⏱️  0:00  User opens browser
⏱️  0:15  User uploads Synthea folder (630k rows)
⏱️  0:30  User inspects dataset (shows metadata)
⏱️  0:45  User clicks "Start" (conversion begins)
⏱️  0:50  Stage 1 complete: CSV parsed (5s)
⏱️  1:35  Stage 2 complete: Mapped to FHIR (45s)
⏱️  1:43  Stage 3 complete: Generated bundles (8s)
⏱️  2:41  Stage 4 complete: Validation done (58s)
⏱️  2:42  Conversion complete - shows summary
⏱️  2:45  User clicks "Store validated"
⏱️  2:50  Persistence complete (615k resources)
⏱️  2:55  User views issues
⏱️  3:00  User inspects patient data
```

---

## **KEY FEATURES FROM USER PERSPECTIVE**

✅ **What Works**
- ✓ Upload CSV or enter local path
- ✓ Inspect dataset before processing
- ✓ Real-time progress tracking (stages, resources)
- ✓ Auto-refresh of job status
- ✓ View validation issues
- ✓ Inspect individual patient resources
- ✓ Store validated resources to database

⚠️ **What's Pending**
- ⏹ Multiple adapter support (only Synthea CSV)
- ⏹ Claims (837) adapter
- ⏹ Pharmacy (NCPDP) adapter
- ⏹ Lab (LOINC) adapter
- ⏹ EHR/HIE (C-CDA) adapter
- ⏹ HEDIS measure calculations
- ⏹ Gap detection / enrollment validation

---
