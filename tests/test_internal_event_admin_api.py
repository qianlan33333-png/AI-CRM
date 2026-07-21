from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.execution_runtime.commands import QueueRuntimeCommandService
from aicrm_next.platform_foundation.internal_events import (
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventService,
    reset_internal_event_fixture_state,
)
from aicrm_next.platform_foundation.internal_events.repository import build_internal_event_repository
from tests.admin_auth_test_helpers import install_admin_action_tokens


def _context(trace_id: str = "WXP_EVENT_CENTER") -> CommandContext:
    return CommandContext(
        actor_id="pytest",
        actor_type="system",
        request_id="req-event-center",
        trace_id=trace_id,
        source_route="/api/h5/wechat-pay/notify",
    )


def _seed_payment_event() -> str:
    reset_internal_event_fixture_state()
    registry = InternalEventConsumerRegistry()
    for name in [
        "order_projection_consumer",
        "webhook_order_paid_consumer",
        "customer_business_summary_consumer",
        "dnd_policy_consumer",
        "ai_assist_notify_consumer",
    ]:
        registry.register("payment.succeeded", name, lambda event, run: InternalEventConsumerResult(status="succeeded"))
    service = InternalEventService(build_internal_event_repository(), registry)
    emitted = service.emit_event(
        event_type="payment.succeeded",
        aggregate_type="wechat_pay_order",
        aggregate_id="77",
        subject_type="customer",
        subject_id="wm_event_center",
        idempotency_key="payment.succeeded:WXP_EVENT_CENTER",
        source_module="public_product.h5_wechat_pay",
        context=_context(),
        payload={"raw": "must_not_be_returned"},
        payload_summary={
            "out_trade_no": "WXP_EVENT_CENTER",
            "phone": "13800001234",
            "openid": "openid-secret",
            "unionid": "unionid-secret",
            "token": "token-secret",
            "safe": "visible",
        },
    )
    event_id = emitted["event"]["event_id"]
    repo = build_internal_event_repository()
    runs, _ = service.list_consumer_runs({"event_id": event_id}, limit=20)
    by_name = {run.consumer_name: run for run in runs}

    success = repo.record_attempt(
        run=by_name["order_projection_consumer"],
        status="succeeded",
        request_summary={"phone": "13800001234"},
        response_summary={"projected": True},
    )
    repo.mark_result(by_name["order_projection_consumer"].id, status="succeeded", attempt_id=success.attempt_id, result_summary={"label": "ok"})
    failed = repo.record_attempt(
        run=by_name["webhook_order_paid_consumer"],
        status="failed_retryable",
        request_summary={"Authorization": "Bearer hidden"},
        response_summary={"access_token": "hidden", "retry": True},
        error_code="timeout",
        error_message="webhook timeout",
    )
    repo.mark_result(
        by_name["webhook_order_paid_consumer"].id,
        status="failed_retryable",
        attempt_id=failed.attempt_id,
        result_summary={"retry": True},
        error_code="timeout",
        error_message="webhook timeout",
    )
    skipped = repo.record_attempt(
        run=by_name["dnd_policy_consumer"],
        status="skipped",
        request_summary={},
        response_summary={"reason": "dnd_policy_not_configured"},
    )
    repo.mark_result(
        by_name["dnd_policy_consumer"].id,
        status="skipped",
        attempt_id=skipped.attempt_id,
        result_summary={"reason": "dnd_policy_not_configured"},
    )
    return event_id


def test_internal_event_admin_api_lists_filters_and_redacts_payload(next_client: TestClient) -> None:
    event_id = _seed_payment_event()

    response = next_client.get(
        "/api/admin/internal-events",
        params={
            "event_type": "payment.succeeded",
            "aggregate_type": "wechat_pay_order",
            "aggregate_id": "77",
            "subject_type": "customer",
            "subject_id": "wm_event_center",
            "consumer_name": "webhook_order_paid_consumer",
            "consumer_status": "failed_retryable",
            "trace_id": "WXP_EVENT_CENTER",
            "source_module": "public_product.h5_wechat_pay",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["route_owner"] == "ai_crm_next"
    assert body["total"] == 1
    assert body["items"][0]["event_id"] == event_id
    assert body["items"][0]["consumer_total"] == 5
    assert body["items"][0]["succeeded_count"] == 1
    assert body["items"][0]["failed_count"] == 1
    assert body["items"][0]["skipped_count"] == 1
    assert body["counts"]["total"] == 1
    assert body["counts"]["failed_retryable"] == 1
    assert body["counts"]["failed_terminal"] == 0
    assert body["items"][0]["payload_summary_json"]["phone"] == "[redacted]"
    assert body["items"][0]["payload_summary_json"]["openid"] == "[redacted]"
    assert body["items"][0]["payload_summary_json"]["unionid"] == "[redacted]"
    assert body["items"][0]["payload_summary_json"]["token"] == "[redacted]"
    assert body["items"][0]["payload_summary_json"]["safe"] == "visible"
    assert "payload_json" not in body["items"][0]
    assert "13800001234" not in str(body)
    assert "openid-secret" not in str(body)
    assert "unionid-secret" not in str(body)
    assert "token-secret" not in str(body)


def test_internal_event_admin_detail_attempts_retry_skip_and_diagnostics(
    next_client: TestClient,
    next_pg_schema,
    monkeypatch,
) -> None:
    event_id = _seed_payment_event()
    del next_pg_schema, monkeypatch
    tokens = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry"),
        ("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip"),
    )

    detail = next_client.get(f"/api/admin/internal-events/{event_id}")
    diagnostics = next_client.get("/api/admin/internal-events/diagnostics")
    command_service = QueueRuntimeCommandService()
    retry_target = command_service.read_internal_consumer_target(
        event_id,
        "webhook_order_paid_consumer",
    )
    skip_target = command_service.read_internal_consumer_target(
        event_id,
        "ai_assist_notify_consumer",
    )
    assert retry_target is not None
    assert skip_target is not None
    unauthorized = next_client.post(f"/api/admin/internal-events/{event_id}/consumers/webhook_order_paid_consumer/retry", json={})
    retried = next_client.post(
        f"/api/admin/internal-events/{event_id}/consumers/webhook_order_paid_consumer/retry",
        headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry")]},
        json={
            "actor": "pytest-operator",
            "reason": "approved retry after timeout review",
            "expected_version": retry_target.version_token,
        },
    )
    skipped = next_client.post(
        f"/api/admin/internal-events/{event_id}/consumers/ai_assist_notify_consumer/skip",
        json={
            "admin_action_token": tokens[("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip")],
            "actor": "pytest-operator",
            "reason": "manual_noop",
            "expected_version": skip_target.version_token,
        },
    )

    body = detail.json()
    assert detail.status_code == 200
    assert body["event"]["event_id"] == event_id
    assert "payload_json" not in body["event"]
    assert body["payload_summary_json"]["phone"] == "[redacted]"
    webhook = [run for run in body["consumer_runs"] if run["consumer_name"] == "webhook_order_paid_consumer"][0]
    assert webhook["last_error_code"] == "timeout"
    assert webhook["retryable"] is True
    assert body["attempts"][1]["request_summary_json"]["Authorization"] == "[redacted]"
    assert body["attempts"][1]["response_summary_json"]["access_token"] == "[redacted]"
    assert diagnostics.status_code == 200
    assert "due_count" in diagnostics.json()
    assert unauthorized.status_code == 401
    assert retried.status_code == 202
    assert retried.json()["action"] == "retry"
    assert retried.json()["status"] == "pending"
    assert skipped.status_code == 202
    assert skipped.json()["action"] == "skip"
    assert skipped.json()["status"] == "skipped"
