from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from itsdangerous import URLSafeSerializer

from aicrm_next.platform.shared import signed_context


def test_sidebar_product_context_token_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "sidebar-context-native-secret")

    token = signed_context.build_sidebar_product_context_token(
        external_userid="wm_ext_001",
        owner_userid="sales_01",
        bind_by_userid="advisor_01",
    )
    result = signed_context.load_sidebar_product_context_token(token)

    assert result["ok"] is True
    assert result["status"] == "valid"
    assert result["context"]["external_userid"] == "wm_ext_001"
    assert result["context"]["owner_userid"] == "sales_01"
    assert result["context"]["bind_by_userid"] == "advisor_01"
    assert result["context"]["source"] == signed_context.SIDEBAR_PRODUCT_CONTEXT_RESOLVED_SOURCE
    assert result["context"]["issued_at"]
    assert result["context"]["expires_at"]


def test_sidebar_product_context_legacy_compatible_payload(monkeypatch) -> None:
    secret = "sidebar-context-legacy-compatible-secret"
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", secret)
    now = int(datetime.now(timezone.utc).timestamp())
    token = URLSafeSerializer(secret, salt="aicrm-sidebar-product-context-v1").dumps(
        {
            "external_userid": "wm_legacy_001",
            "owner_userid": "sales_legacy",
            "bind_by_userid": "owner_legacy",
            "source": "sidebar_product_link",
            "issued_at": now,
            "expires_at": now + 3600,
        }
    )

    result = signed_context.load_sidebar_product_context_token(token)

    assert result["ok"] is True
    assert result["status"] == "valid"
    assert result["context"]["external_userid"] == "wm_legacy_001"
    assert result["context"]["owner_userid"] == "sales_legacy"
    assert result["context"]["bind_by_userid"] == "owner_legacy"
    assert result["context"]["source"] == "signed_sidebar_product_link"


def test_sidebar_product_context_invalid_missing_expired(monkeypatch) -> None:
    secret = "sidebar-context-expiry-secret"
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", secret)
    now = int(datetime.now(timezone.utc).timestamp())
    expired = URLSafeSerializer(secret, salt=signed_context.SIDEBAR_PRODUCT_CONTEXT_SALT).dumps(
        {
            "external_userid": "wm_expired",
            "owner_userid": "sales_01",
            "bind_by_userid": "sales_01",
            "source": signed_context.SIDEBAR_PRODUCT_CONTEXT_SOURCE,
            "issued_at": now - 7200,
            "expires_at": now - 3600,
        }
    )

    assert signed_context.load_sidebar_product_context_token("") == {"ok": False, "status": "missing", "context": {}}
    assert signed_context.load_sidebar_product_context_token("not-a-valid-token") == {"ok": False, "status": "invalid", "context": {}}
    assert signed_context.load_sidebar_product_context_token(expired) == {"ok": False, "status": "expired", "context": {}}


def test_sidebar_product_context_ttl_clamp(monkeypatch) -> None:
    monkeypatch.setenv("SIDEBAR_PRODUCT_CONTEXT_TOKEN_TTL_SECONDS", "59")
    monkeypatch.delenv("SIDEBAR_CONTEXT_TOKEN_TTL_SECONDS", raising=False)
    assert signed_context.sidebar_product_context_ttl_seconds() == 3600

    monkeypatch.setenv("SIDEBAR_PRODUCT_CONTEXT_TOKEN_TTL_SECONDS", str(365 * 86400))
    assert signed_context.sidebar_product_context_ttl_seconds() == 180 * 86400

    monkeypatch.delenv("SIDEBAR_PRODUCT_CONTEXT_TOKEN_TTL_SECONDS", raising=False)
    monkeypatch.setenv("SIDEBAR_CONTEXT_TOKEN_TTL_SECONDS", "7200")
    assert signed_context.sidebar_product_context_ttl_seconds() == 7200


def test_append_ctx_fragment_keeps_credential_out_of_http_request_target() -> None:
    token = "token.with/special+chars"

    assert signed_context.append_ctx_fragment("/pay/a", token) == "/pay/a#aicrm_ctx=token.with%2Fspecial%2Bchars"
    assert signed_context.append_ctx_fragment("/pay/a?x=1", token) == "/pay/a?x=1#aicrm_ctx=token.with%2Fspecial%2Bchars"
    assert signed_context.append_ctx_fragment("/pay/a", "") == "/pay/a"


def test_signed_context_has_no_legacy_imports() -> None:
    source = Path("aicrm_next/platform/shared/signed_context.py").read_text(encoding="utf-8")
    forbidden = ["wecom_" + "ability_service", "current_" + "app", "fl" + "ask"]

    for marker in forbidden:
        assert marker not in source
