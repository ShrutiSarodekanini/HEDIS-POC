"""Load the seed data in data/seed into the team-aligned schema.

Usage (from the repo root, after `alembic -c db/alembic.ini upgrade head`):
    python scripts/db/load_seed.py           # load into an empty database
    python scripts/db/load_seed.py --reset   # empty every table first (development only)

Everything loads in one transaction:
- one source_batch row, stamped on every team-table row that has a batch_id
- organizations, practitioners, patients (with a minimal FHIR Patient), coverage, encounters
- measure definitions and rules, star weights and cut points
- the engine run, one measure_result per patient per measure, care gaps and their detail
- documents and structured files as received files with status "pending", ready for document intake.
  No page text is created here.

The Synthea CSVs in data/seed/synthea stay as files for the engine services.
"""

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seed"
MIGRATION = ROOT / "db" / "migrations" / "versions" / "0003_align_team_schema.py"

#: Fixed namespace so seed IDs (batch, gaps, encounters) are the same on every load.
SEED_NAMESPACE = uuid.UUID("3b5e1c0a-8f4d-4c6e-9a2b-7d1f0e5c9a41")
BATCH_ID = uuid.uuid5(SEED_NAMESPACE, "seed-batch")

PAYER_BY_PRODUCT_LINE = {"commercial": "Commercial", "medicare": "Medicare Advantage", "medicaid": "Medicaid"}
GENDER_BY_SEX = {"M": "male", "F": "female"}

#: Team tables whose rows count towards source_batch.resource_count.
TEAM_TABLES = [
    "organization", "practitioner", "patient", "coverage", "encounter", "document_reference", "fhir_resource_raw",
    "measure_definition", "measure_result", "care_gap", "star_cutpoint",
]


def connect() -> psycopg.Connection:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def blank_to_none(value):
    return None if value in ("", None) else value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at_utc(day: str, hour: int = 9) -> datetime:
    """Seed visits carry a date only; 09:00 UTC keeps them on the same calendar day in US time zones."""
    return datetime.combine(date.fromisoformat(day), time(hour, 0), tzinfo=timezone.utc)


def migration_tables() -> list[str]:
    """Every table of the aligned schema, read from the migration's DROP_NEW list so the two cannot drift."""
    text = MIGRATION.read_text(encoding="utf-8")
    block = re.search(r'DROP_NEW = r"""(.*?)"""', text, re.S).group(1)
    names = re.search(r"DROP TABLE IF EXISTS(.*?)CASCADE;", block, re.S).group(1)
    return [n.strip() for n in names.split(",") if n.strip()]


def reset(cur) -> None:
    cur.execute("TRUNCATE " + ", ".join(migration_tables()) + " RESTART IDENTITY CASCADE")


def load_batch(cur) -> None:
    cur.execute(
        """INSERT INTO source_batch (batch_id, source_system, status, resource_count, started_at)
           VALUES (%s,'hedis-poc-seed','completed',0,%s)""",
        (BATCH_ID, datetime.now(timezone.utc)))


def finish_batch(cur) -> None:
    """Set resource_count once every team table is filled."""
    total = 0
    for table in TEAM_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        total += cur.fetchone()[0]
    cur.execute("UPDATE source_batch SET resource_count = %s WHERE batch_id = %s", (total, BATCH_ID))


def load_organizations(cur) -> None:
    for o in read_csv(SEED / "members" / "organizations.csv"):
        cur.execute("INSERT INTO organization (organization_id, name, city, batch_id) VALUES (%s,%s,%s,%s)",
                    (o["id"], o["name"], blank_to_none(o.get("city")), BATCH_ID))
        cur.execute("INSERT INTO organization_quality (organization_id, kind, state, closure_rate) VALUES (%s,%s,%s,%s)",
                    (o["id"], o["kind"], blank_to_none(o.get("state")), blank_to_none(o.get("closure_rate"))))


def load_practitioners(cur) -> dict[str, str]:
    """Return a name -> practitioner_id map for document authors."""
    by_name: dict[str, str] = {}
    for p in read_csv(SEED / "members" / "providers.csv"):
        number = int(p["id"].split("-")[-1])
        cur.execute(
            "INSERT INTO practitioner (practitioner_id, npi, family_name, active, batch_id) VALUES (%s,%s,%s,true,%s)",
            (p["id"], f"99{number:08d}", p["name"].split()[-1], BATCH_ID))
        by_name[p["name"]] = p["id"]
    return by_name


def fhir_patient(m: dict) -> dict:
    return {
        "resourceType": "Patient", "id": m["synthea_patient_id"],
        "identifier": [{"system": "urn:hedis-poc:member-id", "value": m["member_id"]}],
        "name": [{"family": m["last"], "given": [m["first"]]}],
        "gender": GENDER_BY_SEX[m["sex"]], "birthDate": m["birth_date"],
        "address": [{"city": m.get("city"), "state": m.get("state")}],
    }


def load_patients(cur) -> dict[str, str]:
    """Return an mrn -> patient_id map."""
    by_mrn: dict[str, str] = {}
    source = "data/seed/members/members.csv"
    for m in read_csv(SEED / "members" / "members.csv"):
        pid = m["synthea_patient_id"]
        by_mrn[m["member_id"]] = pid
        cur.execute(
            """INSERT INTO patient (patient_id, mrn, gender, birth_sex, birth_date, source_file, batch_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (pid, m["member_id"], GENDER_BY_SEX[m["sex"]], m["sex"], m["birth_date"], source, BATCH_ID))
        cur.execute(
            """INSERT INTO fhir_resource_raw (resource_type, resource_id, patient_id, raw, batch_id)
               VALUES ('Patient',%s,%s,%s,%s)""",
            (pid, pid, Jsonb(fhir_patient(m)), BATCH_ID))
        cur.execute(
            """INSERT INTO coverage (coverage_id, patient_id, payer_name, period_start, period_end, batch_id)
               VALUES (%s,%s,%s,'2026-01-01','2026-12-31',%s)""",
            (f"{pid}-coverage", pid, PAYER_BY_PRODUCT_LINE.get(m.get("product_line")), BATCH_ID))
    return by_mrn


def insert_encounter(cur, mrn: str, patient_id: str, kind: str, day: str, org_id, status: str,
                     encounter_id: str | None = None) -> str:
    eid = encounter_id or str(uuid.uuid5(SEED_NAMESPACE, f"encounter:{mrn}:{kind}:{day}"))
    cur.execute(
        """INSERT INTO encounter (encounter_id, patient_id, status, class_code, practitioner_npi, organization_id,
               period_start, batch_id) VALUES (%s,%s,%s,'AMB',NULL,%s,%s,%s) ON CONFLICT (encounter_id) DO NOTHING""",
        (eid, patient_id, status, org_id, at_utc(day), BATCH_ID))
    return eid


def load_engagement(cur, patients: dict[str, str]) -> None:
    for e in read_csv(SEED / "members" / "engagement.csv"):
        mrn, org = e["member_id"], blank_to_none(e.get("attributed_org_id"))
        for kind, column, status in (("next_visit", "next_visit_date", "planned"),
                                     ("last_contact", "last_contact_date", "finished")):
            day = blank_to_none(e.get(column))
            if day:
                insert_encounter(cur, mrn, patients[mrn], kind, day, org, status)


def load_measures(cur) -> dict:
    lib = yaml.safe_load((SEED / "measures" / "measures.yaml").read_text(encoding="utf-8"))
    year = lib["measurement_year"]
    for m in lib["measures"]:
        cur.execute("INSERT INTO measure_definition (measure_id, measurement_year) VALUES (%s,%s)", (m["code"], year))
        cur.execute(
            """INSERT INTO measure_rule (measure_id, measurement_year, name, kind, rule_version, population, windows,
                   thresholds, most_recent_counts, allowed_evidence_types, ai_role, rule_text, prompt_template,
                   prompt_placeholders, verify_before_use, sme_review)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (m["code"], year, m["name"], m["kind"], lib["rule_version"], Jsonb(m["population"]), Jsonb(m["windows"]),
             Jsonb(m.get("thresholds")), bool(m.get("most_recent_counts")), Jsonb(m["allowed_evidence_types"]),
             m.get("ai_role"), Jsonb(m["rule_text"]), m["prompt_template"], Jsonb(m.get("prompt_placeholders", [])),
             Jsonb(m.get("verify_before_use")), Jsonb(lib.get("sme_review"))))
    return lib


def load_stars(cur, year: int) -> None:
    stars = json.loads((SEED / "measures" / "star_cut_points.json").read_text(encoding="utf-8"))
    for s in stars["measures"]:
        cur.execute(
            """INSERT INTO measure_star_weight (measure_id, measurement_year, star_year, star_measure_id, weight,
                   measure_type, higher_is_better, scoring_note, source_ref, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (s["hedis_code"], year, stars["star_year"], s["star_measure_id"], s["weight"], s["measure_type"],
             s["higher_is_better"], s.get("scoring_note"), s.get("source_ref"), s.get("confidence")))
        lows = {int(k): v for k, v in s["cut_points"].items() if v is not None}
        for star in sorted(lows):
            high = lows.get(star + 1, 100 if star == 5 else None)
            cur.execute(
                """INSERT INTO star_cutpoint (measure_id, measurement_year, star_rating, rate_low_pct, rate_high_pct,
                       source) VALUES (%s,%s,%s,%s,%s,'cms-published-prior-year')""",
                (s["hedis_code"], year, star, lows[star], high))


def load_engine_run(cur, run: dict) -> None:
    cur.execute(
        """INSERT INTO engine_runs (run_id, engine, engine_version, measure_spec_version, measurement_year, run_at,
               source_file) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (run["run_id"], run["engine"], run["engine_version"], run["measure_spec_version"], run["measurement_year"],
         run["run_at"], "data/seed/engine/engine_run_MY2026.json"))


def fits_population(member: dict, population: dict, year: int) -> bool:
    """True when the member is in the measure's age and sex population (age on 31 Dec of the year)."""
    born = date.fromisoformat(member["birth_date"])
    age = year - born.year - ((12, 31) < (born.month, born.day))
    if population.get("age_min") is not None and age < population["age_min"]:
        return False
    if population.get("age_max") is not None and age > population["age_max"]:
        return False
    sex = population.get("sex")
    return sex not in ("female", "male") or GENDER_BY_SEX[member["sex"]] == sex


def insert_result(cur, patient_id: str, code: str, run: dict, numerator: bool) -> int:
    cur.execute(
        """INSERT INTO measure_result (patient_id, measure_id, measurement_year, in_denominator, in_numerator, excluded,
               engine_version, computed_at) VALUES (%s,%s,%s,true,%s,false,%s,%s) RETURNING result_pk""",
        (patient_id, code, run["measurement_year"], numerator, run["engine_version"], run["run_at"]))
    return cur.fetchone()[0]


def load_results(cur, run: dict, lib: dict, patients: dict[str, str]) -> dict[tuple[str, str], int]:
    """One measure_result per patient per measure, so counts equal the engine's measure summaries.

    The engine file lists only gaps, so compliant patients are chosen from those who fit the measure's population.
    Returns (mrn, measure) -> result_pk for the gap rows.
    """
    members = sorted(read_csv(SEED / "members" / "members.csv"), key=lambda m: m["member_id"])
    population = {m["code"]: m["population"] for m in lib["measures"]}
    gap_pk: dict[tuple[str, str], int] = {}
    for summary in run["measure_summaries"]:
        code = summary["measure_code"]
        gap_mrns = {g["member"]["member_id"] for g in run["gaps"] if g["measure_code"] == code}
        for mrn in sorted(gap_mrns):
            gap_pk[(mrn, code)] = insert_result(cur, patients[mrn], code, run, False)
        rest = [m for m in members if m["member_id"] not in gap_mrns]
        fitting = [m for m in rest if fits_population(m, population[code], run["measurement_year"])]
        if len(fitting) < summary["numerator"]:
            print(f"warning: {code}: only {len(fitting)} members fit the population, "
                  f"filling up to {summary['numerator']} from the rest")
            fitting += [m for m in rest if m not in fitting]
        for m in fitting[:summary["numerator"]]:
            insert_result(cur, m["synthea_patient_id"], code, run, True)
    return gap_pk


def event_description(event: dict | None) -> str | None:
    if not event:
        return None
    return f"{event.get('value')} ({event.get('type')}, {event.get('source')})"


def load_gaps(cur, run: dict, patients: dict[str, str], gap_pk: dict[tuple[str, str], int]) -> None:
    for g in run["gaps"]:
        mrn, code, year = g["member"]["member_id"], g["measure_code"], run["measurement_year"]
        gap_id = uuid.uuid5(SEED_NAMESPACE, f"gap:{g['gap_id']}")
        event = g.get("last_relevant_event")
        org = (g.get("attributed_provider") or {}).get("org_id")
        cur.execute(
            """INSERT INTO care_gap (gap_id, patient_id, measure_id, measurement_year, status, missing_element,
                   last_relevant_event_date, last_relevant_event_description, deadline_date,
                   source_measure_result_pk, opened_at) VALUES (%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s)""",
            (gap_id, patients[mrn], code, year, g["missing_element"], (event or {}).get("date"),
             event_description(event), g["deadline"], gap_pk[(mrn, code)], run["run_at"]))
        episode = g.get("episode")
        visit_id = None
        if episode:
            visit_id = insert_encounter(cur, mrn, patients[mrn], "episode", episode["visit_date"], org, "finished",
                                        episode.get("encounter_id"))
        cur.execute(
            """INSERT INTO care_gap_detail (gap_id, seed_code, engine_run_id, gap_type, last_event, exclusion_check,
                   episode, episode_encounter_id, attributed_org_id, review_status, pending_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',0)""",
            (gap_id, g["gap_id"], run["run_id"], g["gap_type"], Jsonb(event), Jsonb(g.get("exclusion_check")),
             Jsonb(episode), visit_id, org))


def register_file(cur, path: Path, org_id, patient_id, needs_match: bool) -> int:
    cur.execute(
        """INSERT INTO source_files (sha256, original_name, stored_path, detected_type, size_bytes, source_org_id,
               patient_id, match_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (sha256(path), path.name, str(path.relative_to(ROOT)).replace("\\", "/"),
         mimetypes.guess_type(path.name)[0] or "application/octet-stream", path.stat().st_size, org_id, patient_id,
         "needs_match" if needs_match else "matched"))
    return cur.fetchone()[0]


def load_documents(cur, patients: dict[str, str], authors: dict[str, str]) -> None:
    cur.execute("SELECT name, organization_id FROM organization")
    org_by_name = dict(cur.fetchall())
    manifest = json.loads((SEED / "documents" / "manifest.json").read_text(encoding="utf-8"))
    duplicates = []
    for d in manifest:
        path = SEED / "documents" / d["file"] if not Path(d["file"]).is_absolute() else Path(d["file"])
        if not path.exists():
            path = SEED / d["file"]
        org_id = org_by_name.get(d.get("source_org"))
        patient_id = patients.get(d.get("member_id"))
        file_id = register_file(cur, path, org_id, patient_id, patient_id is None)
        cur.execute(
            """INSERT INTO document_reference (document_reference_id, patient_id, status, type_code, content_type,
                   doc_date, batch_id) VALUES (%s,%s,'current',%s,%s,%s,%s)""",
            (d["doc_id"], patient_id, d.get("type"), d.get("mime_type"),
             at_utc(d["date"], 0) if d.get("date") else None, BATCH_ID))
        cur.execute(
            """INSERT INTO document_conversion (document_reference_id, source_file_id, source_org_id,
                   author_practitioner_id, page_count, conversion_status) VALUES (%s,%s,%s,%s,%s,'pending')""",
            (d["doc_id"], file_id, org_id, authors.get(d.get("author")), d.get("page_count")))
        if d.get("duplicate_of"):
            duplicates.append((d["duplicate_of"], d["doc_id"]))
    for original, duplicate in duplicates:
        cur.execute("UPDATE document_conversion SET near_duplicate_of = %s WHERE document_reference_id = %s",
                    (original, duplicate))
    for path in sorted((SEED / "structured").glob("*")):
        if path.suffix.lower() in (".csv", ".xlsx"):
            register_file(cur, path, None, None, False)


def table_counts(cur) -> dict[str, int]:
    counts = {}
    for table in migration_tables():
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def audit(cur, counts: dict) -> None:
    cur.execute("INSERT INTO audit_events (entity_type, entity_id, action, actor, payload) VALUES (%s,%s,%s,%s,%s)",
                ("seed", "data/seed", "seed.loaded", "load_seed.py", Jsonb(counts)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="empty every table before loading (development only)")
    args = parser.parse_args()
    run = json.loads((SEED / "engine" / "engine_run_MY2026.json").read_text(encoding="utf-8"))

    with connect() as conn, conn.cursor() as cur:
        if args.reset:
            reset(cur)
        load_batch(cur)
        load_organizations(cur)
        authors = load_practitioners(cur)
        patients = load_patients(cur)
        load_engagement(cur, patients)
        lib = load_measures(cur)
        load_stars(cur, lib["measurement_year"])
        load_engine_run(cur, run)
        gap_pk = load_results(cur, run, lib, patients)
        load_gaps(cur, run, patients, gap_pk)
        load_documents(cur, patients, authors)
        finish_batch(cur)
        counts = table_counts(cur)
        audit(cur, counts)
        conn.commit()
    width = max(len(t) for t in counts)
    for table, n in counts.items():
        if n:
            print(f"{table:<{width}}  {n}")


if __name__ == "__main__":
    main()
