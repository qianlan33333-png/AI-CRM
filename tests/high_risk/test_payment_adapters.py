from __future__ import annotations

import pytest

from aicrm_next.channels.integration_gateway.audit import reset_audit_events
from aicrm_next.channels.integration_gateway.idempotency import reset_idempotency_store
from aicrm_next.channels.integration_gateway.payment_adapters import ProductWriteGateway, WeChatPayAdapter


pytestmark = pytest.mark.high_risk


def setup_function() -> None:
    reset_audit_events()
    reset_idempotency_store()


def test_fake_payment_is_idempotent_and_never_calls_a_provider() -> None:
    adapter = WeChatPayAdapter("fake")
    first = adapter.create_jsapi_order(
        order_id="order-current-1",
        product_id="product-current",
        openid="openid-fixture",
        amount=990,
        idempotency_key="checkout-current-1",
    )
    second = adapter.create_jsapi_order(
        order_id="order-current-1",
        product_id="product-current",
        openid="openid-fixture",
        amount=990,
        idempotency_key="checkout-current-1",
    )
    assert first["result"] == second["result"]
    assert first["result"]["provider_called"] is False
    assert first["side_effect_executed"] is False


def test_payment_and_product_guards_fail_closed_and_scrub_secrets() -> None:
    blocked = WeChatPayAdapter("production").query_order(order_id="order-current-2")
    preview = ProductWriteGateway("fake").create_product(
        product_code="service-current",
        amount=19900,
        payload_summary={"api_key": "must-not-survive", "display_name": "Current product"},
    )
    assert blocked["ok"] is False
    assert blocked["error_code"] == "production_guard_failed"
    assert blocked["side_effect_executed"] is False
    assert preview["target"]["payload_summary"]["payload_keys"] == ["display_name"]
