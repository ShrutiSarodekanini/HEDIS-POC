"""Local username/password auth for the integration app, and the role a user needs for each kind of action.

The POC uses app-managed users in PostgreSQL with bearer sessions stored server-side.
Emails are lowercase-only. Session tokens expire after 24 hours.

Roles follow the users named in UC-HC-02: quality analysts, medical record abstractors, outreach coordinators and the
compliance (audit) team, plus administrators. PERMISSIONS says which roles may do what; the API checks it on every
route (app.py `requires`), and the UI mirrors it only to hide what a role cannot use.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import HTTPException

from services.document_intake.db import audit

log = logging.getLogger(__name__)

SESSION_TTL_HOURS = 24
PASSWORD_ITERATIONS = 210_000
ROLES = {"admin", "analyst", "abstractor", "outreach", "auditor"}
# A new sign-up gets the least access until an administrator approves it and picks its role.
SIGNUP_ROLE = "outreach"
ACCESS_STATUSES = {"pending", "approved", "rejected"}

PERMISSIONS: dict[str, frozenset[str]] = {
    # Dashboards, the prioritized work list, measures, stars and data quality summaries.
    "view": frozenset(ROLES),
    # Anything with patient records: gap detail and evidence quotes, document pages, patient FHIR data, patient lists.
    "view_records": frozenset({"admin", "analyst", "abstractor", "auditor"}),
    # Run AI evidence for a gap, confirm or reject evidence, and ask the engine to rerun a gap.
    "review": frozenset({"admin", "analyst", "abstractor"}),
    # Start measure calculations.
    "calculate": frozenset({"admin", "analyst"}),
    # Uploads, users, exports, data quality runs, reset and sample data.
    "admin": frozenset({"admin"}),
}


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


def ensure_bootstrap_admin(conn: psycopg.Connection) -> bool:
    """Create the admin named by HEDIS_BOOTSTRAP_ADMIN_EMAIL and HEDIS_BOOTSTRAP_ADMIN_PASSWORD if it does not exist.

    Does nothing when either setting is empty or the email already has an account (its password and role are never
    changed). Returns whether an approved, active admin exists afterwards."""
    email = os.environ.get("HEDIS_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("HEDIS_BOOTSTRAP_ADMIN_PASSWORD", "")
    if email and password:
        try:
            user_email = normalize_email(email)
            validate_password(password)
        except AuthError as exc:
            log.warning("Bootstrap admin not created, HEDIS_BOOTSTRAP_ADMIN_* is not valid: %s", exc)
        else:
            found = conn.execute("SELECT 1 FROM app_user WHERE email = %s", (user_email,)).fetchone()
            if not found:
                user_id = conn.execute(
                    """INSERT INTO app_user (email, password_hash, role, is_active, access_status)
                       VALUES (%s, %s, 'admin', true, 'approved') RETURNING user_id""",
                    (user_email, hash_password(password)),
                ).fetchone()[0]
                audit(conn, "app_user", str(user_id), "user.bootstrap_admin",
                      {"email": user_email})  # phi-ok: staff admin sign-in email, required by the bootstrap audit
    row = conn.execute(
        """SELECT EXISTS (SELECT 1 FROM app_user
                           WHERE role = 'admin' AND is_active AND access_status = 'approved')"""
    ).fetchone()
    return bool(row[0])


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
               VALUES (%s, %s, %s, false, 'pending')""",
            (user_email, hash_password(password), SIGNUP_ROLE),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ValueError("This email is already registered.") from exc


def set_password(conn: psycopg.Connection, user_id: int, new_password: str) -> None:
    """Set a user's password (administrator action) and revoke that user's sessions. Raises LookupError, AuthError."""
    validate_password(new_password)
    row = conn.execute("UPDATE app_user SET password_hash = %s, updated_at = now() WHERE user_id = %s RETURNING user_id",
                       (hash_password(new_password), user_id)).fetchone()
    if row is None:
        raise LookupError("User account not found.")
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


def set_access_status(conn: psycopg.Connection, user_id: int, access_status: str, actor: AuthUser) -> dict:
    """Approve or reject an account, revoke its sessions whenever access is removed, and audit the change."""
    if access_status not in ACCESS_STATUSES:
        raise ValueError("Access status must be pending, approved, or rejected.")
    if user_id == actor.user_id:
        raise ValueError("You cannot change your own account access.")
    before = conn.execute("SELECT access_status FROM app_user WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
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
    audit(conn, "app_user", str(user_id), "user.access_changed",
          {"user_id": user_id, "access_status_before": before[0], "access_status": access_status},
          actor=actor.email)  # phi-ok: actor is the admin's sign-in email, not patient data
    keys = ("user_id", "email", "role", "access_status", "is_active", "created_at", "updated_at")
    return dict(zip(keys, row))


def set_role(conn: psycopg.Connection, user_id: int, role: str, actor: AuthUser) -> dict:
    """Set an approved user's role, revoke sessions so it takes effect on next sign-in, and audit the change."""
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(sorted(ROLES))}.")
    if user_id == actor.user_id and role != "admin":
        raise ValueError("You cannot remove your own administrator role.")
    before = conn.execute("SELECT role FROM app_user WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
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
    audit(conn, "app_user", str(user_id), "user.role_changed",
          {"user_id": user_id, "role_before": before[0], "role": role},
          actor=actor.email)  # phi-ok: actor is the admin's sign-in email, not patient data
    keys = ("user_id", "email", "role", "access_status", "is_active", "created_at", "updated_at")
    return dict(zip(keys, row))


def ensure_permission(user: AuthUser, permission: str) -> AuthUser:
    """The user, or 403 when their role does not have this permission (PERMISSIONS)."""
    allowed = PERMISSIONS[permission]
    if user.role not in allowed:
        raise HTTPException(status_code=403,
                            detail=f"Your role ({user.role}) cannot do this. Roles that can: {', '.join(sorted(allowed))}.")
    return user


def ensure_admin(user: AuthUser) -> AuthUser:
    return ensure_permission(user, "admin")
