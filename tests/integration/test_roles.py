"""Roles and permissions (services/integration/auth.py PERMISSIONS), enforced by the API on every route.

The matrix test signs in as each role with a stand-in user and checks which routes answer 403. A route the role may
use gets past the permission check and then fails on the missing database, which is any status but 401 or 403.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as root
from services.integration import auth
from services.integration import routes as integration_routes

GAP = "00000000-0000-0000-0000-000000000000"
ALL = set(auth.ROLES)

# (method, path, json body, roles that may call it)
MATRIX = [
    ("GET", "/api/gaps", None, ALL),
    ("GET", "/api/stars/catalog", None, ALL),
    ("GET", f"/api/gaps/{GAP}", None, ALL - {"outreach"}),
    ("GET", "/api/documents/D-1/pages/1", None, ALL - {"outreach"}),
    ("GET", "/api/ingestion/jobs", None, ALL - {"outreach"}),
    ("GET", "/api/calculation/patients", None, ALL - {"outreach"}),
    ("POST", f"/api/gaps/{GAP}/evidence", None, {"admin", "analyst", "abstractor"}),
    ("POST", f"/api/evidence/{GAP}/decision", {"decision": "confirm"}, {"admin", "analyst", "abstractor"}),
    ("POST", f"/api/gaps/{GAP}/rerun", None, {"admin", "analyst", "abstractor"}),
    ("POST", "/api/calculation/run", {}, {"admin", "analyst"}),
    ("POST", "/api/supplemental/export", None, {"admin"}),
    ("POST", "/api/ingestion/jobs/start", {}, {"admin"}),
    ("POST", "/api/automation/process-gaps", None, {"admin"}),
    ("GET", "/api/admin/users", None, {"admin"}),
]


def _no_db():
    yield None


@pytest.fixture
def as_role():
    def sign_in(role: str) -> TestClient:
        root.app.dependency_overrides.update({
            root.current_auth_user: lambda: auth.AuthUser(user_id=99, email=f"{role}@example.org", role=role),
            root.get_auth_conn: _no_db, integration_routes.get_conn: _no_db,
            root.app.state.database.get_session: _no_db})
        return TestClient(root.app, raise_server_exceptions=False)

    yield sign_in
    root.app.dependency_overrides.clear()


@pytest.mark.parametrize("role", sorted(ALL))
def test_each_role_reaches_only_its_routes(as_role, role: str) -> None:
    client = as_role(role)
    wrong = []
    for method, path, body, allowed in MATRIX:
        status = client.request(method, path, json=body).status_code
        if (role in allowed) == (status in (401, 403)):
            wrong.append(f"{method} {path}: {status} for {role} (allowed: {role in allowed})")
    assert wrong == []


def test_every_permission_includes_admin_and_view_includes_every_role() -> None:
    assert all("admin" in roles for roles in auth.PERMISSIONS.values())
    assert auth.PERMISSIONS["view"] == frozenset(auth.ROLES)
    assert auth.PERMISSIONS["review"] <= auth.PERMISSIONS["view_records"]


def test_forbidden_message_names_the_roles_that_can() -> None:
    with pytest.raises(HTTPException) as caught:
        auth.ensure_permission(auth.AuthUser(1, "o@example.org", "outreach"), "review")
    assert caught.value.status_code == 403
    assert "abstractor" in caught.value.detail and "outreach" in caught.value.detail


class _Conn:
    """Returns queued rows to fetchone and records every statement."""

    def __init__(self, *rows: tuple | None) -> None:
        self.rows = list(rows)
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> "_Conn":
        self.sql.append((sql, params))
        return self

    def fetchone(self) -> tuple | None:
        return self.rows.pop(0) if self.rows else None


ADMIN = auth.AuthUser(1, "admin@example.org", "admin")
_ROW = (5, "abs@example.org", "abstractor", "approved", True, None, None)


def _audits(conn: _Conn) -> list[tuple]:
    return [params for sql, params in conn.sql if "INSERT INTO audit_events" in sql]


def test_role_change_is_audited_with_the_admin_as_actor() -> None:
    conn = _Conn(("analyst",), _ROW)
    auth.set_role(conn, 5, "abstractor", ADMIN)
    [(entity_type, entity_id, action, actor, payload)] = _audits(conn)
    assert (entity_type, entity_id, action, actor) == ("app_user", "5", "user.role_changed", "admin@example.org")
    assert payload.obj == {"user_id": 5, "role_before": "analyst", "role": "abstractor"}


def test_access_change_is_audited() -> None:
    conn = _Conn(("pending",), _ROW)
    auth.set_access_status(conn, 5, "approved", ADMIN)
    [(_, _, action, actor, payload)] = _audits(conn)
    assert (action, actor) == ("user.access_changed", "admin@example.org")
    assert payload.obj == {"user_id": 5, "access_status_before": "pending", "access_status": "approved"}


def test_unknown_role_is_refused_before_any_change() -> None:
    conn = _Conn()
    with pytest.raises(ValueError, match="Role must be one of"):
        auth.set_role(conn, 5, "user", ADMIN)
    assert conn.sql == []
