from __future__ import annotations

from aicrm_next.extensions.commerce.commerce.payment_tagging import (
    OWNER_USERID,
    PRODUCT_CODE,
    PRODUCT_CODES,
    TAG_ID,
    product_paid_wecom_tag_consumer,
    resolve_payment_tag_identity,
)
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    InMemoryExternalEffectRepository,
    WECOM_CONTACT_TAG_MARK,
)
from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun


def _event(*, product_code: str = PRODUCT_CODE) -> InternalEvent:
    return InternalEvent(
        event_id="iev_payment_tag",
        aggregate_id="42",
        actor_id="wechat_pay_notify",
        trace_id="WXP_PAYMENT_TAG",
        payload_json={
            "order": {
                "id": 42,
                "out_trade_no": "WXP_PAYMENT_TAG",
                "product_code": product_code,
                "status": "paid",
                "trade_state": "SUCCESS",
                "unionid": "union_payment_tag",
            }
        },
    )


def _run() -> InternalEventConsumerRun:
    return InternalEventConsumerRun(consumer_name="product_paid_wecom_tag_consumer")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _IdentityConn:
    def execute(self, _query, params):
        assert params[-1] == "wm_cached_other_owner"
        return _Rows(
            [
                {
                    "external_userid": "wm_cached_other_owner",
                    "follow_user_userid": "OtherOwner",
                    "status": "active",
                }
            ]
        )


def test_identity_resolution_uses_external_id_without_cached_owner_prevalidation() -> None:
    result = resolve_payment_tag_identity(
        _IdentityConn(),
        {"external_userid": "wm_cached_other_owner"},
        OWNER_USERID,
    )

    assert result == {
        "ok": True,
        "external_userid": "wm_cached_other_owner",
        "follow_user_userid": OWNER_USERID,
    }


def test_non_matching_product_is_skipped_without_identity_lookup() -> None:
    result = product_paid_wecom_tag_consumer(
        _event(product_code="other_product"),
        _run(),
        identity_resolver=lambda *_args: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    assert result.status == "skipped"
    assert result.response_summary["reason"] == "product_rule_not_matched"


def test_each_matching_paid_product_plans_one_idempotent_wecom_tag_job() -> None:
    assert PRODUCT_CODES == {
        "prd_20260707050545_291025",
        "prd_20260713083438_75670b",
    }

    for product_code in PRODUCT_CODES:
        _assert_matching_paid_order_plans_one_idempotent_wecom_tag_job(product_code)


def _assert_matching_paid_order_plans_one_idempotent_wecom_tag_job(product_code: str) -> None:
    repo = InMemoryExternalEffectRepository()
    effects = ExternalEffectService(repo)
    resolver = lambda _order, owner: {
        "ok": True,
        "external_userid": "wm_payment_tag",
        "follow_user_userid": owner,
    }

    first = product_paid_wecom_tag_consumer(
        _event(product_code=product_code),
        _run(),
        identity_resolver=resolver,
        external_effects=effects,
    )
    second = product_paid_wecom_tag_consumer(
        _event(product_code=product_code),
        _run(),
        identity_resolver=resolver,
        external_effects=effects,
    )
    jobs, total = effects.list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})

    assert first.status == "succeeded"
    assert first.response_summary["external_effect_job_created"] is True
    assert second.status == "succeeded"
    assert second.response_summary["external_effect_job_reused"] is True
    assert first.result_summary["product_code"] == product_code
    assert total == 1
    assert jobs[0].payload_json == {
        "external_userid": "wm_payment_tag",
        "follow_user_userid": OWNER_USERID,
        "tag_ids": [TAG_ID],
        "product_code": product_code,
        "out_trade_no": "WXP_PAYMENT_TAG",
    }


def test_missing_identity_retries_without_owner_relation_precheck() -> None:
    pending = product_paid_wecom_tag_consumer(
        _event(),
        _run(),
        identity_resolver=lambda *_args: {"ok": False, "reason": "wecom_identity_pending"},
    )

    assert pending.status == "failed_retryable"
    assert pending.error_code == "wecom_identity_pending"
