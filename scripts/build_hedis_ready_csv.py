from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a HEDIS-ready CSV cohort by keeping only patients with IPP=true in one or more measures."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--csv-dir", type=Path, required=True, help="Directory containing Synthea CSV files")
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        required=True,
        help="Directory of patient bundle JSON files produced by CSV->FHIR conversion",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--measurement-year", type=int, default=2026)
    parser.add_argument("--min-ipp-measures", type=int, default=1)
    parser.add_argument("--per-measure-timeout", type=int, default=180)
    parser.add_argument(
        "--measure-pattern",
        default="independent_public_*",
        help="Measure folder glob under engine/examples",
    )
    parser.add_argument(
        "--exclude-measures",
        default="",
        help="Comma-separated measure folder names to skip, e.g. independent_public_bpd,independent_public_cbp",
    )
    return parser.parse_args()


def load_patient_id_map(bundle_dir: Path) -> dict[str, str]:
    """Map Patient.id (FHIR) -> source patient id (bundle filename stem)."""
    out: dict[str, str] = {}
    for file in sorted(bundle_dir.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("resourceType") != "Bundle":
            continue
        fhir_patient_id = None
        for entry in data.get("entry", []):
            resource = (entry or {}).get("resource", {})
            if isinstance(resource, dict) and resource.get("resourceType") == "Patient" and resource.get("id"):
                fhir_patient_id = str(resource["id"])
                break
        if fhir_patient_id:
            out[fhir_patient_id] = file.stem
    return out


def extract_patient_and_ipp(gaps_bundle: dict[str, Any]) -> tuple[str | None, bool]:
    patient_fhir_id = None
    ipp = False

    for entry in gaps_bundle.get("entry", []):
        resource = (entry or {}).get("resource", {}) if isinstance(entry, dict) else {}
        if not isinstance(resource, dict):
            continue

        if resource.get("resourceType") == "Composition":
            ref = ((resource.get("subject") or {}).get("reference") or "")
            if ref.startswith("Patient/"):
                patient_fhir_id = ref.split("/", 1)[1]

        if resource.get("resourceType") == "Patient" and resource.get("id"):
            patient_fhir_id = str(resource.get("id"))

        if resource.get("resourceType") == "MeasureReport":
            subject_ref = ((resource.get("subject") or {}).get("reference") or "")
            if subject_ref.startswith("Patient/"):
                patient_fhir_id = subject_ref.split("/", 1)[1]
            group = (resource.get("group") or [{}])[0]
            for population in (group.get("population") or []):
                coding = ((population.get("code") or {}).get("coding") or [])
                code = coding[0].get("code") if coding else None
                count = int(population.get("count") or 0)
                if code == "initial-population" and count > 0:
                    ipp = True

    return patient_fhir_id, ipp


def discover_measure_bundles(engine_root: Path, pattern: str) -> list[Path]:
    bundles: list[Path] = []
    for measure_dir in sorted((engine_root / "examples").glob(pattern)):
        bundles.extend(sorted(measure_dir.glob("**/*-bundle.json")))
    return bundles


def extract_root_lib_ref(measure_bundle: Path) -> str:
    payload = json.loads(measure_bundle.read_text(encoding="utf-8"))
    measure = None
    if payload.get("resourceType") == "Measure":
        measure = payload
    else:
        for entry in payload.get("entry", []):
            resource = (entry or {}).get("resource", {}) if isinstance(entry, dict) else {}
            if isinstance(resource, dict) and resource.get("resourceType") == "Measure":
                measure = resource
                break

    if not isinstance(measure, dict):
        stem = measure_bundle.stem
        return stem[:-7] if stem.endswith("-bundle") else stem

    libraries = measure.get("library")
    lib = None
    if isinstance(libraries, list) and libraries:
        lib = libraries[0]
    elif isinstance(libraries, (str, dict)):
        lib = libraries

    ref = None
    if isinstance(lib, dict):
        ref = lib.get("reference")
    elif isinstance(lib, str):
        ref = lib

    if isinstance(ref, str) and ref.strip():
        tail = ref.strip().rsplit("/", 1)[-1]
        return tail.split("|", 1)[0].strip()

    mid = measure.get("id")
    if isinstance(mid, str) and mid.strip():
        return mid.strip()

    stem = measure_bundle.stem
    return stem[:-7] if stem.endswith("-bundle") else stem


def parse_excluded(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def keep_rows(input_file: Path, output_file: Path, selected_patient_ids: set[str]) -> int:
    with input_file.open("r", newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        rows = list(reader)

    if not rows:
        output_file.write_text("", encoding="utf-8")
        return 0

    header = reader.fieldnames or []
    expected_key_by_file = {
        "patients.csv": "Id",
        "claims.csv": "PATIENTID",
        "encounters.csv": "PATIENT",
        "conditions.csv": "PATIENT",
        "observations.csv": "PATIENT",
        "procedures.csv": "PATIENT",
        "medications.csv": "PATIENT",
        "immunizations.csv": "PATIENT",
        "careplans.csv": "PATIENT",
        "coverages.csv": "PATIENT",
        "payer_transitions.csv": "PATIENT",
    }
    expected_key = expected_key_by_file.get(input_file.name, "PATIENT")
    header_lookup = {h.lower(): h for h in header}
    key_col = header_lookup.get(expected_key.lower())
    if key_col is None:
        raise SystemExit(
            f"Missing expected patient key column '{expected_key}' in {input_file.name}. "
            f"Header columns: {header}"
        )

    filtered = [row for row in rows if (row.get(key_col) or "") in selected_patient_ids]

    with output_file.open("w", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=header)
        writer.writeheader()
        writer.writerows(filtered)

    return len(filtered)


def main() -> None:
    args = parse_args()

    repo_root = args.repo_root.resolve()
    csv_dir = args.csv_dir.resolve()
    bundle_dir = args.bundle_dir.resolve()
    output_dir = args.output_dir.resolve()

    engine_root = repo_root / "services" / "measure_calc" / "engine"
    cli = engine_root / "build" / "cli.js"

    if not cli.exists():
        raise SystemExit(f"CLI not found: {cli}")
    if not csv_dir.exists():
        raise SystemExit(f"CSV dir not found: {csv_dir}")
    if not bundle_dir.exists():
        raise SystemExit(f"Bundle dir not found: {bundle_dir}")

    measure_bundles = discover_measure_bundles(engine_root, args.measure_pattern)
    excluded = parse_excluded(args.exclude_measures)
    if excluded:
        measure_bundles = [mb for mb in measure_bundles if mb.parent.name not in excluded]
    if not measure_bundles:
        raise SystemExit("No measure bundle files found.")

    fhir_to_source = load_patient_id_map(bundle_dir)
    if not fhir_to_source:
        raise SystemExit("No patient mapping found in bundle dir.")

    ipp_hits_by_source_patient: dict[str, set[str]] = defaultdict(set)
    run_status: list[dict[str, Any]] = []

    start = f"{args.measurement_year}-01-01"
    end = f"{args.measurement_year}-12-31"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for mb in measure_bundles:
            key = mb.parent.name
            out_file = tmp / f"{key}.json"
            root_lib_ref = extract_root_lib_ref(mb)
            cmd = [
                "node",
                str(cli),
                "gaps",
                "--measure-bundle",
                str(mb),
                "--patients-directory",
                str(bundle_dir),
                "--root-lib-ref",
                root_lib_ref,
                "--measurement-period-start",
                start,
                "--measurement-period-end",
                end,
                "--trust-meta-profile",
                "false",
                "--fast-mode",
                "--out-file",
                str(out_file),
            ]

            try:
                run = subprocess.run(
                    cmd,
                    cwd=engine_root,
                    text=True,
                    capture_output=True,
                    timeout=args.per_measure_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                run_status.append({"measure": key, "status": "timeout"})
                continue

            if run.returncode != 0 or not out_file.exists():
                run_status.append(
                    {
                        "measure": key,
                        "status": "error",
                        "tail": (run.stderr or run.stdout)[-300:],
                    }
                )
                continue

            rows = json.loads(out_file.read_text(encoding="utf-8"))
            measure_ipp_count = 0
            for gaps_bundle in rows:
                fhir_pid, ipp = extract_patient_and_ipp(gaps_bundle)
                if not fhir_pid or not ipp:
                    continue
                source_pid = fhir_to_source.get(fhir_pid)
                if not source_pid:
                    continue
                ipp_hits_by_source_patient[source_pid].add(key)
                measure_ipp_count += 1

            run_status.append({"measure": key, "status": "ok", "ipp_true_count": measure_ipp_count})

    selected = {
        pid
        for pid, measures in ipp_hits_by_source_patient.items()
        if len(measures) >= args.min_ipp_measures
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    files_to_filter = [
        "patients.csv",
        "encounters.csv",
        "conditions.csv",
        "observations.csv",
        "procedures.csv",
        "medications.csv",
        "immunizations.csv",
        "careplans.csv",
        "claims.csv",
        "payer_transitions.csv",
        "coverages.csv",
    ]

    row_counts: dict[str, int] = {}
    for filename in files_to_filter:
        src = csv_dir / filename
        if not src.exists():
            continue
        dst = output_dir / filename
        row_counts[filename] = keep_rows(src, dst, selected)

    # Guardrail: avoid silently producing clinically empty cohorts.
    non_patient_files = [
        name for name in row_counts.keys() if name != "patients.csv"
    ]
    non_patient_total = sum(row_counts[name] for name in non_patient_files)
    if selected and non_patient_total == 0:
        raise SystemExit(
            "Filtered cohort has zero non-patient rows. "
            "Refusing to write an unusable test cohort; check patient selection and key-column mapping."
        )

    summary = {
        "measurement_year": args.measurement_year,
        "bundle_dir": str(bundle_dir),
        "csv_dir": str(csv_dir),
        "output_dir": str(output_dir),
        "excluded_measures": sorted(excluded),
        "total_bundle_patients": len(fhir_to_source),
        "selected_patients": len(selected),
        "min_ipp_measures": args.min_ipp_measures,
        "measure_runs": run_status,
        "selected_patient_ids_sample": sorted(selected)[:50],
        "rows_written_by_file": row_counts,
    }

    summary_path = output_dir / "hedis_ready_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"SELECTED_PATIENTS={len(selected)}")
    print(f"SUMMARY_FILE={summary_path}")


if __name__ == "__main__":
    main()
