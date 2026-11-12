"""Every API route needs a signed-in user, except the few listed in PUBLIC.

The sweep sends every operation in each app's OpenAPI description without a token and expects 401, so a new
endpoint cannot ship open by accident. FastAPI resolves sub-dependencies (the sign-in check) before path, query and
body validation, so dummy path values are enough. The database dependencies are replaced, so no database is needed.
"""

import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app as root
from services.document_intake import api as intake_api
from services.integration import routes as integration_routes

# Sign-in itself, the static HTML shells, and browser error reports (masked, rate limited, sent before sign-in too).
# /metrics and the OpenAPI description are not in the schema, so the sweep does not see them.
PUBLIC = {"/", "/ingestion", "/star-results", "/api/auth/signin", "/api/auth/signup", "/api/client-errors"}
_PARAM = re.compile(r"\{[^}]+\}")


def _no_db():
    yield None  # the token check fails before a connection is used


def _operations(app) -> list[tuple[str, str]]:
    return sorted((method.upper(), path) for path, item in app.openapi()["paths"].items()
                  for method in item if path not in PUBLIC)


@pytest.fixture
def client():
    overrides = {root.get_auth_conn: _no_db, integration_routes.get_conn: _no_db,
                 root.app.state.database.get_session: _no_db}
    root.app.dependency_overrides.update(overrides)
    yield TestClient(root.app)  # not entered as a context manager, so the lifespan (workers) never starts
    root.app.dependency_overrides.clear()


@pytest.fixture
def intake_client():
    intake_api.app.dependency_overrides[intake_api.get_conn] = _no_db
    yield TestClient(intake_api.app)
    intake_api.app.dependency_overrides.clear()


def _open(client: TestClient, operations: list[tuple[str, str]]) -> list[str]:
    found = []
    for method, path in operations:
        response = client.request(method, _PARAM.sub("1", path))
        if response.status_code != 401:
            found.append(f"{method} {path} -> {response.status_code}")
    return found


def test_every_main_app_operation_needs_sign_in(client: TestClient) -> None:
    operations = _operations(root.app)
    assert len(operations) > 50
    assert _open(client, operations) == []


def test_every_document_intake_operation_needs_sign_in(intake_client: TestClient) -> None:
    assert _open(intake_client, _operations(intake_api.app)) == []


def test_batch_progress_websocket_needs_sign_in(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ingestion/ws/batch/1") as socket:
            socket.receive_json()


def test_the_old_manual_close_route_is_gone() -> None:
    assert "/api/gaps/{gap_id}/close" not in root.app.openapi()["paths"]
    assert "/api/gaps/{gap_id}/rerun" in root.app.openapi()["paths"]
