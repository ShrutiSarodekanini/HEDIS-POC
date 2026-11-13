from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import httpx
import pytest

from services.evidence_extraction.config import SETTINGS, provider_policy_error
from services.evidence_extraction.contracts import GapContext, ModelError, PageRef, Prompt
from services.evidence_extraction.model_client import OpenRouterClient
from services.evidence_extraction.validator import parse_findings, validate


def _prompt() -> Prompt:
    page = PageRef(
        document_id="D-TEST",
        page_no=1,
        doc_type="clinic note",
        document_date=date(2026, 4, 17),
        source_org_id="ORG-TEST",
        method="plain_text",
        handwritten=False,
        low_confidence=False,
        text="BP 122/74 on 2026-04-17",
    )
    return Prompt(
        system="Return JSON in the findings schema.",
        user="[DOC D-TEST | page 1 | clinic note | 2026-04-17 | ORG-TEST | plain_text]\nBP 122/74 on 2026-04-17",
        pages=(page,),
    )


def _client_settings():
    return replace(
        SETTINGS,
        openrouter_api_key="sk-or-test-key",
        data_class="synthetic",
        provider="openrouter",
        provider_mode="hosted",
        provider_has_baa=False,
        provider_allowed_data_classes=("synthetic",),
    )


def _gap(*, age: int) -> GapContext:
    return GapContext(
        gap_id="00000000-0000-0000-0000-000000000001",
        patient_id="P-TEST",
        measure_code="CBP",
        measurement_year=2026,
        age=age,
        sex="F",
        gap_type="numerator",
        missing_element="Blood pressure control",
        last_event=None,
        exclusion_check=None,
        episode=None,
        deadline=date(2026, 12, 31),
        seed_code="G-TEST",
    )


def _measure() -> dict:
    return {
        "code": "CBP",
        "allowed_evidence_types": {
            "numerator": ["blood_pressure_reading", "hrhpv_test_age_30_plus"],
            "exclusion": [],
        },
        "windows": [
            {"evidence_type": "blood_pressure_reading", "from": "2026-01-01", "to": "2026-12-31"},
            {"evidence_type": "hrhpv_test_age_30_plus", "from": "2026-01-01", "to": "2026-12-31"},
        ],
        "thresholds": {"systolic_lt": 140, "diastolic_lt": 90},
    }


def _page(text: str) -> PageRef:
    return PageRef(
        document_id="D-TEST",
        page_no=1,
        doc_type="clinic note",
        document_date=date(2026, 4, 17),
        source_org_id="ORG-TEST",
        method="plain_text",
        handwritten=False,
        low_confidence=False,
        text=text,
    )


def test_provider_policy_blocks_hosted_phi_without_baa() -> None:
    settings = replace(
        SETTINGS,
        data_class="phi",
        provider="openrouter",
        provider_mode="hosted",
        provider_has_baa=False,
        provider_allowed_data_classes=("synthetic", "phi"),
    )
    message = provider_policy_error(settings)
    assert message is not None
    assert "without a BAA" in message


def test_http_4xx_error_message_excludes_provider_body_excerpt() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"patient_name=Alice Example"}')

    client = OpenRouterClient(settings=_client_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)

    with pytest.raises(ModelError) as raised:
        client.complete(_prompt())

    err = raised.value
    assert err.code == "http_400"
    assert "Alice Example" not in str(err)


def test_golden_case_keeps_valid_bp_finding() -> None:
    findings = parse_findings(
        json.dumps(
            {
                "findings": [
                    {
                        "document_id": "D-TEST",
                        "page_no": 1,
                        "excerpt": "BP 122/74 on 2026-04-17",
                        "evidence_kind": "numerator",
                        "evidence_type": "blood_pressure_reading",
                        "service_date": "2026-04-17",
                        "value": "122/74",
                        "confidence": 0.92,
                        "reason": "Recent controlled blood pressure reading",
                        "part": "",
                    }
                ]
            }
        )
    )

    result = validate(_gap(age=58), _measure(), [_page("Follow-up visit. BP 122/74 on 2026-04-17. Stable.")], findings)
    assert len(result.kept) == 1
    assert len(result.dropped) == 0


def test_golden_case_drops_hallucinated_quote() -> None:
    findings = parse_findings(
        json.dumps(
            {
                "findings": [
                    {
                        "document_id": "D-TEST",
                        "page_no": 1,
                        "excerpt": "BP 118/70 on 2026-04-20",
                        "evidence_kind": "numerator",
                        "evidence_type": "blood_pressure_reading",
                        "service_date": "2026-04-20",
                        "value": "118/70",
                        "confidence": 0.88,
                        "reason": "Controlled blood pressure reading",
                        "part": "",
                    }
                ]
            }
        )
    )

    result = validate(_gap(age=58), _measure(), [_page("Follow-up visit. BP 122/74 on 2026-04-17. Stable.")], findings)
    assert len(result.kept) == 0
    assert len(result.dropped) == 1
    assert "quote is not on" in result.dropped[0].reason
