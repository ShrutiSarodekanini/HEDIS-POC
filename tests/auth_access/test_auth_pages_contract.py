from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_auth_access_page_contains_mode_switch_and_feedback_states() -> None:
    source = (ROOT / "ui" / "src" / "pages" / "AuthAccessPage.tsx").read_text(encoding="utf-8")

    required_fragments = [
        "Sign in",
        "Sign up",
        "Create account",
        "Passwords do not match.",
        "Authentication endpoints are not available on this backend profile.",
        "Sign in with your approved account",
        "Register with a lowercase email.",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_signin_and_signup_forms_contain_required_fields() -> None:
    signin_source = (ROOT / "ui" / "src" / "pages" / "SignInPage.tsx").read_text(encoding="utf-8")
    signup_source = (ROOT / "ui" / "src" / "pages" / "SignUpPage.tsx").read_text(encoding="utf-8")

    signin_required = [
        "Email (lowercase)",
        "Password",
        "Sign in",
        "Please wait...",
    ]
    signup_required = [
        "Email (lowercase)",
        "Password",
        "Confirm password",
        "Create account",
        "Please wait...",
    ]

    for fragment in signin_required:
        assert fragment in signin_source
    for fragment in signup_required:
        assert fragment in signup_source


def test_auth_pages_exclude_merge_conflict_markers() -> None:
    for relative in [
        "ui/src/pages/AuthAccessPage.tsx",
        "ui/src/pages/SignInPage.tsx",
        "ui/src/pages/SignUpPage.tsx",
    ]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "<<<<<<<" not in source
        assert ">>>>>>>" not in source
