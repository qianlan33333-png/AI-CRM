from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.ai_audience_ops import register_ai_audience_event_consumers
from aicrm_next.internal_event_composition import register_payment_succeeded_consumers
from aicrm_next.platform_foundation.external_effects import WEBHOOK_ORDER_PAID_PUSH, ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.payment import PAYMENT_SUCCEEDED_EVENT_TYPE
from aicrm_next.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.public_product import h5_wechat_pay
from aicrm_next.public_product.h5_wechat_pay import _apply_transaction
from tests.admin_auth_test_helpers import install_admin_action_tokens


PAYMENT_CONSUMERS = frozenset({
    "order_projection_consumer",
    "service_period_entitlement_consumer",
    "webhook_order_paid_consumer",
    "ai_audience_source_poke_consumer",
    "customer_read_model_dirty_consumer",
    "customer_timeline_projection_consumer",
    "customer_business_summary_consumer",
    "dnd_policy_consumer",
    "ai_assist_notify_consumer",
    "product_paid_wecom_tag_consumer",
})


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _PaymentConn:
    def __init__(self, *, out_trade_no: str = "WXP_SINGLE_CONSUMER"):
        self.order = {
            "id": 881,
            "out_trade_no": out_trade_no,
            "product_code": "subscription_trial_month",
            "product_name": "Single Consumer Slice",
            "amount_total": 990,
            "payer_total": 990,
            "status": "paying",
            "trade_state": "NOTPAY",
            "external_userid": "wm_single_consumer",
            "userid_snapshot": "user_single",
            "respondent_key": "respondent_single",
            "mobile_snapshot": "13800005678",
            "paid_at": "",
        }
        self.delivery: dict | None = None

    def execute(self, query, params):
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
            return _FakeCursor({"id": 3, "product_code": "subscription_trial_month", "name": "Single Consumer Slice", "amount_total": 990})
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


def _transaction(out_trade_no: str = "WXP_SINGLE_CONSUMER") -> dict:
    return {
        "out_trade_no": out_trade_no,
        "trade_state": "SUCCESS",
        "transaction_id": f"wx_tx_{out_trade_no}",
        "bank_type": "OTHERS",
        "success_time": "2026-06-14T12:42:36+08:00",
        "amount": {"payer_total": 990},
        "payer": {"openid": "openid_single"},
    }


def _enable_shadow_payment_events(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "payment.succeeded")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_DISABLE_LEGACY_AUTOMATION_DIRECT", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    monkeypatch.setattr("aicrm_next.commerce.external_push_admin.resolve_and_validate_public_https_url", lambda url: url)
    monkeypatch.setattr(
        h5_wechat_pay,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: build_internal_event_repository().enqueue_outbox(request).to_dict(),
    )


def _reset_state() -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _emit_payment(monkeypatch, *, out_trade_no: str = "WXP_SINGLE_CONSUMER"):
    _reset_state()
    _enable_shadow_payment_events(monkeypatch)

    def fake_external_push_plan(*, order, transaction, domain_event_outbox_id):
        del transaction, domain_event_outbox_id
        job = ExternalEffectService().plan_effect(
            effect_type=WEBHOOK_ORDER_PAID_PUSH,
            adapter_name="webhook",
            operation="order_paid_push",
            target_type="external_push_delivery",
            target_id=f"deliv_{order['id']}",
            business_type="commerce_order",
            business_id=str(order["id"]),
            payload={"webhook_url": "https://example.com/order-paid"},
            idempotency_key=f"commerce-external-push:deliv_{order['id']}",
        )
        return {"ok": True, "external_effect_job_id": job["id"]}

    monkeypatch.setattr(
        "aicrm_next.internal_event_composition._plan_order_paid_external_push_effect_from_db",
        fake_external_push_plan,
    )
    _apply_transaction(_PaymentConn(out_trade_no=out_trade_no), _transaction(out_trade_no))
    register_payment_succeeded_consumers()
    register_ai_audience_event_consumers()
    assert InternalEventOutboxRelay().relay_due(limit=10)["ok"] is True
    event = InternalEventService().list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]
    runs, total = InternalEventService().list_consumer_runs({"event_id": event.event_id})
    assert total == len(PAYMENT_CONSUMERS)
    assert {run.consumer_name for run in runs} == PAYMENT_CONSUMERS
    return event


def _run(event_id: str, consumer_name: str, *, dry_run: bool = False, force: bool = False, reason: str = "production_gray_single_consumer"):
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        consumer_name,
        dry_run=dry_run,
        force=force,
        reason=reason,
    )


def _statuses(event_id: str) -> dict[str, str]:
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event_id})
    return {run.consumer_name: run.status for run in runs}


def test_single_consumer_run_requires_token(next_client: TestClient, monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    response = next_client.post(
        f"/api/admin/internal-events/{event.event_id}/consumers/order_projection_consumer/run",
        json={"dry_run": True},
    )

    assert response.status_code == 401
    assert response.json()["error"] in {"automation_internal_token_not_configured", "internal_token_required", "缺少 admin_action_token"}

    token = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run"),
    )[("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run")]
    authorized = next_client.post(
        f"/api/admin/internal-events/{event.event_id}/consumers/order_projection_consumer/run",
        json={"reason": "production_gray_single_consumer"},
        headers={"X-Admin-Action-Token": token},
    )
    attempts = InternalEventService().list_attempts(event_id=event.event_id)

    assert authorized.status_code == 200
    assert authorized.json()["dry_run"] is True
    assert authorized.json()["counts"]["candidate_count"] == 1
    assert authorized.json()["counts"]["processed_count"] == 0
    assert authorized.json()["real_external_call_executed"] is False
    assert attempts == []


def test_single_consumer_dry_run_does_not_change_state(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    result = _run(event.event_id, "order_projection_consumer", dry_run=True)
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id})
    attempts = InternalEventService().list_attempts(event_id=event.event_id)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["processed_count"] == 0
    assert result["real_external_call_executed"] is False
    assert {run.status for run in runs} == {"pending"}
    assert attempts == []


def test_single_consumer_execute_only_updates_specified_consumer(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    result = _run(event.event_id, "order_projection_consumer")
    statuses = _statuses(event.event_id)
    attempts = InternalEventService().list_attempts(event_id=event.event_id)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["counts"]["processed_count"] == 1
    assert result["counts"]["succeeded_count"] == 1
    assert statuses["order_projection_consumer"] == "succeeded"
    assert {name: status for name, status in statuses.items() if name != "order_projection_consumer"} == {
        name: "pending" for name in PAYMENT_CONSUMERS - {"order_projection_consumer"}
    }
    assert len(attempts) == 1
    assert attempts[0].consumer_name == "order_projection_consumer"


def test_webhook_single_consumer_creates_external_effect_without_external_attempt(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)
    jobs_before, total_before = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "881"})

    result = _run(event.event_id, "webhook_order_paid_consumer")
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "881"})
    attempts = ExternalEffectService().list_attempts(jobs[0].id)
    response_summary = result["attempt"]["response_summary_json"]

    assert jobs_before == []
    assert total_before == 0
    assert result["counts"]["succeeded_count"] == 1
    assert result["real_external_call_executed"] is False
    assert total == 1
    assert jobs[0].idempotency_key.startswith("commerce-external-push:")
    assert jobs[0].execution_mode == "execute"
    assert jobs[0].status == "queued"
    assert jobs[0].payload_json["webhook_url"] == "https://example.com/order-paid"
    assert jobs[0].attempt_count == 0
    assert attempts == []
    assert response_summary["external_effect_job_reused"] is False
    assert response_summary["external_effect_job_created"] is True
    assert event.event_id


def test_skipped_single_consumer_records_reason_without_touching_other_runs(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    result = _run(event.event_id, "dnd_policy_consumer")
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id})
    dnd = next(run for run in runs if run.consumer_name == "dnd_policy_consumer")

    assert result["counts"]["skipped_count"] == 1
    assert dnd.status == "skipped"
    assert dnd.result_summary_json["reason"] == "dnd_policy_not_configured"
    assert {run.status for run in runs if run.consumer_name != "dnd_policy_consumer"} == {"pending"}


def test_retired_automation_single_consumer_is_not_dispatchable(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    result = _run(event.event_id, "automation_payment_consumer")
    statuses = _statuses(event.event_id)

    assert result["ok"] is False
    assert result["error"] == "consumer_run_not_found"
    assert "automation_payment_consumer" not in statuses
    assert set(statuses.values()) == {"pending"}


def test_repeated_webhook_single_consumer_does_not_duplicate_external_effect_job(monkeypatch) -> None:
    event = _emit_payment(monkeypatch)

    first = _run(event.event_id, "webhook_order_paid_consumer")
    second = _run(event.event_id, "webhook_order_paid_consumer", force=True)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "881"})

    assert first["counts"]["succeeded_count"] == 1
    assert second["counts"]["succeeded_count"] == 1
    assert first["attempt"]["response_summary_json"]["external_effect_job_created"] is True
    assert first["attempt"]["response_summary_json"]["external_effect_job_reused"] is False
    assert second["attempt"]["response_summary_json"]["external_effect_job_reused"] is True
    assert total == 1
    assert jobs[0].idempotency_key.startswith("commerce-external-push:")
    assert jobs[0].attempt_count == 0
