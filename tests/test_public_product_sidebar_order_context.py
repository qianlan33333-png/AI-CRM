from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.extensions.commerce.public_product.sidebar_order_context import resolve_sidebar_order_context
from aicrm_next.platform.shared.signed_context import build_sidebar_product_context_token


def _token(monkeypatch) -> str:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "sidebar-order-context-secret")
    return build_sidebar_product_context_token(
        external_userid="wm_ext_order_001",
        owner_userid="sales_01",
        bind_by_userid="advisor_01",
    )


def test_sidebar_order_context_valid_token_payload_mobile(monkeypatch) -> None:
    result = resolve_sidebar_order_context(
        context_token=_token(monkeypatch),
        payment_identity={"openid": "op_001", "unionid": "un_001", "payer_name": "Payer"},
        product={"require_mobile": True},
        payload_mobile="185 6588 3798",
        existing_binding={"mobile": "13900000000"},
    )

    assert result["external_userid"] == "wm_ext_order_001"
    assert result["owner_userid"] == "sales_01"
    assert result["bind_by_userid"] == "advisor_01"
    assert result["openid"] == "op_001"
    assert result["unionid"] == "un_001"
    assert result["payer_name"] == "Payer"
    assert result["mobile"] == "18565883798"
    assert result["mobile_source"] == "payload"
    assert result["context_source"] == "signed_sidebar_product_link"
    assert result["context_status"] == "valid"
    assert result["require_mobile_effective"] is False


def test_sidebar_order_context_uses_existing_binding_mobile(monkeypatch) -> None:
    result = resolve_sidebar_order_context(
        context_token=_token(monkeypatch),
        payment_identity={},
        product={"require_mobile": True},
        payload_mobile="",
        existing_binding={"mobile": "+86 139-0000-0000"},
    )

    assert result["mobile"] == "13900000000"
    assert result["mobile_source"] == "existing_binding"
    assert result["require_mobile_effective"] is False


@pytest.mark.parametrize("mobile", ["1856588379", "185658837988", "12565883798"])
def test_sidebar_order_context_rejects_invalid_required_mobile(monkeypatch, mobile: str) -> None:
    result = resolve_sidebar_order_context(
        context_token=_token(monkeypatch),
        payment_identity={},
        product={"require_mobile": True},
        payload_mobile=mobile,
        existing_binding={},
    )

    assert result["mobile"] == ""
    assert result["mobile_source"] == "none"
    assert result["require_mobile_effective"] is True


def test_sidebar_order_context_missing_or_invalid_ctx(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "sidebar-order-context-secret")

    missing = resolve_sidebar_order_context(context_token="")
    invalid = resolve_sidebar_order_context(context_token="bad-token")

    assert missing["external_userid"] == ""
    assert missing["context_status"] == "missing"
    assert missing["context_source"] == ""
    assert invalid["external_userid"] == ""
    assert invalid["context_status"] == "invalid"
    assert invalid["context_source"] == ""


def test_sidebar_order_context_requires_mobile_effective(monkeypatch) -> None:
    result = resolve_sidebar_order_context(
        context_token=_token(monkeypatch),
        payment_identity={},
        product={"require_mobile": True},
        payload_mobile="",
        existing_binding={},
    )

    assert result["mobile"] == ""
    assert result["mobile_source"] == "none"
    assert result["require_mobile_effective"] is True


def test_sidebar_order_context_has_no_legacy_imports() -> None:
    source = Path("aicrm_next/extensions/commerce/public_product/sidebar_order_context.py").read_text(encoding="utf-8")
    forbidden = ["wecom_" + "ability_service", "current_" + "app", "fl" + "ask", "legacy_" + "flask_facade"]

    for marker in forbidden:
        assert marker not in source
