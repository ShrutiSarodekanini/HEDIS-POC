"""Local username/password auth for the integration app.

The POC uses app-managed users in PostgreSQL with bearer sessions stored server-side.
Emails are lowercase-only. Session tokens expire after 24 hours.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import HTTPException

SESSION_TTL_HOURS = 24
PASSWORD_ITERATIONS = 210_000
ROLES = {"admin", "user"}
ACCESS_STATUSES = {"pending", "approved", "rejected"}

_DEFAULT_USERS = [
    ("admin@gmail.com", "Admin@123", "admin"),
    ("user@gmail.com", "User@123", "user"),
]


@dataclass(frozen=True)
class AuthUser:
    user_id: int
    email: str
    role: str


class AuthError(ValueError):
    """Raised when auth inputs are invalid."""


class AccessNotApproved(AuthError):
    """Raised when an account has not received administrator approval."""


def normalize_email(email: str) -> str:
    value = (email or "").strip()
    if not value:
        raise AuthError("Email is required.")
    if value != value.lower():
        raise AuthError("Email must use lowercase letters only.")
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise AuthError("Email is not valid.")
    return value


def validate_password(password: str) -> None:
    if not password or len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, rounds, salt_b64, digest_b64 = stored_hash.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        rounds_i = int(rounds)
        salt = _b64_decode(salt_b64)
        expected = _b64_decode(digest_b64)
    except (ValueError, binascii.Error):
        return False
    try:
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds_i)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_default_users(conn: psycopg.Connection) -> None:
    """Create default demo users once, with lowercase emails."""
    for email, password, role in _DEFAULT_USERS:
        found = conn.execute("SELECT 1 FROM app_user WHERE email = %s", (email,)).fetchone()
        if found:
            continue
        conn.execute(
            """INSERT INTO app_user (email, password_hash, role, is_active, access_status)
               VALUES (%s, %s, %s, true, 'approved')""",
            (email, hash_password(password), role),
        )


def create_session(conn: psycopg.Connection, user_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS)
    conn.execute(
        """INSERT INTO auth_session (session_id, user_id, token_hash, expires_at)
           VALUES (%s, %s, %s, %s)""",
        (str(uuid.uuid4()), user_id, _token_hash(token), expires_at),
    )
    return token, expires_at


def signin(conn: psycopg.Connection, email: str, password: str) -> tuple[AuthUser, str, datetime]:
    user_email = normalize_email(email)
    row = conn.execute(
        """SELECT user_id, email, password_hash, role, is_active, access_status
           FROM app_user WHERE email = %s""",
        (user_email,),
    ).fetchone()
    if row is None or not verify_password(password, row[2]):
        raise LookupError("Email or password is not valid.")
    if not row[4] or row[5] != "approved":
        raise AccessNotApproved("This account is awaiting administrator approval.")
    user = AuthUser(user_id=row[0], email=row[1], role=row[3])
    token, expires_at = create_session(conn, user.user_id)
    return user, token, expires_at


def signup(conn: psycopg.Connection, email: str, password: str) -> None:
    user_email = normalize_email(email)
    validate_password(password)
    try:
        conn.execute(
            """INSERT INTO app_user (email, password_hash, role, is_active, access_status)
               VALUES (%s, %s, 'user', false, 'pending')""",
            (user_email, hash_password(password)),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ValueError("This email is already registered.") from exc


def reset_password_for_email(conn: psycopg.Connection, email: str, new_password: str) -> None:
    user_email = normalize_email(email)
    validate_password(new_password)
    row = conn.execute("""SELECT user_id FROM app_user
                           WHERE email = %s AND is_active AND access_status = 'approved'""", (user_email,)).fetchone()
    if row is None:
        raise LookupError("Email does not exist.")
    user_id = row[0]
    conn.execute("UPDATE app_user SET password_hash = %s, updated_at = now() WHERE user_id = %s",
                 (hash_password(new_password), user_id))
    conn.execute("UPDATE auth_session SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Sign in required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token is required.")
    return token.strip()


def current_user(conn: psycopg.Connection, authorization: str | None) -> AuthUser:
    token = _bearer_token(authorization)
    row = conn.execute(
        """SELECT u.user_id, u.email, u.role
             FROM auth_session s
             JOIN app_user u ON u.user_id = s.user_id
            WHERE s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND u.is_active
              AND u.access_status = 'approved'""",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Session expired or not valid. Sign in again.")
    return AuthUser(user_id=row[0], email=row[1], role=row[2])


def signout(conn: psycopg.Connection, authorization: str | None) -> None:
    token = _bearer_token(authorization)
    conn.execute("UPDATE auth_session SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
                 (_token_hash(token),))


def list_users(conn: psycopg.Connection) -> list[dict]:
    """Return user access records for the administrator-only user management screen."""
    rows = conn.execute(
        """SELECT user_id, email, role, access_status, is_active, created_at, updated_at
             FROM app_user
            ORDER BY CASE access_status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                     created_at DESC"""
    ).fetchall()
    keys = ("user_id", "email", "role", "access_status", "is_active", "created_at", "updated_at")
    return [dict(zip(keys, row)) for row in rows]


def set_access_status(conn: psycopg.Connection, user_id: int, access_status: str, actor_id: int) -> dict:
    """Approve or reject an account and revoke its sessions whenever access is removed."""
    if access_status not in ACCESS_STATUSES:
        raise ValueError("Access status must be pending, approved, or rejected.")
    if user_id == actor_id:
        raise ValueError("You cannot change your own account access.")
    active = access_status == "approved"
    row = conn.execute(
        """UPDATE app_user
              SET access_status = %s, is_active = %s, updated_at = now()
            WHERE user_id = %s
        RETURNING user_id, email, role, access_status, is_active, created_at, updated_at""",
        (access_status, active, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("User account not found.")
    if not active:
        conn.execute("UPDATE auth_session SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    keys = ("user_id", "email", "role", "access_status", "is_active", "created_at", "updated_at")
    return dict(zip(keys, row))


def set_role(conn: psycopg.Connection, user_id: int, role: str, actor_id: int) -> dict:
    """Set an approved user's role and revoke sessions so the new authorization takes effect on next sign-in."""
    if role not in ROLES:
        raise ValueError("Role must be admin or user.")
    if user_id == actor_id and role != "admin":
        raise ValueError("You cannot remove your own administrator role.")
    row = conn.execute(
        """UPDATE app_user
              SET role = %s, updated_at = now()
            WHERE user_id = %s AND is_active AND access_status = 'approved'
        RETURNING user_id, email, role, access_status, is_active, created_at, updated_at""",
        (role, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Only an approved user can receive a role.")
    conn.execute("UPDATE auth_session SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    keys = ("user_id", "email", "role", "access_status", "is_active", "created_at", "updated_at")
    return dict(zip(keys, row))


def ensure_admin(user: AuthUser) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access is required.")
    return user
