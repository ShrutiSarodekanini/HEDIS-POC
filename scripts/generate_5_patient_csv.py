from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


MEASURE_CODES = ("aab", "bcse", "ccse", "cole", "eed", "gsd", "ked", "spd")
POP_ALIASES = {
    "IPP": "IPP",
    "DEN": "DEN",
    "NUM": "NUM",
    "INITIAL-POPULATION": "IPP",
    "DENOMINATOR": "DEN",
    "NUMERATOR": "NUM",
}

PATIENT_COLUMN_CANDIDATES = ("PATIENT", "PATIENTID", "patient", "patient_id", "MEMBER_ID")
PATIENT_ID_COLUMN_CANDIDATES = ("Id", "ID", "id")

ID_EXPECTED_FILES = {
    "patients.csv",
    "encounters.csv",
    "conditions.csv",
    "observations.csv",
    "procedures.csv",
    "medications.csv",
    "immunizations.csv",
    "careplans.csv",
    "claims.csv",
    "coverages.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a 5-patient CSV package from Synthea where patients match IPP+DEN+NUM "
            "for the active 8 measures (AAB, BCSE, CCSE, COLE, EED, GSD, KED, SPD)."
        )
    )
    parser.add_argument(
        "--synthea-root",
        default=r"C:\synthea\synthea-master\output_hedis_csv",
        help="Root folder containing Synthea output datasets.",
    )
    parser.add_argument(
        "--dataset-dir",
        default="",
        help="Optional explicit dataset directory containing patients.csv.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of patients to export.",
    )
    parser.add_argument(
        "--min-full-met",
        type=int,
        default=1,
        help=(
            "Minimum number of measures where IPP+DEN+NUM are all true for preferred selection. "
            "If not enough patients match, script fills remaining slots with best available scores."
        ),
    )
    parser.add_argument(
        "--measurement-start",
        default="2026-01-01",
        help="Measurement period start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--measurement-end",
        default="2026-12-31",
        help="Measurement period end date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--output-root",
        default="C:\\",
        help="Root folder where export folder will be created.",
    )
    return parser.parse_args()


def load_json_with_fallback(path: Path) -> dict[str, Any]:
    last_exc: Exception | None = None
    for enc in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise RuntimeError(f"Unable to parse JSON file: {path} ({last_exc})")


def discover_dataset_dir(synthea_root: Path, explicit_dir: str) -> Path:
    if explicit_dir:
        candidate = Path(explicit_dir)
        if (candidate / "patients.csv").is_file():
            return candidate
        raise RuntimeError(f"dataset-dir is missing patients.csv: {candidate}")

    matches = [p.parent for p in synthea_root.rglob("patients.csv") if p.is_file()]
    if not matches:
        raise RuntimeError(f"No dataset with patients.csv found under: {synthea_root}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def extract_patient_id(bundle: dict[str, Any]) -> str | None:
    for entry in bundle.get("entry", []):
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource", {})
        if isinstance(resource, dict) and resource.get("resourceType") == "Patient" and resource.get("id"):
            return str(resource["id"])
    return None


def resolve_measures(engine_root: Path) -> list[Path]:
    all_paths = list((engine_root / "examples").glob("independent_public_*/**/*-bundle.json"))
    selected: list[Path] = []
    for code in MEASURE_CODES:
        matches = sorted([p for p in all_paths if code in p.parent.name.lower()])
        if not matches:
            raise RuntimeError(f"Missing required measure bundle for code: {code}")
        selected.append(matches[0])
    return selected


def root_lib_ref(measure_bundle_path: Path) -> str:
    payload = load_json_with_fallback(measure_bundle_path)
    resources = [payload]
    if payload.get("resourceType") == "Bundle":
        resources = [
            entry.get("resource", {})
            for entry in payload.get("entry", [])
            if isinstance(entry, dict)
        ]

    measure = next((r for r in resources if isinstance(r, dict) and r.get("resourceType") == "Measure"), {})
    libraries = measure.get("library")
    if isinstance(libraries, list):
        libraries = libraries[0] if libraries else None

    ref = libraries.get("reference") if isinstance(libraries, dict) else libraries if isinstance(libraries, str) else None
    return ref or str(measure.get("id") or measure_bundle_path.stem.replace("-bundle", ""))


def extract_flags(payload: Any) -> dict[str, bool]:
    out = {"IPP": False, "DEN": False, "NUM": False}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            population_type = node.get("populationType")
            canonical = POP_ALIASES.get(str(population_type).upper()) if population_type is not None else None
            if canonical and "result" in node:
                out[canonical] = out[canonical] or bool(node.get("result"))
            for value in node.values():
                walk(value)
            return

        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return out


def run_measure(
    engine_root: Path,
    cli_path: Path,
    measure_path: Path,
    patient_bundle_path: Path,
    measurement_start: str,
    measurement_end: str,
) -> dict[str, bool]:
    cmd = [
        "node",
        str(cli_path),
        "detailed",
        "--measure-bundle",
        str(measure_path),
        "--patient-bundles",
        str(patient_bundle_path),
        "--root-lib-ref",
        root_lib_ref(measure_path),
        "--measurement-period-start",
        measurement_start,
        "--measurement-period-end",
        measurement_end,
        "--trust-meta-profile",
        "false",
    ]

    proc = subprocess.run(cmd, cwd=engine_root, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Measure run failed for {measure_path.name}: {detail[-1000:]}")

    payload = json.loads(proc.stdout)
    return extract_flags(payload)


def normalize_header(value: str) -> str:
    return value.strip().lower().lstrip("\ufeff")


def find_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {normalize_header(name): name for name in fieldnames}
    for candidate in candidates:
        found = normalized.get(normalize_header(candidate))
        if found:
            return found
    return None


def rows_from_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def ensure_id_column(csv_path: Path) -> None:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = list(csv.reader(handle))
    if not reader:
        return

    header = reader[0]
    if any(normalize_header(name) == "id" for name in header):
        return

    updated = [["Id", *header]]
    for idx, row in enumerate(reader[1:], start=1):
        updated.append([f"{csv_path.stem}-row-{idx}", *row])

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(updated)


def main() -> None:
    args = parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from services.data_ingestion.app.mapping import bundles_by_patient, map_dataset
    from services.data_ingestion.app.synthea import parse_dataset

    engine_root = repo_root / "services" / "measure_calc" / "engine"
    cli_path = engine_root / "build" / "cli.js"
    if not cli_path.is_file():
        raise RuntimeError(f"Engine CLI not found: {cli_path}")

    dataset_dir = discover_dataset_dir(Path(args.synthea_root), args.dataset_dir)

    parsed = parse_dataset(str(dataset_dir))
    mapped = map_dataset(parsed)
    grouped = bundles_by_patient(mapped.resources)

    patients: list[dict[str, Any]] = []
    for source_key, value in grouped.items():
        bundle = value.get("bundle", value) if isinstance(value, dict) else value
        if isinstance(bundle, dict) and bundle.get("resourceType") == "Bundle":
            patients.append(
                {
                    "source_key": str(source_key),
                    "fhir_patient_id": extract_patient_id(bundle),
                    "bundle": bundle,
                }
            )

    if len(patients) < args.top_n:
        raise RuntimeError(f"Only {len(patients)} patient bundles found; need at least {args.top_n}.")

    measures = resolve_measures(engine_root)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        for patient in patients:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", patient["source_key"])
            patient_file = temp_path / f"{safe_name}.json"
            patient_file.write_text(json.dumps(patient["bundle"]), encoding="utf-8")

            per_measure: dict[str, dict[str, bool]] = {}
            for measure_path in measures:
                measure_name = f"{measure_path.parent.name}/{measure_path.name}"
                flags = run_measure(
                    engine_root,
                    cli_path,
                    measure_path,
                    patient_file,
                    args.measurement_start,
                    args.measurement_end,
                )
                per_measure[measure_name] = flags

            values = list(per_measure.values())
            patient["per_measure"] = per_measure
            patient["full_met_count"] = sum(1 for f in values if f["IPP"] and f["DEN"] and f["NUM"])
            patient["den_count"] = sum(1 for f in values if f["DEN"])
            patient["num_count"] = sum(1 for f in values if f["NUM"])
            patient["ipp_count"] = sum(1 for f in values if f["IPP"])

    preferred = [p for p in patients if p["full_met_count"] >= args.min_full_met]
    remaining = [p for p in patients if p["full_met_count"] < args.min_full_met]

    sort_key = lambda p: (  # noqa: E731
        -p["full_met_count"],
        -p["den_count"],
        -p["num_count"],
        -p["ipp_count"],
        p["source_key"],
    )

    preferred.sort(key=sort_key)
    remaining.sort(key=sort_key)

    selected = preferred[: args.top_n]
    if len(selected) < args.top_n:
        selected.extend(remaining[: args.top_n - len(selected)])

    selected_keys = {p["source_key"] for p in selected}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = Path(args.output_root) / f"hedis_poc_{args.top_n}_patients_csv_{stamp}"
    per_patient_root = export_dir / "per_patient"
    combined_root = export_dir / "combined"
    per_patient_root.mkdir(parents=True, exist_ok=False)
    combined_root.mkdir(parents=True, exist_ok=False)

    per_patient_file_count = 0
    combined_file_count = 0
    per_patient_row_counts: dict[str, dict[str, int]] = {p["source_key"]: {} for p in selected}
    combined_row_counts: dict[str, int] = {}

    for csv_path in sorted(dataset_dir.glob("*.csv")):
        fieldnames, rows = rows_from_csv(csv_path)
        if not fieldnames:
            continue

        file_name = csv_path.name
        patient_col = find_column(fieldnames, PATIENT_COLUMN_CANDIDATES)
        id_col_for_patients = find_column(fieldnames, PATIENT_ID_COLUMN_CANDIDATES) if file_name.lower() == "patients.csv" else None

        selected_rows_by_patient: dict[str, list[dict[str, str]]] = {p["source_key"]: [] for p in selected}

        for row in rows:
            key_value = None
            if file_name.lower() == "patients.csv" and id_col_for_patients:
                key_value = (row.get(id_col_for_patients) or "").strip()
            elif patient_col:
                key_value = (row.get(patient_col) or "").strip()

            if key_value in selected_keys:
                selected_rows_by_patient[key_value].append(row)

        any_rows_for_file = False
        combined_rows: list[dict[str, str]] = []

        for patient in selected:
            source_key = patient["source_key"]
            patient_rows = selected_rows_by_patient.get(source_key, [])
            if not patient_rows:
                continue

            any_rows_for_file = True
            target = per_patient_root / source_key / file_name
            write_csv(target, fieldnames, patient_rows)
            per_patient_file_count += 1
            per_patient_row_counts[source_key][file_name] = len(patient_rows)
            combined_rows.extend(patient_rows)

        if any_rows_for_file:
            combined_target = combined_root / file_name
            write_csv(combined_target, fieldnames, combined_rows)
            combined_file_count += 1
            combined_row_counts[file_name] = len(combined_rows)

    for csv_file in per_patient_root.rglob("*.csv"):
        if csv_file.name.lower() in ID_EXPECTED_FILES:
            ensure_id_column(csv_file)
    for csv_file in combined_root.glob("*.csv"):
        if csv_file.name.lower() in ID_EXPECTED_FILES:
            ensure_id_column(csv_file)

    strict_all8_count = sum(1 for p in patients if p["full_met_count"] == len(measures))

    summary = {
        "dataset_dir": str(dataset_dir),
        "measurement_period": {
            "start": args.measurement_start,
            "end": args.measurement_end,
        },
        "top_n": args.top_n,
        "min_full_met": args.min_full_met,
        "strict_all8_count": strict_all8_count,
        "selected_patients": [
            {
                "source_key": p["source_key"],
                "fhir_patient_id": p.get("fhir_patient_id"),
                "full_met_count": p["full_met_count"],
                "den_count": p["den_count"],
                "num_count": p["num_count"],
                "ipp_count": p["ipp_count"],
                "per_measure": p["per_measure"],
            }
            for p in selected
        ],
        "csv_counts": {
            "per_patient_total_files": per_patient_file_count,
            "combined_files": combined_file_count,
            "per_patient_rows_by_file": per_patient_row_counts,
            "combined_rows_by_file": combined_row_counts,
        },
    }
    summary_path = export_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    zip_path = Path.home() / "Downloads" / f"{export_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in export_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(export_dir.parent))

    print(f"DATASET_DIR={dataset_dir}")
    print(f"EXPORT_DIR={export_dir}")
    print(f"ZIP_FILE={zip_path}")
    print(f"STRICT_ALL8_COUNT={strict_all8_count}")
    print("SELECTED_PATIENTS=" + ",".join(p["source_key"] for p in selected))
    print(
        "TOP_SCORES="
        + json.dumps(
            [
                {
                    "source_key": p["source_key"],
                    "full_met_count": p["full_met_count"],
                    "den_count": p["den_count"],
                    "num_count": p["num_count"],
                    "ipp_count": p["ipp_count"],
                }
                for p in selected
            ],
            separators=(",", ":"),
        )
    )
    print(f"PER_PATIENT_TOTAL_CSV_FILES={per_patient_file_count}")
    print(f"COMBINED_CSV_FILES={combined_file_count}")


if __name__ == "__main__":
    main()
