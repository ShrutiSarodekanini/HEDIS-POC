from __future__ import annotations

import time

import httpx


def test_auth_signin_and_me_for_admin(
    client: httpx.Client,
    admin_credentials: tuple[str, str],
    openapi_paths: set[str],
) -> None:
    if "/api/auth/signin" not in openapi_paths or "/api/auth/me" not in openapi_paths:
        return

    email, password = admin_credentials
    signin = client.post("/api/auth/signin", json={"email": email, "password": password})
    assert signin.status_code == 200, signin.text
    payload = signin.json()
    token = payload.get("token")
    assert isinstance(token, str) and token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    me_payload = me.json()
    assert me_payload.get("email") == email
    assert me_payload.get("role") == "admin"


def test_auth_rejects_wrong_password(
    client: httpx.Client,
    admin_credentials: tuple[str, str],
    openapi_paths: set[str],
) -> None:
    if "/api/auth/signin" not in openapi_paths:
        return

    email, _ = admin_credentials
    response = client.post("/api/auth/signin", json={"email": email, "password": "WrongPassword@123"})
    assert response.status_code == 401, response.text


def test_auth_signout_revokes_token(
    client: httpx.Client,
    user_credentials: tuple[str, str],
    openapi_paths: set[str],
) -> None:
    required = {"/api/auth/signin", "/api/auth/me", "/api/auth/signout"}
    if not required.issubset(openapi_paths):
        return

    email, password = user_credentials
    signin = client.post("/api/auth/signin", json={"email": email, "password": password})
    assert signin.status_code == 200, signin.text
    token = signin.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    signout = client.post("/api/auth/signout", headers=headers, json={})
    assert signout.status_code == 200, signout.text

    # Session revocation is immediate, but a tiny delay avoids race conditions in slower environments.
    time.sleep(0.05)
    me_after = client.get("/api/auth/me", headers=headers)
    assert me_after.status_code == 401, me_after.text


def test_admin_list_requires_admin_role(
    client: httpx.Client,
    user_headers: dict[str, str],
    admin_headers: dict[str, str],
    openapi_paths: set[str],
) -> None:
    if "/api/admin/users" not in openapi_paths:
        return

    forbidden = client.get("/api/admin/users", headers=user_headers)
    assert forbidden.status_code in {401, 403}, forbidden.text

    allowed = client.get("/api/admin/users", headers=admin_headers)
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()
    assert isinstance(payload.get("users"), list)


def test_admin_mutations_forbid_non_admin(
    client: httpx.Client,
    user_headers: dict[str, str],
    openapi_paths: set[str],
) -> None:
    if "/api/admin/users/{user_id}/role" not in openapi_paths:
        return

    role_response = client.put("/api/admin/users/1/role", headers=user_headers, json={"role": "admin"})
    assert role_response.status_code in {401, 403}, role_response.text
