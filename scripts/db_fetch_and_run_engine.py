from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import desc, select

# Ensure repo root is importable when running this script directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.data_ingestion.app.db import session_scope  # noqa: E402
from services.data_ingestion.app.models import ConversionJob, FhirResource  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a patient FHIR bundle from PostgreSQL and optionally run a HEDIS measure."
    )
    parser.add_argument("--job-id", help="Conversion job ID to read from.")
    parser.add_argument("--patient-id", help="Patient ID to export from the job.")
    parser.add_argument(
        "--output",
        required=True,
        help="Output bundle file path (for example exports/db_patient.bundle.json).",
    )
    parser.add_argument(
        "--run-measure-bundle",
        help="Optional path to a measure bundle JSON. If provided, runs the engine detailed command.",
    )
    parser.add_argument("--measurement-year", type=int, default=2026)
    parser.add_argument(
        "--print-engine-json",
        action="store_true",
        help="Print full engine JSON output (can be very large).",
    )
    return parser.parse_args()


def root_ref(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    resources = [data] if data.get("resourceType") != "Bundle" else [
        e.get("resource", {}) for e in data.get("entry", []) if isinstance(e, dict)
    ]
    measure = next((r for r in resources if r.get("resourceType") == "Measure"), {})
    lib = measure.get("library")
    if isinstance(lib, list):
        lib = lib[0] if lib else None
    ref = lib.get("reference") if isinstance(lib, dict) else lib if isinstance(lib, str) else None
    return ref or measure.get("id") or path.stem[:-7]


def resolve_job_id(explicit_job_id: str | None) -> str:
    if explicit_job_id:
        return explicit_job_id
    with session_scope() as session:
        latest = session.execute(
            select(ConversionJob).order_by(desc(ConversionJob.started_at)).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            raise SystemExit("No conversion jobs found in database.")
        return latest.id


def resolve_patient_id(job_id: str, explicit_patient_id: str | None) -> str:
    if explicit_patient_id:
        return explicit_patient_id

    with session_scope() as session:
        pid = session.execute(
            select(FhirResource.patient_id)
            .where(
                (FhirResource.job_id == job_id)
                & (FhirResource.resource_type == "Patient")
                & (FhirResource.patient_id.is_not(None))
            )
            .limit(1)
        ).scalar_one_or_none()
        if pid is None:
            pid = session.execute(
                select(FhirResource.patient_id)
                .where((FhirResource.job_id == job_id) & (FhirResource.patient_id.is_not(None)))
                .limit(1)
            ).scalar_one_or_none()
    if pid is None:
        raise SystemExit(f"No patient_id found for job {job_id}.")
    return pid


def export_bundle(job_id: str, patient_id: str, output_path: Path) -> int:
    with session_scope() as session:
        resources = session.execute(
            select(FhirResource.resource_json)
            .where((FhirResource.job_id == job_id) & (FhirResource.patient_id == patient_id))
            .order_by(FhirResource.id)
        ).scalars().all()

    entries = [{"resource": r} for r in resources if isinstance(r, dict) and r.get("resourceType")]
    if not entries:
        raise SystemExit(f"No FHIR resources found for job_id={job_id} patient_id={patient_id}.")

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return len(entries)


def _population_flags(payload: object) -> dict[str, bool]:
    flags = {"IPP": False, "DEN": False, "NUM": False}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            population_type = node.get("populationType")
            if population_type in flags and "result" in node:
                flags[population_type] = flags[population_type] or bool(node.get("result"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return flags


def run_engine(bundle_path: Path, measure_bundle_path: Path, measurement_year: int, print_engine_json: bool) -> int:
    repo = Path(__file__).resolve().parents[1]
    engine_root = repo / "services" / "measure_calc" / "engine"
    cli_path = engine_root / "build" / "cli.js"

    if not cli_path.exists():
        raise SystemExit("Engine CLI not built. Run: npm run backend:build")

    cmd = [
        "node",
        str(cli_path),
        "detailed",
        "--measure-bundle",
        str(measure_bundle_path),
        "--patient-bundles",
        str(bundle_path),
        "--root-lib-ref",
        root_ref(measure_bundle_path),
        "--measurement-period-start",
        f"{measurement_year}-01-01",
        "--measurement-period-end",
        f"{measurement_year}-12-31",
        "--trust-meta-profile",
        "false",
    ]

    completed = subprocess.run(cmd, cwd=engine_root, text=True, capture_output=True, check=False)
    print(f"ENGINE_EXIT={completed.returncode}")
    if completed.returncode != 0:
        print((completed.stderr or completed.stdout)[-2000:])
        return completed.returncode

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("ENGINE_OUTPUT_PARSE_ERROR")
        print(completed.stdout[-2000:])
        return 2

    result_count = len(payload) if isinstance(payload, list) else 1
    first_result = payload[0] if isinstance(payload, list) and payload else payload
    print(f"ENGINE_RESULT_COUNT={result_count}")
    print("ENGINE_POP_FLAGS=" + json.dumps(_population_flags(first_result), separators=(",", ":")))

    if print_engine_json:
        print(json.dumps(payload, indent=2))

    return 0


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()

    job_id = resolve_job_id(args.job_id)
    patient_id = resolve_patient_id(job_id, args.patient_id)
    count = export_bundle(job_id, patient_id, output_path)

    print(f"JOB_ID={job_id}")
    print(f"PATIENT_ID={patient_id}")
    print(f"RESOURCES_EXPORTED={count}")
    print(f"BUNDLE_FILE={output_path}")

    if args.run_measure_bundle:
        measure_bundle = Path(args.run_measure_bundle).resolve()
        if not measure_bundle.exists():
            raise SystemExit(f"Measure bundle not found: {measure_bundle}")
        return run_engine(output_path, measure_bundle, args.measurement_year, args.print_engine_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
