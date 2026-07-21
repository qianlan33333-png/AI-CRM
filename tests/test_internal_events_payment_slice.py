from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from aicrm_next.internal_event_composition import register_payment_succeeded_consumers
from aicrm_next.platform_foundation.external_effects import WEBHOOK_ORDER_PAID_PUSH, ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.payment import PAYMENT_SUCCEEDED_EVENT_TYPE
from aicrm_next.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.public_product import h5_wechat_pay
from aicrm_next.public_product.h5_wechat_pay import _apply_transaction


PAYMENT_CONSUMERS = frozenset({
    "ai_assist_notify_consumer",
    "ai_audience_source_poke_consumer",
    "customer_business_summary_consumer",
    "customer_read_model_dirty_consumer",
    "customer_timeline_projection_consumer",
    "dnd_policy_consumer",
    "order_projection_consumer",
    "product_paid_wecom_tag_consumer",
    "service_period_entitlement_consumer",
    "webhook_order_paid_consumer",
})


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _PaymentConn:
    def __init__(self):
        self.order = {
            "id": 77,
            "out_trade_no": "WXP_INTERNAL_PAYMENT",
            "product_code": "subscription_trial_month",
            "product_name": "Internal Payment Slice",
            "amount_total": 990,
            "payer_total": 990,
            "status": "paying",
            "trade_state": "NOTPAY",
            "external_userid": "wm_internal_payment",
            "userid_snapshot": "user_internal",
            "respondent_key": "respondent_internal",
            "mobile_snapshot": "13800001234",
            "paid_at": "",
        }
        self.queries: list[str] = []
        self.delivery: dict | None = None

    def execute(self, query, params):
        self.queries.append(query)
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT * FROM wechat_pay_orders"):
            return _FakeCursor(dict(self.order))
        if normalized.startswith("UPDATE wechat_pay_orders"):
            self.order.update(
                {
                    "status": params[0],
                    "trade_state": params[1],
                    "transaction_id": params[2],
                    "bank_type": params[3],
                    "payer_total": params[4],
                    "paid_at": params[6],
                    "notify_payload_json": params[7],
                }
            )
            return _FakeCursor(dict(self.order))
        if "FROM wechat_pay_products" in normalized:
            return _FakeCursor({"id": 3, "product_code": "subscription_trial_month", "name": "Internal Payment Slice", "amount_total": 990})
        if "FROM external_push_config" in normalized:
            return _FakeCursor(
                {
                    "id": 5,
                    "tenant_id": "aicrm",
                    "target_type": "product",
                    "target_id": "3",
                    "event_type": "transaction.paid",
                    "enabled": True,
                    "webhook_url": "https://example.com/order-paid",
                    "secret": "order-secret",
                    "push_type": "paid_notify",
                    "day": 7,
                    "frequency": 1,
                    "remark": "测试外推",
                    "custom_params": {},
                }
            )
        if "INSERT INTO external_push_delivery" in normalized:
            self.delivery = {
                "id": 8,
                "tenant_id": params[0],
                "config_id": params[1],
                "event_type": params[2],
                "delivery_id": params[3],
                "target_type": "product",
                "target_id": params[4],
                "order_id": params[5],
                "product_id": params[6],
                "status": "pending",
                "attempt_count": 0,
                "request_url": params[7],
            }
            return _FakeCursor(dict(self.delivery))
        if "UPDATE external_push_delivery" in normalized:
            assert self.delivery is not None
            self.delivery.update(
                {
                    "status": params[0],
                    "attempt_count": params[1],
                    "request_url": params[2],
                    "request_headers": params[3],
                    "request_body": params[4],
                    "response_status": params[5],
                    "response_body": params[6],
                    "error_message": params[7],
                    "next_retry_at": params[8],
                    "delivery_id": params[9],
                }
            )
            return _FakeCursor(dict(self.delivery))
        raise AssertionError(query)


def _transaction(out_trade_no: str = "WXP_INTERNAL_PAYMENT") -> dict:
    return {
        "out_trade_no": out_trade_no,
        "trade_state": "SUCCESS",
        "transaction_id": f"wx_tx_{out_trade_no}",
        "bank_type": "OTHERS",
        "success_time": "2026-06-13T10:00:00+08:00",
        "amount": {"payer_total": 990},
        "payer": {"openid": "openid_internal"},
    }


def _enable_payment_events(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_DISABLE_LEGACY_AUTOMATION_DIRECT", "1")
    monkeypatch.setattr("aicrm_next.commerce.external_push_admin.resolve_and_validate_public_https_url", lambda url: url)
    monkeypatch.setattr(
        h5_wechat_pay,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: build_internal_event_repository().enqueue_outbox(request).to_dict(),
    )


def _apply_and_relay(conn, transaction):
    order = _apply_transaction(conn, transaction)
    register_payment_succeeded_consumers()
    relayed = InternalEventOutboxRelay().relay_due(limit=10)
    assert relayed["ok"] is True
    return order


def _reset_state() -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _patch_canonical_external_push_planner(monkeypatch, *, configured: bool = True) -> None:
    def fake_plan(*, order, transaction, domain_event_outbox_id):
        del transaction, domain_event_outbox_id
        if not configured:
            return {"ok": True, "skipped": True, "reason": "external_push_config_unavailable"}
        job = ExternalEffectService().plan_effect(
            effect_type=WEBHOOK_ORDER_PAID_PUSH,
            adapter_name="webhook",
            operation="order_paid_push",
            target_type="external_push_delivery",
            target_id="deliv_internal_payment",
            business_type="commerce_order",
            business_id=str(order["id"]),
            payload={"webhook_url": "https://example.com/order-paid"},
            idempotency_key="commerce-external-push:deliv_internal_payment",
        )
        return {"ok": True, "external_effect_job_id": job["id"]}

    monkeypatch.setattr(
        "aicrm_next.internal_event_composition._plan_order_paid_external_push_effect_from_db",
        fake_plan,
    )


def _delete_order_paid_external_effect_jobs(business_id: str) -> None:
    del business_id
    reset_external_effect_fixture_state()


def test_payment_success_emits_payment_succeeded_and_duplicate_notify_is_idempotent(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)

    conn = _PaymentConn()
    first = _apply_and_relay(conn, _transaction())
    second = _apply_and_relay(conn, _transaction())
    events, event_total = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert first["status"] == "paid"
    assert second["status"] == "paid"
    assert event_total == 1
    assert events[0].event_type == "payment.succeeded"
    assert events[0].aggregate_type == "wechat_pay_order"
    assert events[0].aggregate_id == "77"
    assert events[0].subject_type == "customer"
    assert events[0].subject_id == "wm_internal_payment"
    assert events[0].idempotency_key == "payment.succeeded:WXP_INTERNAL_PAYMENT"
    assert events[0].source_module == "public_product.h5_wechat_pay"
    assert events[0].source_route == "/api/h5/wechat-pay/notify"
    assert events[0].trace_id == "WXP_INTERNAL_PAYMENT"
    assert events[0].payload_summary_json["mobile_masked"] == "138****1234"
    assert "13800001234" not in str(events[0].payload_summary_json)
    consumer_names = {run.consumer_name for run in runs}
    assert run_total == len(PAYMENT_CONSUMERS)
    assert consumer_names == PAYMENT_CONSUMERS


def test_webhook_order_paid_consumer_creates_external_effect_job_without_external_call(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)
    _patch_canonical_external_push_planner(monkeypatch)
    conn = _PaymentConn()
    _apply_and_relay(conn, _transaction())
    event = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]
    jobs_before, total_before = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "77"})

    result = InternalEventWorker().run_due(batch_size=1, dry_run=False, consumer_names=["webhook_order_paid_consumer"])
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "77"})
    attempts = ExternalEffectService().list_attempts(jobs[0].id)
    response_summary = result["items"][0]["attempt"]["response_summary_json"]

    assert jobs_before == []
    assert total_before == 0
    assert result["counts"]["succeeded_count"] == 1
    assert result["real_external_call_executed"] is False
    assert total == 1
    assert jobs[0].idempotency_key.startswith("commerce-external-push:")
    assert jobs[0].execution_mode == "execute"
    assert jobs[0].status == "queued"
    assert jobs[0].payload_json["webhook_url"] == "https://example.com/order-paid"
    assert attempts == []
    assert response_summary["external_effect_job_reused"] is False
    assert response_summary["external_effect_job_created"] is True
    assert response_summary["external_effect_job_id"] == jobs[0].id
    assert event.event_id


def test_retired_automation_payment_consumer_is_not_registered(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)
    _apply_and_relay(_PaymentConn(), _transaction())
    event = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]

    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id, "consumer_name": "automation_payment_consumer"})
    result = InternalEventWorker().dispatch_one_consumer(event.event_id, "automation_payment_consumer", dry_run=False)
    attempts = InternalEventService().list_attempts(event_id=event.event_id)

    assert runs == []
    assert result["ok"] is False
    assert result["error"] == "consumer_run_not_found"
    assert [attempt for attempt in attempts if attempt.consumer_name == "automation_payment_consumer"] == []


def test_webhook_order_paid_consumer_skips_when_no_configured_job_or_production_config(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)
    _patch_canonical_external_push_planner(monkeypatch, configured=False)
    _apply_and_relay(_PaymentConn(), _transaction())
    reset_external_effect_fixture_state()
    _delete_order_paid_external_effect_jobs("WXP_INTERNAL_PAYMENT")

    result = InternalEventWorker().run_due(batch_size=1, dry_run=False, consumer_names=["webhook_order_paid_consumer"])
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "77"})
    response_summary = result["items"][0]["attempt"]["response_summary_json"]

    assert result["counts"]["succeeded_count"] == 1
    assert total == 0
    assert jobs == []
    assert response_summary["external_effect_job_reused"] is False
    assert response_summary["external_effect_job_created"] is False
    assert response_summary["skipped"] is True
    assert response_summary["reason"] == "external_push_config_unavailable"


def test_dnd_consumer_is_skipped_with_visible_reason(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)
    _apply_and_relay(_PaymentConn(), _transaction())
    event = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]

    result = InternalEventWorker().run_due(batch_size=1, dry_run=False, consumer_names=["dnd_policy_consumer"])
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id, "consumer_name": "dnd_policy_consumer"})
    attempts = [attempt for attempt in InternalEventService().list_attempts(event_id=event.event_id) if attempt.consumer_name == "dnd_policy_consumer"]

    assert result["counts"]["skipped_count"] == 1
    assert runs[0].status == "skipped"
    assert runs[0].result_summary_json["reason"] == "dnd_policy_not_configured"
    assert attempts[0].status == "skipped"
    assert attempts[0].response_summary_json["reason"] == "dnd_policy_not_configured"


def test_retired_automation_consumer_absence_does_not_affect_payment_apply_result(monkeypatch) -> None:
    _reset_state()
    _enable_payment_events(monkeypatch)

    order = _apply_and_relay(_PaymentConn(), _transaction())
    event = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]
    result = InternalEventWorker().dispatch_one_consumer(event.event_id, "automation_payment_consumer", dry_run=False)

    assert order["status"] == "paid"
    assert result["ok"] is False
    assert result["error"] == "consumer_run_not_found"
