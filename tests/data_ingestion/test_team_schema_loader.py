from psycopg.types.json import Jsonb

from services.data_ingestion.team_schema_loader import _MEDICATION_CODE
from services.document_intake import db


def test_medication_code_expression_supports_r4_and_r5_resources():
    r4_resource = {"medicationCodeableConcept": {"coding": [{"code": "R4-CODE"}]}}
    r5_resource = {"medication": {"concept": {"coding": [{"code": "R5-CODE"}]}}}
    expression = _MEDICATION_CODE.replace("r.resource_json", "resource_json")

    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {expression} FROM (VALUES (%s::jsonb), (%s::jsonb)) AS resources(resource_json)",
            (Jsonb(r4_resource), Jsonb(r5_resource)),
        ).fetchall()

    assert [row[0] for row in rows] == ["R4-CODE", "R5-CODE"]