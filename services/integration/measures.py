"""Bridge B: run the measure engine for one batch and turn its results into measure_result rows and care gaps.

For each loaded patient one FHIR bundle is written from fhir_resource_raw; the fqm-execution engine then runs in
"detailed" mode for the nine measures in MEASURE_FOLDERS, called the same way the data ingestion service's /run-request-file
endpoint calls it (same command line, root library lookup and blood pressure bundle pruning, reused from that
module). Every patient sent must come back for every measure, or the step fails with the counts.

Gaps are the results in the denominator, not met and not excluded. Two measures need a bridge to the stored data
(see INVERSE_MEASURES and COMBINED_BP_MEASURES below); the engine, its measures and its pruning are not changed.
A later run closes open gaps it shows met or excluded.
"""

import json
import logging
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg

from services.data_ingestion.app.api import (
    _extract_root_library_reference,
    _maybe_prune_run_request_bundle,
    _to_member_result,
)
from services.document_intake.db import audit

from .config import SETTINGS, Settings

log = logging.getLogger(__name__)

ENGINE = "fqm-execution"
ENGINE_VERSION = "1.8.5"
EXAMPLES = SETTINGS.engine_root / "examples"
# AAB, CCS-E and SPD have no star cut points or weights in the reference data, so they are scored as non-Star
# measures and their cards show no star.
MEASURE_FOLDERS = ("independent_public_bcse", "independent_public_cole", "independent_public_eed",
                   "independent_public_gsd", "independent_public_ked", "independent_public_cbp",
                   "independent_public_aab", "independent_public_ccse", "independent_public_spd")
LIBRARY_TO_MEASURE = {
    "PublicBreastCancerScreeningTechnicalDemo": "BCS-E",
    "PublicColorectalCancerScreeningTechnicalDemo": "COL-E",
    "PublicEedDiabetesEyeExamTechnicalDemo": "EED",
    "PublicGsdGlycemicStatusTechnicalDemo": "GSD",
    "PublicKidneyHealthTechnicalDemo": "KED",
    "CMS165FHIRControllingHighBP": "CBP",
    "PublicAabAntibioticAvoidanceTechnicalDemo": "AAB",
    "CMS124FHIRCervicalCancerScreen": "CCS-E",
    "PublicSpdStatinTherapyTechnicalDemo": "SPD",
}
GAP_NAMESPACE = uuid.UUID("6f0f4a52-4f59-4b7e-9a1e-2c8d3b7e5a10")
BP_NAMESPACE = uuid.UUID("0d8f2c3e-6a1b-4f7d-9c55-3e2b8a41d7f6")

# CBP (CMS165) reads blood pressure only from combined readings (US Core blood pressure profile) with systolic and
# diastolic components. The CSV-to-FHIR mapping writes them as two separate Observations, so for CBP only, each pair
# of the same encounter and time becomes one combined reading in the engine bundle. The separate ones are left out of
# that file: the engine runs with --trust-meta-profile false, so it would read them as readings without components,
# and the CQL's lowest-reading sort fails on them. Nothing stored changes; other measures get the bundle as stored.
COMBINED_BP_MEASURES = {"CBP"}
SYSTOLIC, DIASTOLIC, BP_PANEL = "8480-6", "8462-4", "85354-9"
US_CORE_BP = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-blood-pressure"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"

# The GSD bundle (independent_public_gsd) puts POOR glycemic control in its numerator: most recent A1c above 9.0, an
# A1c with no result, or no A1c in the year, although the bundle says improvementNotation "increase". We store "met"
# in measure_result.in_numerator, so for these measures met = in the denominator and not in the engine's numerator,
# and a gap is the engine's numerator. The engine and its bundle are not changed.
INVERSE_MEASURES = {"GSD"}
MISSING_ELEMENT = {"GSD": "Glycemic Status Assessment: most recent A1c above 9.0, or no A1c result in {year}"}

_BUNDLES = """
SELECT patient_id,
       jsonb_build_object('resourceType', 'Bundle', 'type', 'collection',
                          'entry', jsonb_agg(jsonb_build_object('resource', raw) ORDER BY resource_pk))::text
  FROM fhir_resource_raw
 WHERE patient_id = ANY(%s)
 GROUP BY patient_id
"""

_RESULTS = """
INSERT INTO measure_result (patient_id, measure_id, measurement_year, in_denominator, in_numerator, excluded,
                            engine_version, computed_at)
SELECT r.patient_id, %s, %s, r.in_denominator, r.in_numerator, r.excluded, %s, now()
  FROM unnest(%s::text[], %s::boolean[], %s::boolean[], %s::boolean[])
       AS r(patient_id, in_denominator, in_numerator, excluded)
RETURNING result_pk, patient_id
"""

_GAPS = """
INSERT INTO care_gap (gap_id, patient_id, measure_id, measurement_year, status, missing_element, deadline_date,
                      source_measure_result_pk, opened_at)
SELECT g.gap_id, g.patient_id, %s, %s, 'open', %s, %s, g.result_pk, now()
  FROM unnest(%s::uuid[], %s::text[], %s::bigint[]) AS g(gap_id, patient_id, result_pk)
ON CONFLICT (gap_id) DO NOTHING
"""

# Gap ids are fixed per patient, measure and year, so a later run that shows the care done (or the patient excluded)
# closes the existing gap; a reviewer's closed_pending_engine or excluded_pending_engine becomes final here.
_CLOSE_GAPS = """
WITH result AS (
    SELECT * FROM unnest(%(patients)s::text[], %(met)s::boolean[], %(excluded)s::boolean[])
             AS r(patient_id, met, excluded)
     WHERE r.met OR r.excluded
), closing AS (
    SELECT g.gap_id, r.excluded, d.review_status AS before
      FROM care_gap g
      JOIN result r ON r.patient_id = g.patient_id
      LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
     WHERE g.measure_id = %(measure)s AND g.measurement_year = %(year)s AND g.status = 'open'
), closed AS (
    UPDATE care_gap g SET status = 'closed', closed_at = now()
      FROM closing c WHERE g.gap_id = c.gap_id
    RETURNING g.gap_id
), detail AS (
    UPDATE care_gap_detail d
       SET review_status = CASE WHEN c.excluded THEN 'excluded' ELSE 'closed' END, updated_at = now()
      FROM closing c WHERE d.gap_id = c.gap_id
    RETURNING d.gap_id
)
SELECT c.gap_id, c.before, CASE WHEN c.excluded THEN 'excluded' ELSE 'closed' END
  FROM closing c JOIN closed USING (gap_id)
"""

_GAP_DETAILS = """
INSERT INTO care_gap_detail (gap_id, engine_run_id, gap_type, attributed_org_id, review_status, pending_count)
SELECT g.gap_id, %s, 'numerator', g.org_id, 'open', 0
  FROM unnest(%s::uuid[], %s::text[]) AS g(gap_id, org_id)
ON CONFLICT (gap_id) DO NOTHING
"""


@dataclass(frozen=True)
class MeasureBundle:
    measure_id: str
    path: Path
    library: str
    library_version: str | None


@dataclass
class MeasureOutcome:
    """Per measure: the engine's populations and the gaps written for this batch."""
    denominator: int = 0
    numerator: int = 0
    excluded: int = 0
    gaps_new: int = 0
    gaps_open: int = 0
    gaps_closed: int = 0


@dataclass
class EngineRun:
    run_id: str
    patients: int
    measures: dict[str, MeasureOutcome] = field(default_factory=dict)

    def counts(self) -> dict:
        return {"engine_run_id": self.run_id, "patients": self.patients,
                "measures": {m: vars(o) for m, o in self.measures.items()},
                "gaps_open": sum(o.gaps_open for o in self.measures.values()),
                "gaps_closed_by_engine": sum(o.gaps_closed for o in self.measures.values())}


def measure_bundles() -> list[MeasureBundle]:
    """The measure bundles in MEASURE_FOLDERS, with their root library and version."""
    found = []
    for folder in MEASURE_FOLDERS:
        path = next((EXAMPLES / folder).glob("*-bundle.json"))
        library = _extract_root_library_reference(path)
        if library not in LIBRARY_TO_MEASURE:
            raise LookupError(f"measure bundle {path.name} has library {library}, which is not mapped to a measure")
        entries = json.loads(path.read_text(encoding="utf-8")).get("entry", [])
        version = next((e["resource"].get("version") for e in entries
                        if e["resource"].get("resourceType") == "Library" and library in (e["resource"].get("id"),
                                                                                          e["resource"].get("name"))),
                       None)
        found.append(MeasureBundle(LIBRARY_TO_MEASURE[library], path, library, version))
    return found


def run_measures(conn: psycopg.Connection, patient_ids: list[str], attributed_org: dict[str, str | None],
                 batch_label: str, settings: Settings = SETTINGS, measure_ids: list[str] | None = None) -> EngineRun:
    """Run the engine for these patients (all measures, or the chosen ones) and write results and gaps in the
    caller's transaction."""
    bundles = measure_bundles()
    if measure_ids:
        unknown = set(measure_ids) - {b.measure_id for b in bundles}
        if unknown:
            raise ValueError(f"unknown measure: {', '.join(sorted(unknown))}")
        bundles = [b for b in bundles if b.measure_id in measure_ids]
    year = settings.measurement_year
    work = settings.work_dir / f"engine-{batch_label}"
    shutil.rmtree(work, ignore_errors=True)
    try:
        files = _write_bundles(conn, patient_ids, work / "patients")
        run = EngineRun(run_id=f"{ENGINE}-{batch_label}-{uuid.uuid4().hex[:8]}", patients=len(files))
        conn.execute(
            """INSERT INTO engine_runs (run_id, engine, engine_version, measure_spec_version, measurement_year, run_at,
                   source_file) VALUES (%s, %s, %s, %s, %s, now(), %s)""",
            (run.run_id, ENGINE, ENGINE_VERSION, "engine examples independent_public_*", year,
             "services/measure_calc/engine/examples"))
        names = dict(conn.execute("SELECT measure_id, name FROM measure_rule WHERE measurement_year = %s",
                                  (year,)).fetchall())
        evaluations = _evaluate_measures(bundles, files, work, settings)
        for bundle in bundles:
            populations = evaluations[bundle.measure_id]
            run.measures[bundle.measure_id] = _write_results(conn, bundle, populations, attributed_org, run.run_id,
                                                             names.get(bundle.measure_id, bundle.measure_id), year)
            log.info("measure %s: %s", bundle.measure_id, vars(run.measures[bundle.measure_id]))
        return run
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _write_bundles(conn: psycopg.Connection, patient_ids: list[str], folder: Path) -> dict[str, Path]:
    """One FHIR collection bundle per patient, from every raw resource stored for that patient."""
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    with conn.cursor(name="integration_bundles") as cur:
        cur.execute(_BUNDLES, (list(patient_ids),))
        for patient_id, bundle_text in cur:
            path = folder / f"{len(files):06d}.json"
            path.write_text(bundle_text, encoding="utf-8")
            files[patient_id] = path
    missing = len(set(patient_ids) - set(files))
    if missing:
        raise RuntimeError(f"{missing} of {len(patient_ids)} patients have no stored FHIR resources")
    return files


def _evaluate_measures(bundles: list[MeasureBundle], files: dict[str, Path], work: Path,
                       settings: Settings) -> dict[str, dict[str, dict]]:
    """Evaluate independent measures concurrently while retaining deterministic result persistence."""
    worker_count = min(settings.engine_workers, len(bundles))
    if worker_count == 1:
        return {bundle.measure_id: _evaluate(bundle, files, work, settings) for bundle in bundles}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hedis-measure") as executor:
        futures = {bundle.measure_id: executor.submit(_evaluate, bundle, files, work, settings) for bundle in bundles}
        return {bundle.measure_id: futures[bundle.measure_id].result() for bundle in bundles}


def _evaluate(bundle: MeasureBundle, files: dict[str, Path], work: Path, settings: Settings) -> dict[str, dict]:
    """Engine populations per patient for one measure, in chunks so Node's memory stays bounded."""
    root_ref = _extract_root_library_reference(bundle.path)
    patients = list(files)
    results: dict[str, dict] = {}
    for start in range(0, len(patients), settings.engine_chunk):
        chunk = patients[start:start + settings.engine_chunk]
        paths = []
        for patient_id in chunk:
            # Pruning rewrites the file, so each measure works on its own copy.
            copy = work / bundle.measure_id / files[patient_id].name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(files[patient_id], copy)
            added = _add_combined_bp(copy) if bundle.measure_id in COMBINED_BP_MEASURES else 0
            _maybe_prune_run_request_bundle(copy, bundle.measure_id, bundle.path)
            if added and _count_code(copy, BP_PANEL) < added:
                raise RuntimeError(f"blood pressure pruning removed combined readings for {bundle.measure_id}")
            paths.append(str(copy))
        for entry in _call_engine(bundle, root_ref, paths, settings):
            member = _to_member_result(entry, bundle.measure_id)
            results[member["member_id"]] = member["populations"]
        shutil.rmtree(work / bundle.measure_id, ignore_errors=True)
    sent, back = set(patients), set(results)
    if sent != back:
        raise RuntimeError(f"engine returned {len(back & sent)} of {len(sent)} patients for {bundle.measure_id} "
                           f"({len(sent - back)} missing, {len(back - sent)} unexpected)")
    return results


def _code(resource: dict) -> str | None:
    codings = (resource.get("code") or {}).get("coding") or [{}]
    return codings[0].get("code")


def _count_code(path: Path, code: str) -> int:
    entries = json.loads(path.read_text(encoding="utf-8")).get("entry", [])
    return sum(1 for e in entries if e["resource"].get("resourceType") == "Observation" and _code(e["resource"]) == code)


def _bp_component(code: str, display: str, source: dict) -> dict:
    quantity = source.get("valueQuantity") or {}
    return {"code": {"coding": [{"system": LOINC, "code": code, "display": display}]},
            "valueQuantity": {"value": quantity.get("value"), "unit": "mm[Hg]", "system": UCUM, "code": "mm[Hg]"}}


def _add_combined_bp(path: Path) -> int:
    """Replace systolic and diastolic Observations with one combined reading per pair of the same encounter and time."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    pairs: dict[tuple, dict[str, list[dict]]] = {}
    kept = []
    for entry in bundle.get("entry", []):
        r = entry["resource"]
        if r.get("resourceType") == "Observation" and _code(r) in (SYSTOLIC, DIASTOLIC):
            key = (r.get("effectiveDateTime"), (r.get("encounter") or {}).get("reference"))
            pairs.setdefault(key, {SYSTOLIC: [], DIASTOLIC: []})[_code(r)].append(r)
        else:
            kept.append(entry)
    added = []
    for (effective, encounter), found in pairs.items():
        if effective is None or encounter is None:
            continue
        systolic = sorted(found[SYSTOLIC], key=lambda r: r["id"])
        diastolic = sorted(found[DIASTOLIC], key=lambda r: r["id"])
        for s, d in zip(systolic, diastolic):
            added.append({"resource": {
                "resourceType": "Observation", "id": str(uuid.uuid5(BP_NAMESPACE, f"{s['id']}:{d['id']}")),
                "meta": {"profile": [US_CORE_BP]}, "status": "final",
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                           "code": "vital-signs"}]}],
                "code": {"coding": [{"system": LOINC, "code": BP_PANEL,
                                      "display": "Blood pressure panel with all children optional"}]},
                "subject": s.get("subject"), "encounter": {"reference": encounter}, "effectiveDateTime": effective,
                "component": [_bp_component(SYSTOLIC, "Systolic blood pressure", s),
                              _bp_component(DIASTOLIC, "Diastolic blood pressure", d)]}})
    if pairs:
        bundle["entry"] = kept + added
        path.write_text(json.dumps(bundle), encoding="utf-8")
    return len(added)


RETRY_WAIT_SECONDS = 5
# Windows reports a killed Node process as exit 4294967295 (-1 unsigned), usually when the machine is overloaded.
_CRASH_EXIT_CODES = {4294967295}


def _run_engine(cmd: list[str], settings: Settings, measure_id: str, patients: int) -> subprocess.CompletedProcess:
    """Run one engine call; a crash (negative exit code or no output at all) is retried once after 5 seconds."""
    for attempt in (1, 2):
        completed = subprocess.run(cmd, cwd=settings.engine_root, text=True, capture_output=True, check=False,
                                   encoding="utf-8")
        crashed = (completed.returncode < 0 or completed.returncode in _CRASH_EXIT_CODES
                   or not (completed.stdout.strip() or completed.stderr.strip()))
        if not crashed or attempt == 2:
            return completed
        log.warning("engine call for %s (%d patients) crashed with exit %s and %s; retrying once in %ds",
                    measure_id, patients, completed.returncode,
                    "no output" if not completed.stderr.strip() else "an error", RETRY_WAIT_SECONDS)
        time.sleep(RETRY_WAIT_SECONDS)
    return completed


def _call_engine(bundle: MeasureBundle, root_ref: str | None, paths: list[str], settings: Settings) -> list[dict]:
    """The same command line as the ingestion service's /run-request-file, for several patient bundles at once."""
    cli = settings.engine_root / "build" / "cli.js"
    if not cli.exists():
        raise RuntimeError("Measure engine CLI is not built. Run `npm run backend:build`.")
    year = settings.measurement_year
    cmd = ["node", str(cli), "detailed", "--measure-bundle", str(bundle.path), "--patient-bundles", *paths,
           "--measurement-period-start", f"{year}-01-01", "--measurement-period-end", f"{year}-12-31",
           "--trust-meta-profile", "false"]
    if root_ref:
        cmd.extend(["--root-lib-ref", root_ref])
    completed = _run_engine(cmd, settings, bundle.measure_id, len(paths))
    if completed.returncode != 0:
        first = (completed.stderr.strip() or completed.stdout.strip() or "no output").splitlines()[0][:300]
        raise RuntimeError(f"engine failed for {bundle.measure_id} (exit {completed.returncode}): {first}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        first = (completed.stderr.strip() or completed.stdout.strip() or "no output").splitlines()[0][:300]
        raise RuntimeError(f"engine output for {bundle.measure_id} is not JSON: {first}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"engine returned no patient list for {bundle.measure_id}")
    return payload


def _write_results(conn: psycopg.Connection, bundle: MeasureBundle, populations: dict[str, dict],
                   attributed_org: dict[str, str | None], run_id: str, measure_name: str, year: int) -> MeasureOutcome:
    """measure_result for every patient, a gap for every result in the denominator but not met or excluded, and
    open gaps closed where this run shows the measure met or the patient excluded."""
    patients = sorted(populations)
    den = [populations[p]["denominator"] is True for p in patients]
    num = [populations[p]["numerator"] is True for p in patients]
    exc = [populations[p]["denominator_exclusion"] is True for p in patients]
    version = f"{ENGINE} {ENGINE_VERSION} / {bundle.library} {bundle.library_version or 'unversioned'}"
    if bundle.measure_id in INVERSE_MEASURES:
        num = [d and not n for d, n in zip(den, num)]
        version += " / numerator inverted (engine counts poor control)"
    rows = conn.execute(_RESULTS, (bundle.measure_id, year, version, patients, den, num, exc)).fetchall()
    result_pk = {patient: pk for pk, patient in rows}
    gap_patients = [p for p, d, n, e in zip(patients, den, num, exc) if d and not n and not e]
    gap_ids = [uuid.uuid5(GAP_NAMESPACE, f"{p}:{bundle.measure_id}:{year}") for p in gap_patients]
    missing = MISSING_ELEMENT.get(bundle.measure_id, "{name}: the measure engine found no qualifying event in {year}")
    new = conn.execute(_GAPS, (bundle.measure_id, year, missing.format(name=measure_name, year=year),
                               date(year, 12, 31), gap_ids, gap_patients, [result_pk[p] for p in gap_patients])).rowcount
    conn.execute(_GAP_DETAILS, (run_id, gap_ids, [attributed_org.get(p) for p in gap_patients]))
    closed = _close_met_gaps(conn, bundle.measure_id, year, run_id, patients, [d and n for d, n in zip(den, num)], exc)
    return MeasureOutcome(denominator=sum(den), numerator=sum(d and n for d, n in zip(den, num)), excluded=sum(exc),
                          gaps_new=new, gaps_open=len(gap_patients), gaps_closed=closed)


def _close_met_gaps(conn: psycopg.Connection, measure_id: str, year: int, run_id: str, patients: list[str],
                    met: list[bool], excluded: list[bool]) -> int:
    """Close this measure's open gaps whose patient this run shows met or excluded; one audit event each."""
    rows = conn.execute(_CLOSE_GAPS, {"measure": measure_id, "year": year, "patients": patients, "met": met,
                                      "excluded": excluded}).fetchall()
    for gap_id, review_before, review_after in rows:
        audit(conn, "care_gap", str(gap_id), "gap.closed_by_engine",
              {"measure_id": measure_id, "engine_run_id": run_id, "review_status_before": review_before,
               "review_status": review_after}, actor=SETTINGS.actor)
    return len(rows)


def gap_counts(conn: psycopg.Connection, patient_ids: list[str]) -> dict[str, int]:
    """Open gaps per measure for these patients, for the batch summary."""
    return dict(conn.execute(
        "SELECT measure_id, count(*) FROM care_gap WHERE status = 'open' AND patient_id = ANY(%s) GROUP BY 1",
        (list(patient_ids),)).fetchall())
