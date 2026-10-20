"""Load the seed data in data/seed into the database.

Usage (from the repo root, after `alembic -c db/alembic.ini upgrade head`):
    python scripts/db/load_seed.py           # load into an empty database
    python scripts/db/load_seed.py --reset   # empty every table first (development only)

What it loads:
- organizations, providers, members, engagement (data/seed/members)
- measures and star info (data/seed/measures)
- the engine run, measure summaries and gaps (data/seed/engine)
- documents and structured files as received files with status "pending", ready for
  US-02 intake and conversion. No page text is created here; converting files to
  pages is the job of the US-02 converters.

The Synthea CSVs in data/seed/synthea stay as files for the engine services.
"""

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seed"

TABLES_IN_DELETE_ORDER = [
    "export_run_items", "export_runs", "audit_events", "supplemental_records", "review_decisions",
    "evidence_candidates", "extraction_attempts", "extraction_runs", "structured_records", "page_words",
    "document_pages", "documents", "source_files", "gaps", "measure_summaries", "engine_runs",
    "member_engagement", "members", "measure_star_info", "measures", "providers", "organizations",
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


def reset(cur) -> None:
    cur.execute("TRUNCATE " + ", ".join(TABLES_IN_DELETE_ORDER) + " RESTART IDENTITY CASCADE")


def load_reference(cur) -> None:
    for o in read_csv(SEED / "members" / "organizations.csv"):
        cur.execute(
            "INSERT INTO organizations (id, name, kind, city, state, closure_rate) VALUES (%s,%s,%s,%s,%s,%s)",
            (o["id"], o["name"], o["kind"], blank_to_none(o.get("city")), blank_to_none(o.get("state")),
             blank_to_none(o.get("closure_rate"))))
    for p in read_csv(SEED / "members" / "providers.csv"):
        cur.execute("INSERT INTO providers (id, name, org_id, specialty) VALUES (%s,%s,%s,%s)",
                    (p["id"], p["name"], p["org_id"], blank_to_none(p.get("specialty"))))

    lib = yaml.safe_load((SEED / "measures" / "measures.yaml").read_text(encoding="utf-8"))
    year = lib["measurement_year"]
    for m in lib["measures"]:
        cur.execute(
            """INSERT INTO measures (code, measurement_year, name, kind, rule_version, population, windows, thresholds,
                   most_recent_counts, allowed_evidence_types, ai_role, rule_text, prompt_template, prompt_placeholders,
                   verify_before_use, sme_review)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (m["code"], year, m["name"], m["kind"], lib["rule_version"], Jsonb(m["population"]), Jsonb(m["windows"]),
             Jsonb(m.get("thresholds")), bool(m.get("most_recent_counts")), Jsonb(m["allowed_evidence_types"]),
             m.get("ai_role"), Jsonb(m["rule_text"]), m["prompt_template"], Jsonb(m.get("prompt_placeholders", [])),
             Jsonb(m.get("verify_before_use")), Jsonb(lib.get("sme_review"))))

    stars = json.loads((SEED / "measures" / "star_cut_points.json").read_text(encoding="utf-8"))
    for s in stars["measures"]:
        cur.execute(
            """INSERT INTO measure_star_info (measure_code, measurement_year, star_year, star_measure_id, weight, measure_type,
                   higher_is_better, scoring_note, cut_points, min_rate_for_star, source_ref, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (s["hedis_code"], year, stars["star_year"], s["star_measure_id"], s["weight"], s["measure_type"],
             s["higher_is_better"], s["scoring_note"], Jsonb(s["cut_points"]), Jsonb(s["min_rate_for_star"]),
             s["source_ref"], s["confidence"]))


def load_members(cur) -> None:
    for m in read_csv(SEED / "members" / "members.csv"):
        cur.execute(
            """INSERT INTO members (member_id, synthea_patient_id, first_name, last_name, sex, birth_date, city, state,
                   product_line, pcp_org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (m["member_id"], blank_to_none(m.get("synthea_patient_id")), m["first"], m["last"], m["sex"], m["birth_date"],
             blank_to_none(m.get("city")), blank_to_none(m.get("state")), blank_to_none(m.get("product_line")),
             blank_to_none(m.get("pcp_org_id"))))
    for e in read_csv(SEED / "members" / "engagement.csv"):
        cur.execute(
            """INSERT INTO member_engagement (member_id, next_visit_date, last_contact_date, attributed_org_id)
               VALUES (%s,%s,%s,%s)""",
            (e["member_id"], blank_to_none(e.get("next_visit_date")), blank_to_none(e.get("last_contact_date")),
             blank_to_none(e.get("attributed_org_id"))))


def load_engine(cur) -> None:
    path = SEED / "engine" / "engine_run_MY2026.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    year = run["measurement_year"]
    cur.execute(
        """INSERT INTO engine_runs (run_id, engine, engine_version, measure_spec_version, measurement_year, run_at, source_file)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (run["run_id"], run["engine"], run["engine_version"], run["measure_spec_version"], year, run["run_at"],
         str(path.relative_to(ROOT))))
    for s in run["measure_summaries"]:
        cur.execute(
            """INSERT INTO measure_summaries (run_id, measure_code, measurement_year, denominator, numerator, exclusions, rate)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (run["run_id"], s["measure_code"], year, s["denominator"], s["numerator"], s.get("exclusions", 0), s.get("rate")))
    for g in run["gaps"]:
        provider = g.get("attributed_provider") or {}
        cur.execute(
            """INSERT INTO gaps (gap_id, run_id, member_id, measure_code, measurement_year, gap_type, missing_element,
                   last_event, deadline, exclusion_check, episode, attributed_org_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (g["gap_id"], run["run_id"], g["member"]["member_id"], g["measure_code"], year, g["gap_type"],
             g["missing_element"], Jsonb(g.get("last_relevant_event")), g["deadline"], Jsonb(g.get("exclusion_check")),
             Jsonb(g.get("episode")), provider.get("org_id")))


def register_file(cur, path: Path, org_id, member_id, needs_match: bool) -> int:
    cur.execute(
        """INSERT INTO source_files (sha256, original_name, stored_path, detected_type, size_bytes, source_org_id,
               member_id, match_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (sha256(path), path.name, str(path.relative_to(ROOT)).replace("\\", "/"),
         mimetypes.guess_type(path.name)[0] or "application/octet-stream", path.stat().st_size, org_id, member_id,
         "needs_match" if needs_match else "matched"))
    return cur.fetchone()[0]


def load_documents(cur) -> None:
    cur.execute("SELECT name, id FROM organizations")
    org_by_name = dict(cur.fetchall())
    cur.execute("SELECT name, id FROM providers")
    provider_by_name = dict(cur.fetchall())

    manifest = json.loads((SEED / "documents" / "manifest.json").read_text(encoding="utf-8"))
    pending_duplicates = []
    for d in manifest:
        path = SEED / "documents" / d["file"] if not Path(d["file"]).is_absolute() else Path(d["file"])
        if not path.exists():
            path = SEED / d["file"]
        org_id = org_by_name.get(d.get("source_org"))
        file_id = register_file(cur, path, org_id, d.get("member_id"), d.get("member_id") is None)
        cur.execute(
            """INSERT INTO documents (id, source_file_id, member_id, doc_type, document_date, source_org_id,
                   author_provider_id, page_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (d["doc_id"], file_id, d.get("member_id"), d.get("type"), d.get("date"), org_id,
             provider_by_name.get(d.get("author")), d.get("page_count")))
        if d.get("duplicate_of"):
            pending_duplicates.append((d["duplicate_of"], d["doc_id"]))
    for original, duplicate in pending_duplicates:
        cur.execute("UPDATE documents SET near_duplicate_of = %s WHERE id = %s", (original, duplicate))

    for path in sorted((SEED / "structured").glob("*")):
        if path.suffix.lower() in (".csv", ".xlsx"):
            register_file(cur, path, None, None, False)


def audit(cur, counts: dict) -> None:
    cur.execute("INSERT INTO audit_events (entity_type, entity_id, action, actor, payload) VALUES (%s,%s,%s,%s,%s)",
                ("seed", "data/seed", "seed.loaded", "load_seed.py", Jsonb(counts)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="empty every table before loading (development only)")
    args = parser.parse_args()

    with connect() as conn, conn.cursor() as cur:
        if args.reset:
            reset(cur)
        load_reference(cur)
        load_members(cur)
        load_engine(cur)
        load_documents(cur)
        counts = {}
        for table in reversed(TABLES_IN_DELETE_ORDER):
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        audit(cur, counts)
        conn.commit()
    width = max(len(t) for t in counts)
    for table, n in counts.items():
        if n:
            print(f"{table:<{width}}  {n}")


if __name__ == "__main__":
    main()
