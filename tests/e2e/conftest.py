from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _as_token(response: httpx.Response) -> str:
    payload = response.json()
    token = payload.get("token")
    assert isinstance(token, str) and token
    return token


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


@pytest.fixture(scope="session")
def client(base_url: str) -> httpx.Client:
    with httpx.Client(base_url=base_url, timeout=30.0) as shared:
        yield shared


@pytest.fixture(scope="session")
def openapi_spec(client: httpx.Client) -> dict[str, Any]:
    try:
        response = client.get("/openapi.json")
    except httpx.HTTPError as exc:
        pytest.skip(f"E2E backend is not reachable at {client.base_url!s}: {type(exc).__name__}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    assert "paths" in payload
    return payload


@pytest.fixture(scope="session")
def openapi_paths(openapi_spec: dict[str, Any]) -> set[str]:
    paths = openapi_spec.get("paths", {})
    assert isinstance(paths, dict)
    return set(paths.keys())


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    return (
        os.getenv("E2E_ADMIN_EMAIL", "admin@gmail.com"),
        os.getenv("E2E_ADMIN_PASSWORD", "Admin@123"),
    )


@pytest.fixture(scope="session")
def user_credentials() -> tuple[str, str]:
    return (
        os.getenv("E2E_USER_EMAIL", "user@gmail.com"),
        os.getenv("E2E_USER_PASSWORD", "User@123"),
    )


@pytest.fixture(scope="session")
def admin_token(
    client: httpx.Client,
    openapi_paths: set[str],
    admin_credentials: tuple[str, str],
) -> str:
    if "/api/auth/signin" not in openapi_paths:
        pytest.skip("/api/auth/signin is not available in this backend profile.")

    email, password = admin_credentials
    response = client.post("/api/auth/signin", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return _as_token(response)


@pytest.fixture(scope="session")
def user_token(
    client: httpx.Client,
    openapi_paths: set[str],
    user_credentials: tuple[str, str],
) -> str:
    if "/api/auth/signin" not in openapi_paths:
        pytest.skip("/api/auth/signin is not available in this backend profile.")

    email, password = user_credentials
    response = client.post("/api/auth/signin", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return _as_token(response)


@pytest.fixture(scope="session")
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def user_headers(user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}"}
