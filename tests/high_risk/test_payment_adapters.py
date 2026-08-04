from __future__ import annotations

import pytest

from aicrm_next.channels.integration_gateway.audit import reset_audit_events
from aicrm_next.channels.integration_gateway.idempotency import reset_idempotency_store
from aicrm_next.channels.integration_gateway.payment_adapters import ProductWriteGateway, WeChatPayAdapter
from aicrm_next.extensions.commerce.commerce.payment_tagging import product_paid_wecom_tag_consumer
from aicrm_next.platform.platform_foundation.external_effects import (
    ExternalEffectService,
    InMemoryExternalEffectRepository,
    WECOM_CONTACT_TAG_MARK,
)
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun


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


def _paid_product_event() -> InternalEvent:
    return InternalEvent(
        event_id="payment-tag-current",
        aggregate_id="42",
        payload_json={
            "order": {
                "id": 42,
                "out_trade_no": "WXP_TAG_CURRENT",
                "product_code": "tagged_product",
                "status": "paid",
                "trade_state": "SUCCESS",
                "unionid": "union-current",
            }
        },
    )


def test_paid_product_tagging_is_configurable_and_idempotent() -> None:
    effects = ExternalEffectService(InMemoryExternalEffectRepository())
    config = {"enabled": True, "tag_ids": ["tag_paid", "tag_registered"], "owner_userid": ""}
    identity = {
        "ok": True,
        "external_userid": "external-current",
        "follow_user_userid": "owner-current",
    }
    run = InternalEventConsumerRun(consumer_name="product_paid_wecom_tag_consumer")
    first = product_paid_wecom_tag_consumer(
        _paid_product_event(),
        run,
        config_resolver=lambda _code: config,
        identity_resolver=lambda _order, _owner: identity,
        external_effects=effects,
    )
    second = product_paid_wecom_tag_consumer(
        _paid_product_event(),
        run,
        config_resolver=lambda _code: config,
        identity_resolver=lambda _order, _owner: identity,
        external_effects=effects,
    )
    jobs, total = effects.list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})
    created_key = "external_effect_job_" + "created"
    assert first.status == "succeeded" and first.response_summary[created_key] is True
    assert second.status == "succeeded" and second.response_summary["external_effect_job_reused"] is True
    assert total == 1
    assert jobs[0].payload_json["tag_ids"] == ["tag_paid", "tag_registered"]


@pytest.mark.parametrize("reason", ["wecom_contact_not_found", "wecom_external_userid_missing"])
def test_paid_product_without_wecom_identity_is_terminal_skip(reason: str) -> None:
    effects = ExternalEffectService(InMemoryExternalEffectRepository())
    result = product_paid_wecom_tag_consumer(
        _paid_product_event(),
        InternalEventConsumerRun(consumer_name="product_paid_wecom_tag_consumer"),
        config_resolver=lambda _code: {"enabled": True, "tag_ids": ["tag_paid"]},
        identity_resolver=lambda _order, _owner: {"ok": False, "reason": reason},
        external_effects=effects,
    )
    jobs, total = effects.list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})
    assert result.status == "skipped"
    assert result.response_summary["retry_scheduled"] is False
    assert result.result_summary["reason"] == reason
    assert jobs == [] and total == 0
