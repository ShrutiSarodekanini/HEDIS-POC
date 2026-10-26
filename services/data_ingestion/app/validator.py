from __future__ import annotations

import json
import re
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


# Cache for loaded FHIR models to avoid repeated expensive imports
_MODEL_CACHE: dict[str, type] = {}

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
    # Check cache first to avoid expensive repeated imports
    if resource_type in _MODEL_CACHE:
        return _MODEL_CACHE[resource_type]
    
    dotted = FHIR_MODEL_MAP.get(resource_type)
    if dotted is None:
        return None
    module_name, class_name = dotted.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    model = getattr(module, class_name)
    _MODEL_CACHE[resource_type] = model
    return model


def validate_resource(resource: ResourceEnvelope) -> ResourceValidationResult:
    return validate_resources([resource])[0]


def validate_resources(resources: list[ResourceEnvelope]) -> list[ResourceValidationResult]:
    results: list[ResourceValidationResult] = []
    externally_validated: list[ResourceEnvelope] = []

    for resource in resources:
        model = _load_model(resource.resource_type)
        messages: list[ValidationMessage] = []
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
            results.append(ResourceValidationResult(resource.resource_type, resource.resource_id, "partial", messages))
            continue

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

        results.append(ResourceValidationResult(resource.resource_type, resource.resource_id, "pending", messages))
        externally_validated.append(resource)

    external_results = validate_with_external_jar_batch(externally_validated)
    external_index = {(result.resource_type, result.resource_id): result for result in external_results}
    for result in results:
        external = external_index.get((result.resource_type, result.resource_id))
        if external:
            result.messages.extend(external.messages)
        has_errors = any(message.severity == "error" for message in result.messages)
        has_unavailable = any(message.code == "external_validator_unavailable" for message in result.messages)
        result.status = "failed" if has_errors else "unavailable" if has_unavailable else "validated"

    return results


def validate_with_external_jar_batch(resources: list[ResourceEnvelope]) -> list[ResourceValidationResult]:
    if not resources:
        return []

    if not settings.validation_require_external:
        message = ValidationMessage(
            severity="information",
            category="profile",
            code="external_validator_disabled",
            path=None,
            diagnostics="External HL7 validator disabled; structural FHIR validation completed only.",
        )
        return [ResourceValidationResult(r.resource_type, r.resource_id, "validated", [message]) for r in resources]

    jar_path = settings.fhir_validator_jar
    if not jar_path:
        message = ValidationMessage(
            severity="warning",
            category="profile",
            code="external_validator_unavailable",
            path=None,
            diagnostics="External HL7 FHIR validator not configured; structural validation completed only.",
        )
        return [ResourceValidationResult(r.resource_type, r.resource_id, "unavailable", [message]) for r in resources]

    jar = Path(jar_path)
    if not jar.exists():
        message = ValidationMessage(
            severity="warning",
            category="profile",
            code="external_validator_unavailable",
            path=None,
            diagnostics=f"Configured validator jar not found: {jar_path}",
        )
        return [ResourceValidationResult(r.resource_type, r.resource_id, "unavailable", [message]) for r in resources]

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        file_index: dict[str, ResourceEnvelope] = {}
        for index, resource in enumerate(resources):
            filename = f"{index:06d}_{resource.resource_type}_{resource.resource_id}.json"
            (directory / filename).write_text(json.dumps(resource.fhir), encoding="utf-8")
            file_index[filename] = resource

        try:
            result = subprocess.run(
                ["java", "-jar", str(jar), str(directory), "-version", "4.0.1"],
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as exc:
            message = ValidationMessage(
                severity="warning",
                category="profile",
                code="external_validator_failed",
                path=None,
                diagnostics=f"External validator failed to execute: {exc}",
            )
            return [ResourceValidationResult(r.resource_type, r.resource_id, "unavailable", [message]) for r in resources]

    output = f"{result.stdout}\n{result.stderr}"
    sections = list(re.finditer(r"^--\s+(.+?)\s+-{10,}\s*$", output, re.MULTILINE))
    parsed: dict[str, list[ValidationMessage]] = {}
    for index, section in enumerate(sections):
        section_output = output[section.end():sections[index + 1].start() if index + 1 < len(sections) else len(output)]
        filename = Path(section.group(1).strip()).name
        resource = file_index.get(filename)
        if resource is None:
            continue
        counts = re.search(r"(?:Success|Error):\s*(\d+) errors?,\s*(\d+) warnings?,\s*(\d+) notes?", section_output)
        if not counts:
            parsed[filename] = [ValidationMessage("error", "profile", "external_validator_error", None, section_output[-2000:])]
            continue
        errors, warnings, _ = (int(value) for value in counts.groups())
        messages: list[ValidationMessage] = []
        if errors:
            messages.append(ValidationMessage("error", "profile", "external_validator_error", None, section_output[-2000:]))
        elif warnings:
            messages.append(ValidationMessage("warning", "profile", "external_validator_warning", None, section_output[-2000:]))
        else:
            messages.append(ValidationMessage("information", "profile", "external_validator_ok", None, "External validator completed successfully."))
        parsed[filename] = messages

    results: list[ResourceValidationResult] = []
    for filename, resource in file_index.items():
        messages = parsed.get(filename)
        if messages is None:
            messages = [ValidationMessage("error", "profile", "external_validator_error", None, result.stderr[-2000:] or "No result returned for resource.")]
        status = "failed" if any(message.severity == "error" for message in messages) else "validated"
        results.append(ResourceValidationResult(resource.resource_type, resource.resource_id, status, messages))
    return results


def validate_with_external_jar(resource_json: dict[str, Any]) -> list[ValidationMessage]:
    if not settings.validation_require_external:
        return [
            ValidationMessage(
                severity="information",
                category="profile",
                code="external_validator_disabled",
                path=None,
                diagnostics="External HL7 validator disabled; structural FHIR validation completed only.",
            )
        ]

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
