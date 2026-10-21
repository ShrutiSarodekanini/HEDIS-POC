"""Single source of truth for the HEDIS POC seed data.

Every generator reads this module, so the Synthea-style patient data, the engine gap file,
the evidence documents and the expected outcomes stay consistent with each other.

Design rules:
- Structured data (Synthea CSVs, engine gaps) never contains evidence that exists only in documents.
  That evidence is what the AI step must find.
- Every scenario gap states its expected outcome, so extraction and review can be scored.
- All people, places and values are fictional.
"""

MY = 2026
RUN = {
    "run_id": "RUN-2026-10-12-001",
    "engine": "hedis-poc-engine",
    "engine_version": "0.1.0-seed",
    "measure_spec_version": "MY2026",
    "measurement_year": MY,
    "run_at": "2026-10-12T02:00:00Z",
}
AAB_INTAKE = ("2025-07-01", "2026-06-30")

# kind: pcp (primary care, has a closure rate), specialist, lab, hospital, dialysis, hie, portal, urgent_care
ORGANIZATIONS = [
    {"id": "ORG-RFC", "name": "Riverside Family Clinic", "kind": "pcp", "city": "Springfield", "state": "MA", "closure_rate": 0.72},
    {"id": "ORG-LIM", "name": "Lakeside Internal Medicine", "kind": "pcp", "city": "Worcester", "state": "MA", "closure_rate": 0.65},
    {"id": "ORG-NEN", "name": "Northside Endocrinology", "kind": "specialist", "city": "Lowell", "state": "MA", "closure_rate": 0.58},
    {"id": "ORG-WHA", "name": "Women's Health Associates", "kind": "pcp", "city": "Springfield", "state": "MA", "closure_rate": 0.70},
    {"id": "ORG-ECH", "name": "Eastside Community Health", "kind": "pcp", "city": "Brockton", "state": "MA", "closure_rate": 0.35},
    {"id": "ORG-VUC", "name": "Valley Urgent Care", "kind": "urgent_care", "city": "Worcester", "state": "MA", "closure_rate": None},
    {"id": "ORG-CEA", "name": "Clearview Eye Associates", "kind": "specialist", "city": "Lowell", "state": "MA", "closure_rate": None},
    {"id": "ORG-CGI", "name": "Central Gastroenterology", "kind": "specialist", "city": "Worcester", "state": "MA", "closure_rate": None},
    {"id": "ORG-CHO", "name": "County General Hospital", "kind": "hospital", "city": "Springfield", "state": "MA", "closure_rate": None},
    {"id": "ORG-MDC", "name": "Metro Dialysis Center", "kind": "dialysis", "city": "Worcester", "state": "MA", "closure_rate": None},
    {"id": "ORG-SIM", "name": "Springfield Imaging Center", "kind": "specialist", "city": "Springfield", "state": "MA", "closure_rate": None},
    {"id": "ORG-LAB", "name": "Commonwealth Reference Laboratory", "kind": "lab", "city": "Boston", "state": "MA", "closure_rate": None},
    {"id": "ORG-SDX", "name": "Stool DNA Diagnostics Lab", "kind": "lab", "city": "Boston", "state": "MA", "closure_rate": None},
    {"id": "ORG-HIE", "name": "State Health Information Exchange", "kind": "hie", "city": "Boston", "state": "MA", "closure_rate": None},
    {"id": "ORG-PRT", "name": "Member Portal", "kind": "portal", "city": None, "state": None, "closure_rate": None},
    {"id": "ORG-HOS", "name": "Comfort Hospice Services", "kind": "hospice", "city": "Brockton", "state": "MA", "closure_rate": None},
]

PROVIDERS = [
    {"id": "PRV-001", "name": "Dr. Anita Patel", "org": "ORG-RFC", "specialty": "Family Medicine"},
    {"id": "PRV-002", "name": "Dr. Marcus Reed", "org": "ORG-LIM", "specialty": "Internal Medicine"},
    {"id": "PRV-003", "name": "Dr. Helen Cho", "org": "ORG-NEN", "specialty": "Endocrinology"},
    {"id": "PRV-004", "name": "Dr. Laura Benson", "org": "ORG-WHA", "specialty": "Obstetrics and Gynecology"},
    {"id": "PRV-005", "name": "Dr. Samuel Ortiz", "org": "ORG-ECH", "specialty": "Family Medicine"},
    {"id": "PRV-006", "name": "Dr. Kevin Liu", "org": "ORG-VUC", "specialty": "Urgent Care"},
    {"id": "PRV-007", "name": "Dr. Priya Nair, OD", "org": "ORG-CEA", "specialty": "Optometry"},
    {"id": "PRV-008", "name": "Dr. Daniel Xu", "org": "ORG-CGI", "specialty": "Gastroenterology"},
    {"id": "PRV-009", "name": "Dr. Rachel Stone", "org": "ORG-CHO", "specialty": "Emergency Medicine"},
    {"id": "PRV-010", "name": "Dr. Omar Haddad", "org": "ORG-MDC", "specialty": "Nephrology"},
    {"id": "PRV-011", "name": "Dr. Grace Kim", "org": "ORG-SIM", "specialty": "Radiology"},
]

# conditions use SNOMED CT (as Synthea does). onset is YYYY-MM-DD.
# clinical: structured facts the Synthea generator must create. It must NOT add anything that would
# satisfy a scenario gap's numerator or exclusion unless listed here.
MEMBERS = [
    {"member_id": "M-001", "first": "Robert", "last": "Hayes", "sex": "M", "birth_date": "1968-03-14", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-RFC", "next_visit": "2026-10-23", "last_contact": "2026-04-10",
     "conditions": [{"code": "55822004", "desc": "Hyperlipidemia", "onset": "2015-06-01"}], "clinical": []},
    {"member_id": "M-002", "first": "Linda", "last": "Moore", "sex": "F", "birth_date": "1965-07-02", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-RFC", "next_visit": None, "last_contact": "2026-03-18", "conditions": [], "clinical": []},
    {"member_id": "M-003", "first": "Patricia", "last": "Gomez", "sex": "F", "birth_date": "1959-01-22", "city": "Springfield", "product_line": "medicare",
     "pcp_org": "ORG-WHA", "next_visit": "2026-11-04", "last_contact": "2026-05-06", "conditions": [], "clinical": []},
    {"member_id": "M-004", "first": "Karen", "last": "White", "sex": "F", "birth_date": "1981-05-30", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-WHA", "next_visit": "2026-12-01", "last_contact": "2026-07-21", "conditions": [], "clinical": []},
    {"member_id": "M-005", "first": "Maria", "last": "Lopez", "sex": "F", "birth_date": "1988-09-12", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-WHA", "next_visit": None, "last_contact": "2026-01-15", "conditions": [], "clinical": []},
    {"member_id": "M-006", "first": "Susan", "last": "Clark", "sex": "F", "birth_date": "1971-11-03", "city": "Brockton", "product_line": "medicaid",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2026-03-09", "conditions": [], "clinical": []},
    {"member_id": "M-007", "first": "Ashley", "last": "Brown", "sex": "F", "birth_date": "1997-04-25", "city": "Brockton", "product_line": "medicaid",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2026-08-05", "conditions": [], "clinical": []},
    {"member_id": "M-008", "first": "Dorothy", "last": "Evans", "sex": "F", "birth_date": "1955-11-20", "city": "Lowell", "product_line": "medicare",
     "pcp_org": "ORG-NEN", "next_visit": "2026-10-28", "last_contact": "2026-09-05",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2010-04-12"},
                    {"code": "26929004", "desc": "Alzheimer's disease", "onset": "2024-02-20"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 9.2, "unit": "%", "date": "2026-03-11"},
                  {"type": "medication", "rxnorm": "860975", "desc": "metformin 500 MG Oral Tablet", "start": "2010-05-01"}]},
    {"member_id": "M-009", "first": "James", "last": "Carter", "sex": "M", "birth_date": "1960-02-11", "city": "Worcester", "product_line": "medicare",
     "pcp_org": "ORG-LIM", "next_visit": "2026-11-18", "last_contact": "2026-09-30",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2016-09-03"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 7.2, "unit": "%", "date": "2026-05-10"},
                  {"type": "bp", "systolic": 142, "diastolic": 86, "date": "2026-06-12"},
                  {"type": "medication", "rxnorm": "860975", "desc": "metformin 500 MG Oral Tablet", "start": "2016-10-01"},
                  {"type": "medication", "rxnorm": "617312", "desc": "atorvastatin 40 MG Oral Tablet", "start": "2016-10-01", "fills_in_my": True}]},
    {"member_id": "M-010", "first": "Nancy", "last": "Allen", "sex": "F", "birth_date": "1974-06-08", "city": "Lowell", "product_line": "commercial",
     "pcp_org": "ORG-NEN", "next_visit": "2026-12-09", "last_contact": "2026-09-18",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2018-01-22"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 8.9, "unit": "%", "date": "2026-02-02"},
                  {"type": "bp", "systolic": 128, "diastolic": 80, "date": "2026-09-18"},
                  {"type": "medication", "rxnorm": "617312", "desc": "atorvastatin 40 MG Oral Tablet", "start": "2018-03-01", "fills_in_my": True}]},
    {"member_id": "M-011", "first": "Michael", "last": "Turner", "sex": "M", "birth_date": "1963-08-02", "city": "Worcester", "product_line": "commercial",
     "pcp_org": "ORG-LIM", "next_visit": "2026-10-21", "last_contact": "2026-10-01",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2012-03-15"},
                    {"code": "59621000", "desc": "Essential hypertension", "onset": "2014-07-09"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 7.0, "unit": "%", "date": "2026-04-12"},
                  {"type": "bp", "systolic": 146, "diastolic": 88, "date": "2026-07-02"},
                  {"type": "medication", "rxnorm": "617312", "desc": "atorvastatin 40 MG Oral Tablet", "start": "2013-01-10", "stop": "2025-05-20", "fills_in_my": False},
                  {"type": "medication", "rxnorm": "314076", "desc": "lisinopril 10 MG Oral Tablet", "start": "2014-08-01"}]},
    {"member_id": "M-012", "first": "David", "last": "Wilson", "sex": "M", "birth_date": "1978-12-19", "city": "Brockton", "product_line": "medicaid",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2026-07-08",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2020-11-30"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 7.4, "unit": "%", "date": "2026-05-28"}]},
    {"member_id": "M-013", "first": "Jennifer", "last": "Lee", "sex": "F", "birth_date": "1982-10-05", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-WHA", "next_visit": "2026-10-26", "last_contact": "2026-08-12",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2021-02-14"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 6.9, "unit": "%", "date": "2026-06-03"}]},
    {"member_id": "M-014", "first": "Charles", "last": "Young", "sex": "M", "birth_date": "1954-01-30", "city": "Springfield", "product_line": "medicare",
     "pcp_org": "ORG-RFC", "next_visit": "2026-12-15", "last_contact": "2026-09-22",
     "conditions": [{"code": "59621000", "desc": "Essential hypertension", "onset": "2008-05-19"}],
     "clinical": [{"type": "bp", "systolic": 148, "diastolic": 90, "date": "2026-05-20"}]},
    {"member_id": "M-015", "first": "Betty", "last": "King", "sex": "F", "birth_date": "1946-04-17", "city": "Worcester", "product_line": "medicare",
     "pcp_org": "ORG-LIM", "next_visit": None, "last_contact": "2026-06-30",
     "conditions": [{"code": "59621000", "desc": "Essential hypertension", "onset": "2000-03-08"}],
     "clinical": [{"type": "bp", "systolic": 152, "diastolic": 84, "date": "2026-04-02"}]},
    {"member_id": "M-016", "first": "Thomas", "last": "Wright", "sex": "M", "birth_date": "1967-03-09", "city": "Brockton", "product_line": "commercial",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2026-10-02",
     "conditions": [{"code": "59621000", "desc": "Essential hypertension", "onset": "2019-10-21"}],
     "clinical": [{"type": "bp", "systolic": 150, "diastolic": 92, "date": "2026-03-15"}]},
    {"member_id": "M-017", "first": "Emily", "last": "Scott", "sex": "F", "birth_date": "1992-02-14", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-WHA", "next_visit": "2026-10-30", "last_contact": "2026-05-19",
     "conditions": [{"code": "59621000", "desc": "Essential hypertension", "onset": "2023-06-02"}],
     "clinical": [{"type": "bp", "systolic": 144, "diastolic": 92, "date": "2026-02-20"}]},
    {"member_id": "M-018", "first": "George", "last": "Adams", "sex": "M", "birth_date": "1951-06-23", "city": "Springfield", "product_line": "medicare",
     "pcp_org": "ORG-RFC", "next_visit": "2026-11-12", "last_contact": "2026-08-20", "conditions": [], "clinical": []},
    {"member_id": "M-019", "first": "Kevin", "last": "Baker", "sex": "M", "birth_date": "1976-01-15", "city": "Worcester", "product_line": "commercial",
     "pcp_org": "ORG-LIM", "next_visit": None, "last_contact": "2026-03-03", "conditions": [], "clinical": []},
    {"member_id": "M-020", "first": "Brian", "last": "Nelson", "sex": "M", "birth_date": "1984-09-01", "city": "Worcester", "product_line": "commercial",
     "pcp_org": None, "next_visit": None, "last_contact": "2026-02-10", "conditions": [],
     "clinical": [{"type": "aab_episode", "date": "2026-02-10", "org": "ORG-VUC", "dx_code": "10509002", "dx_desc": "Acute bronchitis",
                   "antibiotic_rxnorm": "308182", "antibiotic_desc": "Amoxicillin 250 MG Oral Capsule", "dispensed": "2026-02-10"}]},
    {"member_id": "M-021", "first": "Helen", "last": "Rivera", "sex": "F", "birth_date": "1958-03-27", "city": "Brockton", "product_line": "medicare",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2026-04-20", "conditions": [],
     "clinical": [{"type": "aab_episode", "date": "2026-03-05", "org": "ORG-ECH", "dx_code": "10509002", "dx_desc": "Acute bronchitis",
                   "antibiotic_rxnorm": "308460", "antibiotic_desc": "Azithromycin 250 MG Oral Tablet", "dispensed": "2026-03-06"}]},
    {"member_id": "M-022", "first": "Ethan", "last": "Price", "sex": "M", "birth_date": "2017-05-11", "city": "Brockton", "product_line": "medicaid",
     "pcp_org": "ORG-ECH", "next_visit": "2027-01-10", "last_contact": "2025-12-15", "conditions": [],
     "clinical": [{"type": "aab_episode", "date": "2025-12-15", "org": "ORG-ECH", "dx_code": "4120002", "dx_desc": "Bronchiolitis",
                   "antibiotic_rxnorm": "308182", "antibiotic_desc": "Amoxicillin 250 MG Oral Capsule", "dispensed": "2025-12-15"}]},
    {"member_id": "M-023", "first": "Sandra", "last": "Hill", "sex": "F", "birth_date": "1964-08-30", "city": "Brockton", "product_line": "commercial",
     "pcp_org": "ORG-ECH", "next_visit": None, "last_contact": "2025-01-14", "conditions": [], "clinical": []},
    {"member_id": "M-024", "first": "Paul", "last": "Green", "sex": "M", "birth_date": "1956-12-02", "city": "Lowell", "product_line": "medicare",
     "pcp_org": None, "next_visit": None, "last_contact": "2025-04-08",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2015-08-17"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 9.6, "unit": "%", "date": "2025-11-03"}]},
    {"member_id": "M-025", "first": "Ronald", "last": "Ward", "sex": "M", "birth_date": "1969-04-04", "city": "Springfield", "product_line": "commercial",
     "pcp_org": "ORG-RFC", "next_visit": "2026-11-02", "last_contact": "2026-09-02",
     "conditions": [{"code": "44054006", "desc": "Diabetes mellitus type 2", "onset": "2017-05-25"}],
     "clinical": [{"type": "observation", "loinc": "4548-4", "desc": "Hemoglobin A1c", "value": 8.3, "unit": "%", "date": "2026-03-01"},
                  {"type": "medication", "rxnorm": "617312", "desc": "atorvastatin 40 MG Oral Tablet", "start": "2017-07-01", "fills_in_my": True}]},
    {"member_id": "M-026", "first": "Barbara", "last": "Cook", "sex": "F", "birth_date": "1960-10-10", "city": "Worcester", "product_line": "medicare",
     "pcp_org": "ORG-LIM", "next_visit": "2026-10-19", "last_contact": "2026-10-05",
     "conditions": [{"code": "59621000", "desc": "Essential hypertension", "onset": "2011-02-07"}],
     "clinical": [{"type": "bp", "systolic": 145, "diastolic": 91, "date": "2026-04-15"}]},
]

# expected.status is the status after a correct review:
#   Closed, Excluded, Open, Partially evidenced, Closed (structured)
# expected.find: evidence the AI should propose and a reviewer should approve.
# expected.traps: text that must not become approved evidence for this gap.
GAPS = [
    {"gap_id": "G-0001", "member_id": "M-001", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-101", "quote": "Colonoscopy 2019, Dr. Xu, no polyps.", "kind": "numerator"}],
                  "traps": ["father had colon cancer", "repeat colonoscopy in 2029", "Colonoscopy recommended"]}},
    {"gap_id": "G-0002", "member_id": "M-002", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": ["FIT kit mailed", "Colonoscopy 2014"],
                  "note": "FIT kit never returned; the 2014 colonoscopy is outside the 2017 to 2026 window."}},
    {"gap_id": "G-0003", "member_id": "M-002", "measure": "BCS-E", "missing": "No mammogram from 2024-10-01 to 2026-12-31", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-202", "quote": "Screening mammogram 2025-11-03", "kind": "numerator"}], "traps": []}},
    {"gap_id": "G-0004", "member_id": "M-003", "measure": "BCS-E", "missing": "No mammogram from 2024-10-01 to 2026-12-31", "last_event": None,
     "expected": {"status": "Excluded", "find": [{"doc": "D-301", "quote": "bilateral mastectomy 2015", "kind": "exclusion"}], "traps": []}},
    {"gap_id": "G-0005", "member_id": "M-003", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-302", "quote": "Cologuard", "kind": "numerator"}], "traps": []}},
    {"gap_id": "G-0006", "member_id": "M-004", "measure": "BCS-E", "missing": "No mammogram from 2024-10-01 to 2026-12-31", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": ["Screening mammogram ordered", "Breast ultrasound"]}},
    {"gap_id": "G-0007", "member_id": "M-004", "measure": "CCS-E", "missing": "No cervical cytology or hrHPV test on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-402", "quote": "Pap smear 03/2024, NILM", "kind": "numerator"}],
                  "traps": ["supracervical hysterectomy"], "note": "Supracervical hysterectomy keeps the cervix, so it is not an exclusion."}},
    {"gap_id": "G-0008", "member_id": "M-005", "measure": "CCS-E", "missing": "No cervical cytology or hrHPV test on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-501", "quote": "high-risk HPV test", "kind": "numerator"}], "traps": ["Pap smear 2021"],
                  "note": "HPV test in 2023 at age 34 counts. The 2021 Pap in D-502 is outside the 2024 to 2026 window."}},
    {"gap_id": "G-0009", "member_id": "M-006", "measure": "CCS-E", "missing": "No cervical cytology or hrHPV test on record", "last_event": None,
     "expected": {"status": "Excluded", "find": [{"doc": "D-601", "quote": "Total abdominal hysterectomy", "kind": "exclusion"}], "traps": []}},
    {"gap_id": "G-0010", "member_id": "M-007", "measure": "CCS-E", "missing": "No cervical cytology or hrHPV test on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": ["Pap due", "a Pap a few years ago"]}},
    {"gap_id": "G-0011", "member_id": "M-008", "measure": "GSD", "missing": "Most recent HbA1c or GMI in 2026 is not under 8.0%",
     "last_event": {"type": "lab_result", "date": "2026-03-11", "value": "9.2%", "code": "LOINC 4548-4", "source": "lab_feed"}, "exclusion_check": True,
     "expected": {"status": "Closed", "find": [{"doc": "D-801", "quote": "Hemoglobin A1c: 7.6 %", "kind": "numerator"}],
                  "optional": [{"doc": "D-803", "quote": "Uses a walker, two falls this year", "kind": "exclusion"},
                               {"doc": "D-803", "quote": "Advanced Alzheimer dementia", "kind": "exclusion"}],
                  "traps": ["around 7"],
                  "note": "D-805 is a near-duplicate of D-801 from the HIE and should be linked, not extracted twice. "
                          "Age 71 with frailty and advanced illness (D-803) also meets the GSD exclusion; either outcome is "
                          "acceptable for review."}},
    {"gap_id": "G-0012", "member_id": "M-008", "measure": "EED", "missing": "No retinal eye exam on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-804", "quote": "No diabetic retinopathy", "kind": "numerator"}],
                  "optional": [{"doc": "D-803", "quote": "Uses a walker, two falls this year", "kind": "exclusion"},
                               {"doc": "D-803", "quote": "Advanced Alzheimer dementia", "kind": "exclusion"}],
                  "traps": [],
                  "note": "Age 71 with frailty and advanced illness (D-803) also meets the EED exclusion; either outcome is "
                          "acceptable for review."}},
    {"gap_id": "G-0013", "member_id": "M-008", "measure": "KED", "missing": "No eGFR and uACR in 2026", "last_event": None,
     "expected": {"status": "Excluded",
                  "find": [{"doc": "D-803", "quote": "Uses a walker, two falls this year", "kind": "exclusion"},
                           {"doc": "D-803", "quote": "Advanced Alzheimer dementia", "kind": "exclusion"}],
                  "traps": ["A1c ordered today", "around 7"],
                  "note": "No kidney test on record. Age 71 (66 to 80) with frailty (walker, falls) and advanced illness "
                          "(advanced Alzheimer dementia) in D-803 meets the KED exclusion. Corrected 2026-10-21: it was "
                          "first marked Open with no documents."}},
    {"gap_id": "G-0014", "member_id": "M-009", "measure": "KED", "missing": "No eGFR and uACR in 2026", "last_event": None,
     "expected": {"status": "Partially evidenced", "find": [{"doc": "D-901", "quote": "eGFR 68", "kind": "numerator"}],
                  "traps": ["Urine albumin 12 mg/L", "Urine creatinine 110 mg/dL"],
                  "note": "Albumin (2026-05-10) and creatinine (2026-05-16) are 6 days apart, so they do not form a uACR."}},
    {"gap_id": "G-0015", "member_id": "M-009", "measure": "BPD", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-06-12", "value": "142/86", "code": "LOINC 8480-6/8462-4", "source": "claim"},
     "expected": {"status": "Closed", "find": [{"doc": "D-903", "quote": "131/79", "kind": "numerator"}], "traps": [],
                  "note": "Photo of a vitals flowsheet; the most recent row (2026-09-30) counts."}},
    {"gap_id": "G-0016", "member_id": "M-010", "measure": "EED", "missing": "No retinal eye exam on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": ["Referred to ophthalmology", "Visual acuity 20/30"]}},
    {"gap_id": "G-0017", "member_id": "M-010", "measure": "GSD", "missing": "Most recent HbA1c or GMI in 2026 is not under 8.0%",
     "last_event": {"type": "lab_result", "date": "2026-02-02", "value": "8.9%", "code": "LOINC 4548-4", "source": "lab_feed"},
     "expected": {"status": "Open", "find": [], "traps": ["HbA1c 8.4%"], "note": "8.4% is newer but not under 8.0%."}},
    {"gap_id": "G-0018", "member_id": "M-010", "measure": "KED", "missing": "No eGFR and uACR in 2026", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-1003", "quote": "eGFR 91", "kind": "numerator"},
                                               {"doc": "D-1003", "quote": "22 mg/g", "kind": "numerator"}], "traps": []}},
    {"gap_id": "G-0019", "member_id": "M-011", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-07-02", "value": "146/88", "code": "CPT-II 3077F, 3079F", "source": "claim"},
     "expected": {"status": "Closed", "find": [{"doc": "D-1101", "quote": "BP 132/78", "kind": "numerator"}], "traps": ["around 125/80", "Triage BP 128/80"]}},
    {"gap_id": "G-0020", "member_id": "M-011", "measure": "BPD", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-07-02", "value": "146/88", "code": "CPT-II 3077F, 3079F", "source": "claim"},
     "expected": {"status": "Closed", "find": [{"doc": "D-1101", "quote": "BP 132/78", "kind": "numerator"}], "traps": ["around 125/80", "Triage BP 128/80"]}},
    {"gap_id": "G-0021", "member_id": "M-011", "measure": "SPD", "missing": "No statin dispensed in 2026", "last_event": None,
     "expected": {"status": "Excluded", "find": [{"doc": "D-1104", "quote": "severe myalgia", "kind": "exclusion"}], "traps": ["patient declined"]}},
    {"gap_id": "G-0022", "member_id": "M-012", "measure": "SPD", "missing": "No statin dispensed in 2026", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": ["patient declined", "atorvastatin 20 mg daily"],
                  "note": "A statin on the medication list is not a pharmacy fill, and declining is not an exclusion."}},
    {"gap_id": "G-0023", "member_id": "M-012", "measure": "EED", "missing": "No retinal eye exam on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-1202", "quote": "Autonomous AI retinal exam", "kind": "numerator"}], "traps": []}},
    {"gap_id": "G-0024", "member_id": "M-013", "measure": "SPD", "missing": "No statin dispensed in 2026", "last_event": None,
     "expected": {"status": "Excluded", "find": [{"doc": "D-1301", "quote": "14 weeks gestation", "kind": "exclusion"}], "traps": []}},
    {"gap_id": "G-0025", "member_id": "M-014", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-05-20", "value": "148/90", "code": "LOINC 8480-6/8462-4", "source": "ehr"},
     "expected": {"status": "Closed", "find": [{"doc": "D-1401", "quote": "134/82", "kind": "numerator"}], "traps": [],
                  "note": "Handwritten note; the finding must be flagged handwritten."}},
    {"gap_id": "G-0026", "member_id": "M-015", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-04-02", "value": "152/84", "code": "LOINC 8480-6/8462-4", "source": "ehr"},
     "expected": {"status": "Excluded", "find": [{"doc": "D-1501", "quote": "hemodialysis", "kind": "exclusion"}], "traps": [],
                  "note": "Faxed TIFF from the dialysis center."}},
    {"gap_id": "G-0027", "member_id": "M-016", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-03-15", "value": "150/92", "code": "LOINC 8480-6/8462-4", "source": "ehr"},
     "expected": {"status": "Open", "find": [], "traps": ["BP 138/90", "128/78"],
                  "note": "138/90 fails the diastolic limit; the 128/78 reading has no date."}},
    {"gap_id": "G-0028", "member_id": "M-017", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-02-20", "value": "144/92", "code": "LOINC 8480-6/8462-4", "source": "ehr"},
     "expected": {"status": "Excluded", "find": [{"doc": "D-1701", "quote": "pregnancy confirmed", "kind": "exclusion"}], "traps": [],
                  "note": "Poorly lit phone photo."}},
    {"gap_id": "G-0029", "member_id": "M-018", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Excluded", "find": [{"doc": "D-1801", "quote": "total colectomy", "kind": "exclusion"}], "traps": [],
                  "note": "The document is from 2020: exclusions count at any time, so the date filter must not drop it."}},
    {"gap_id": "G-0030", "member_id": "M-019", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Closed", "find": [{"doc": "D-1901", "quote": "Flexible sigmoidoscopy performed 2023-05-16", "kind": "numerator"}], "traps": []}},
    {"gap_id": "G-0031", "member_id": "M-020", "measure": "AAB", "missing": "Antibiotic dispensed within 3 days of an acute bronchitis visit",
     "last_event": None, "episode": {"visit_date": "2026-02-10", "antibiotic": "Amoxicillin 250 MG Oral Capsule", "dispensed": "2026-02-10"},
     "expected": {"status": "Open", "find": [], "traps": ["requested antibiotics"]}},
    {"gap_id": "G-0032", "member_id": "M-021", "measure": "AAB", "missing": "Antibiotic dispensed within 3 days of an acute bronchitis visit",
     "last_event": None, "episode": {"visit_date": "2026-03-05", "antibiotic": "Azithromycin 250 MG Oral Tablet", "dispensed": "2026-03-06"},
     "expected": {"status": "Excluded", "find": [{"doc": "D-2101", "quote": "Admitted to hospice care", "kind": "exclusion"}], "traps": []}},
    {"gap_id": "G-0033", "member_id": "M-022", "measure": "AAB", "missing": "Antibiotic dispensed within 3 days of an acute bronchiolitis visit",
     "last_event": None, "episode": {"visit_date": "2025-12-15", "antibiotic": "Amoxicillin 250 MG Oral Capsule", "dispensed": "2025-12-15"},
     "expected": {"status": "Open", "find": [], "traps": ["acute otitis media"],
                  "note": "A competing diagnosis in a note is information only; supplemental data counts for required exclusions only."}},
    {"gap_id": "G-0034", "member_id": "M-023", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents. Low priority, mailed FIT kit action."}},
    {"gap_id": "G-0035", "member_id": "M-023", "measure": "BCS-E", "missing": "No mammogram from 2024-10-01 to 2026-12-31", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents."}},
    {"gap_id": "G-0036", "member_id": "M-024", "measure": "GSD", "missing": "No HbA1c or GMI result in 2026", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents, no PCP, no contact in 18 months."}},
    {"gap_id": "G-0037", "member_id": "M-024", "measure": "EED", "missing": "No retinal eye exam on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents."}},
    {"gap_id": "G-0038", "member_id": "M-024", "measure": "KED", "missing": "No eGFR and uACR in 2026", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents."}},
    {"gap_id": "G-0041", "member_id": "M-024", "measure": "BPD", "missing": "No blood pressure reading in 2026", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents; member has had no contact since 2025-04."}},
    {"gap_id": "G-0042", "member_id": "M-024", "measure": "SPD", "missing": "No statin dispensed in 2026", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents."}},
    {"gap_id": "G-0043", "member_id": "M-024", "measure": "COL-E", "missing": "No colorectal cancer screening on record", "last_event": None,
     "expected": {"status": "Open", "find": [], "traps": [], "note": "No documents."}},
    {"gap_id": "G-0039", "member_id": "M-025", "measure": "GSD", "missing": "Most recent HbA1c or GMI in 2026 is not under 8.0%",
     "last_event": {"type": "lab_result", "date": "2026-03-01", "value": "8.3%", "code": "LOINC 4548-4", "source": "lab_feed"},
     "expected": {"status": "Closed (structured)", "find": [], "traps": [],
                  "structured": {"file": "riverside_lab_export_2026-10.csv", "row": 1, "value": "7.1 %", "date": "2026-09-02"},
                  "note": "Closed by the CSV lab export, without AI."}},
    {"gap_id": "G-0040", "member_id": "M-026", "measure": "CBP", "missing": "Most recent BP in 2026 is not under 140/90",
     "last_event": {"type": "blood_pressure", "date": "2026-04-15", "value": "145/91", "code": "LOINC 8480-6/8462-4", "source": "ehr"},
     "expected": {"status": "Closed (structured)", "find": [], "traps": ["~125/80"],
                  "structured": {"file": "lakeside_vitals_2026Q3.xlsx", "row": 1, "value": "136/84", "date": "2026-08-30"},
                  "note": "Closed by the Excel vitals row. The free-text notes row is an AI trap."}},
]

# format: txt | pdf (digital, real text layer) | scan_pdf (image-only PDF) | photo_jpg | handwritten_jpg | docx | tiff_fax | png
# member_id None means the file has no member ID and must go through member matching.
DOCUMENTS = [
    {"doc_id": "D-101", "member_id": "M-001", "type": "clinic note", "date": "2026-04-10", "org": "ORG-RFC", "author": "PRV-001", "format": "pdf",
     "text": "Progress Note\nDate of service: 2026-04-10\nPatient: Robert Hayes  DOB: 03/14/1968  Member ID: M-001\n\n"
             "Reason for visit: annual physical.\nHistory: GERD, hyperlipidemia. Colonoscopy 2019, Dr. Xu, no polyps.\n"
             "Family history: father had colon cancer, colonoscopy 2020.\n"
             "Plan: discussed repeat colonoscopy in 2029. Continue atorvastatin. Return in 6 months."},
    {"doc_id": "D-102", "member_id": "M-001", "type": "HIE record", "date": "2026-06-01", "org": "ORG-HIE", "author": None, "format": "txt",
     "text": "Referral summary (State HIE)\nPatient: Robert Hayes  DOB: 1968-03-14\nColonoscopy recommended; patient to schedule with GI."},
    {"doc_id": "D-201", "member_id": "M-002", "type": "clinic note", "date": "2026-03-18", "org": "ORG-RFC", "author": "PRV-001", "format": "txt",
     "text": "Progress Note 2026-03-18\nPatient: Linda Moore  DOB: 07/02/1965  Member ID: M-002\n"
             "Colorectal screening: FIT kit mailed 2026-02-04; not returned yet. Prior colonoscopy 2014, normal.\n"
             "Plan: reminder call, offer colonoscopy referral."},
    {"doc_id": "D-202", "member_id": "M-002", "type": "radiology report", "date": "2025-11-03", "org": "ORG-SIM", "author": "PRV-011", "format": "scan_pdf",
     "text": "SPRINGFIELD IMAGING CENTER\nPatient: Linda Moore  DOB: 07/02/1965  Member ID: M-002\n"
             "Exam: Screening mammogram 2025-11-03, bilateral, with tomosynthesis.\n"
             "Findings: scattered fibroglandular density. No suspicious mass or calcification.\n"
             "Impression: BI-RADS 1, negative. Routine screening in 12 months.\nRead by: Dr. Grace Kim"},
    {"doc_id": "D-301", "member_id": "M-003", "type": "clinic note", "date": "2026-05-06", "org": "ORG-WHA", "author": "PRV-004", "format": "photo_jpg",
     "text": "Women's Health Associates - Visit Note 05/06/2026\nPatient: Patricia Gomez  DOB 01/22/1959  ID M-003\n"
             "Past surgical history: bilateral mastectomy 2015 for breast cancer.\n"
             "Doing well. No new complaints. Continue annual follow-up with oncology."},
    {"doc_id": "D-302", "member_id": "M-003", "type": "lab report", "date": "2025-08-14", "org": "ORG-SDX", "author": None, "format": "pdf",
     "text": "STOOL DNA DIAGNOSTICS LAB - RESULT REPORT\nPatient: Patricia Gomez  DOB: 01/22/1959  Member ID: M-003\n"
             "Test: Cologuard (multitarget stool DNA with FIT)\nCollected: 2025-08-11   Resulted: 2025-08-14\nResult: NEGATIVE"},
    {"doc_id": "D-401", "member_id": "M-004", "type": "clinic note", "date": "2026-07-21", "org": "ORG-WHA", "author": "PRV-004", "format": "docx",
     "text": "Visit Note 2026-07-21\nPatient: Karen White  DOB: 05/30/1981  Member ID: M-004\n"
             "Breast ultrasound 2026-07-02 for a palpable lump: simple benign cyst.\n"
             "Screening mammogram ordered; patient will schedule."},
    {"doc_id": "D-402", "member_id": "M-004", "type": "clinic note", "date": "2026-02-02", "org": "ORG-WHA", "author": "PRV-004", "format": "txt",
     "text": "Gynecology Visit 2026-02-02\nPatient: Karen White  DOB: 05/30/1981  Member ID: M-004\n"
             "Surgical history: supracervical hysterectomy 2015 for fibroids, cervix retained.\n"
             "Pap smear 03/2024, NILM. Next cervical screening due 2027."},
    {"doc_id": "D-501", "member_id": "M-005", "type": "letter", "date": "2026-01-15", "org": "ORG-WHA", "author": "PRV-004", "format": "docx",
     "text": "Women's Health Associates\n2026-01-15\nRe: Maria Lopez  DOB: 09/12/1988  Member ID: M-005\n\n"
             "Results summary: high-risk HPV test 2023-06-14, negative. Cytology not performed with this test.\n"
             "Recommended next screening: 2028."},
    {"doc_id": "D-502", "member_id": None, "type": "clinic note", "date": "2026-02-20", "org": "ORG-PRT", "author": None, "format": "photo_jpg",
     "member_hint": {"name": "Maria Lopez", "dob": "1988-09-12", "expected_match": "M-005"},
     "text": "Outside record (uploaded by member)\nName: Maria Lopez   DOB: 09/12/1988\nPap smear 2021, normal."},
    {"doc_id": "D-601", "member_id": "M-006", "type": "clinic note", "date": "2026-03-09", "org": "ORG-ECH", "author": "PRV-005", "format": "handwritten_jpg",
     "text": "Susan Clark  M-006  3/9/26\nPSH: Total abdominal hysterectomy 2012 (fibroids), cervix removed.\nNo GYN complaints today."},
    {"doc_id": "D-701", "member_id": "M-007", "type": "clinic note", "date": "2026-08-05", "org": "ORG-ECH", "author": "PRV-005", "format": "txt",
     "text": "Progress Note 2026-08-05\nPatient: Ashley Brown  DOB: 04/25/1997  Member ID: M-007\n"
             "Pap due; patient will schedule.\nPatient reports she had a Pap a few years ago at another clinic; no record available."},
    {"doc_id": "D-801", "member_id": "M-008", "type": "lab report", "date": "2026-08-20", "org": "ORG-LAB", "author": None, "format": "pdf",
     "text": "COMMONWEALTH REFERENCE LABORATORY\nPatient: Dorothy Evans  DOB: 11/20/1955  Member ID: M-008\n"
             "Hemoglobin A1c: 7.6 % (collected 2026-08-20)\nReference range: 4.0 to 5.6 %"},
    {"doc_id": "D-802", "member_id": "M-008", "type": "clinic note", "date": "2026-09-05", "org": "ORG-NEN", "author": "PRV-003", "format": "txt",
     "text": "Endocrinology Follow-up 2026-09-05\nPatient: Dorothy Evans  Member ID: M-008\n"
             "A1c ordered today. Patient says her last A1c was around 7."},
    {"doc_id": "D-803", "member_id": "M-008", "type": "clinic note", "date": "2026-09-05", "org": "ORG-NEN", "author": "PRV-003", "format": "txt",
     "text": "Functional status 2026-09-05\nPatient: Dorothy Evans  Member ID: M-008\n"
             "Uses a walker, two falls this year. Advanced Alzheimer dementia, on donepezil."},
    {"doc_id": "D-804", "member_id": "M-008", "type": "eye exam letter", "date": "2026-06-17", "org": "ORG-CEA", "author": "PRV-007", "format": "docx",
     "text": "Clearview Eye Associates\n2026-06-17\nRe: Dorothy Evans  DOB: 11/20/1955  Member ID: M-008\n\n"
             "Dilated fundus examination performed 2026-06-17 by Dr. Priya Nair, OD.\n"
             "No diabetic retinopathy OU. Recommend annual diabetic eye exam."},
    {"doc_id": "D-805", "member_id": "M-008", "type": "lab report", "date": "2026-08-21", "org": "ORG-HIE", "author": None, "format": "pdf",
     "duplicate_of": "D-801",
     "text": "COMMONWEALTH REFERENCE LABORATORY (via State HIE)\nPatient: Dorothy Evans  DOB: 11/20/1955  Member ID: M-008\n"
             "Hemoglobin A1c: 7.6 % (collected 2026-08-20)\nReference range: 4.0 to 5.6 %"},
    {"doc_id": "D-901", "member_id": "M-009", "type": "lab report", "date": "2026-05-10", "org": "ORG-LAB", "author": None, "format": "pdf",
     "text": "COMMONWEALTH REFERENCE LABORATORY\nPatient: James Carter  DOB: 02/11/1960  Member ID: M-009\n"
             "Collected 2026-05-10\neGFR 68 mL/min/1.73m2 (CKD-EPI).\nUrine albumin 12 mg/L."},
    {"doc_id": "D-902", "member_id": "M-009", "type": "lab report", "date": "2026-05-16", "org": "ORG-LAB", "author": None, "format": "pdf",
     "text": "COMMONWEALTH REFERENCE LABORATORY\nPatient: James Carter  DOB: 02/11/1960  Member ID: M-009\n"
             "Collected 2026-05-16\nUrine creatinine 110 mg/dL."},
    {"doc_id": "D-903", "member_id": "M-009", "type": "vitals flowsheet", "date": "2026-09-30", "org": "ORG-LIM", "author": "PRV-002", "format": "photo_jpg",
     "table": {"title": "Lakeside Internal Medicine - Vitals Flowsheet - James Carter (M-009)",
               "columns": ["Date", "BP", "Pulse", "Weight (lb)"],
               "rows": [["2026-06-12", "142/86", "78", "201"], ["2026-08-19", "128/76", "74", "199"], ["2026-09-30", "131/79", "72", "198"]]},
     "text": "Lakeside Internal Medicine - Vitals Flowsheet - James Carter (M-009)\n"
             "Date | BP | Pulse | Weight (lb)\n2026-06-12 | 142/86 | 78 | 201\n2026-08-19 | 128/76 | 74 | 199\n2026-09-30 | 131/79 | 72 | 198"},
    {"doc_id": "D-1001", "member_id": "M-010", "type": "clinic note", "date": "2026-04-14", "org": "ORG-NEN", "author": "PRV-003", "format": "txt",
     "text": "Endocrinology Visit 2026-04-14\nPatient: Nancy Allen  DOB: 06/08/1974  Member ID: M-010\n"
             "Referred to ophthalmology for diabetic eye exam.\nVisual acuity 20/30 OU checked today in clinic."},
    {"doc_id": "D-1002", "member_id": "M-010", "type": "clinic note", "date": "2026-09-18", "org": "ORG-NEN", "author": "PRV-003", "format": "txt",
     "text": "Endocrinology Follow-up 2026-09-18\nPatient: Nancy Allen  Member ID: M-010\n"
             "HbA1c 8.4% (2026-09-15), improving from 8.9%. Increase metformin."},
    {"doc_id": "D-1003", "member_id": "M-010", "type": "lab report", "date": "2026-04-22", "org": "ORG-LAB", "author": None, "format": "pdf",
     "text": "COMMONWEALTH REFERENCE LABORATORY\nPatient: Nancy Allen  DOB: 06/08/1974  Member ID: M-010\n"
             "Collected 2026-04-22\neGFR 91 mL/min/1.73m2 (CKD-EPI).\nUrine albumin/creatinine ratio (uACR): 22 mg/g."},
    {"doc_id": "D-1101", "member_id": "M-011", "type": "clinic note", "date": "2026-09-14", "org": "ORG-LIM", "author": "PRV-002", "format": "txt",
     "text": "Progress Note 2026-09-14\nPatient: Michael Turner  DOB: 08/02/1963  Member ID: M-011\n"
             "Follow-up for hypertension and diabetes. Vitals: BP 132/78, HR 72. Tolerating lisinopril. Continue current plan."},
    {"doc_id": "D-1102", "member_id": "M-011", "type": "portal upload", "date": "2026-10-01", "org": "ORG-PRT", "author": None, "format": "png",
     "text": "Home BP log (uploaded by member)\nMichael Turner  M-011\nPatient reports readings around 125/80 most mornings."},
    {"doc_id": "D-1103", "member_id": "M-011", "type": "ED note", "date": "2026-08-01", "org": "ORG-CHO", "author": "PRV-009", "format": "pdf",
     "text": "COUNTY GENERAL HOSPITAL - EMERGENCY DEPARTMENT\nPatient: Michael Turner  DOB: 08/02/1963  Member ID: M-011\n"
             "Seen 2026-08-01 for ankle sprain. Triage BP 128/80. Discharged home."},
    {"doc_id": "D-1104", "member_id": "M-011", "type": "clinic note", "date": "2026-04-12", "org": "ORG-LIM", "author": "PRV-002", "format": "txt",
     "text": "Progress Note 2026-04-12\nPatient: Michael Turner  Member ID: M-011\n"
             "Stopped atorvastatin in 2025 due to severe myalgia. Discussed rosuvastatin; patient declined."},
    {"doc_id": "D-1201", "member_id": "M-012", "type": "clinic note", "date": "2026-05-28", "org": "ORG-ECH", "author": "PRV-005", "format": "txt",
     "text": "Progress Note 2026-05-28\nPatient: David Wilson  DOB: 12/19/1978  Member ID: M-012\n"
             "Med list: metformin 1000 mg twice daily; atorvastatin 20 mg daily (patient states he is not taking it).\n"
             "Statin therapy recommended; patient declined."},
    {"doc_id": "D-1202", "member_id": "M-012", "type": "eye exam report", "date": "2026-07-08", "org": "ORG-ECH", "author": None, "format": "pdf",
     "text": "EASTSIDE COMMUNITY HEALTH - DIABETIC EYE SCREENING\nPatient: David Wilson  DOB: 12/19/1978  Member ID: M-012\n"
             "Autonomous AI retinal exam performed 2026-07-08.\nResult: Negative for more-than-mild diabetic retinopathy. Rescreen in 12 months."},
    {"doc_id": "D-1301", "member_id": "M-013", "type": "prenatal note", "date": "2026-08-12", "org": "ORG-WHA", "author": "PRV-004", "format": "txt",
     "text": "Prenatal Visit 2026-08-12\nPatient: Jennifer Lee  DOB: 10/05/1982  Member ID: M-013\n"
             "G2P1 at 14 weeks gestation. Type 2 diabetes managed with insulin during pregnancy."},
    {"doc_id": "D-1401", "member_id": "M-014", "type": "clinic note", "date": "2026-09-22", "org": "ORG-RFC", "author": "PRV-001", "format": "handwritten_jpg",
     "text": "Charles Young  M-014\n9/22/26\nBP 134/82 L arm seated\nHR 70  feels well\ncont. amlodipine"},
    {"doc_id": "D-1501", "member_id": "M-015", "type": "dialysis center letter", "date": "2026-06-30", "org": "ORG-MDC", "author": "PRV-010", "format": "tiff_fax",
     "text": "METRO DIALYSIS CENTER  FAX\nTo: Lakeside Internal Medicine   Date: 2026-06-30\nRe: Betty King  DOB: 04/17/1946  Member ID: M-015\n\n"
             "Ms. King receives in-center hemodialysis three times weekly for end-stage renal disease (ESRD), started 2025-11-03.\n"
             "Please send updated medication list.\nDr. Omar Haddad, Nephrology"},
    {"doc_id": "D-1601", "member_id": "M-016", "type": "clinic note", "date": "2026-10-02", "org": "ORG-ECH", "author": "PRV-005", "format": "txt",
     "text": "Progress Note 2026-10-02\nPatient: Thomas Wright  DOB: 03/09/1967  Member ID: M-016\nBP 138/90 today. Increase lisinopril to 20 mg."},
    {"doc_id": "D-1602", "member_id": "M-016", "type": "outside record", "date": "2026-06-11", "org": "ORG-HIE", "author": None, "format": "txt",
     "text": "Outside records summary (State HIE)\nPatient: Thomas Wright  DOB: 1967-03-09\nOutside records list a BP of 128/78; date not recorded."},
    {"doc_id": "D-1701", "member_id": "M-017", "type": "clinic note", "date": "2026-05-19", "org": "ORG-WHA", "author": "PRV-004", "format": "photo_jpg",
     "photo_quality": "poor_light",
     "text": "OB Intake 05/19/2026\nPatient: Emily Scott  DOB 02/14/1992  ID M-017\nPregnancy confirmed, 9 weeks gestation. Chronic hypertension, on labetalol."},
    {"doc_id": "D-1801", "member_id": "M-018", "type": "discharge summary", "date": "2020-03-02", "org": "ORG-CHO", "author": None, "format": "pdf",
     "text": "COUNTY GENERAL HOSPITAL - DISCHARGE SUMMARY\nPatient: George Adams  DOB: 06/23/1951  Member ID: M-018\n"
             "Admitted 2020-02-25  Discharged 2020-03-02\nDiagnosis: adenocarcinoma of the sigmoid colon.\n"
             "Procedure: total colectomy 2020-02-27. Uneventful recovery."},
    {"doc_id": "D-1901", "member_id": "M-019", "type": "specialist letter", "date": "2026-03-03", "org": "ORG-CGI", "author": "PRV-008", "format": "docx",
     "text": "Central Gastroenterology\n2026-03-03\nRe: Kevin Baker  DOB: 01/15/1976  Member ID: M-019\n\n"
             "Flexible sigmoidoscopy performed 2023-05-16; no polyps. Next screening due 2028."},
    {"doc_id": "D-2001", "member_id": "M-020", "type": "urgent care note", "date": "2026-02-10", "org": "ORG-VUC", "author": "PRV-006", "format": "txt",
     "text": "Valley Urgent Care 2026-02-10\nPatient: Brian Nelson  DOB: 09/01/1984  Member ID: M-020\n"
             "Dx: acute bronchitis. Lungs clear, no fever. Patient requested antibiotics; amoxicillin prescribed."},
    {"doc_id": "D-2101", "member_id": "M-021", "type": "hospice admission note", "date": "2026-04-20", "org": "ORG-HOS", "author": None, "format": "scan_pdf",
     "text": "COMFORT HOSPICE SERVICES\nPatient: Helen Rivera  DOB: 03/27/1958  Member ID: M-021\n"
             "Admitted to hospice care 2026-04-18. Primary diagnosis: metastatic pancreatic cancer."},
    {"doc_id": "D-2201", "member_id": "M-022", "type": "clinic note", "date": "2025-12-15", "org": "ORG-ECH", "author": "PRV-005", "format": "txt",
     "text": "Pediatric Visit 2025-12-15\nPatient: Ethan Price  DOB: 05/11/2017  Member ID: M-022\n"
             "Acute bronchiolitis. Also left acute otitis media on exam. Amoxicillin started."},
    {"doc_id": "D-9001", "member_id": None, "type": "unknown", "date": "2026-09-09", "org": "ORG-PRT", "author": None, "format": "photo_jpg",
     "photo_quality": "blurry", "member_hint": {"name": "J. Smith", "dob": None, "expected_match": None},
     "text": "Name: J. Smith\nVisit 9/9/26\nBP 126/80"},
]

# Structured sample files. These are loaded as coded records, not read by the AI
# (except free-text columns). Row numbers are 1-based data rows.
STRUCTURED_FILES = [
    {"file": "riverside_lab_export_2026-10.csv", "format": "csv", "source_org": "ORG-RFC",
     "columns": ["Patient ID", "Patient Name", "DOB", "Test Name", "Result", "Units", "Collected", "Ordering Provider"],
     "rows": [
         {"values": ["M-025", "Ronald Ward", "04/04/1969", "Hemoglobin A1c", "7.1", "%", "09/02/2026", "Dr. Anita Patel"], "expect": "load", "closes": "G-0039"},
         {"values": ["M-001", "Robert Hayes", "03/14/1968", "LDL Cholesterol", "128", "mg/dL", "08/10/2026", "Dr. Anita Patel"], "expect": "load"},
         {"values": ["M-014", "Charles Young", "01/30/1954", "HbA1c", "6.1", "%", "07/14/2026", "Dr. Anita Patel"], "expect": "load"},
         {"values": ["M-025", "Ronald Ward", "04/04/1969", "A1C", "7.2", "mg/dL", "09/20/2026", "Dr. Anita Patel"], "expect": "reject", "reason": "unit mg/dL is not valid for HbA1c"},
         {"values": ["M-009", "James Carter", "02/11/1960", "eGFR", "70", "mL/min/1.73m2", "", "Dr. Marcus Reed"], "expect": "reject", "reason": "missing collection date"},
         {"values": ["M-999", "Unknown Person", "01/01/1970", "Hemoglobin A1c", "6.8", "%", "08/15/2026", "Dr. Anita Patel"], "expect": "reject", "reason": "member not found"},
     ]},
    {"file": "lakeside_vitals_2026Q3.xlsx", "format": "xlsx", "source_org": "ORG-LIM",
     "columns": ["Member ID", "Visit Date", "Systolic", "Diastolic", "Pulse", "Notes"],
     "rows": [
         {"values": ["M-026", "2026-08-30", 136, 84, 76, ""], "expect": "load", "closes": "G-0040"},
         {"values": ["M-026", "2026-09-25", None, 82, 74, ""], "expect": "reject", "reason": "systolic missing"},
         {"values": ["M-026", "2026-10-05", None, None, None, "Patient reports home readings ~125/80"], "expect": "free_text_to_ai",
          "reason": "notes column goes to AI extraction; approximate home readings are a trap"},
         {"values": ["M-016", "2026-10-02", 138, 90, 80, ""], "expect": "load", "note": "Loads but does not close G-0027 (diastolic 90)."},
     ]},
]

# Background population: procedurally generated members with structured data only (no documents).
# Non-compliant background members become gaps in the engine file and stay Open (outreach work).
BACKGROUND = {
    "count": 400,
    "seed": 2026,
    "first_member_number": 101,   # M-101 onwards
    "first_gap_number": 1001,     # G-1001 onwards
    "target_compliance": {        # share of eligible background members already compliant in structured data
        "COL-E": 0.72, "BCS-E": 0.75, "CCS-E": 0.68, "EED": 0.70, "GSD": 0.68,
        "BPD": 0.70, "KED": 0.48, "CBP": 0.66, "SPD": 0.78, "AAB": 0.65,
    },
    "prevalence": {"diabetes": 0.22, "hypertension": 0.38},
    "aab_episode_rate": 0.06,
}
