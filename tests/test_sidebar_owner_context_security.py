from __future__ import annotations

from datetime import datetime as RealDateTime
from time import time

from aicrm_next.platform.shared import signed_context
from aicrm_next.platform.shared.signed_context import (
    build_sidebar_owner_context_token,
    validate_sidebar_owner_context,
)
from aicrm_next.platform.shared.signed_session import sign_session_payload


def _viewer_session(
    *,
    viewer_userid: str = "owner-a",
    external_userid: str = "external-a",
    session_id: str = "session-a",
    corp_id: str = "corp-a",
) -> str:
    return sign_session_payload(
        {
            "auth_source": "wecom_sidebar_oauth",
            "wecom_userid": viewer_userid,
            "external_userid": external_userid,
            "session_id": session_id,
            "corp_id": corp_id,
            "iat": int(time()),
        }
    )


def test_sidebar_owner_context_accepts_only_matching_viewer_customer_session_and_corp(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-context-security")
    token = build_sidebar_owner_context_token(
        viewer_userid="owner-a",
        external_userid="external-a",
        session_id="session-a",
        corp_id="corp-a",
    )

    valid = validate_sidebar_owner_context(
        token=token,
        viewer_session_cookie=_viewer_session(),
        external_userid="external-a",
        expected_corp_id="corp-a",
    )

    assert valid["ok"] is True
    assert valid["context"]["owner_userid"] == "owner-a"
    assert valid["context"]["external_userid"] == "external-a"


def test_sidebar_owner_context_rejects_tamper_purpose_and_cross_scope_replay(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-context-negative")
    token = build_sidebar_owner_context_token(
        viewer_userid="owner-a",
        external_userid="external-a",
        session_id="session-a",
        corp_id="corp-a",
    )

    cases = {
        "tampered": validate_sidebar_owner_context(
            token=f"{token}tampered",
            viewer_session_cookie=_viewer_session(),
            external_userid="external-a",
        ),
        "cross_employee": validate_sidebar_owner_context(
            token=token,
            viewer_session_cookie=_viewer_session(viewer_userid="owner-b"),
            external_userid="external-a",
        ),
        "cross_customer": validate_sidebar_owner_context(
            token=token,
            viewer_session_cookie=_viewer_session(external_userid="external-b"),
            external_userid="external-b",
        ),
        "replayed_in_new_session": validate_sidebar_owner_context(
            token=token,
            viewer_session_cookie=_viewer_session(session_id="session-b"),
            external_userid="external-a",
        ),
        "cross_corp": validate_sidebar_owner_context(
            token=token,
            viewer_session_cookie=_viewer_session(corp_id="corp-b"),
            external_userid="external-a",
        ),
    }
    wrong_purpose = signed_context._owner_serializer().dumps(
        {
            "viewer_userid": "owner-a",
            "owner_userid": "owner-a",
            "external_userid": "external-a",
            "session_fingerprint": signed_context.sidebar_session_fingerprint("session-a"),
            "corp_id": "corp-a",
            "source": "sidebar_product_link",
            "issued_at": int(time()),
            "expires_at": int(time()) + 300,
        }
    )
    cases["purpose_mismatch"] = validate_sidebar_owner_context(
        token=wrong_purpose,
        viewer_session_cookie=_viewer_session(),
        external_userid="external-a",
    )

    assert {name: result["ok"] for name, result in cases.items()} == {
        "tampered": False,
        "cross_employee": False,
        "cross_customer": False,
        "replayed_in_new_session": False,
        "cross_corp": False,
        "purpose_mismatch": False,
    }


def test_sidebar_owner_context_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-context-expired")

    class FrozenDateTime:
        current = 1_700_000_000

        @classmethod
        def now(cls, tz=None):
            return RealDateTime.fromtimestamp(cls.current, tz=tz)

    monkeypatch.setattr(signed_context, "datetime", FrozenDateTime)
    token = build_sidebar_owner_context_token(
        viewer_userid="owner-a",
        external_userid="external-a",
        session_id="session-a",
        corp_id="corp-a",
        ttl_seconds=60,
    )
    FrozenDateTime.current += 61

    result = validate_sidebar_owner_context(
        token=token,
        viewer_session_cookie=_viewer_session(),
        external_userid="external-a",
    )

    assert result == {"ok": False, "status": "expired", "context": {}}
