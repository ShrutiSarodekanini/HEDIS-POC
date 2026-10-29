# Test Data Structure - HEDIS POC

Production-ready test data for all adapter types with real-world naming conventions.

## Directory Structure

```
data/seed/
├── synthea/
│   └── csv/                          # Synthea CSV data (default adapter)
│       ├── patients.csv
│       ├── encounters.csv
│       ├── conditions.csv
│       ├── medications.csv
│       └── procedures.csv
│
├── claims/                           # EDI 837 Claims (claims_837 adapter)
│   ├── healthcare_claims.837         # Simple claim (JANE DOE office visit)
│   └── medical_claims_20261026.837   # Realistic production-style claim (JOHN DOE)
│
├── ncpdp/                            # Pharmacy (ncpdp_pharmacy adapter)
│   ├── pharmacy_claims.ncpdp.txt     # Batch 1: Common medications (8 records)
│   └── pharmacy_claims_batch2.ncpdp.txt  # Batch 2: Additional patients (5 records)
│
├── documents/                        # C-CDA Documents (ccda_hie adapter)
│   └── (existing C-CDA samples)
│
└── fhir/                             # FHIR JSON (ehr_fhir_r4 adapter)
    └── (existing FHIR bundles)
```

## File Naming Conventions

### Production Standard → File Extension Mapping

| Adapter | Format | Extension | Example |
|---------|--------|-----------|---------|
| `synthea_csv` | CSV (pipe/comma-delimited) | `.csv` | `patients.csv`, `encounters.csv` |
| `claims_837` | EDI Healthcare Claims | `.837` | `medical_claims_20261026.837` |
| `ncpdp_pharmacy` | Pipe-delimited pharmacy | `.ncpdp.txt` | `pharmacy_claims.ncpdp.txt` |
| `ccda_hie` | CDA Clinical Document | `.xml`, `.ccda` | `patient_record.ccda.xml` |
| `ehr_fhir_r4` | FHIR JSON resources | `.json`, `.fhir` | `patient_bundle.json`, `practitioner.fhir` |

**Why This Matters:**
- Production healthcare systems export with these exact conventions
- Adapter auto-detection relies on file extensions (not guessing)
- File naming is part of HL7/X12/NCPDP industry standards
- Testing with production naming validates real-world compatibility

## Test Data Details

### Claims (837) - EDI Healthcare Claims

**Files:**
- `healthcare_claims.837` - Simple example (1 claim, JANE DOE)
- `medical_claims_20261026.837` - Realistic example (1 claim, JOHN DOE, production naming)

**Format:** EDI 837 (ISA/GS/ST segments)
**Records per file:** 1 claim
**Fields:** Submitter, Payer, Provider, Subscriber (patient), Service lines, Amounts
**Why 837 format:** Industry standard for health insurance claims (X12 EDI)

**Example Content:**
```
ISA*00*          *00*          *ZZ*SYNTHETIC_CLAIMS*ZZ*TEST_PAYER
GS*HC*SYNTHETIC_CLAIMS*TEST_PAYER*20261026*1200*1*X*005010X222A1
ST*837*0001*005010X222A1
NM1*IL*1*DOE*JOHN****MI*MEM001              ← Subscriber/Patient
CLM*CLM001*150.00***11:B:1*Y*A*Y*Y           ← Claim info
SV1*HC:99213*150.00*UN*1***1~                ← Service (office visit)
DTP*472*D8*20261020~                         ← Service date
```

**Upload Testing:**
```
1. UI: Upload data/seed/claims/ folder
2. Auto-detect: "Claims (837)" (detects .837 extension)
3. Adapter: claims_837
4. Expected: Ingestion processes EDI claim → FHIR Claim resource
```

---

### Pharmacy (NCPDP) - Prescription/Medication Dispense

**Files:**
- `pharmacy_claims.ncpdp.txt` - Batch 1 (8 pharmacy transactions)
- `pharmacy_claims_batch2.ncpdp.txt` - Batch 2 (5 pharmacy transactions)

**Format:** Pipe-delimited (|) with header row
**Records per file:** 8-13 medication dispenses
**Fields:** Prescription ID, Patient ID, Medication, Dates, Costs, Status
**Why .ncpdp.txt:** Industry standard for pharmacy claims (NCPDP standard)

**Example Content:**
```
prescription_id|rx_number|patient_id|member_id|member_name|member_dob|...
RX001|RX123456789|PAT001|MEM001|John Doe|19800101|...LISINOPRIL 10MG|30|30
RX002|RX123456790|PAT002|MEM002|Mary Johnson|19751215|...METFORMIN 500MG|60|30
```

**Medications in Test Data:**
- Lisinopril (ACE inhibitor - hypertension)
- Metformin (Antidiabetic - type 2 diabetes)
- Levothyroxine (Thyroid hormone replacement)
- Simvastatin (Statin - cholesterol)
- Omeprazole (PPI - acid reflux)
- Sertraline (SSRI - depression/anxiety)
- Metoprolol (Beta-blocker - hypertension/cardiac)
- Amlodipine (Calcium channel blocker - hypertension)

**Upload Testing:**
```
1. UI: Upload data/seed/ncpdp/ folder
2. Auto-detect: "NCPDP Pharmacy" (detects .ncpdp.txt extension)
3. Adapter: ncpdp_pharmacy
4. Expected: Ingestion processes pharmacy claims → FHIR MedicationRequest/MedicationDispense
```

---

### FHIR JSON - Standard Healthcare Exchange

**Files:** Existing in data/seed/fhir/ (bundles with resource types in names)
**Format:** JSON with resourceType fields
**Records:** Variable per bundle
**Examples:** patient*, practitioner*, encounter*, medication*, claim*, bundle*

**Upload Testing:**
```
1. UI: Upload data/seed/fhir/ folder
2. Auto-detect: "FHIR JSON" (detects .json + FHIR resource pattern)
3. Adapter: ehr_fhir_r4
4. Expected: Direct FHIR validation and persistence
```

---

### Synthea CSV - Default Format

**Files:** Existing in data/seed/synthea/csv/
**Format:** CSV standard (comma or pipe-delimited)
**Records:** Thousands per file (realistic synthetic EHR)
**Tables:** patients, encounters, conditions, medications, procedures, observations

**Upload Testing:**
```
1. UI: Upload data/seed/synthea/csv/ folder
2. Auto-detect: "CSV" (default - looks for patient.csv, encounter.csv)
3. Adapter: synthea_csv
4. Expected: Full Synthea pipeline → FHIR resources with HEDIS gap detection
```

---

## Testing Workflow

### End-to-End Testing (All Adapters)

```bash
# 1. Start backend
python services/data_ingestion/main.py

# 2. Start frontend
cd ui && npm run dev

# 3. Test each adapter in browser (localhost:3000/run)
```

| Adapter | Test File | Expected Dropdown | Status |
|---------|-----------|-------------------|--------|
| Synthea CSV | `data/seed/synthea/csv/` | CSV | ✅ |
| FHIR JSON | `data/seed/fhir/` | FHIR JSON | ✅ |
| C-CDA | `data/seed/documents/` | C-CDA Documents | ✅ |
| Claims (837) | `data/seed/claims/` | Claims (837) | ✅ |
| NCPDP Pharmacy | `data/seed/ncpdp/` | NCPDP Pharmacy | ✅ |

### Key Verification Points

1. **Auto-Detection Works**
   - Upload folder → Correct adapter shown in dropdown
   - File extensions correctly trigger adapter routing

2. **Friendly Display Names Work**
   - Dropdown shows user-friendly name, not `adapter_name`
   - Examples: "CSV", "FHIR JSON", "Claims (837)", "NCPDP Pharmacy"

3. **Ingestion Succeeds**
   - "Run ingestion" completes without error
   - Resources created in database
   - No PHI in logs (member IDs only, not names/dates)

4. **Resource Pipeline Works**
   - Resources appear in correct types
   - FHIR validation passes
   - Gap detection active (Synthea)

---

## Real-World Production Mapping

### How Files Arrive in Production

```
Healthcare Organization (EHR/Claims Payer) Export
    ↓
File arrives at SFTP server with production naming:
    ├── medical_claims_20261026.837         ← Claims dept exports daily
    ├── pharmacy_claims_20261026.ncpdp.txt  ← Pharmacy dept exports nightly
    ├── patient_bundle_001.json             ← EHR exports on-demand
    └── clinical_summary.ccda.xml           ← Documents sent by HIE
    ↓
HEDIS Ingestion Pipeline (Your System)
    ├── Extension detected automatically
    ├── Correct adapter routed (no manual selection)
    ├── Data validated per adapter rules
    ├── Converted to FHIR standard format
    └── Stored in database with audit trail (PHI-masked)
    ↓
Gap Identification & Prioritization
    ├── HEDIS measures checked against patient records
    ├── Missing care identified
    ├── Priority ranked by clinical/financial impact
    └── Sent to care team for review
```

---

## Notes

- All patient names in NCPDP/Claims are synthetic (test data only)
- All dates use 2026-10 (future date for testing)
- All IDs are synthetic (MEM001, PAT001, NPI examples)
- Real production data uses same naming patterns with real dates
- File extensions are **not** optional - they're part of the standard

---

**Last Updated:** 2026-10-26
**Test Data Version:** 1.0
**Status:** Ready for production testing
