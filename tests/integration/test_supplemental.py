"""Confirmed supplemental data becomes the FHIR resources the measure engine reads (services/integration/supplemental.py).

The last tests run the real fqm-execution engine on the example patients, so they are skipped when Node or the built
engine CLI is missing.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from services.integration import measures, supplemental
from services.integration.config import SETTINGS

ROOT = Path(__file__).resolve().parents[2]


def _record(measure: str, evidence_type: str, value: str | None = None, when: date | None = date(2026, 6, 15),
            record_id: int = 7, patient: str = "p-1") -> supplemental.Record:
    return supplemental.Record(record_id, patient, measure, evidence_type, when, value)


def test_every_target_is_an_allowed_evidence_type() -> None:
    seed = yaml.safe_load((ROOT / "data/seed/measures/measures.yaml").read_text(encoding="utf-8"))
    allowed = {(m["code"], t) for m in seed["measures"]
               for kind in ("numerator", "exclusion") for t in m["allowed_evidence_types"][kind] or []}
    assert set(supplemental.TARGETS) <= allowed


def test_mammogram_becomes_a_tagged_completed_procedure() -> None:
    [resource] = supplemental.to_resources(_record("BCS-E", "mammogram"))
    assert resource["resourceType"] == "Procedure"
    assert resource["status"] == "completed"
    assert resource["performedDateTime"] == "2026-06-15"
    assert resource["subject"] == {"reference": "Patient/p-1"}
    assert resource["code"]["coding"][0]["code"] == "168749009"
    assert resource["meta"]["tag"] == [{"system": supplemental.RECORD_TAG, "code": "7"}]


def test_a1c_value_is_read_as_a_percentage() -> None:
    [resource] = supplemental.to_resources(_record("GSD", "hba1c", "HbA1c 7.2 %"))
    assert resource["valueQuantity"]["value"] == 7.2
    assert resource["valueQuantity"]["unit"] == "%"


def test_blood_pressure_becomes_a_reading_with_its_encounter() -> None:
    encounter, reading = supplemental.to_resources(_record("CBP", "blood_pressure_reading", "BP 128/78 sitting"))
    assert encounter["resourceType"] == "Encounter"
    assert reading["encounter"] == {"reference": f"Encounter/{encounter['id']}"}
    assert reading["meta"]["profile"] == [supplemental.US_CORE_BP]
    values = {c["code"]["coding"][0]["code"]: c["valueQuantity"]["value"] for c in reading["component"]}
    assert values == {supplemental.SYSTOLIC: 128, supplemental.DIASTOLIC: 78}


@pytest.mark.parametrize(("record", "expected"), [
    (_record("COL-E", "ct_colonography"), "has no input for ct_colonography"),
    (_record("BCS-E", "mammogram", when=None), "has no service date"),
    (_record("GSD", "hba1c", "patient said it was fine"), "not a percentage"),
    (_record("GSD", "hba1c", "72"), "not a percentage"),
    (_record("CBP", "blood_pressure_reading", "elevated"), "not systolic/diastolic"),
    (_record("CBP", "blood_pressure_reading", "80/120"), "out of range"),
])
def test_unusable_records_carry_a_reason_without_the_value(record: supplemental.Record, expected: str) -> None:
    inputs = supplemental.build([record])
    assert inputs.resources == {} and inputs.used == []
    [unusable] = inputs.unusable
    assert expected in unusable.reason
    assert record.value is None or record.value not in unusable.reason


def test_build_groups_resources_by_patient_and_measure() -> None:
    inputs = supplemental.build([_record("KED", "egfr", record_id=1), _record("KED", "uacr_ratio", record_id=2),
                                 _record("BCS-E", "mammogram", record_id=3, patient="p-2"),
                                 _record("COL-E", "ct_colonography", record_id=4)])
    assert sorted(inputs.resources) == [("p-1", "KED"), ("p-2", "BCS-E")]
    assert len(inputs.resources[("p-1", "KED")]) == 2
    assert inputs.used == [1, 2, 3]
    assert [u.record_id for u in inputs.unusable] == [4]


_ENGINE_READY = shutil.which("node") is not None and (SETTINGS.engine_root / "build" / "cli.js").exists()
_PATIENTS = measures.EXAMPLES


@pytest.mark.skipif(not _ENGINE_READY, reason="needs Node and the built engine CLI (npm run backend:build)")
@pytest.mark.parametrize(("measure", "bundle", "evidence_type", "value", "field", "expected"), [
    ("BCS-E", "independent_public_bcse/patients/patient_negative_synthea_bundle.json", "mammogram", None,
     "numerator", True),
    # GSD's engine numerator is poor control, so a good A1c turns it off (measures.INVERSE_MEASURES).
    ("GSD", "independent_public_gsd/patients/patient_positive_synthea_no_record_bundle.json", "hba1c", "6.8",
     "numerator", False),
    ("CBP", "independent_public_cbp/patients/patient_negative_synthea_bundle.json", "hospice", None,
     "denominator_exclusion", True),
])
def test_engine_reads_supplemental_resources(tmp_path: Path, measure: str, bundle: str, evidence_type: str,
                                            value: str | None, field: str, expected: bool) -> None:
    source = _PATIENTS / bundle
    patient_id = next(e["resource"]["id"] for e in json.loads(source.read_text(encoding="utf-8"))["entry"]
                      if e["resource"]["resourceType"] == "Patient")
    path = tmp_path / "000000.json"
    shutil.copyfile(source, path)
    target = next(b for b in measures.measure_bundles() if b.measure_id == measure)
    year = SETTINGS.measurement_year
    inputs = supplemental.build([_record(measure, evidence_type, value, date(year, 6, 15), patient=patient_id)])

    before = measures._evaluate(target, {patient_id: path}, tmp_path / "before", SETTINGS)[patient_id]
    after = measures._evaluate(target, {patient_id: path}, tmp_path / "after", SETTINGS, inputs.resources)[patient_id]

    assert before[field] is not expected
    assert after[field] is expected
