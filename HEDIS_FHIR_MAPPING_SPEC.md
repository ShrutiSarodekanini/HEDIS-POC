# HEDIS Core FHIR IG Mapping Specification

## Overview

This document defines how CSV data from various sources maps to FHIR R4 resources according to **NCQA's HEDIS Core FHIR Implementation Guide**.

Each mapping includes:
1. **Source Format** - Where data comes from (CSV file type)
2. **Source Field/Code** - Column name in CSV
3. **Transformation** - Value conversion or normalization rules
4. **FHIR Resource and Path** - Where it belongs in FHIR R4 structure
5. **Terminology** - Code system and value set
6. **Validation** - Constraints and required values
7. **Traceability** - Original source tracking (file, row, ID)

---

## Part 1: Patient Demographics (Synthea patients.csv)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 1.1 | Synthea CSV | `Id` | UUID as-is | Patient::id | - | Required, UUID v4 | `source_record_id=Id, source_file=patients.csv` |
| 1.2 | Synthea CSV | `FIRST`, `LAST` | Concatenate with space | Patient::name[0].text | - | min 1 char | `name text = FIRST + " " + LAST` |
| 1.3 | Synthea CSV | `BIRTHDATE` | ISO date (YYYY-MM-DD) | Patient::birthDate | - | Required date | `to_iso_date("BIRTHDATE")` |
| 1.4 | Synthea CSV | `GENDER` | Normalize to FHIR | Patient::gender | HL7 V3 AdminGender | male\|female\|other\|unknown | Map: "M"→male, "F"→female, else→unknown |
| 1.5 | Synthea CSV | `RACE` | Map SNOMED code | Patient::extension[us-core-race] | SNOMED CT (OMB Race) | From race valueset | SNOMED code for race |
| 1.6 | Synthea CSV | `ETHNICITY` | Map SNOMED code | Patient::extension[us-core-ethnicity] | SNOMED CT (OMB Ethnicity) | From ethnicity valueset | SNOMED code for ethnicity |
| 1.7 | Synthea CSV | `FIRST` + `LAST` + `BIRTHDATE` | Deterministic hash | Patient::identifier[MRN] | urn:oid:2.16.840.1.113883.4.1 | Unique per patient | SHA1(FIRST+LAST+BIRTHDATE) |
| 1.8 | Synthea CSV | `ADDRESS` | Parse street/city/state/zip | Patient::address[0].* | - | Structured address | Split address components |
| 1.9 | Synthea CSV | `Id` | Synthea source ID | Patient::identifier[synthea] | urn:synthea:patients | - | Direct from CSV |

---

## Part 2: Claims/Encounter Data (CSV or 837 EDI)

### Source: Claims Detailed (claims.csv or 837 Professional)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 2.1 | Claims CSV | `Id` | UUID as-is | Claim::id | - | Required UUID | `source_record_id=Id` |
| 2.2 | Claims CSV | `PATIENT` | FK to Patient | Claim::patient.reference | - | Required, must exist | `Patient/{patient_uuid}` |
| 2.3 | Claims CSV | `ENCOUNTER` | FK to Encounter | Claim::encounter[0].reference | - | Optional, link if exists | `Encounter/{encounter_uuid}` |
| 2.4 | Claims CSV | `CPT_CODE` | CPT code as-is | Claim::item[0].productOrService.coding[0].code | http://www.ama-assn.org/go/cpt | Required, valid CPT | `CPT_CODE from row` |
| 2.5 | Claims CSV | `SERVICE_DATE` | ISO date | Claim::servicedDate | - | Required, <= today | `to_iso_date("SERVICE_DATE")` |
| 2.6 | Claims CSV | `UNITS` | Numeric quantity | Claim::item[0].quantity.value | http://unitsofmeasure.org | Non-negative integer | `to_decimal("UNITS")` |
| 2.7 | Claims CSV | `PLACE_OF_SERVICE` | Map facility code | Claim::facility.reference | - | Valid CMS POS code | Reference Location resource |
| 2.8 | Claims CSV | `CLAIM_AMOUNT` | USD decimal | Claim::total.value | - | Non-negative, 2 decimals | `to_decimal("CLAIM_AMOUNT")` |
| 2.9 | Claims CSV | `PROVIDER_ID` | Provider ID | Claim::provider.reference | - | Valid provider NPI | `Practitioner/{provider_uuid}` |
| 2.10 | Claims CSV | `DIAGNOSIS_CODES` | ICD-10-CM codes | Claim::diagnosis[].diagnosisCodeableConcept | http://hl7.org/fhir/sid/icd-10-cm | Valid ICD-10 format | Comma-separated ICD codes |
| 2.11 | Claims CSV | `MODIFIERS` | CPT modifiers | Claim::item[0].modifier[].coding[0].code | CPT modifiers | Optional, 2-char codes | Comma-separated |
| 2.12 | Claims CSV | `PAYER_ID` | Insurance payer ID | Claim::insurer.reference | - | Valid payer code | `Organization/{payer_uuid}` |

**Example CPT Code Mapping:**
```
Source: CPT_CODE = "45378" (Colonoscopy)
        SERVICE_DATE = "2024-01-15"
        UNITS = "1"
        CLAIM_AMOUNT = "1500.00"

FHIR Output:
{
  "resourceType": "Claim",
  "id": "claim-abc123",
  "patient": {"reference": "Patient/patient-uuid"},
  "servicedDate": "2024-01-15",
  "item": [{
    "sequence": 1,
    "productOrService": {
      "coding": [{
        "system": "http://www.ama-assn.org/go/cpt",
        "code": "45378",
        "display": "Colonoscopy, flexible, proximal to splenic flexure"
      }]
    },
    "servicedDate": "2024-01-15",
    "quantity": {"value": 1, "unit": "1", "code": "1"},
    "unitPrice": {"value": 1500.00, "currency": "USD"}
  }]
}

Traceability:
  source_file = "claims.csv"
  source_record_id = "claim-123"
  row_number = 42
  transformation_rules = [
    "CPT_CODE → item.productOrService.coding[].code",
    "SERVICE_DATE → servicedDate (ISO format)",
    "UNITS → item.quantity.value (numeric)",
    "CLAIM_AMOUNT → unitPrice.value (USD decimal)"
  ]
```

---

## Part 3: Pharmacy/Medication Data (NCPDP or CSV)

### Source: Pharmacy Claims (NCPDP D0 Segment or medications.csv)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 3.1 | Pharmacy CSV | `Id` | UUID as-is | MedicationRequest::id | - | Required UUID | `source_record_id=Id` |
| 3.2 | Pharmacy CSV | `PATIENT` | FK to Patient | MedicationRequest::subject.reference | - | Required, must exist | `Patient/{patient_uuid}` |
| 3.3 | Pharmacy CSV | `MEDICATION_CODE` | RxNorm code | MedicationRequest::medicationCodeableConcept.coding[].code | http://www.nlm.nih.gov/research/umls/rxnorm | Required, valid RxNorm | RxNorm code from NCPDP |
| 3.4 | Pharmacy CSV | `MEDICATION_NAME` | Display text | MedicationRequest::medicationCodeableConcept.text | - | Non-empty string | Direct from CSV |
| 3.5 | Pharmacy CSV | `START` | ISO datetime | MedicationRequest::authoredOn | - | Required, <= today | `to_iso_datetime("START")` |
| 3.6 | Pharmacy CSV | `END` | ISO datetime | MedicationRequest::dosageInstruction[0].timing.repeat.boundEnd | - | Optional, >= START | `to_iso_datetime("END")` |
| 3.7 | Pharmacy CSV | `DOSAGE` | Numeric | MedicationRequest::dosageInstruction[0].doseAndRate[0].doseQuantity.value | http://unitsofmeasure.org | Non-negative number | `to_decimal("DOSAGE")` |
| 3.8 | Pharmacy CSV | `DOSE_UNIT` | UoM | MedicationRequest::dosageInstruction[0].doseAndRate[0].doseQuantity.unit | UCUM | Standard unit (mg, tablet, ml) | Unit from CSV |
| 3.9 | Pharmacy CSV | `FREQUENCY` | Text | MedicationRequest::dosageInstruction[0].text | - | Parse to timing | "once daily"→freq=1/day, "twice daily"→freq=2/day |
| 3.10 | Pharmacy CSV | `ROUTE_CODE` | SNOMED code | MedicationRequest::dosageInstruction[0].route.coding[].code | http://snomed.info/sct | Valid route code | SNOMED route code |
| 3.11 | Pharmacy CSV | `QUANTITY` | Numeric | MedicationRequest::dispenseRequest.quantity.value | - | Non-negative integer | `to_decimal("QUANTITY")` |
| 3.12 | Pharmacy CSV | `REFILLS` | Count | MedicationRequest::dispenseRequest.numberOfRepeatsAllowed | - | Non-negative integer | `to_decimal("REFILLS")` |
| 3.13 | Pharmacy CSV | `DAYS_SUPPLY` | Days | MedicationRequest::dispenseRequest.expectedSupplyDuration.value | http://unitsofmeasure.org | 1-999 days | `to_decimal("DAYS_SUPPLY")` |
| 3.14 | Pharmacy CSV | `FILL_STATUS` | Status | MedicationRequest::extension[fill-status].valueCode | - | filled\|unfilled\|partial | Map from NCPDP |
| 3.15 | Pharmacy CSV | `PRESCRIBER_ID` | NPI | MedicationRequest::requester.reference | - | Valid NPI | `Practitioner/{prescriber_uuid}` |
| 3.16 | Pharmacy CSV | `PHARMACY_ID` | Pharmacy code | MedicationRequest::extension[dispense-pharmacy].valueReference | - | Valid pharmacy code | `Organization/{pharmacy_uuid}` |

**Example Pharmacy Mapping:**
```
Source (NCPDP): 
  RxNorm = "197361" (Metformin 500mg tablet)
  START = "2024-01-15"
  DOSAGE = "500"
  DOSE_UNIT = "mg"
  FREQUENCY = "twice daily"
  QUANTITY = "60"
  REFILLS = "2"
  DAYS_SUPPLY = "30"

FHIR Output:
{
  "resourceType": "MedicationRequest",
  "id": "medrx-def456",
  "subject": {"reference": "Patient/patient-uuid"},
  "medicationCodeableConcept": {
    "coding": [{
      "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
      "code": "197361",
      "display": "Metformin 500 MG Oral Tablet"
    }]
  },
  "authoredOn": "2024-01-15T10:30:00Z",
  "status": "active",
  "intent": "order",
  "dosageInstruction": [{
    "text": "Take 500mg twice daily",
    "timing": {
      "repeat": {
        "frequency": 2,
        "period": 1,
        "periodUnit": "d"
      }
    },
    "doseAndRate": [{
      "doseQuantity": {
        "value": 500,
        "unit": "mg",
        "system": "http://unitsofmeasure.org",
        "code": "mg"
      }
    }],
    "route": {
      "coding": [{
        "system": "http://snomed.info/sct",
        "code": "26643006",
        "display": "Oral"
      }]
    }
  }],
  "dispenseRequest": {
    "quantity": {
      "value": 60,
      "unit": "tablets"
    },
    "numberOfRepeatsAllowed": 2,
    "expectedSupplyDuration": {
      "value": 30,
      "unit": "days",
      "system": "http://unitsofmeasure.org",
      "code": "d"
    }
  }
}

Traceability:
  source_format = "NCPDP"
  source_record_id = "rx-789"
  source_file = "pharmacy.ncpdp"
  transformation_rules = [
    "RxNorm → medicationCodeableConcept.coding[].code (pharmacy database lookup)",
    "START → authoredOn (ISO datetime)",
    "DOSAGE + DOSE_UNIT → doseQuantity (with UCUM conversion)",
    "FREQUENCY → timing.repeat (parsed to frequency/period)",
    "QUANTITY + REFILLS → dispenseRequest (total supply calculation)"
  ]
```

---

## Part 4: Lab Results (LOINC/Observations)

### Source: Lab Results (CSV with LOINC codes)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 4.1 | Lab CSV | `Id` | UUID as-is | Observation::id | - | Required UUID | `source_record_id=Id` |
| 4.2 | Lab CSV | `PATIENT` | FK to Patient | Observation::subject.reference | - | Required, must exist | `Patient/{patient_uuid}` |
| 4.3 | Lab CSV | `CODE` | LOINC code | Observation::code.coding[0].code | http://loinc.org | Required, valid LOINC | LOINC code from CSV |
| 4.4 | Lab CSV | `DESCRIPTION` | LOINC display name | Observation::code.text | - | Non-empty string | Direct from CSV |
| 4.5 | Lab CSV | `DATE` | ISO datetime | Observation::effectiveDateTime | - | Required, <= today | `to_iso_datetime("DATE")` |
| 4.6 | Lab CSV | `VALUE` | Numeric or text | Observation::valueQuantity.value OR valueString | - | Type-dependent | Decimal or string |
| 4.7 | Lab CSV | `UNITS` | UCUM unit | Observation::valueQuantity.unit | http://unitsofmeasure.org | Must match LOINC allowed units | UCUM code |
| 4.8 | Lab CSV | `REFERENCE_LOW` | Number | Observation::referenceRange[0].low.value | - | Non-null if REFERENCE_HIGH | `to_decimal("REFERENCE_LOW")` |
| 4.9 | Lab CSV | `REFERENCE_HIGH` | Number | Observation::referenceRange[0].high.value | - | Non-null if REFERENCE_LOW | `to_decimal("REFERENCE_HIGH")` |
| 4.10 | Lab CSV | `INTERPRETATION` | Result flag | Observation::interpretation[0].coding[0].code | HL7 V2.0 Result Value | H\|L\|H>\|<L\|A\|N\|etc | Result interpretation |
| 4.11 | Lab CSV | `VALUE` vs REFERENCE | Computed | Observation::interpretation[0].coding[0] | - | AUTO: H if value > high, L if < low | Algorithm to compute |
| 4.12 | Lab CSV | `SPECIMEN_TYPE` | Type | Observation::specimen.reference | SNOMED specimen | Blood\|Urine\|CSF\|etc | `Specimen/{specimen_uuid}` |
| 4.13 | Lab CSV | `TEST_METHOD` | Method code | Observation::method.coding[0].code | SNOMED CT | Lab method code | SNOMED method code |
| 4.14 | Lab CSV | `STATUS` | Status | Observation::status | Observation Status | final\|preliminary\|amended | Status from CSV |
| 4.15 | Lab CSV | `ISSUED_DATE` | ISO datetime | Observation::issued | - | >= effectiveDateTime | `to_iso_datetime("ISSUED_DATE")` |

**Example Lab Mapping (HbA1c):**
```
Source:
  CODE = "4548-4" (LOINC for HbA1c)
  DESCRIPTION = "Hemoglobin A1c"
  DATE = "2024-01-15"
  VALUE = "7.4"
  UNITS = "%"
  REFERENCE_LOW = "4.0"
  REFERENCE_HIGH = "5.7"
  SPECIMEN_TYPE = "Blood"
  TEST_METHOD = "HPLC"
  STATUS = "final"

FHIR Output:
{
  "resourceType": "Observation",
  "id": "obs-ghi789",
  "status": "final",
  "code": {
    "coding": [{
      "system": "http://loinc.org",
      "code": "4548-4",
      "display": "Hemoglobin A1c/Hemoglobin.total in Blood"
    }],
    "text": "Hemoglobin A1c"
  },
  "subject": {"reference": "Patient/patient-uuid"},
  "effectiveDateTime": "2024-01-15T14:30:00Z",
  "issued": "2024-01-15T16:45:00Z",
  "valueQuantity": {
    "value": 7.4,
    "unit": "%",
    "system": "http://unitsofmeasure.org",
    "code": "%"
  },
  "referenceRange": [{
    "low": {
      "value": 4.0,
      "unit": "%",
      "system": "http://unitsofmeasure.org",
      "code": "%"
    },
    "high": {
      "value": 5.7,
      "unit": "%",
      "system": "http://unitsofmeasure.org",
      "code": "%"
    }
  }],
  "interpretation": [{
    "coding": [{
      "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
      "code": "H",
      "display": "High"
    }]
  }],
  "specimen": {
    "reference": "Specimen/specimen-jkl123"
  },
  "method": {
    "coding": [{
      "system": "http://snomed.info/sct",
      "code": "312676001",
      "display": "Chromatography"
    }]
  }
}

Traceability:
  source_format = "Lab CSV"
  source_record_id = "lab-obs-456"
  source_file = "observations.csv"
  transformation_rules = [
    "CODE → code.coding[].code (LOINC lookup)",
    "VALUE → valueQuantity.value (decimal parsing)",
    "UNITS → valueQuantity.unit (UCUM mapping)",
    "VALUE > REFERENCE_HIGH → interpretation.coding[].code = 'H'",
    "REFERENCE_LOW + REFERENCE_HIGH → referenceRange[] (bounds)",
    "SPECIMEN_TYPE → specimen.reference (with type validation)"
  ]
```

---

## Part 5: Coverage/Enrollment Data

### Source: Coverage Claims (CSV or NCPDP file 01)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 5.1 | Coverage CSV | `Id` | UUID as-is | Coverage::id | - | Required UUID | `source_record_id=Id` |
| 5.2 | Coverage CSV | `PATIENT` | FK to Patient | Coverage::beneficiary.reference | - | Required, must exist | `Patient/{patient_uuid}` |
| 5.3 | Coverage CSV | `START` | ISO date | Coverage::period.start | - | Required, <= END | `to_iso_date("START")` |
| 5.4 | Coverage CSV | `END` | ISO date | Coverage::period.end | - | Optional, >= START | `to_iso_date("END")` |
| 5.5 | Coverage CSV | `PAYER` | Payer name | Coverage::payor[0].display | - | Non-empty string | Payer org name |
| 5.6 | Coverage CSV | `MEMBER_ID` | Member ID | Coverage::identifier[0].value | - | Unique per payer | Member ID from insurance |
| 5.7 | Coverage CSV | `GROUP_NUMBER` | Group ID | Coverage::grouping.group | - | Company group code | Group number from employer |
| 5.8 | Coverage CSV | `COVERAGE_TYPE` | Type | Coverage::type.coding[0].code | HL7 V3 ActCode | HMO\|PPO\|POS\|INDEMNITY\|etc | Coverage type code |
| 5.9 | Coverage CSV | `PLAN_NAME` | Plan name | Coverage::class[0].value | - | Non-empty string | Plan name from policy |
| 5.10 | Coverage CSV | `NETWORK_STATUS` | In/Out network | Coverage::extension[network-status] | - | in-network\|out-network | Network indicator |
| **5.11** | **Coverage CSV** | **START→END calculation** | **Gap detection** | **Coverage::extension[gap-detected]** | **- (custom)** | **Gap > 45 days** | **HEDIS continuous enrollment rule** |
| 5.12 | Coverage CSV | `START` | Coverage period | Coverage::period.start | - | Required | First coverage start |
| 5.13 | Coverage CSV | `END` | Coverage period | Coverage::period.end | - | Required | Last coverage end |

**Example Coverage Mapping (with Gap Detection):**
```
Source Records:
  Row 1: MEMBER_ID="M123", PAYER="BlueCross", START="2024-01-01", END="2024-06-30"
  Row 2: MEMBER_ID="M123", PAYER="BlueCross", START="2024-09-01", END="2024-12-31"
         ← GAP: 2024-07-01 to 2024-08-31 = 62 days (EXCEEDS 45-day HEDIS limit)

FHIR Output:
{
  "resourceType": "Coverage",
  "id": "cov-jkl012",
  "status": "active",
  "period": {
    "start": "2024-01-01",
    "end": "2024-06-30"
  },
  "beneficiary": {"reference": "Patient/patient-uuid"},
  "payor": [{
    "reference": "Organization/payer-uuid",
    "display": "BlueCross BlueShield"
  }],
  "identifier": [
    {
      "system": "urn:member-id",
      "value": "M123"
    },
    {
      "system": "urn:group-number",
      "value": "GRP-4567"
    }
  ],
  "type": {
    "coding": [{
      "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
      "code": "HMO",
      "display": "Health Maintenance Organization"
    }]
  },
  "class": [{
    "type": {
      "coding": [{
        "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
        "code": "plan",
        "display": "Plan"
      }]
    },
    "value": "BlueCross HMO Select"
  }],
  "extension": [
    {
      "url": "http://hedis.ncqa.org/StructureDefinition/enrollment-gap-detected",
      "valueBoolean": true
    },
    {
      "url": "http://hedis.ncqa.org/StructureDefinition/gap-details",
      "extension": [
        {
          "url": "gap-start-date",
          "valueDate": "2024-07-01"
        },
        {
          "url": "gap-end-date",
          "valueDate": "2024-08-31"
        },
        {
          "url": "gap-days",
          "valueInteger": 62
        },
        {
          "url": "continuous-enrollment-compliant",
          "valueBoolean": false
        }
      ]
    }
  ]
}

Validation Rules (HEDIS Continuous Enrollment):
  - Coverage period must be continuous with max 45-day gap
  - If gap > 45 days: member fails continuous enrollment requirement
  - Record gap in extension for measure calculation

Traceability:
  source_format = "Coverage CSV"
  source_record_id = "cov-M123-2024"
  source_file = "coverages.csv"
  transformation_rules = [
    "PATIENT → beneficiary.reference (FK lookup)",
    "START + END → period (date range)",
    "Calculate gaps between consecutive periods",
    "Gap > 45 days → extension[gap-detected] = true",
    "Gap data → extension[gap-details] (for audit trail)"
  ]
```

---

## Part 6: EHR/HIE Data (C-CDA or FHIR Pass-through)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 6.1 | C-CDA XML | CDA::clinicalDocument/templateId | Extract section | - | C-CDA sections | Required section ID | Parse XML path |
| 6.2 | C-CDA XML | Allergy entry | Convert to AllergyIntolerance | AllergyIntolerance::* | SNOMED (substance codes) | Valid substance code | XML entry ID |
| 6.3 | C-CDA XML | Problem section | Convert to Condition | Condition::* | ICD-10 or SNOMED | Valid problem code | Problem list entry |
| 6.4 | C-CDA XML | Medication section | Convert to MedicationStatement | MedicationStatement::* | RxNorm | Valid med code | Med list entry |
| 6.5 | C-CDA XML | Vital signs | Convert to Observation | Observation::* | LOINC | Valid vital code | Vital sign observation |
| 6.6 | FHIR JSON | Patient JSON | Validate & persist | Patient::* | - | Valid FHIR Patient | Parse as-is |
| 6.7 | FHIR JSON | Bundle resource | Extract entries | Individual resources | - | Valid Bundle structure | Bundle ID tracking |

---

## Part 7: CMS Provider Access API (0057-F)

| # | Source Format | Source Field | Transformation | FHIR Resource::Path | Terminology | Validation | Traceability |
|---|---|---|---|---|---|---|---|
| 7.1 | CMS API | $everything | Fetch all resources | Patient::* | - | Required endpoint | Patient ID query |
| 7.2 | CMS API | Patient resource | Validate & normalize | Patient::* | - | Valid FHIR R4 | CMS API response |
| 7.3 | CMS API | Condition resources | Extract diagnoses | Condition::* | ICD-10 from API | Valid codes | API resource ID |

---

## Current Implementation Status ✅❌

| Mapping Area | Status | Coverage | Notes |
|---|---|---|---|
| Patient Demographics | ✅ PARTIAL | 70% | Missing race/ethnicity extensions, MRN identifier |
| Claims/Procedures | ❌ MINIMAL | 20% | No CPT items, service dates, amounts, place of service |
| Pharmacy/Medications | ✅ BASIC | 40% | Has RxNorm mapping, missing dosage, refills, fill status |
| Lab/Observations | ✅ GOOD | 85% | Has LOINC, missing reference ranges, interpretation |
| Coverage/Enrollment | ❌ NONE | 0% | **CRITICAL: No gap detection for HEDIS continuous enrollment** |
| EHR/HIE C-CDA | ❌ NOT STARTED | 0% | Requires XML parser |
| CMS APIs | ❌ NOT STARTED | 0% | Requires OAuth2 client |

---

## Next Steps: Enhance Mappings

### Priority 1: Add Missing Claim Details (HIGH)
```python
def map_claim_enhanced(record: SourceRecord):
    # Add CPT items with service dates and amounts
    # Add place of service
    # Add diagnosis codes
    # Add modifiers
```

### Priority 2: Add Coverage Gap Detection (HIGH - HEDIS REQUIREMENT)
```python
def detect_enrollment_gaps(coverages: list[Coverage]) -> list[GapWarning]:
    # Detect gaps > 45 days
    # Mark continuous enrollment compliance
    # Return FHIR extension data
```

### Priority 3: Enhance Lab Interpretation (MEDIUM)
```python
def compute_observation_interpretation(value, ref_low, ref_high):
    # Auto-compute H/L/N based on reference ranges
    # Validate against LOINC rules
```

### Priority 4: Add Dosage & Refills to Meds (MEDIUM)
```python
def map_medication_request_enhanced(record: SourceRecord):
    # Add dosageInstruction with frequency parsing
    # Add dispenseRequest with refills
    # Add fill status extension
```

### Priority 5: Add C-CDA Parser (LOW)
```python
def parse_ccda_to_fhir(xml_content: str) -> list[ResourceEnvelope]:
    # Parse XML sections
    # Convert to FHIR resources
    # Maintain traceability to source
```

---

## Implementation Code Template

```python
@dataclass
class MappingMetadata:
    """Tracks transformation details for traceability and validation."""
    source_file: str              # e.g., "claims.csv"
    source_record_id: str         # e.g., "claim-123"
    row_number: int               # CSV line number
    source_fields_used: list[str] # e.g., ["CPT_CODE", "SERVICE_DATE", "CLAIM_AMOUNT"]
    transformations: dict[str, str]  # e.g., {"CPT_CODE": "CPT → item.productOrService.code"}
    validation_rules_applied: list[str]  # e.g., ["Valid CPT format", "Date <= today"]
    terminology_systems: list[str]   # e.g., ["http://www.ama-assn.org/go/cpt"]

def map_claim_hedis_compliant(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    """Map claim CSV to FHIR Claim following HEDIS Core FHIR IG."""
    
    # Track all mappings for traceability
    metadata = MappingMetadata(
        source_file=record.source_file,
        source_record_id=record.source_record_id,
        row_number=record.row_number,
        source_fields_used=[],
        transformations={},
        validation_rules_applied=[],
        terminology_systems=[]
    )
    
    issues: list[RowIssue] = []
    
    # 2.1: Claim ID
    claim_id = record.source_record_id
    metadata.source_fields_used.append("Id")
    
    # 2.2: Patient FK
    patient_id = record.patient_id
    if not patient_id:
        issues.append(RowIssue(..., code="missing_patient_fk"))
        return None, issues
    metadata.source_fields_used.append("PATIENT")
    
    # 2.4: CPT Code (NEW)
    cpt_code = record.data.get("CPT_CODE")
    if not cpt_code:
        issues.append(RowIssue(..., code="missing_cpt_code"))
        return None, issues
    metadata.source_fields_used.append("CPT_CODE")
    metadata.transformations["CPT_CODE"] = "CPT_CODE → item.productOrService.coding[].code"
    metadata.terminology_systems.append("http://www.ama-assn.org/go/cpt")
    metadata.validation_rules_applied.append("Valid CPT format (5 digits or 4 digits + modifier)")
    
    # 2.5: Service Date (NEW)
    service_date = to_iso_date(record.data.get("SERVICE_DATE"))
    if not service_date:
        issues.append(RowIssue(..., code="missing_service_date"))
        return None, issues
    metadata.source_fields_used.append("SERVICE_DATE")
    metadata.transformations["SERVICE_DATE"] = "SERVICE_DATE → servicedDate (ISO date)"
    metadata.validation_rules_applied.append("Date must be <= today")
    
    # Build FHIR Claim
    fhir = {
        "resourceType": "Claim",
        "id": claim_id,
        "status": "active",
        "use": "claim",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
        "patient": {"reference": f"Patient/{deterministic_id('Patient', 'patients.csv', patient_id)}"},
        "servicedDate": service_date,
        "item": [{
            "sequence": 1,
            "productOrService": codeable_concept(
                cpt_code,
                record.data.get("CPT_DESCRIPTION", cpt_code),
                system="http://www.ama-assn.org/go/cpt"
            ),
            "servicedDate": service_date,
        }],
        "identifier": [{"system": "urn:synthea:claims", "value": record.source_record_id}],
        "meta": {
            "extension": [{
                "url": "http://hedis.ncqa.org/mapping-metadata",
                "extension": [
                    {"url": "source-file", "valueString": metadata.source_file},
                    {"url": "source-record-id", "valueString": metadata.source_record_id},
                    {"url": "row-number", "valueInteger": metadata.row_number},
                    {"url": "source-fields", "valueString": ",".join(metadata.source_fields_used)},
                    {"url": "terminology-systems", "valueString": ",".join(metadata.terminology_systems)},
                ]
            }]
        }
    }
    
    # Add optional fields (2.6-2.12)
    if units := record.data.get("UNITS"):
        fhir["item"][0]["quantity"] = {"value": to_decimal(units)}
        metadata.source_fields_used.append("UNITS")
    
    if claim_amount := to_decimal(record.data.get("CLAIM_AMOUNT")):
        fhir["item"][0]["unitPrice"] = {"value": float(claim_amount), "currency": "USD"}
        metadata.source_fields_used.append("CLAIM_AMOUNT")
    
    # Validate & return
    return envelope(record, "Claim", fhir["id"], fhir), issues
```

---

## HEDIS Compliance Checklist

- [ ] All mappings documented with 7-column table (source format → terminology)
- [ ] Traceability implemented: source_file, row_number, source_record_id, transformation rules
- [ ] Terminology: Every code field mapped to correct code system (CPT, LOINC, RxNorm, SNOMED, ICD-10, etc.)
- [ ] Validation: Every required field has validation rule + error handling
- [ ] Gap Detection: Coverage periods checked for >45 day gaps (continuous enrollment)
- [ ] CPT Items: Claims include service dates, place of service, amounts
- [ ] Dosage Detail: Medications include dosage, refills, fill status
- [ ] Lab Interpretation: Observations include reference ranges, interpretation codes
- [ ] Extensions: Custom FHIR extensions for HEDIS-specific data (gaps, fill-status, etc.)
- [ ] Unit Testing: Mappings tested against sample data from each source type
