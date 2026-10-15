from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import settings
from .mapping import ResourceEnvelope


@dataclass
class ValidationMessage:
    severity: str
    category: str
    code: str
    path: str | None
    diagnostics: str


@dataclass
class ResourceValidationResult:
    resource_type: str
    resource_id: str
    status: str
    messages: list[ValidationMessage] = field(default_factory=list)


FHIR_MODEL_MAP = {
    "Patient": "fhir.resources.patient.Patient",
    "Encounter": "fhir.resources.encounter.Encounter",
    "Condition": "fhir.resources.condition.Condition",
    "Observation": "fhir.resources.observation.Observation",
    "Procedure": "fhir.resources.procedure.Procedure",
    "MedicationRequest": "fhir.resources.medicationrequest.MedicationRequest",
    "Immunization": "fhir.resources.immunization.Immunization",
    "CarePlan": "fhir.resources.careplan.CarePlan",
    "Claim": "fhir.resources.claim.Claim",
    "Coverage": "fhir.resources.coverage.Coverage",
}


def _load_model(resource_type: str):
    dotted = FHIR_MODEL_MAP.get(resource_type)
    if dotted is None:
        return None
    module_name, class_name = dotted.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def validate_resource(resource: ResourceEnvelope) -> ResourceValidationResult:
    messages: list[ValidationMessage] = []

    model = _load_model(resource.resource_type)
    if model is None:
        messages.append(
            ValidationMessage(
                severity="warning",
                category="profile",
                code="unsupported_resource_type",
                path=None,
                diagnostics="No structural validator mapped for this resource type.",
            )
        )
        return ResourceValidationResult(resource.resource_type, resource.resource_id, "partial", messages)

    try:
        if hasattr(model, "model_validate"):
            model.model_validate(resource.fhir)
        elif hasattr(model, "parse_obj"):
            model.parse_obj(resource.fhir)
        else:
            raise TypeError(f"Unsupported FHIR model API for {resource.resource_type}")
    except ValidationError as exc:
        for err in exc.errors():
            path = ".".join(str(x) for x in err.get("loc", [])) or None
            messages.append(
                ValidationMessage(
                    severity="error",
                    category="structural",
                    code=err.get("type", "validation_error"),
                    path=path,
                    diagnostics=err.get("msg", "FHIR model validation error."),
                )
            )
    except Exception as exc:
        messages.append(
            ValidationMessage(
                severity="error",
                category="structural",
                code="validation_exception",
                path=None,
                diagnostics=str(exc),
            )
        )

    external = validate_with_external_jar(resource.fhir)
    messages.extend(external)

    has_errors = any(m.severity == "error" for m in messages)
    has_unavailable = any(m.code == "external_validator_unavailable" for m in messages)

    if has_errors:
        return ResourceValidationResult(resource.resource_type, resource.resource_id, "failed", messages)

    if has_unavailable:
        return ResourceValidationResult(resource.resource_type, resource.resource_id, "unavailable", messages)

    return ResourceValidationResult(resource.resource_type, resource.resource_id, "validated", messages)


def validate_with_external_jar(resource_json: dict[str, Any]) -> list[ValidationMessage]:
    jar_path = settings.fhir_validator_jar
    if not jar_path:
        return [
            ValidationMessage(
                severity="warning",
                category="profile",
                code="external_validator_unavailable",
                path=None,
                diagnostics="External HL7 FHIR validator not configured; structural validation completed only.",
            )
        ]

    jar = Path(jar_path)
    if not jar.exists():
        return [
            ValidationMessage(
                severity="warning",
                category="profile",
                code="external_validator_unavailable",
                path=None,
                diagnostics=f"Configured validator jar not found: {jar_path}",
            )
        ]

    with tempfile.TemporaryDirectory() as temp_dir:
        payload_path = Path(temp_dir) / "resource.json"
        payload_path.write_text(json.dumps(resource_json), encoding="utf-8")

        try:
            result = subprocess.run(
                ["java", "-jar", str(jar), str(payload_path), "-version", "4.0.1"],
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as exc:
            return [
                ValidationMessage(
                    severity="warning",
                    category="profile",
                    code="external_validator_failed",
                    path=None,
                    diagnostics=f"External validator failed to execute: {exc}",
                )
            ]

    messages: list[ValidationMessage] = []
    if result.returncode != 0:
        messages.append(
            ValidationMessage(
                severity="error",
                category="profile",
                code="external_validator_error",
                path=None,
                diagnostics=result.stdout[-2000:] or result.stderr[-2000:] or "External validator returned non-zero status.",
            )
        )
    else:
        messages.append(
            ValidationMessage(
                severity="information",
                category="profile",
                code="external_validator_ok",
                path=None,
                diagnostics="External validator completed successfully.",
            )
        )

    return messages
