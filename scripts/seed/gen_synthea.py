"""Generate the Synthea-format patient data and the engine, member and expected-outcome files.

Usage (from the repo root):
    python scripts/seed/gen_synthea.py            # generate everything under data/seed
    python scripts/seed/gen_synthea.py --check    # verify the files already on disk

Everything comes from scripts/seed/scenario.py (26 scenario members and 40 scenario gaps) plus a
seeded background population. Output is deterministic: fixed seed, uuid5 identifiers, no wall clock.

Design rule: a scenario gap's evidence (and any exclusion) must exist only in documents. The generator
creates the structured facts listed in each scenario member's `conditions` and `clinical` lists, plain
scaffolding, and compliant data for the measures where a scenario member is eligible but has no gap.
"""

import argparse
import csv
import hashlib
import json
import random
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenario as sc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seed"
CSV_DIR = SEED_DIR / "synthea" / "csv"
ENGINE_FILE = SEED_DIR / "engine" / "engine_run_MY2026.json"
MEMBERS_DIR = SEED_DIR / "members"
EXPECTED_FILE = SEED_DIR / "expected" / "expected_findings.json"
MEASURES_FILE = SEED_DIR / "measures" / "measures.yaml"

# Internal row keys, one list per table, in the same column order as DISK_HEADERS below. Rows are dicts
# keyed by these names; write_csv and read_csv translate to and from the real file headers by position.
HEADERS = {
    "patients": ["Id", "BirthDate", "DeathDate", "SSN", "Drivers", "Passport", "Prefix", "First", "Middle", "Last",
                 "Suffix", "Maiden", "Marital", "Race", "Ethnicity", "Gender", "BirthPlace", "Address", "City",
                 "State", "County", "FIPS County Code", "Zip", "Lat", "Lon", "Healthcare_Expenses",
                 "Healthcare_Coverage", "Income"],
    "organizations": ["Id", "Name", "Address", "City", "State", "Zip", "Lat", "Lon", "Phone", "Revenue",
                      "Utilization", "NPI"],
    "providers": ["Id", "Organization", "Name", "Gender", "Speciality", "Address", "City", "State", "Zip", "Lat",
                  "Lon", "Encounters", "Procedures", "NPI"],
    "payers": ["Id", "Name", "Ownership", "Address", "City", "State_Headquartered", "Zip", "Phone",
               "Amount_Covered", "Amount_Uncovered", "Revenue", "Covered_Encounters", "Uncovered_Encounters",
               "Covered_Medications", "Uncovered_Medications", "Covered_Procedures", "Uncovered_Procedures",
               "Covered_Immunizations", "Uncovered_Immunizations", "Unique_Customers", "QOLS_Avg",
               "Member_Months"],
    "payer_transitions": ["Patient", "Member ID", "Start_Year", "End_Year", "Payer", "Secondary Payer",
                          "Ownership", "Owner Name"],
    "encounters": ["Id", "Start", "Stop", "Patient", "Organization", "Provider", "Payer", "EncounterClass", "Code",
                   "Description", "Base_Encounter_Cost", "Total_Claim_Cost", "Payer_Coverage", "ReasonCode",
                   "ReasonDescription"],
    "conditions": ["Start", "Stop", "Patient", "Encounter", "System", "Code", "Description"],
    "observations": ["Date", "Patient", "Encounter", "Category", "Code", "Description", "Value", "Units", "Type"],
    "procedures": ["Start", "Stop", "Patient", "Encounter", "System", "Code", "Description", "Base_Cost",
                   "ReasonCode", "ReasonDescription"],
    "medications": ["Start", "Stop", "Patient", "Payer", "Encounter", "Code", "Description", "Base_Cost",
                    "Payer_Coverage", "Dispenses", "TotalCost", "ReasonCode", "ReasonDescription"],
}
# Output headers exactly as Synthea 3.x writes them: CSVConstants.HEADER_LINE_MAP in
# src/main/java/org/mitre/synthea/export/CSVConstants.java (synthetichealth/synthea, master branch).
DISK_HEADERS = {
    "patients": "Id,BIRTHDATE,DEATHDATE,SSN,DRIVERS,PASSPORT,PREFIX,FIRST,MIDDLE,LAST,SUFFIX,MAIDEN,MARITAL,RACE,"
                "ETHNICITY,GENDER,BIRTHPLACE,ADDRESS,CITY,STATE,COUNTY,FIPS,ZIP,LAT,LON,HEALTHCARE_EXPENSES,"
                "HEALTHCARE_COVERAGE,INCOME",
    "organizations": "Id,NAME,ADDRESS,CITY,STATE,ZIP,LAT,LON,PHONE,REVENUE,UTILIZATION,NPI",
    "providers": "Id,ORGANIZATION,NAME,GENDER,SPECIALITY,ADDRESS,CITY,STATE,ZIP,LAT,LON,ENCOUNTERS,PROCEDURES,NPI",
    "payers": "Id,NAME,OWNERSHIP,ADDRESS,CITY,STATE_HEADQUARTERED,ZIP,PHONE,AMOUNT_COVERED,AMOUNT_UNCOVERED,REVENUE,"
              "COVERED_ENCOUNTERS,UNCOVERED_ENCOUNTERS,COVERED_MEDICATIONS,UNCOVERED_MEDICATIONS,COVERED_PROCEDURES,"
              "UNCOVERED_PROCEDURES,COVERED_IMMUNIZATIONS,UNCOVERED_IMMUNIZATIONS,UNIQUE_CUSTOMERS,QOLS_AVG,"
              "MEMBER_MONTHS",
    "payer_transitions": "PATIENT,MEMBERID,START_DATE,END_DATE,PAYER,SECONDARY_PAYER,PLAN_OWNERSHIP,OWNER_NAME",
    "encounters": "Id,START,STOP,PATIENT,ORGANIZATION,PROVIDER,PAYER,ENCOUNTERCLASS,CODE,DESCRIPTION,"
                  "BASE_ENCOUNTER_COST,TOTAL_CLAIM_COST,PAYER_COVERAGE,REASONCODE,REASONDESCRIPTION",
    "conditions": "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION",
    "observations": "DATE,PATIENT,ENCOUNTER,CATEGORY,CODE,DESCRIPTION,VALUE,UNITS,TYPE",
    "procedures": "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION,BASE_COST,REASONCODE,REASONDESCRIPTION",
    "medications": "START,STOP,PATIENT,PAYER,ENCOUNTER,CODE,DESCRIPTION,BASE_COST,PAYER_COVERAGE,DISPENSES,TOTALCOST,"
                   "REASONCODE,REASONDESCRIPTION",
}
DISK_HEADERS = {k: v.split(",") for k, v in DISK_HEADERS.items()}
assert all(len(HEADERS[k]) == len(v) for k, v in DISK_HEADERS.items())
SORT_COL = {"encounters": "Start", "conditions": "Start", "observations": "Date", "procedures": "Start",
            "medications": "Start"}
CHART_TABLES = ["encounters", "conditions", "observations", "procedures", "medications"]
MEMBER_FILES = {
    "members": ["member_id", "synthea_patient_id", "first", "last", "sex", "birth_date", "city", "state",
                "product_line", "pcp_org_id"],
    "organizations": ["id", "name", "kind", "city", "state", "closure_rate"],
    "providers": ["id", "name", "org_id", "specialty"],
    "engagement": ["member_id", "next_visit_date", "last_contact_date", "attributed_org_id"],
}

MY_START, MY_END = date(2026, 1, 1), date(2026, 12, 31)
RUN_DATE = date(2026, 10, 12)
LAST_DATA = date(2026, 10, 9)  # latest date any generated clinical event may carry
NS = uuid.uuid5(uuid.NAMESPACE_DNS, "hedis-poc.kanini.demo")
PAYER_NAME = "Kanini Demo Health Plan"
SNOMED = "SNOMED-CT"

# Code sets. SNOMED for conditions/procedures, LOINC for observations, RxNorm for medications.
DIABETES = {"44054006", "46635009"}
HTN = {"59621000"}
BRONCH = {"10509002", "4120002"}
COLONOSCOPY, MAMMOGRAM, PAP, EYE_EXAM = "73761001", "71651007", "169230002", "252779009"
LOINC_FIT, LOINC_HPV = "57905-2", "21440-3"
LOINC_A1C, LOINC_SBP, LOINC_DBP, LOINC_EGFR, LOINC_UACR = "4548-4", "8480-6", "8462-4", "33914-3", "14959-1"
STATINS = {"617312": "atorvastatin 40 MG Oral Tablet", "314231": "simvastatin 10 MG Oral Tablet",
           "859419": "rosuvastatin calcium 10 MG Oral Tablet"}
ANTIBIOTICS = {"308182": "Amoxicillin 250 MG Oral Capsule", "308460": "Azithromycin 250 MG Oral Tablet"}
METFORMIN = ("860975", "metformin 500 MG Oral Tablet")
LISINOPRIL = ("314076", "lisinopril 10 MG Oral Tablet")
ACETAMINOPHEN = ("313782", "Acetaminophen 325 MG Oral Tablet")
MED_REASON = {"860975": ("44054006", "Diabetes mellitus type 2"), "314076": ("59621000", "Essential hypertension")}
PROC_COST = {COLONOSCOPY: 2800.00, MAMMOGRAM: 215.00, PAP: 95.00, EYE_EXAM: 125.00}
DX = {"44054006": "Diabetes mellitus type 2", "46635009": "Diabetes mellitus type 1",
      "59621000": "Essential hypertension", "10509002": "Acute bronchitis", "4120002": "Bronchiolitis"}

# Wording that would indicate exclusion evidence. None of it may appear in structured data.
FORBIDDEN_WORDS = ["mastectomy", "hysterectomy", "colectomy", "dialysis", "end-stage", "end stage", "esrd",
                   "pregnan", "gestation", "hospice", "palliative", "myalgia", "myositis", "rhabdo", "cirrhosis",
                   "colorectal cancer", "colon cancer", "carcinoma", "neoplasm", "transplant", "nephrectomy"]

# (class, code, description, base cost, minutes)
ENC_KINDS = {
    "wellness": ("wellness", "162673000", "General examination of patient", 136.80, 30),
    "office": ("ambulatory", "185349003", "Encounter for check up", 129.16, 20),
    "symptom": ("ambulatory", "185345009", "Encounter for symptom", 129.16, 20),
    "urgent": ("urgentcare", "702927004", "Urgent care clinic", 160.00, 30),
}

CITIES = {  # city: (zip prefix, county, FIPS, lat, lon)
    "Springfield": ("011", "Hampden County", "25013", 42.1015, -72.5898),
    "Worcester": ("016", "Worcester County", "25027", 42.2626, -71.8023),
    "Lowell": ("018", "Middlesex County", "25017", 42.6334, -71.3162),
    "Brockton": ("023", "Plymouth County", "25023", 42.0834, -71.0184),
    "Boston": ("021", "Suffolk County", "25025", 42.3601, -71.0589),
}
PROVIDER_GENDER = {"PRV-001": "F", "PRV-002": "M", "PRV-003": "F", "PRV-004": "F", "PRV-005": "M", "PRV-006": "M",
                   "PRV-007": "F", "PRV-008": "M", "PRV-009": "F", "PRV-010": "M", "PRV-011": "F"}
BG_PCP_ORGS = ["ORG-RFC", "ORG-LIM", "ORG-WHA", "ORG-ECH", "ORG-NEN"]
BG_PCP_WEIGHTS = [30, 25, 15, 22, 8]


# ----------------------------------------------------------------------------- small helpers

def uid(kind: str, key: str) -> str:
    """Stable uuid5 identifier."""
    return str(uuid.uuid5(NS, f"{kind}|{key}"))


def pd(text: str) -> date:
    """Parse an ISO date or the date part of an ISO timestamp."""
    return date.fromisoformat(text[:10])


def add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def rdate(rng: random.Random, lo: date, hi: date) -> date:
    """Random date in [lo, hi]. Callers make sure lo <= hi."""
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def stamp(key: str, d: date) -> str:
    """Deterministic clinic-hours timestamp for a date."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return f"{d.isoformat()}T{8 + h % 9:02d}:{(h // 9) % 60:02d}:00Z"


def plus_minutes(ts: str, minutes: int) -> str:
    return (datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_source(m: dict, d: date, options: list[str]) -> str:
    h = int(hashlib.sha256(f"{m['member_id']}|{d}".encode()).hexdigest()[:6], 16)
    return options[h % len(options)]


def money(x: float) -> str:
    return f"{x:.2f}"


def office_org(m: dict) -> str:
    return m["pcp_org"] or "ORG-CHO"


def lab_org(m: dict) -> str:
    return m["pcp_org"] or "ORG-LAB"


def load_specs() -> list[dict]:
    return yaml.safe_load(MEASURES_FILE.read_text(encoding="utf-8"))["measures"]


def window(spec: dict, evidence: str) -> tuple[date, date]:
    """Window dates for an evidence type from measures.yaml (open ends become date.min / MY end)."""
    for w in spec["windows"]:
        if w["evidence_type"] == evidence:
            return (pd(w["from"]) if w["from"] else date.min), (pd(w["to"]) if w["to"] else MY_END)
    raise KeyError(evidence)


# ----------------------------------------------------------------------------- chart evaluation
# A chart is {table: [row dicts]} for one patient, with the Synthea header names as keys. The same
# evaluator runs on in-memory rows during generation and on CSV rows in --check.

def episodes(m: dict, chart: dict, spec: dict) -> list[dict]:
    """AAB episodes: bronchitis or bronchiolitis visits in the intake window, with any antibiotic fill."""
    lo, hi = window(spec, "bronchitis_visit_intake_period")
    out = []
    for e in chart["encounters"]:
        visit = pd(e["Start"])
        if e["ReasonCode"] not in BRONCH or not lo <= visit <= hi:
            continue
        if months_between(pd(m["birth_date"]), visit) < spec["population"]["age_min_months"]:
            continue
        fills = [r for r in chart["medications"] if r["Code"] in ANTIBIOTICS
                 and visit <= pd(r["Start"]) <= visit + timedelta(days=3)]
        out.append({"visit_date": visit, "encounter_id": e["Id"], "dx_code": e["ReasonCode"],
                    "dx_desc": e["ReasonDescription"], "fill": fills[0] if fills else None})
    return sorted(out, key=lambda x: x["visit_date"])


def is_eligible(spec: dict, m: dict, chart: dict) -> bool:
    pop = spec["population"]
    if spec["code"] == "AAB":
        return bool(episodes(m, chart, spec))
    age = MY_END.year - pd(m["birth_date"]).year
    if pop["age_min"] is not None and age < pop["age_min"]:
        return False
    if pop["age_max"] is not None and age > pop["age_max"]:
        return False
    if pop["sex"] == "female" and m["sex"] != "F":
        return False
    cond = pop["condition"]
    if cond:
        codes = DIABETES if "diabetes" in cond else HTN
        return any(c["Code"] in codes and pd(c["Start"]) <= MY_END for c in chart["conditions"])
    return True


def ev(kind: str, d: date, value: str, code: str, source: str) -> dict:
    return {"type": kind, "date": d.isoformat(), "value": value, "code": code, "source": source}


def bp_readings(chart: dict) -> dict:
    """Date -> (lowest systolic, lowest diastolic) across observations on that date."""
    sbp, dbp = defaultdict(list), defaultdict(list)
    for o in chart["observations"]:
        if o["Code"] == LOINC_SBP:
            sbp[pd(o["Date"])].append(float(o["Value"]))
        elif o["Code"] == LOINC_DBP:
            dbp[pd(o["Date"])].append(float(o["Value"]))
    return {d: (min(sbp[d]), min(dbp[d])) for d in sbp if d in dbp}


def last_proc_event(m: dict, procs: list[dict], desc: str):
    if not procs:
        return None
    p = max(procs, key=lambda r: r["Start"])
    return ev("procedure", pd(p["Start"]), desc, f"SNOMED {p['Code']}", pick_source(m, pd(p["Start"]), ["claim"]))


def evaluate(spec: dict, m: dict, chart: dict):
    """Return (compliant, missing_element, last_relevant_event) for one member and measure."""
    code = spec["code"]
    procs = lambda c: [p for p in chart["procedures"] if p["Code"] == c]  # noqa: E731
    obs = lambda c: [o for o in chart["observations"] if o["Code"] == c]  # noqa: E731
    birth = pd(m["birth_date"])
    if code == "COL-E":
        lo, hi = window(spec, "colonoscopy")
        flo, fhi = window(spec, "fobt_or_fit")
        ok = any(lo <= pd(p["Start"]) <= hi for p in procs(COLONOSCOPY)) or \
            any(flo <= pd(o["Date"]) <= fhi for o in obs(LOINC_FIT))
        return ok, "No colorectal cancer screening on record", last_proc_event(m, procs(COLONOSCOPY), "Colonoscopy")
    if code == "BCS-E":
        lo, hi = window(spec, "mammogram")
        ok = any(lo <= pd(p["Start"]) <= hi for p in procs(MAMMOGRAM))
        return ok, "No mammogram from 2024-10-01 to 2026-12-31", last_proc_event(m, procs(MAMMOGRAM), "Mammography")
    if code == "CCS-E":
        clo, chi = window(spec, "cervical_cytology")
        hlo, hhi = window(spec, "hrhpv_test_age_30_plus")
        ok = any(clo <= pd(p["Start"]) <= chi for p in procs(PAP)) or \
            any(hlo <= pd(o["Date"]) <= hhi and months_between(birth, pd(o["Date"])) >= 360 for o in obs(LOINC_HPV))
        return ok, "No cervical cytology or hrHPV test on record", \
            last_proc_event(m, procs(PAP), "Papanicolaou smear of cervix")
    if code == "EED":
        lo, hi = window(spec, "retinal_or_dilated_eye_exam")
        ok = any(lo <= pd(p["Start"]) <= hi for p in procs(EYE_EXAM))
        return ok, "No retinal eye exam on record", last_proc_event(m, procs(EYE_EXAM), "Retinal examination")
    if code == "GSD":
        lo, hi = window(spec, "hba1c")
        a1c = obs(LOINC_A1C)
        year = [o for o in a1c if lo <= pd(o["Date"]) <= hi]
        last = None
        if a1c:
            o = max(a1c, key=lambda r: (r["Date"], -float(r["Value"])))
            last = ev("lab_result", pd(o["Date"]), f"{o['Value']}%", "LOINC 4548-4", "lab_feed")
        if not year:
            return False, "No HbA1c or GMI result in 2026", last
        latest = max(pd(o["Date"]) for o in year)
        value = min(float(o["Value"]) for o in year if pd(o["Date"]) == latest)
        return value < spec["thresholds"]["hba1c_lt"], \
            "Most recent HbA1c or GMI in 2026 is not under 8.0%", last
    if code in ("BPD", "CBP"):
        lo, hi = window(spec, "blood_pressure_reading")
        readings = bp_readings(chart)
        last = None
        if readings:
            d = max(readings)
            s, dia = readings[d]
            last = ev("blood_pressure", d, f"{int(s)}/{int(dia)}", "LOINC 8480-6/8462-4",
                      pick_source(m, d, ["ehr", "claim"]))
        year = [d for d in readings if lo <= d <= hi]
        if not year:
            return False, "No blood pressure reading in 2026", last
        s, dia = readings[max(year)]
        th = spec["thresholds"]
        return s < th["systolic_lt"] and dia < th["diastolic_lt"], "Most recent BP in 2026 is not under 140/90", last
    if code == "KED":
        elo, ehi = window(spec, "egfr")
        ulo, uhi = window(spec, "uacr_ratio")
        e26 = [o for o in obs(LOINC_EGFR) if elo <= pd(o["Date"]) <= ehi]
        u26 = [o for o in obs(LOINC_UACR) if ulo <= pd(o["Date"]) <= uhi]
        labs = obs(LOINC_EGFR) + obs(LOINC_UACR)
        last = None
        if labs:
            o = max(labs, key=lambda r: r["Date"])
            last = ev("lab_result", pd(o["Date"]), f"{o['Value']} {o['Units']}", f"LOINC {o['Code']}", "lab_feed")
        missing = "No eGFR and uACR in 2026" if not e26 and not u26 else \
            ("No uACR in 2026" if e26 else "No eGFR in 2026")
        return bool(e26 and u26), missing, last
    if code == "SPD":
        lo = MY_START
        statins = [r for r in chart["medications"] if r["Code"] in STATINS and int(r["Dispenses"] or 0) > 0]
        active = [r for r in statins if pd(r["Start"]) <= MY_END and (not r["Stop"] or pd(r["Stop"]) >= lo)]
        last = None
        if statins:
            r = max(statins, key=lambda x: x["Start"])
            last = ev("medication", pd(r["Start"]), r["Description"], f"RxNorm {r['Code']}", "pharmacy_claim")
        return bool(active), "No statin dispensed in 2026", last
    raise KeyError(code)


def evaluate_member(specs: list[dict], m: dict, chart: dict) -> list[dict]:
    """All findings for one member: one per eligible measure (per episode for AAB)."""
    out = []
    for spec in specs:
        if spec["code"] == "AAB":
            for ep in episodes(m, chart, spec):
                word = "bronchiolitis" if ep["dx_code"] == "4120002" else "bronchitis"
                out.append({"spec": spec, "compliant": ep["fill"] is None, "episode": ep, "last_event": None,
                            "missing": f"Antibiotic dispensed within 3 days of an acute {word} visit"})
        elif is_eligible(spec, m, chart):
            ok, missing, last = evaluate(spec, m, chart)
            out.append({"spec": spec, "compliant": ok, "episode": None, "last_event": last, "missing": missing})
    return out


# ----------------------------------------------------------------------------- row builder

class Builder:
    """Collects Synthea rows. Rows are dicts keyed by the data dictionary headers."""

    def __init__(self) -> None:
        self.rows = {name: [] for name in HEADERS}
        self.by_patient = defaultdict(lambda: {t: [] for t in CHART_TABLES})
        self.enc_rows: dict[str, dict] = {}
        self.provider_of = {}
        for p in sc.PROVIDERS:
            self.provider_of.setdefault(p["org"], p["id"])
        self.payer_id = uid("payer", PAYER_NAME)

    def chart(self, m: dict) -> dict:
        return self.by_patient[m["patient_id"]]

    def _add(self, table: str, m: dict, row: dict) -> dict:
        full = {h: "" for h in HEADERS[table]}
        full.update({k: str(v) for k, v in row.items()})
        self.rows[table].append(full)
        if table in CHART_TABLES:
            self.by_patient[m["patient_id"]][table].append(full)
        return full

    def enc(self, m: dict, d: date, org: str, kind: str = "office", reason: tuple | None = None) -> str:
        """Encounter on a date at an org. One encounter per patient, date and org."""
        eid = uid("encounter", f"{m['member_id']}|{d}|{org}")
        if eid in self.enc_rows:
            row = self.enc_rows[eid]
            if reason:
                row["ReasonCode"], row["ReasonDescription"] = reason
            if kind in ("symptom", "urgent"):
                cls, code, desc, base, _ = ENC_KINDS[kind]
                row.update({"EncounterClass": cls, "Code": code, "Description": desc})
            return eid
        cls, code, desc, base, minutes = ENC_KINDS[kind]
        start = stamp(f"{m['patient_id']}|{d}|{org}", d)
        prov = self.provider_of.get(org, "")
        self.enc_rows[eid] = self._add("encounters", m, {
            "Id": eid, "Start": start, "Stop": plus_minutes(start, minutes), "Patient": m["patient_id"],
            "Organization": uid("org", org), "Provider": uid("provider", prov) if prov else "",
            "Payer": self.payer_id, "EncounterClass": cls, "Code": code, "Description": desc,
            "Base_Encounter_Cost": money(base), "ReasonCode": reason[0] if reason else "",
            "ReasonDescription": reason[1] if reason else ""})
        return eid

    def proc(self, m: dict, d: date, org: str, code: str, desc: str, reason: tuple | None = None) -> None:
        eid = self.enc(m, d, org)
        self._add("procedures", m, {
            "Start": self.enc_rows[eid]["Start"], "Stop": plus_minutes(self.enc_rows[eid]["Start"], 15),
            "Patient": m["patient_id"], "Encounter": eid, "System": SNOMED, "Code": code, "Description": desc,
            "Base_Cost": money(PROC_COST.get(code, 100.0)),
            "ReasonCode": reason[0] if reason else "", "ReasonDescription": reason[1] if reason else ""})

    def obs(self, m: dict, d: date, org: str, code: str, desc: str, value, units: str, category: str) -> None:
        eid = self.enc(m, d, org)
        numeric = isinstance(value, (int, float))
        self._add("observations", m, {
            "Date": self.enc_rows[eid]["Start"], "Patient": m["patient_id"], "Encounter": eid,
            "Category": category, "Code": code, "Description": desc, "Value": value, "Units": units,
            "Type": "numeric" if numeric else "text"})

    def bp(self, m: dict, d: date, org: str, sys_v: int, dia_v: int) -> None:
        self.obs(m, d, org, LOINC_SBP, "Systolic Blood Pressure", sys_v, "mm[Hg]", "vital-signs")
        self.obs(m, d, org, LOINC_DBP, "Diastolic Blood Pressure", dia_v, "mm[Hg]", "vital-signs")

    def med(self, m: dict, start: date, stop: date | None, code: str, desc: str, org: str,
            reason: tuple | None = None, encounter_id: str | None = None, cost: float = 18.50,
            dispenses: int | None = None) -> None:
        eid = encounter_id or self.enc(m, start, org)
        end = min(stop or RUN_DATE, RUN_DATE)
        count = dispenses if dispenses is not None else max(1, months_between(start, end))
        total = cost * count
        self._add("medications", m, {
            "Start": stamp(f"{m['patient_id']}|med|{code}|{start}", start),
            "Stop": stamp(f"{m['patient_id']}|medstop|{code}|{stop}", stop) if stop else "",
            "Patient": m["patient_id"], "Payer": self.payer_id, "Encounter": eid, "Code": code,
            "Description": desc, "Base_Cost": money(cost), "Payer_Coverage": money(0.8 * total),
            "Dispenses": count, "TotalCost": money(total),
            "ReasonCode": reason[0] if reason else "", "ReasonDescription": reason[1] if reason else ""})

    def cond(self, m: dict, start: date, stop: date | None, code: str, desc: str, org: str) -> None:
        eid = self.enc(m, start, org)
        self._add("conditions", m, {
            "Start": start.isoformat(), "Stop": stop.isoformat() if stop else "", "Patient": m["patient_id"],
            "Encounter": eid, "System": SNOMED, "Code": code, "Description": desc})


# ----------------------------------------------------------------------------- compliant / non-compliant data
# Each make_* writes the structured data for one measure. ok=True makes the member compliant,
# ok=False leaves the member non-compliant (nothing, or a decoy outside the window or out of range).

def make_col(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    birth = pd(m["birth_date"])
    if ok:
        if rng.random() < 0.85:
            lo = max(date(2017, 1, 1), add_years(birth, 45))
            b.proc(m, rdate(rng, lo, date(2026, 9, 30)), "ORG-CGI", COLONOSCOPY, "Colonoscopy")
        else:
            b.obs(m, rdate(rng, MY_START, date(2026, 9, 30)), lab_org(m), LOINC_FIT,
                  "Hemoglobin.gastrointestinal [Presence] in Stool by Immunoassay", "Negative", "", "laboratory")
    elif rng.random() < 0.15 and add_years(birth, 45) <= date(2016, 12, 31):
        b.proc(m, rdate(rng, add_years(birth, 45), date(2016, 12, 31)), "ORG-CGI", COLONOSCOPY, "Colonoscopy")


def make_bcs(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    birth = pd(m["birth_date"])
    if ok:
        b.proc(m, rdate(rng, max(date(2024, 10, 1), add_years(birth, 40)), date(2026, 9, 30)), "ORG-SIM",
               MAMMOGRAM, "Mammography")
    elif rng.random() < 0.15:
        b.proc(m, rdate(rng, max(date(2022, 1, 1), add_years(birth, 40)), date(2024, 9, 30)), "ORG-SIM",
               MAMMOGRAM, "Mammography")


def make_ccs(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    birth = pd(m["birth_date"])
    org = m["pcp_org"] or "ORG-WHA"
    hpv_lo = max(date(2022, 1, 1), add_years(birth, 30))
    if ok:
        if hpv_lo <= date(2026, 9, 30) and rng.random() < 0.45:
            b.obs(m, rdate(rng, hpv_lo, date(2026, 9, 30)), org, LOINC_HPV,
                  "High risk HPV DNA [Presence] in Cervix", "Negative", "", "laboratory")
        else:
            lo = max(date(2024, 1, 1), add_years(birth, 21))
            b.proc(m, rdate(rng, lo, date(2026, 9, 30)), org, PAP, "Papanicolaou smear of cervix")
    elif rng.random() < 0.15 and add_years(birth, 21) <= date(2021, 12, 31):
        b.proc(m, rdate(rng, max(date(2020, 1, 1), add_years(birth, 21)), date(2021, 12, 31)), org, PAP,
               "Papanicolaou smear of cervix")


def make_eed(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    if ok:
        b.proc(m, rdate(rng, MY_START, date(2026, 9, 30)), "ORG-CEA", EYE_EXAM, "Retinal examination")
    elif rng.random() < 0.2:
        b.proc(m, rdate(rng, date(2024, 1, 1), date(2024, 12, 31)), "ORG-CEA", EYE_EXAM, "Retinal examination")


def distinct_dates(rng: random.Random, n: int, lo: date, hi: date) -> list[date]:
    days = rng.sample(range((hi - lo).days + 1), n)
    return sorted(lo + timedelta(days=x) for x in days)


def make_gsd(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    org = lab_org(m)
    if not ok and rng.random() < 0.3:  # no 2026 result at all
        if rng.random() < 0.8:
            b.obs(m, rdate(rng, date(2025, 2, 1), date(2025, 11, 30)), org, LOINC_A1C, "Hemoglobin A1c",
                  round(rng.uniform(6.5, 11.0), 1), "%", "laboratory")
        return
    dates = distinct_dates(rng, rng.choice([1, 1, 2, 3]), date(2026, 1, 5), date(2026, 10, 5))
    for i, d in enumerate(dates):
        if i == len(dates) - 1:
            value = round(rng.uniform(5.6, 7.9), 1) if ok else round(rng.uniform(8.0, 11.8), 1)
        else:
            value = round(rng.uniform(6.5, 10.0), 1)
        b.obs(m, d, org, LOINC_A1C, "Hemoglobin A1c", value, "%", "laboratory")


def make_bp(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    org = office_org(m)
    if not ok and rng.random() < 0.2:  # no 2026 reading, only a 2025 one
        b.bp(m, rdate(rng, date(2025, 2, 1), date(2025, 11, 30)), org, rng.randint(118, 150), rng.randint(70, 94))
        return
    dates = distinct_dates(rng, rng.choice([1, 2, 3]), date(2026, 1, 5), date(2026, 10, 5))
    for i, d in enumerate(dates):
        if i < len(dates) - 1:
            b.bp(m, d, org, rng.randint(118, 160), rng.randint(68, 98))
        elif ok:
            b.bp(m, d, org, rng.randint(108, 138), rng.randint(62, 88))
        elif rng.random() < 0.7:
            b.bp(m, d, org, rng.randint(140, 172), rng.randint(70, 98))
        else:
            b.bp(m, d, org, rng.randint(118, 138), rng.randint(90, 100))


def make_ked(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    org = lab_org(m)
    d1 = rdate(rng, MY_START, date(2026, 9, 30))
    d2 = d1 if rng.random() < 0.5 else rdate(rng, MY_START, date(2026, 9, 30))
    if ok:
        b.obs(m, d1, org, LOINC_EGFR, "Glomerular filtration rate/1.73 sq M.predicted", rng.randint(38, 110),
              "mL/min/{1.73_m2}", "laboratory")
        b.obs(m, d2, org, LOINC_UACR, "Albumin/Creatinine [Mass Ratio] in Urine", rng.randint(3, 60), "mg/g",
              "laboratory")
        return
    r = rng.random()
    if r < 0.5:
        if rng.random() < 0.4:  # decoy: both labs, but in 2025
            b.obs(m, date(2025, 5, 14), org, LOINC_EGFR, "Glomerular filtration rate/1.73 sq M.predicted",
                  rng.randint(40, 105), "mL/min/{1.73_m2}", "laboratory")
            b.obs(m, date(2025, 5, 14), org, LOINC_UACR, "Albumin/Creatinine [Mass Ratio] in Urine",
                  rng.randint(3, 40), "mg/g", "laboratory")
    elif r < 0.85:
        b.obs(m, d1, org, LOINC_EGFR, "Glomerular filtration rate/1.73 sq M.predicted", rng.randint(38, 110),
              "mL/min/{1.73_m2}", "laboratory")
    else:
        b.obs(m, d2, org, LOINC_UACR, "Albumin/Creatinine [Mass Ratio] in Urine", rng.randint(3, 60), "mg/g",
              "laboratory")


def make_spd(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    code = rng.choice(sorted(STATINS))
    desc = STATINS[code]
    if ok:
        start = rdate(rng, date(2022, 1, 1), date(2026, 8, 1))
        b.med(m, start, None, code, desc, office_org(m), cost=14.20)
    elif rng.random() < 0.35:  # statin that ended in 2025
        start = rdate(rng, date(2021, 1, 1), date(2024, 6, 30))
        b.med(m, start, rdate(rng, date(2024, 12, 1), date(2025, 11, 30)), code, desc, office_org(m), cost=14.20)


def make_aab(b: Builder, m: dict, rng: random.Random, ok: bool) -> None:
    """Background AAB episode. ok means no antibiotic was dispensed."""
    visit = rdate(rng, date(2025, 7, 1), date(2026, 6, 26))
    code = "4120002" if months_between(pd(m["birth_date"]), visit) < 24 else "10509002"
    urgent = rng.random() < 0.3
    org = "ORG-VUC" if urgent or not m["pcp_org"] else m["pcp_org"]
    abx = None if ok else rng.choice(sorted(ANTIBIOTICS))
    delay = rng.randint(0, 3)
    add_episode(b, m, visit, org, code, abx, visit + timedelta(days=delay), extra_med=ok and rng.random() < 0.5)


def add_episode(b: Builder, m: dict, visit: date, org: str, dx_code: str, abx: str | None,
                dispensed: date, extra_med: bool = False) -> None:
    reason = (dx_code, DX[dx_code])
    eid = b.enc(m, visit, org, "urgent" if org == "ORG-VUC" else "symptom", reason)
    b.cond(m, visit, visit + timedelta(days=14), dx_code, DX[dx_code], org)
    if abx:
        b.med(m, dispensed, dispensed + timedelta(days=7), abx, ANTIBIOTICS[abx], org, reason, eid, 9.80, 1)
    if extra_med:
        b.med(m, visit, visit + timedelta(days=7), ACETAMINOPHEN[0], ACETAMINOPHEN[1], org, reason, eid, 4.10, 1)


MAKERS = {"COL-E": make_col, "BCS-E": make_bcs, "CCS-E": make_ccs, "EED": make_eed, "GSD": make_gsd,
          "BPD": make_bp, "KED": make_ked, "CBP": make_bp, "SPD": make_spd}


# ----------------------------------------------------------------------------- members

def finish_member(m: dict) -> dict:
    m["patient_id"] = uid("patient", m["member_id"])
    return m


def scenario_members() -> list[dict]:
    out = []
    for s in sc.MEMBERS:
        m = dict(s)
        m["state"] = "MA"
        out.append(finish_member(m))
    return out


def age_bucket_year(rng: random.Random) -> int:
    r = rng.random()
    lo, hi = (3, 17) if r < 0.06 else (18, 39) if r < 0.28 else (40, 64) if r < 0.70 else (65, 85)
    return MY_END.year - rng.randint(lo, hi)


def condition_prob(base: float, age: int) -> float:
    if age < 18:
        return 0.0
    return base * (0.3 if age < 30 else 0.8 if age < 50 else 1.2 if age < 65 else 1.4)


def background_members(fake: Faker) -> list[dict]:
    """Procedurally generated members: demographics, conditions and an AAB flag. Clinical data comes later."""
    bg = sc.BACKGROUND
    org_city = {o["id"]: o["city"] for o in sc.ORGANIZATIONS}
    out = []
    for i in range(bg["count"]):
        number = bg["first_member_number"] + i
        rng = random.Random(f"{bg['seed']}|member|{number}")
        fake.seed_instance(bg["seed"] * 1000 + number)
        sex = "F" if rng.random() < 0.52 else "M"
        year = age_bucket_year(rng)
        birth = date(year, rng.randint(1, 12), rng.randint(1, 28))
        age = MY_END.year - year
        orgs = [o for o in BG_PCP_ORGS if not (sex == "M" and o == "ORG-WHA")]
        weights = [w for o, w in zip(BG_PCP_ORGS, BG_PCP_WEIGHTS) if o in orgs]
        pcp = rng.choices(orgs, weights)[0] if rng.random() > 0.05 else None
        city = org_city[pcp] if pcp and rng.random() < 0.85 else rng.choice(["Springfield", "Worcester", "Lowell", "Brockton"])
        if age >= 65 and rng.random() < 0.9:
            product = "medicare"
        elif age < 18:
            product = "medicaid" if rng.random() < 0.6 else "commercial"
        else:
            product = rng.choices(["commercial", "medicaid", "medicare"], [65, 32, 3])[0]
        conditions = []
        for key, code, base in (("diabetes", None, bg["prevalence"]["diabetes"]),
                                ("hypertension", "59621000", bg["prevalence"]["hypertension"])):
            lo = max(add_years(birth, 18) + timedelta(days=60), date(2004, 1, 1))
            if rng.random() < condition_prob(base, age) and lo <= date(2025, 6, 30):
                if key == "diabetes":
                    code = "44054006" if rng.random() < 0.92 else "46635009"
                conditions.append({"code": code, "desc": DX[code], "onset": rdate(rng, lo, date(2025, 6, 30)).isoformat()})
        last_contact = rdate(rng, date(2025, 6, 1), date(2026, 10, 10))
        next_visit = rdate(rng, date(2026, 10, 15), date(2027, 1, 31)) if rng.random() < 0.55 else None
        m = {"member_id": f"M-{number}", "first": fake.first_name_female() if sex == "F" else fake.first_name_male(),
             "last": fake.last_name(), "sex": sex, "birth_date": birth.isoformat(), "city": city, "state": "MA",
             "product_line": product, "pcp_org": pcp, "next_visit": next_visit.isoformat() if next_visit else None,
             "last_contact": last_contact.isoformat(), "conditions": conditions, "clinical": [],
             "has_aab": rng.random() < bg["aab_episode_rate"], "number": number}
        out.append(finish_member(m))
    return out


def add_scaffolding(b: Builder, m: dict, rng: random.Random) -> None:
    """Neutral encounters and background conditions and medications. Never carries measure evidence."""
    org = m["pcp_org"]
    for c in m["conditions"]:
        b.cond(m, pd(c["onset"]), None, c["code"], c["desc"], office_org(m))
    if org:
        b.enc(m, rdate(rng, date(2025, 2, 1), date(2025, 11, 30)), org, "wellness")
        b.enc(m, pd(m["last_contact"]), org, "office")
    if not m.get("scenario"):
        for c in m["conditions"]:
            if c["code"] in DIABETES:
                b.med(m, pd(c["onset"]) + timedelta(days=14), None, METFORMIN[0], METFORMIN[1], office_org(m),
                      MED_REASON["860975"])
            elif c["code"] in HTN:
                b.med(m, pd(c["onset"]) + timedelta(days=14), None, LISINOPRIL[0], LISINOPRIL[1], office_org(m),
                      MED_REASON["314076"])


def add_clinical(b: Builder, m: dict) -> None:
    """The structured facts a scenario member lists in `clinical`, exactly as given."""
    for f in m["clinical"]:
        kind = f["type"]
        if kind == "observation":
            b.obs(m, pd(f["date"]), lab_org(m), f["loinc"], f["desc"], f["value"], f["unit"], "laboratory")
        elif kind == "bp":
            b.bp(m, pd(f["date"]), office_org(m), f["systolic"], f["diastolic"])
        elif kind == "medication":
            stop = pd(f["stop"]) if f.get("stop") else None
            b.med(m, pd(f["start"]), stop, f["rxnorm"], f["desc"], office_org(m), MED_REASON.get(f["rxnorm"]))
        elif kind == "aab_episode":
            add_episode(b, m, pd(f["date"]), f["org"], f["dx_code"], f["antibiotic_rxnorm"], pd(f["dispensed"]))
        else:
            raise ValueError(f"unknown clinical fact {kind}")


def build_member(b: Builder, m: dict, specs: list[dict], gap_keys: set) -> None:
    rng = random.Random(f"{sc.BACKGROUND['seed']}|data|{m['member_id']}")
    scaffold_rng = random.Random(f"{sc.BACKGROUND['seed']}|scaffold|{m['member_id']}")
    add_scaffolding(b, m, scaffold_rng)
    if m.get("scenario"):
        add_clinical(b, m)
    bg = sc.BACKGROUND
    bp_done = False
    for spec in specs:
        code = spec["code"]
        if code == "AAB":
            if not m.get("scenario") and m.get("has_aab"):
                make_aab(b, m, rng, rng.random() < bg["target_compliance"]["AAB"])
            continue
        chart = b.chart(m)
        if not is_eligible(spec, m, chart):
            continue
        has_gap = (m["member_id"], code) in gap_keys
        if code in ("BPD", "CBP"):
            if bp_done:
                continue
            bp_done = True
            both = [s for s in specs if s["code"] in ("BPD", "CBP") and is_eligible(s, m, chart)]
            if m.get("scenario"):
                flags = {(m["member_id"], s["code"]) in gap_keys for s in both}
                if len(flags) > 1:
                    raise ValueError(f"{m['member_id']}: BPD and CBP gaps disagree, one BP reading cannot do both")
                if not flags.pop() and not evaluate(spec, m, chart)[0]:
                    make_bp(b, m, rng, True)
            else:
                target = min(bg["target_compliance"][s["code"]] for s in both)
                make_bp(b, m, rng, rng.random() < target)
            continue
        if m.get("scenario"):
            if not has_gap and not evaluate(spec, m, chart)[0]:
                MAKERS[code](b, m, rng, True)
        else:
            MAKERS[code](b, m, rng, rng.random() < bg["target_compliance"][code])


# ----------------------------------------------------------------------------- findings, gaps, summaries

def member_ref(m: dict) -> dict:
    age = MY_END.year - pd(m["birth_date"]).year
    return {"member_id": m["member_id"], "synthea_patient_id": m["patient_id"], "birth_date": m["birth_date"],
            "age_at_year_end": age, "sex": m["sex"], "product_line": m["product_line"]}


def exclusion_check(spec: dict, age: int) -> dict:
    code = spec["code"]
    if code in ("CCS-E", "AAB"):
        return {"eligible": False, "reason": "No frailty or advanced illness rule for this measure"}
    if age < 66:
        return {"eligible": False, "reason": "Under 66: frailty and advanced illness rule does not apply"}
    if code in ("KED", "CBP"):
        return {"eligible": True, "reason": "Age 66 or older: frailty with advanced illness (66 to 80) or "
                                            "frailty alone (81 and over) can exclude the member"}
    return {"eligible": True, "reason": "Age 66 or older: frailty with advanced illness can exclude the member"}


def build_gaps(members: list[dict], specs: list[dict], charts: dict, org_names: dict) -> tuple[list, dict]:
    """Evaluate every member. Returns (gap list, {measure: [denominator, numerator]})."""
    scenario_gaps = {(g["member_id"], g["measure"]): g for g in sc.GAPS}
    counts = {s["code"]: [0, 0] for s in specs}
    scen, bg = [], []
    next_number = sc.BACKGROUND["first_gap_number"]
    for m in members:
        for f in evaluate_member(specs, m, charts[m["patient_id"]]):
            code = f["spec"]["code"]
            counts[code][0] += 1
            counts[code][1] += 1 if f["compliant"] else 0
            if f["compliant"]:
                continue
            sg = scenario_gaps.get((m["member_id"], code))
            if m.get("scenario") and not sg:
                raise ValueError(f"unexpected gap for scenario member {m['member_id']} {code}")
            gap = {"gap_id": sg["gap_id"] if sg else f"G-{next_number}"}
            if not sg:
                next_number += 1
            ref = member_ref(m)
            gap.update({"member": ref, "measure_code": code, "measure_name": f["spec"]["name"],
                        "gap_type": "episode" if code == "AAB" else "numerator", "status": "open",
                        "missing_element": sg["missing"] if sg else f["missing"],
                        "last_relevant_event": sg["last_event"] if sg else f["last_event"],
                        "deadline": MY_END.isoformat(),
                        "exclusion_check": exclusion_check(f["spec"], ref["age_at_year_end"]),
                        "attributed_provider": {"org_id": m["pcp_org"], "name": org_names[m["pcp_org"]]}
                        if m["pcp_org"] else None})
            if code == "AAB":
                ep = f["episode"]
                gap["episode"] = {"visit_date": ep["visit_date"].isoformat(), "encounter_id": ep["encounter_id"],
                                  "antibiotic": ep["fill"]["Description"], "dispensed_date": pd(ep["fill"]["Start"]).isoformat()}
            (scen if sg else bg).append(gap)
    missing = set(scenario_gaps) - {(g["member"]["member_id"], g["measure_code"]) for g in scen}
    if missing:
        raise ValueError(f"scenario gaps not produced by the structured data: {sorted(missing)}")
    scen.sort(key=lambda g: g["gap_id"])
    return scen + bg, counts


def summaries(specs: list[dict], counts: dict) -> list[dict]:
    out = []
    for s in specs:
        den, num = counts[s["code"]]
        out.append({"measure_code": s["code"], "denominator": den, "numerator": num, "exclusions": 0,
                    "rate": round(num / den, 4) if den else None})
    return out


def expected_findings(gaps: list[dict]) -> list[dict]:
    by_id = {g["gap_id"]: g for g in sc.GAPS}
    out = []
    for g in gaps:
        sg = by_id.get(g["gap_id"])
        entry = {"gap_id": g["gap_id"], "member_id": g["member"]["member_id"], "measure_code": g["measure_code"]}
        if sg:
            e = sg["expected"]
            entry.update({"expected_status": e["status"], "find": e["find"], "optional": e.get("optional", []),
                          "traps": e["traps"]})
            if "structured" in e:
                entry["structured"] = e["structured"]
            entry["note"] = e.get("note", "")
        else:
            entry.update({"expected_status": "Open", "find": [], "optional": [], "traps": [],
                          "note": "No documents; outreach"})
        out.append(entry)
    return out


# ----------------------------------------------------------------------------- reference tables

def fake_npi(key: str) -> str:
    """Deterministic fake 10-digit NPI-shaped number."""
    return "9" + str(int(hashlib.sha256(f"npi|{key}".encode()).hexdigest()[:12], 16) % 10**9).zfill(9)


def org_address(fake: Faker, idx: int, org: dict) -> dict:
    fake.seed_instance(sc.BACKGROUND["seed"] * 10 + idx)
    if not org["city"]:
        return {"Address": "", "City": "", "State": "", "Zip": "", "Lat": "", "Lon": ""}
    prefix, _, _, lat, lon = CITIES[org["city"]]
    return {"Address": fake.street_address(), "City": org["city"], "State": org["state"],
            "Zip": f"{prefix}{fake.random_int(0, 99):02d}", "Lat": f"{lat + fake.random_int(-40, 40) / 1000:.4f}",
            "Lon": f"{lon + fake.random_int(-40, 40) / 1000:.4f}"}


def patient_row(m: dict, fake: Faker, rng: random.Random) -> dict:
    seed_no = m.get("number") or int(m["member_id"][2:])
    fake.seed_instance(sc.BACKGROUND["seed"] * 1000 + seed_no)
    prefix, county, fips, lat, lon = CITIES[m["city"]]
    age = MY_END.year - pd(m["birth_date"]).year
    return {"Id": m["patient_id"], "BirthDate": m["birth_date"], "SSN": f"999-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
            "Drivers": f"S{rng.randint(10000000, 99999999)}" if age >= 17 else "",
            "Passport": f"X{rng.randint(10000000, 99999999)}X" if rng.random() < 0.3 and age >= 17 else "",
            "Prefix": "" if age < 18 else ("Mr." if m["sex"] == "M" else rng.choice(["Ms.", "Mrs."])),
            "First": m["first"], "Last": m["last"],
            "Marital": "" if age < 18 else rng.choice(["M", "S"]),
            "Race": rng.choices(["white", "black", "asian", "other"], [72, 14, 8, 6])[0],
            "Ethnicity": rng.choices(["nonhispanic", "hispanic"], [84, 16])[0], "Gender": m["sex"],
            "BirthPlace": f"{m['city']}  Massachusetts  US", "Address": fake.street_address(), "City": m["city"],
            "State": "MA", "County": county, "FIPS County Code": fips,
            "Zip": f"{prefix}{rng.randint(0, 99):02d}", "Lat": f"{lat + rng.uniform(-0.03, 0.03):.4f}",
            "Lon": f"{lon + rng.uniform(-0.03, 0.03):.4f}", "Income": rng.randint(18, 160) * 1000}


def finalize(b: Builder, members: list[dict], fake: Faker) -> None:
    """Reference tables, then costs that depend on line items."""
    pidx = {m["patient_id"]: i for i, m in enumerate(members)}
    rng = random.Random(f"{sc.BACKGROUND['seed']}|demographics")
    for m in members:
        row = patient_row(m, fake, rng)
        b._add("patients", m, row)
        age = MY_END.year - pd(m["birth_date"]).year
        owner = f"{m['first']} {m['last']}"
        ownership = "Self"
        if age < 18:
            fake.seed_instance(sc.BACKGROUND["seed"] * 7 + int(m["member_id"][2:]))
            ownership, owner = "Guardian", f"{fake.first_name()} {m['last']}"
        b._add("payer_transitions", m, {
            "Patient": m["patient_id"], "Member ID": m["member_id"], "Start_Year": "2024-01-01",
            "End_Year": "2026-12-31", "Payer": b.payer_id, "Ownership": ownership, "Owner Name": owner})
    # Line-item costs roll up into the encounter claim cost.
    items = defaultdict(float)
    for r in b.rows["procedures"]:
        items[r["Encounter"]] += float(r["Base_Cost"])
    for r in b.rows["medications"]:
        items[r["Encounter"]] += float(r["Base_Cost"])
    for e in b.rows["encounters"]:
        total = float(e["Base_Encounter_Cost"]) + items[e["Id"]]
        e["Total_Claim_Cost"], e["Payer_Coverage"] = money(total), money(0.8 * total)
    spend, covered = defaultdict(float), defaultdict(float)
    for e in b.rows["encounters"]:
        spend[e["Patient"]] += float(e["Total_Claim_Cost"]) - float(e["Payer_Coverage"])
        covered[e["Patient"]] += float(e["Payer_Coverage"])
    for r in b.rows["medications"]:
        spend[r["Patient"]] += float(r["TotalCost"]) - float(r["Payer_Coverage"])
        covered[r["Patient"]] += float(r["Payer_Coverage"])
    for p in b.rows["patients"]:
        p["Healthcare_Expenses"], p["Healthcare_Coverage"] = money(spend[p["Id"]]), money(covered[p["Id"]])
    # Organizations, providers and the payer.
    org_enc, org_rev = defaultdict(int), defaultdict(float)
    prov_enc, prov_proc = defaultdict(int), defaultdict(int)
    enc_provider = {}
    for e in b.rows["encounters"]:
        org_enc[e["Organization"]] += 1
        org_rev[e["Organization"]] += float(e["Total_Claim_Cost"])
        prov_enc[e["Provider"]] += 1
        enc_provider[e["Id"]] = e["Provider"]
    for r in b.rows["procedures"]:
        prov_proc[enc_provider[r["Encounter"]]] += 1
    for i, o in enumerate(sc.ORGANIZATIONS):
        oid = uid("org", o["id"])
        row = {"Id": oid, "Name": o["name"], "Phone": f"413-555-01{i:02d}", "NPI": fake_npi(o["id"]),
               "Revenue": money(org_rev[oid]), "Utilization": org_enc[oid]}
        row.update(org_address(fake, i, o))
        b.rows["organizations"].append({h: str(row.get(h, "")) for h in HEADERS["organizations"]})
    orgs = {o["id"]: o for o in sc.ORGANIZATIONS}
    for p in sc.PROVIDERS:
        pid, o = uid("provider", p["id"]), orgs[p["org"]]
        row = {"Id": pid, "Organization": uid("org", p["org"]), "Name": p["name"], "Gender": PROVIDER_GENDER[p["id"]],
               "Speciality": p["specialty"], "Encounters": prov_enc[pid], "Procedures": prov_proc[pid],
               "NPI": fake_npi(p["id"])}
        row.update({k: v for k, v in org_address(fake, sc.ORGANIZATIONS.index(o), o).items()})
        b.rows["providers"].append({h: str(row.get(h, "")) for h in HEADERS["providers"]})
    total_cov = sum(covered.values())
    total_unc = sum(spend.values())
    b.rows["payers"].append({h: "" for h in HEADERS["payers"]} | {
        "Id": b.payer_id, "Name": PAYER_NAME, "Ownership": "Private", "Address": "100 Demo Plaza", "City": "Boston",
        "State_Headquartered": "MA", "Zip": "02110", "Phone": "617-555-0100", "Amount_Covered": money(total_cov),
        "Amount_Uncovered": money(total_unc), "Revenue": money(total_cov + total_unc),
        "Covered_Encounters": len(b.rows["encounters"]), "Uncovered_Encounters": 0,
        "Covered_Medications": len(b.rows["medications"]), "Uncovered_Medications": 0,
        "Covered_Procedures": len(b.rows["procedures"]), "Uncovered_Procedures": 0, "Covered_Immunizations": 0,
        "Uncovered_Immunizations": 0, "Unique_Customers": len(members), "Member_Months": len(members) * 36})
    for t, col in SORT_COL.items():
        b.rows[t].sort(key=lambda r: (pidx[r["Patient"]], r[col]))
    b.rows["payer_transitions"].sort(key=lambda r: pidx[r["Patient"]])
    b.rows["patients"].sort(key=lambda r: pidx[r["Id"]])


# ----------------------------------------------------------------------------- writing

def write_csv(path: Path, headers: list[str], rows: list[dict], disk_headers: list[str] | None = None) -> None:
    """Write rows keyed by `headers`, using `disk_headers` (same order) for the header row if given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(disk_headers or headers)
        for r in rows:
            w.writerow(["" if r.get(h) is None else r.get(h, "") for h in headers])


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def member_tables(members: list[dict]) -> dict:
    mem = [{"member_id": m["member_id"], "synthea_patient_id": m["patient_id"], "first": m["first"],
            "last": m["last"], "sex": m["sex"], "birth_date": m["birth_date"], "city": m["city"], "state": "MA",
            "product_line": m["product_line"], "pcp_org_id": m["pcp_org"] or ""} for m in members]
    orgs = [{"id": o["id"], "name": o["name"], "kind": o["kind"], "city": o["city"] or "", "state": o["state"] or "",
             "closure_rate": "" if o["closure_rate"] is None else o["closure_rate"]} for o in sc.ORGANIZATIONS]
    provs = [{"id": p["id"], "name": p["name"], "org_id": p["org"], "specialty": p["specialty"]}
             for p in sc.PROVIDERS]
    eng = [{"member_id": m["member_id"], "next_visit_date": m["next_visit"] or "",
            "last_contact_date": m["last_contact"], "attributed_org_id": m["pcp_org"] or ""} for m in members]
    return {"members": mem, "organizations": orgs, "providers": provs, "engagement": eng}


def print_report(counts_rows: dict, gaps: list[dict], sums: list[dict]) -> None:
    print("\nRow counts")
    for name, n in counts_rows.items():
        print(f"  {name:<20}{n:>7}")
    per = defaultdict(int)
    for g in gaps:
        per[g["measure_code"]] += 1
    print("\nGaps per measure")
    for code in [s["measure_code"] for s in sums]:
        print(f"  {code:<8}{per[code]:>5}")
    print(f"  total   {len(gaps):>5}  ({sum(1 for g in gaps if g['gap_id'] < 'G-1001')} scenario, "
          f"{sum(1 for g in gaps if g['gap_id'] >= 'G-1001')} background)")
    print("\nMeasure summaries")
    print(f"  {'measure':<8}{'denom':>7}{'numer':>7}{'excl':>6}{'rate':>9}")
    for s in sums:
        print(f"  {s['measure_code']:<8}{s['denominator']:>7}{s['numerator']:>7}{s['exclusions']:>6}{s['rate']:>9.4f}")


def generate() -> None:
    specs = load_specs()
    fake = Faker("en_US")
    members = scenario_members()
    for m in members:
        m["scenario"] = True
    members += background_members(fake)
    org_names = {o["id"]: o["name"] for o in sc.ORGANIZATIONS}
    gap_keys = {(g["member_id"], g["measure"]) for g in sc.GAPS}
    b = Builder()
    for m in members:
        build_member(b, m, specs, gap_keys)
    gaps, counts = build_gaps(members, specs, {m["patient_id"]: b.chart(m) for m in members}, org_names)
    sums = summaries(specs, counts)
    finalize(b, members, fake)
    for name, headers in HEADERS.items():
        write_csv(CSV_DIR / f"{name}.csv", headers, b.rows[name], DISK_HEADERS[name])
    for name, rows in member_tables(members).items():
        write_csv(MEMBERS_DIR / f"{name}.csv", MEMBER_FILES[name], rows)
    write_json(ENGINE_FILE, {**sc.RUN, "measure_summaries": sums, "gaps": gaps})
    write_json(EXPECTED_FILE, expected_findings(gaps))
    print_report({n: len(r) for n, r in b.rows.items()}, gaps, sums)
    print(f"\nWrote {CSV_DIR}, {MEMBERS_DIR}, {ENGINE_FILE.name}, {EXPECTED_FILE.name}")


# ----------------------------------------------------------------------------- checks

def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def check() -> int:
    """Verify the files on disk. Returns the number of failures."""
    errors: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    specs = load_specs()
    spec_by = {s["code"]: s for s in specs}
    tables = {}
    for name, headers in DISK_HEADERS.items():
        got, rows = read_csv(CSV_DIR / f"{name}.csv")
        need(got == headers, f"{name}.csv headers differ from Synthea's CSVConstants: {got}")
        keys = HEADERS[name]
        tables[name] = [dict(zip(keys, r.values())) for r in rows]  # translate to internal row keys by position
    print("Headers: all 10 Synthea CSVs match Synthea CSVConstants.HEADER_LINE_MAP" if not errors else "Headers: FAILED")

    _, member_rows = read_csv(MEMBERS_DIR / "members.csv")
    for name, headers in MEMBER_FILES.items():
        got, _ = read_csv(MEMBERS_DIR / f"{name}.csv")
        need(got == headers, f"members/{name}.csv headers differ: {got}")
    patients = {r["Id"]: r for r in tables["patients"]}
    need(len(patients) == len(tables["patients"]), "duplicate patient ids")

    # Foreign keys.
    enc_ids = {e["Id"] for e in tables["encounters"]}
    org_ids = {o["Id"] for o in tables["organizations"]}
    prov_ids = {p["Id"] for p in tables["providers"]}
    need(len(enc_ids) == len(tables["encounters"]), "duplicate encounter ids")
    for e in tables["encounters"]:
        need(e["Patient"] in patients and e["Organization"] in org_ids and (not e["Provider"] or e["Provider"] in prov_ids),
             f"encounter {e['Id']} has a dangling reference")
    for t in ("conditions", "observations", "procedures", "medications"):
        for r in tables[t]:
            need(r["Patient"] in patients and r["Encounter"] in enc_ids, f"{t} row has a dangling reference")
    for r in tables["payer_transitions"]:
        need(r["Patient"] in patients, "payer transition for an unknown patient")

    members = []
    for r in member_rows:
        p = patients[r["synthea_patient_id"]]
        members.append({"member_id": r["member_id"], "patient_id": r["synthea_patient_id"], "birth_date": p["BirthDate"],
                        "sex": p["Gender"], "pcp_org": r["pcp_org_id"] or None, "product_line": r["product_line"],
                        "scenario": r["member_id"] in {s["member_id"] for s in sc.MEMBERS}})
        need(p["BirthDate"] == r["birth_date"] and p["Gender"] == r["sex"], f"{r['member_id']} patient/member mismatch")
    charts = {m["patient_id"]: {t: [] for t in CHART_TABLES} for m in members}
    for t in CHART_TABLES:
        for r in tables[t]:
            charts[r["Patient"]][t].append(r)
    org_names = {o["id"]: o["name"] for o in sc.ORGANIZATIONS}
    try:
        gaps, counts = build_gaps(members, specs, charts, org_names)
    except ValueError as exc:
        print(f"\nChecks\n  FAIL {exc}")
        return 1
    sums = summaries(specs, counts)

    # Engine file matches what the structured data says.
    engine = json.loads(ENGINE_FILE.read_text(encoding="utf-8"))
    need(all(engine[k] == v for k, v in sc.RUN.items()), "engine run metadata differs from scenario.RUN")
    need(engine["measure_summaries"] == sums, "engine measure_summaries differ from the CSV data")
    need(len(engine["gaps"]) == len(gaps), "engine gap count differs from the CSV data")
    need({(g["gap_id"], g["member"]["member_id"], g["measure_code"]) for g in engine["gaps"]}
         == {(g["gap_id"], g["member"]["member_id"], g["measure_code"]) for g in gaps}, "engine gap set differs")
    need(engine["gaps"] == gaps, "engine gap objects differ from a fresh evaluation of the CSV data")
    by_member = {m["member_id"]: m for m in members}

    # Every gap member is eligible by age, sex and condition (measures.yaml population), checked independently.
    cond_rows = defaultdict(set)
    for c in tables["conditions"]:
        cond_rows[c["Patient"]].add(c["Code"])
    for g in engine["gaps"]:
        spec, ref = spec_by[g["measure_code"]], g["member"]
        pop, m = spec["population"], by_member[ref["member_id"]]
        need(ref["birth_date"] == m["birth_date"] and ref["sex"] == m["sex"], f"{g['gap_id']} member fields differ")
        age = MY_END.year - pd(ref["birth_date"]).year
        need(ref["age_at_year_end"] == age, f"{g['gap_id']} age_at_year_end wrong")
        if g["measure_code"] == "AAB":
            need(months_between(pd(ref["birth_date"]), pd(g["episode"]["visit_date"])) >= pop["age_min_months"],
                 f"{g['gap_id']} AAB member too young")
            need(window(spec, "bronchitis_visit_intake_period")[0] <= pd(g["episode"]["visit_date"])
                 <= window(spec, "bronchitis_visit_intake_period")[1], f"{g['gap_id']} episode outside intake")
            continue
        need(pop["age_min"] <= age <= pop["age_max"], f"{g['gap_id']} age {age} outside {pop['age_min']}-{pop['age_max']}")
        need(pop["sex"] == "any" or ref["sex"] == "F", f"{g['gap_id']} sex not eligible")
        if pop["condition"]:
            codes = DIABETES if "diabetes" in pop["condition"] else HTN
            need(bool(cond_rows[ref["synthea_patient_id"]] & codes), f"{g['gap_id']} member lacks {pop['condition']}")
        need(g["exclusion_check"]["eligible"] == (age >= 66 and g["measure_code"] not in ("CCS-E", "AAB")),
             f"{g['gap_id']} exclusion_check wrong")

    # Scenario members: gap measures not satisfied, other eligible measures compliant, listed facts present.
    scen_keys = {(g["member_id"], g["measure"]) for g in sc.GAPS}
    for m in (x for x in members if x["scenario"]):
        chart = charts[m["patient_id"]]
        for f in evaluate_member(specs, m, chart):
            key = (m["member_id"], f["spec"]["code"])
            need((key in scen_keys) == (not f["compliant"]),
                 f"{key}: structured data says compliant={f['compliant']}, scenario gap={key in scen_keys}")
    for s in sc.MEMBERS:
        m = by_member[s["member_id"]]
        chart = charts[m["patient_id"]]
        for c in s["conditions"]:
            need(any(r["Code"] == c["code"] and r["Start"] == c["onset"] for r in chart["conditions"]),
                 f"{s['member_id']} missing condition {c['code']}")
        for f in s["clinical"]:
            if f["type"] == "observation":
                need(any(r["Code"] == f["loinc"] and pd(r["Date"]).isoformat() == f["date"] and float(r["Value"]) == f["value"]
                         for r in chart["observations"]), f"{s['member_id']} missing observation {f['loinc']}")
            elif f["type"] == "bp":
                need(any(r["Code"] == LOINC_SBP and pd(r["Date"]).isoformat() == f["date"] and int(r["Value"]) == f["systolic"]
                         for r in chart["observations"]), f"{s['member_id']} missing BP {f['date']}")
            elif f["type"] == "medication":
                need(any(r["Code"] == f["rxnorm"] and pd(r["Start"]).isoformat() == f["start"] for r in chart["medications"]),
                     f"{s['member_id']} missing medication {f['rxnorm']}")
            elif f["type"] == "aab_episode":
                need(any(r["Code"] == f["antibiotic_rxnorm"] and pd(r["Start"]).isoformat() == f["dispensed"]
                         for r in chart["medications"]), f"{s['member_id']} missing antibiotic fill")
    for g in sc.GAPS:
        eg = next(x for x in engine["gaps"] if x["gap_id"] == g["gap_id"])
        need(eg["missing_element"] == g["missing"] and eg["last_relevant_event"] == g["last_event"],
             f"{g['gap_id']} engine gap differs from scenario text")
        m = by_member[g["member_id"]]
        if g["last_event"] and g["last_event"]["type"] in ("lab_result", "blood_pressure"):
            computed = evaluate(spec_by[g["measure"]], m, charts[m["patient_id"]])[2]
            need(bool(computed) and computed["date"] == g["last_event"]["date"] and computed["value"] == g["last_event"]["value"],
                 f"{g['gap_id']} last_event does not match the structured record")

    # M-024 has no 2026 clinical contact of any kind.
    m24 = charts[by_member["M-024"]["patient_id"]]
    for t, col in (("encounters", "Start"), ("observations", "Date"), ("procedures", "Start"), ("medications", "Start")):
        need(all(pd(r[col]).year < 2026 for r in m24[t]), f"M-024 has a 2026 row in {t}")
    need(all(not r["Stop"] or pd(r["Stop"]).year < 2026 for r in m24["medications"]), "M-024 has a statin active in 2026")

    # No structured exclusion evidence anywhere.
    for t in ("conditions", "procedures", "observations", "encounters"):
        for r in tables[t]:
            text = f"{r.get('Description', '')} {r.get('ReasonDescription', '')} {r.get('EncounterClass', '')}".lower()
            hit = [w for w in FORBIDDEN_WORDS if w in text]
            need(not hit, f"{t} row carries exclusion wording {hit}")
    allowed_dx = set(DX) | {c["code"] for s in sc.MEMBERS for c in s["conditions"]}
    allowed_proc = {COLONOSCOPY, MAMMOGRAM, PAP, EYE_EXAM}
    need(all(r["Code"] in allowed_dx for r in tables["conditions"]), "unexpected condition code")
    need(all(r["Code"] in allowed_proc for r in tables["procedures"]), "unexpected procedure code")
    need(all(pd(r["Start"]) <= LAST_DATA or r["Code"] in {c["rxnorm"] for s in sc.MEMBERS for c in s["clinical"]
                                                         if c["type"] == "medication"} for r in tables["medications"]),
         "medication starts after the data cut-off")
    need(all(pd(r["Date"]) <= RUN_DATE for r in tables["observations"]), "observation after the run date")

    # Expected findings.
    expected = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
    need([e["gap_id"] for e in expected] == [g["gap_id"] for g in engine["gaps"]], "expected_findings ids differ from gaps")
    need(all({"gap_id", "member_id", "measure_code", "expected_status", "find", "optional", "traps", "note"} <= set(e)
             for e in expected), "expected_findings entry missing a key")

    # Every measure has background members eligible.
    scen_den = defaultdict(int)
    for m in (x for x in members if x["scenario"]):
        for f in evaluate_member(specs, m, charts[m["patient_id"]]):
            scen_den[f["spec"]["code"]] += 1
    for s in specs:
        need(counts[s["code"]][0] > scen_den[s["code"]], f"{s['code']} has no eligible background members")

    print_report({n: len(r) for n, r in tables.items()}, gaps, sums)
    print("\nChecks")
    if errors:
        for e in errors[:40]:
            print(f"  FAIL {e}")
        print(f"  {len(errors)} failure(s)")
        return len(errors)
    print("  PASS headers, foreign keys, engine file vs CSV data, gap eligibility, scenario gaps unsatisfied "
          "by structured data, scenario facts present, no exclusion evidence, expected findings")
    print("  NOTE M-024 keeps its 2025-11-03 HbA1c; G-0036 last_event stays None because only 2026 results count")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the generated files instead of writing them")
    args = parser.parse_args()
    if args.check:
        sys.exit(1 if check() else 0)
    generate()


if __name__ == "__main__":
    main()
