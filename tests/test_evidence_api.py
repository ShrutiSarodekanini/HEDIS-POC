from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from common.config import AppConfig
from common.database import Database
from common.models import CareGap, Measure, Member, SyntheaRecord


def _test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database_name.lower().endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a database ending in '_test'")
    return database_url


def _reset_and_seed(database_url: str) -> None:
    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(alembic_config, "head")
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url

    database = Database(database_url)
    with database.session_factory.begin() as session:
        session.execute(
            text("TRUNCATE synthea_records, care_gaps, measures, members CASCADE")
        )
        session.add(
            Member(
                member_id="M1001",
                first_name="Alex",
                last_name="Taylor",
                date_of_birth=date(1979, 4, 18),
                gender="Female",
            )
        )
        session.add(
            Measure(
                measure_id="COL",
                name="Colorectal Cancer Screening",
                description=(
                    "Members 45-75 years of age with appropriate colorectal screening."
                ),
                measurement_year=2026,
            )
        )
        session.add(
            CareGap(
                care_gap_id="CG1001",
                member_id="M1001",
                measure_id="COL",
                description=(
                    "No qualifying colorectal cancer screening found in measurement year."
                ),
                status="Open",
                date_identified=date(2026, 1, 12),
                measurement_year=2026,
            )
        )
        session.add(
            SyntheaRecord(
                record_id="SR1001",
                member_id="M1001",
                care_gap_id="CG1001",
                source_type="Encounter",
                source_date=date(2025, 8, 3),
                fact_key="encounter_reason",
                fact_value="Annual wellness visit",
                provenance="synthea/encounters.csv",
            )
        )
    database.dispose()


@pytest.fixture()
def client(tmp_path: Path):
    database_url = _test_database_url()
    storage_dir = tmp_path / "evidence"

    _reset_and_seed(database_url)
    app = create_app(
        AppConfig(evidence_storage_dir=storage_dir, database_url=database_url)
    )
    with TestClient(app) as test_client:
        yield test_client


def _generate(client: TestClient, fmt: str) -> dict:
    response = client.post(
        "/api/evidence/generate",
        json={"member_id": "M1001", "care_gap_id": "CG1001", "format": fmt},
    )
    assert response.status_code == 200
    return response.json()


def test_generate_pdf(client: TestClient):
    payload = _generate(client, "pdf")
    path = Path(payload["file_path"])
    assert payload["format"] == "pdf"
    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.read_bytes().startswith(b"%PDF")

    database_url = client.app.state.config.database_url
    database = Database(database_url)
    with database.session_factory() as session:
        record = session.execute(
            text(
                """
                SELECT fact_value
                FROM synthea_records
                WHERE member_id = :member_id
                  AND care_gap_id = :care_gap_id
                  AND source_type = 'EvidenceDocument'
                  AND provenance = 'app:/api/evidence/generate'
                ORDER BY source_date DESC, record_id DESC
                LIMIT 1
                """
            ),
            {"member_id": "M1001", "care_gap_id": "CG1001"},
        ).scalar_one()
    database.dispose()
    assert record == payload["file_path"]


def test_generate_docx(client: TestClient):
    payload = _generate(client, "docx")
    path = Path(payload["file_path"])
    assert payload["format"] == "docx"
    assert path.exists()
    assert path.suffix == ".docx"
    doc = Document(path)
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "HEDIS Evidence Report" in full_text


def test_generate_png(client: TestClient):
    payload = _generate(client, "png")
    path = Path(payload["file_path"])
    assert payload["format"] == "png"
    assert path.exists()
    assert path.suffix == ".png"
    with Image.open(path) as img:
        assert img.format == "PNG"


def test_unknown_member(client: TestClient):
    response = client.post(
        "/api/evidence/generate",
        json={"member_id": "missing", "care_gap_id": "CG1001", "format": "pdf"},
    )
    assert response.status_code == 404


def test_unknown_care_gap(client: TestClient):
    response = client.post(
        "/api/evidence/generate",
        json={"member_id": "M1001", "care_gap_id": "missing", "format": "pdf"},
    )
    assert response.status_code == 404


def test_relationship_validation(client: TestClient):
    database_url = client.app.state.config.database_url
    database = Database(database_url)
    with database.session_factory.begin() as session:
        session.add(
            Member(
                member_id="M2002",
                first_name="Jamie",
                last_name="Ng",
                date_of_birth=date(1988, 9, 12),
                gender="Male",
            )
        )
    database.dispose()

    response = client.post(
        "/api/evidence/generate",
        json={"member_id": "M2002", "care_gap_id": "CG1001", "format": "pdf"},
    )
    assert response.status_code == 400


def test_unsupported_format(client: TestClient):
    response = client.post(
        "/api/evidence/generate",
        json={"member_id": "M1001", "care_gap_id": "CG1001", "format": "xlsx"},
    )
    assert response.status_code == 400


def test_star_rating_persists_when_member_context_is_provided(client: TestClient):
    response = client.post(
        "/api/star-rating/calculate",
        json={
            "cms_rating_year": 2027,
            "member_id": "M1001",
            "care_gap_id": "CG1001",
            "result": {
                "measure_code": "CBP",
                "measurement_year": 2026,
                "scoring_value": 84,
                "denominator": 100,
                "result_status": "completed",
                "calculation_version": "hedis-engine-1",
                "specification_version": "MY2026",
                "scoring_value_kind": "cms_scoring_value",
            },
        },
    )
    assert response.status_code == 200

    database_url = client.app.state.config.database_url
    database = Database(database_url)
    with database.session_factory() as session:
        fact_value = session.execute(
            text(
                """
                SELECT fact_value
                FROM synthea_records
                WHERE member_id = :member_id
                  AND care_gap_id = :care_gap_id
                  AND source_type = 'StarRating'
                  AND provenance = 'app:/api/star-rating/calculate'
                ORDER BY source_date DESC, record_id DESC
                LIMIT 1
                """
            ),
            {"member_id": "M1001", "care_gap_id": "CG1001"},
        ).scalar_one()
    database.dispose()

    assert '"assigned_star_rating":4' in fact_value
    assert '"rating_status":"assigned"' in fact_value
