from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_evidence_service_contains_integration_review_endpoints() -> None:
    source = (ROOT / "ui" / "src" / "services" / "evidence.service.ts").read_text(encoding="utf-8")

    expected_fragments = [
        'hasOpenApiPath(paths, "/api/measures")',
        'hasOpenApiPath(paths, "/api/gaps")',
        'hasOpenApiPath(paths, "/api/gaps/{gap_id}")',
        'hasOpenApiPath(paths, "/api/gaps/{gap_id}/evidence")',
        'hasOpenApiPath(paths, "/api/evidence/{proposal_id}/decision")',
        'hasOpenApiPath(paths, "/api/supplemental/export")',
        'withApiBase(`/gaps/${encodeURIComponent(gapId)}/evidence`)',
        'withApiBase(`/evidence/${encodeURIComponent(proposalId)}/decision`)',
        'withApiBase("/supplemental/export")',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_evidence_service_excludes_auth_signin_runtime_path() -> None:
    source = (ROOT / "ui" / "src" / "services" / "evidence.service.ts").read_text(encoding="utf-8")

    assert 'withApiBase("/auth/signin")' not in source


def test_backend_source_declares_evidence_review_endpoints() -> None:
    integration_source = (ROOT / "services" / "integration" / "routes.py").read_text(encoding="utf-8")

    expected_decorators = [
        '@router.get("/api/measures")',
        '@router.get("/api/gaps")',
        '@router.get("/api/gaps/{gap_id}")',
        '@router.post("/api/gaps/{gap_id}/evidence")',
        '@router.post("/api/evidence/{proposal_id}/decision")',
        '@router.post("/api/supplemental/export")',
    ]

    for decorator in expected_decorators:
        assert decorator in integration_source
