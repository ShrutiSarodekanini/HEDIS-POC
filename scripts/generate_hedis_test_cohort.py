from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any


MEASURES = ["AAB", "BCSE", "BPD", "CBP", "CCSE", "COLE", "EED", "GSD", "KED", "SPD"]


@dataclass
class PatientFacts:
    patient_id: str
    birthdate: str | None = None
    gender: str | None = None
    age_2026: int | None = None
    condition_texts: set[str] = field(default_factory=set)
    observation_texts: set[str] = field(default_factory=set)
    procedure_texts: set[str] = field(default_factory=set)
    medication_texts: set[str] = field(default_factory=set)
    encounter_texts: set[str] = field(default_factory=set)


def normalize(s: str | None) -> str:
    return (s or "").strip().lower()


def contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def is_female(gender_value: str | None) -> bool:
    g = normalize(gender_value)
    return g in {"f", "female"}


def parse_birthdate_to_age_2026(value: str | None) -> int | None:
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    ref = date(2026, 12, 31)
    return ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day))


def measure_status(f: PatientFacts, measure: str) -> tuple[str, str, str]:
    age = f.age_2026
    female = is_female(f.gender)

    cond = " | ".join(sorted(f.condition_texts))
    obs = " | ".join(sorted(f.observation_texts))
    proc = " | ".join(sorted(f.procedure_texts))
    med = " | ".join(sorted(f.medication_texts))
    enc = " | ".join(sorted(f.encounter_texts))
    text_all = " | ".join([cond, obs, proc, med, enc])

    has_bronchitis = contains_any(text_all, ["acute bronchitis", "bronchitis", "j20"])
    has_antibiotic = contains_any(
        med,
        [
            "amoxicillin",
            "azithromycin",
            "doxycycline",
            "cephalexin",
            "ciprofloxacin",
            "levofloxacin",
            "clindamycin",
            "penicillin",
            "antibiotic",
        ],
    )

    has_hypertension = contains_any(text_all, ["hypertension", "essential hypertension", "i10"])
    has_bp = contains_any(obs, ["blood pressure", "systolic blood pressure", "diastolic blood pressure", "8480-6", "8462-4"])

    has_diabetes = contains_any(text_all, ["diabetes", "type 2 diabetes", "type 1 diabetes", "e11", "e10"])
    has_hba1c = contains_any(obs, ["hemoglobin a1c", "hba1c", "a1c", "4548-4"])
    has_eye_exam = contains_any(text_all, ["retinal", "eye exam", "fundus", "ophthalm", "diabetic eye"])
    has_kidney_eval = contains_any(text_all, ["egfr", "glomerular", "microalbumin", "albumin/creatinine", "uacr", "kidney"])
    has_statin = contains_any(
        med,
        [
            "atorvastatin",
            "rosuvastatin",
            "simvastatin",
            "pravastatin",
            "lovastatin",
            "pitavastatin",
            "fluvastatin",
            "statin",
        ],
    )

    has_mammography = contains_any(text_all, ["mammography", "mammogram", "breast cancer screening"])
    has_cervical_screen = contains_any(text_all, ["pap smear", "cervical", "hpv test", "cervicovaginal"])
    has_colorectal_screen = contains_any(
        text_all,
        ["colonoscopy", "fit", "fobt", "fecal occult", "sigmoidoscopy", "cologuard", "colorectal"],
    )

    def status_relevant(ev: str) -> tuple[str, str, str]:
        return ("relevant_evidence_found", ev, "")

    def status_potential(ev: str, missing: str) -> tuple[str, str, str]:
        return ("potentially_eligible", ev, missing)

    def status_missing(ev: str, missing: str) -> tuple[str, str, str]:
        return ("evidence_missing_or_inconclusive", ev, missing)

    def status_na(reason: str) -> tuple[str, str, str]:
        return ("not_applicable", "", reason)

    if measure == "AAB":
        if has_bronchitis and has_antibiotic:
            return status_relevant("Acute bronchitis context and antibiotic medication evidence found")
        if has_bronchitis:
            return status_potential("Acute bronchitis context found", "No clear antibiotic exposure evidence")
        return status_na("No acute bronchitis evidence")

    if measure == "BCSE":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not female or not (50 <= age <= 74):
            return status_na("Outside female 50-74 screening demographic window")
        if has_mammography:
            return status_relevant("Breast cancer screening/mammography evidence found")
        return status_potential("Demographically in BCSE window", "No mammography evidence found")

    if measure in {"BPD", "CBP"}:
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not (18 <= age <= 85):
            return status_na("Outside hypertension measure age window")
        if has_hypertension and has_bp:
            return status_relevant("Hypertension condition and blood pressure observation evidence found")
        if has_hypertension:
            return status_potential("Hypertension condition found", "No blood pressure observation evidence found")
        return status_na("No hypertension evidence")

    if measure == "CCSE":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not female or not (21 <= age <= 64):
            return status_na("Outside female 21-64 screening demographic window")
        if has_cervical_screen:
            return status_relevant("Cervical screening evidence found")
        return status_potential("Demographically in CCSE window", "No cervical screening evidence found")

    if measure == "COLE":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not (45 <= age <= 75):
            return status_na("Outside colorectal screening age window")
        if has_colorectal_screen:
            return status_relevant("Colorectal screening evidence found")
        return status_potential("Demographically in COLE window", "No colorectal screening evidence found")

    if measure == "EED":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not has_diabetes:
            return status_na("No diabetes evidence")
        if not (18 <= age <= 75):
            return status_na("Outside diabetes eye exam age window")
        if has_eye_exam:
            return status_relevant("Diabetes and eye exam evidence found")
        return status_potential("Diabetes evidence found", "No eye exam evidence found")

    if measure == "GSD":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not has_diabetes:
            return status_na("No diabetes evidence")
        if has_hba1c:
            return status_relevant("Diabetes and HbA1c evidence found")
        return status_potential("Diabetes evidence found", "No HbA1c evidence found")

    if measure == "KED":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not has_diabetes:
            return status_na("No diabetes evidence")
        if has_kidney_eval:
            return status_relevant("Diabetes and kidney evaluation evidence found")
        return status_potential("Diabetes evidence found", "No kidney evaluation evidence found")

    if measure == "SPD":
        if age is None:
            return status_missing("", "Missing birthdate/age")
        if not (40 <= age <= 75):
            return status_na("Outside statin age window")
        if not has_diabetes:
            return status_na("No diabetes evidence for SPD proxy")
        if has_statin:
            return status_relevant("Diabetes and statin medication evidence found")
        return status_potential("Diabetes evidence found", "No statin medication evidence found")

    return status_missing("", "Unknown measure")


def load_facts(csv_dir: Path) -> dict[str, PatientFacts]:
    facts: dict[str, PatientFacts] = {}

    def get_fact(pid: str) -> PatientFacts:
        if pid not in facts:
            facts[pid] = PatientFacts(patient_id=pid)
        return facts[pid]

    with (csv_dir / "patients.csv").open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("Id", "")
            if not pid:
                continue
            item = get_fact(pid)
            item.birthdate = row.get("BIRTHDATE")
            item.gender = row.get("GENDER")
            item.age_2026 = parse_birthdate_to_age_2026(item.birthdate)

    sources = [
        ("conditions.csv", "PATIENT", ["DESCRIPTION", "CODE", "SYSTEM"], "condition_texts"),
        ("observations.csv", "PATIENT", ["DESCRIPTION", "CODE", "VALUE", "UNITS"], "observation_texts"),
        ("procedures.csv", "PATIENT", ["DESCRIPTION", "CODE", "SYSTEM", "REASONDESCRIPTION"], "procedure_texts"),
        ("medications.csv", "PATIENT", ["DESCRIPTION", "CODE", "REASONDESCRIPTION"], "medication_texts"),
        ("encounters.csv", "PATIENT", ["DESCRIPTION", "CODE", "REASONDESCRIPTION", "REASONCODE", "ENCOUNTERCLASS"], "encounter_texts"),
    ]

    for filename, patient_col, cols, target in sources:
        path = csv_dir / filename
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get(patient_col, "")
                if not pid:
                    continue
                item = get_fact(pid)
                pieces = [normalize(row.get(c)) for c in cols if row.get(c)]
                txt = " | ".join([p for p in pieces if p])
                if txt:
                    getattr(item, target).add(txt)

    return facts


def build_matrix(facts: dict[str, PatientFacts]) -> dict[str, dict[str, dict[str, str]]]:
    matrix: dict[str, dict[str, dict[str, str]]] = {}
    for pid, pf in facts.items():
        matrix[pid] = {}
        for m in MEASURES:
            st, ev, miss = measure_status(pf, m)
            matrix[pid][m] = {"status": st, "evidence": ev, "missing": miss}
    return matrix


def select_compact_cohort(matrix: dict[str, dict[str, dict[str, str]]]) -> tuple[list[str], set[str], set[str], dict[str, str]]:
    relevant_patients_by_measure: dict[str, set[str]] = {m: set() for m in MEASURES}
    potential_patients_by_measure: dict[str, set[str]] = {m: set() for m in MEASURES}

    for pid, mm in matrix.items():
        for m in MEASURES:
            st = mm[m]["status"]
            if st == "relevant_evidence_found":
                relevant_patients_by_measure[m].add(pid)
            elif st == "potentially_eligible":
                potential_patients_by_measure[m].add(pid)

    measures_with_any_relevant = {m for m in MEASURES if relevant_patients_by_measure[m]}
    measures_with_potential_only = {
        m for m in MEASURES if not relevant_patients_by_measure[m] and potential_patients_by_measure[m]
    }

    def relevant_count(pid: str) -> int:
        return sum(1 for m in MEASURES if matrix[pid][m]["status"] == "relevant_evidence_found")

    covered_relevant: set[str] = set()
    selected: list[str] = []
    remaining = set(matrix.keys())
    measure_anchor: dict[str, str] = {}

    # Step 1: force explicit per-measure anchors with diversity preference.
    for m in MEASURES:
        target_status = "relevant_evidence_found" if relevant_patients_by_measure[m] else "potentially_eligible"
        candidates = [pid for pid in matrix if matrix[pid][m]["status"] == target_status]
        if not candidates:
            continue

        non_selected = [pid for pid in candidates if pid not in selected]
        pool = non_selected if non_selected else candidates
        best = max(pool, key=lambda pid: (relevant_count(pid), pid))
        if best not in selected:
            selected.append(best)
            if best in remaining:
                remaining.remove(best)
        measure_anchor[m] = best
        if target_status == "relevant_evidence_found":
            covered_relevant.add(m)

    # Step 2: greedy fill only if some measures with relevant evidence are still uncovered.
    while True:
        uncovered = measures_with_any_relevant - covered_relevant
        if not uncovered:
            break

        best_pid = None
        best_score = -1
        for pid in list(remaining):
            score = 0
            for m in uncovered:
                if matrix[pid][m]["status"] == "relevant_evidence_found":
                    score += 1
            if score > best_score:
                best_score = score
                best_pid = pid

        if best_pid is None or best_score <= 0:
            break

        selected.append(best_pid)
        remaining.remove(best_pid)
        for m in MEASURES:
            if matrix[best_pid][m]["status"] == "relevant_evidence_found":
                covered_relevant.add(m)

    uncovered_potential = set(measures_with_potential_only)
    while uncovered_potential:
        best_pid = None
        best_score = -1
        for pid in matrix:
            if pid in selected:
                continue
            score = sum(1 for m in uncovered_potential if matrix[pid][m]["status"] == "potentially_eligible")
            if score > best_score:
                best_score = score
                best_pid = pid
        if best_pid is None or best_score <= 0:
            break
        selected.append(best_pid)
        for m in list(uncovered_potential):
            if matrix[best_pid][m]["status"] == "potentially_eligible":
                uncovered_potential.remove(m)
                if m not in measure_anchor:
                    measure_anchor[m] = best_pid

    measures_missing_relevant = {m for m in MEASURES if m not in covered_relevant}
    measures_with_potential_only = {
        m for m in measures_missing_relevant if any(matrix[pid][m]["status"] == "potentially_eligible" for pid in matrix)
    }
    measures_not_covered = {
        m
        for m in measures_missing_relevant
        if not any(matrix[pid][m]["status"] in {"relevant_evidence_found", "potentially_eligible"} for pid in matrix)
    }

    for m in MEASURES:
        if m in measure_anchor:
            continue
        if relevant_patients_by_measure[m]:
            measure_anchor[m] = max(relevant_patients_by_measure[m], key=lambda pid: (relevant_count(pid), pid))
        elif potential_patients_by_measure[m]:
            measure_anchor[m] = max(potential_patients_by_measure[m], key=lambda pid: (relevant_count(pid), pid))

    return selected, measures_with_potential_only, measures_not_covered, measure_anchor


def ensure_output_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = base_dir.parent / f"{base_dir.name}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_patients_csv(csv_dir: Path, out_dir: Path, selected_ids: set[str]) -> None:
    src = csv_dir / "patients.csv"
    dst = out_dir / "patients.csv"
    with src.open("r", newline="", encoding="utf-8") as fi, dst.open("w", newline="", encoding="utf-8") as fo:
        reader = csv.DictReader(fi)
        writer = csv.DictWriter(fo, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            if row.get("Id", "") in selected_ids:
                writer.writerow(row)


def write_root_filtered_tables(csv_dir: Path, out_dir: Path, selected_ids: set[str]) -> dict[str, int]:
    table_specs = {
        "encounters.csv": "PATIENT",
        "conditions.csv": "PATIENT",
        "observations.csv": "PATIENT",
        "procedures.csv": "PATIENT",
        "medications.csv": "PATIENT",
        "careplans.csv": "PATIENT",
        "immunizations.csv": "PATIENT",
        "coverages.csv": "PATIENT",
        "payer_transitions.csv": "PATIENT",
        "claims.csv": "PATIENTID",
    }

    counts: dict[str, int] = {}
    selected_claim_ids: set[str] = set()

    for filename, patient_col in table_specs.items():
        src = csv_dir / filename
        if not src.exists():
            continue
        dst = out_dir / filename
        count = 0
        with src.open("r", newline="", encoding="utf-8") as fi, dst.open("w", newline="", encoding="utf-8") as fo:
            reader = csv.DictReader(fi)
            writer = csv.DictWriter(fo, fieldnames=reader.fieldnames or [])
            writer.writeheader()
            for row in reader:
                pid = row.get(patient_col, "")
                if pid not in selected_ids:
                    continue
                writer.writerow(row)
                count += 1
                if filename == "claims.csv":
                    claim_id = row.get("Id", "")
                    if claim_id:
                        selected_claim_ids.add(claim_id)
        counts[filename] = count

    tx_src = csv_dir / "claims_transactions.csv"
    if tx_src.exists():
        tx_dst = out_dir / "claims_transactions.csv"
        tx_count = 0
        with tx_src.open("r", newline="", encoding="utf-8") as fi, tx_dst.open("w", newline="", encoding="utf-8") as fo:
            reader = csv.DictReader(fi)
            writer = csv.DictWriter(fo, fieldnames=reader.fieldnames or [])
            writer.writeheader()
            for row in reader:
                pid = row.get("PATIENTID", "")
                claim_id = row.get("CLAIMID", "")
                if pid in selected_ids or claim_id in selected_claim_ids:
                    writer.writerow(row)
                    tx_count += 1
        counts["claims_transactions.csv"] = tx_count

    return counts


def write_coverage_csv(out_dir: Path, selected: list[str], facts: dict[str, PatientFacts], matrix: dict[str, dict[str, dict[str, str]]]) -> None:
    dst = out_dir / "patient_measure_coverage.csv"
    cols = ["patient_id", "gender", "birthdate", "age_2026"]
    for m in MEASURES:
        cols.extend([f"{m}_status", f"{m}_evidence", f"{m}_missing"])

    with dst.open("w", newline="", encoding="utf-8") as fo:
        writer = csv.DictWriter(fo, fieldnames=cols)
        writer.writeheader()
        for pid in selected:
            pf = facts[pid]
            row: dict[str, Any] = {
                "patient_id": pid,
                "gender": pf.gender or "",
                "birthdate": pf.birthdate or "",
                "age_2026": pf.age_2026 if pf.age_2026 is not None else "",
            }
            for m in MEASURES:
                row[f"{m}_status"] = matrix[pid][m]["status"]
                row[f"{m}_evidence"] = matrix[pid][m]["evidence"]
                row[f"{m}_missing"] = matrix[pid][m]["missing"]
            writer.writerow(row)


def write_measure_anchor_csv(out_dir: Path, measure_anchor: dict[str, str], matrix: dict[str, dict[str, dict[str, str]]]) -> None:
    dst = out_dir / "measure_anchor_patients.csv"
    with dst.open("w", newline="", encoding="utf-8") as fo:
        writer = csv.DictWriter(fo, fieldnames=["measure", "anchor_patient_id", "anchor_status", "anchor_evidence", "anchor_missing"])
        writer.writeheader()
        for m in MEASURES:
            pid = measure_anchor.get(m, "")
            st = matrix[pid][m]["status"] if pid else ""
            ev = matrix[pid][m]["evidence"] if pid else ""
            miss = matrix[pid][m]["missing"] if pid else ""
            writer.writerow(
                {
                    "measure": m,
                    "anchor_patient_id": pid,
                    "anchor_status": st,
                    "anchor_evidence": ev,
                    "anchor_missing": miss,
                }
            )


def export_per_patient_tables(csv_dir: Path, out_dir: Path, selected_ids: set[str]) -> dict[str, int]:
    per_patient_dir = out_dir / "patients"
    per_patient_dir.mkdir(parents=True, exist_ok=True)

    table_specs = {
        "encounters.csv": "PATIENT",
        "conditions.csv": "PATIENT",
        "observations.csv": "PATIENT",
        "procedures.csv": "PATIENT",
        "medications.csv": "PATIENT",
        "careplans.csv": "PATIENT",
        "immunizations.csv": "PATIENT",
        "payer_transitions.csv": "PATIENT",
        "claims.csv": "PATIENTID",
    }

    row_counts: dict[str, int] = defaultdict(int)
    selected_claim_ids: set[str] = set()

    for filename, patient_col in table_specs.items():
        src = csv_dir / filename
        if not src.exists():
            continue

        writers: dict[str, tuple[Any, Any]] = {}
        try:
            with src.open("r", newline="", encoding="utf-8") as fi:
                reader = csv.DictReader(fi)
                headers = reader.fieldnames or []
                for row in reader:
                    pid = row.get(patient_col, "")
                    if pid not in selected_ids:
                        continue

                    patient_folder = per_patient_dir / pid
                    patient_folder.mkdir(parents=True, exist_ok=True)
                    dst = patient_folder / filename

                    if pid not in writers:
                        fo = dst.open("w", newline="", encoding="utf-8")
                        w = csv.DictWriter(fo, fieldnames=headers)
                        w.writeheader()
                        writers[pid] = (fo, w)

                    _, w = writers[pid]
                    w.writerow(row)
                    row_counts[filename] += 1

                    if filename == "claims.csv":
                        claim_id = row.get("Id", "")
                        if claim_id:
                            selected_claim_ids.add(claim_id)
        finally:
            for fo, _ in writers.values():
                fo.close()

    tx_src = csv_dir / "claims_transactions.csv"
    if tx_src.exists():
        writers: dict[str, tuple[Any, Any]] = {}
        try:
            with tx_src.open("r", newline="", encoding="utf-8") as fi:
                reader = csv.DictReader(fi)
                headers = reader.fieldnames or []
                for row in reader:
                    pid = row.get("PATIENTID", "")
                    claim_id = row.get("CLAIMID", "")
                    if pid not in selected_ids and claim_id not in selected_claim_ids:
                        continue
                    if pid not in selected_ids:
                        continue

                    patient_folder = per_patient_dir / pid
                    patient_folder.mkdir(parents=True, exist_ok=True)
                    dst = patient_folder / "claims_transactions.csv"

                    if pid not in writers:
                        fo = dst.open("w", newline="", encoding="utf-8")
                        w = csv.DictWriter(fo, fieldnames=headers)
                        w.writeheader()
                        writers[pid] = (fo, w)

                    _, w = writers[pid]
                    w.writerow(row)
                    row_counts["claims_transactions.csv"] += 1
        finally:
            for fo, _ in writers.values():
                fo.close()

    return dict(row_counts)


def write_validation(out_dir: Path, selected: list[str], matrix: dict[str, dict[str, dict[str, str]]], missing_potential: set[str], missing_any: set[str], row_counts: dict[str, int]) -> None:
    path = out_dir / "cohort_validation_summary.csv"

    measures_with_relevant = []
    measures_with_potential = []
    for m in MEASURES:
        rel = sum(1 for pid in selected if matrix[pid][m]["status"] == "relevant_evidence_found")
        pot = sum(1 for pid in selected if matrix[pid][m]["status"] == "potentially_eligible")
        measures_with_relevant.append((m, rel))
        measures_with_potential.append((m, pot))

    with path.open("w", newline="", encoding="utf-8") as fo:
        writer = csv.writer(fo)
        writer.writerow(["metric", "value"])
        writer.writerow(["selected_patients_count", len(selected)])
        writer.writerow(["patients_csv_rows", sum(1 for _ in (out_dir / "patients.csv").open("r", encoding="utf-8")) - 1])
        writer.writerow(["measures_total", len(MEASURES)])
        writer.writerow(["measures_missing_relevant_but_have_potential", ";".join(sorted(missing_potential))])
        writer.writerow(["measures_missing_any_coverage", ";".join(sorted(missing_any))])
        for filename, count in sorted(row_counts.items()):
            writer.writerow([f"rows_exported_{filename}", count])
        for m, rel in measures_with_relevant:
            writer.writerow([f"selected_relevant_count_{m}", rel])
        for m, pot in measures_with_potential:
            writer.writerow([f"selected_potential_count_{m}", pot])


def main() -> None:
    csv_dir = Path(r"C:\synthea\synthea-master\output_hedis_csv\csv")
    output_base = Path(r"C:\synthea\synthea-master\output_hedis_csv\hedis_test_cohort")

    if not csv_dir.exists():
        raise SystemExit(f"Missing CSV directory: {csv_dir}")

    facts = load_facts(csv_dir)
    matrix = build_matrix(facts)
    selected, missing_potential, missing_any, measure_anchor = select_compact_cohort(matrix)

    if not selected:
        raise SystemExit("No cohort could be selected from current dataset.")

    out_dir = ensure_output_dir(output_base)
    selected_set = set(selected)

    write_patients_csv(csv_dir, out_dir, selected_set)
    root_table_counts = write_root_filtered_tables(csv_dir, out_dir, selected_set)
    write_coverage_csv(out_dir, selected, facts, matrix)
    write_measure_anchor_csv(out_dir, measure_anchor, matrix)
    row_counts = export_per_patient_tables(csv_dir, out_dir, selected_set)
    for k, v in root_table_counts.items():
        row_counts[f"root_{k}"] = v
    write_validation(out_dir, selected, matrix, missing_potential, missing_any, row_counts)

    print(f"OUTPUT_DIR={out_dir}")
    print(f"SELECTED_PATIENTS={len(selected)}")
    print(f"SELECTED_IDS={';'.join(selected)}")
    print(f"MISSING_RELEVANT_BUT_POTENTIAL={';'.join(sorted(missing_potential))}")
    print(f"MISSING_ANY_COVERAGE={';'.join(sorted(missing_any))}")


if __name__ == "__main__":
    main()
