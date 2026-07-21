from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.internal_events import (
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventService,
    reset_internal_event_fixture_state,
)
from aicrm_next.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.platform_foundation.internal_events import api as internal_events_api
from aicrm_next.platform_foundation.internal_events.view_model import build_events_payload


def _seed_page_event() -> None:
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
    InternalEventService(build_internal_event_repository(), registry).emit_event(
        event_type="payment.succeeded",
        aggregate_type="wechat_pay_order",
        aggregate_id="77",
        subject_type="customer",
        subject_id="wm_page_event",
        idempotency_key="payment.succeeded:WXP_PAGE_EVENT",
        source_module="public_product.h5_wechat_pay",
        context=CommandContext(trace_id="WXP_PAGE_EVENT", source_route="/api/h5/wechat-pay/notify"),
        payload_summary={"out_trade_no": "WXP_PAGE_EVENT", "phone": "13800001234", "safe": "visible"},
    )


def test_internal_event_admin_page_smoke_and_payment_consumer_copy(next_client: TestClient) -> None:
    _seed_page_event()

    response = next_client.get("/admin/internal-events")

    assert response.status_code == 200
    assert "事件中心" in response.text
    assert 'data-route-owner="ai_crm_next"' in response.text
    assert 'id="statsGrid"' in response.text
    assert 'id="filterForm"' in response.text
    assert 'id="sectionTabs"' in response.text
    assert 'id="internalEventsTable"' in response.text
    assert 'data-execution-page="internal-list"' in response.text
    assert 'id="detailModal"' not in response.text
    for text in [
        "失败可重试",
        "失败不可重试",
        "计数来自消费者执行记录",
        "不代表消息、Webhook、标签等下游业务已经交付",
        "admin_execution_ui.css",
        "admin_execution_ui.js",
        'href="#refresh"',
        'href="#export"',
    ]:
        assert text in response.text
    assert "payload_json" not in response.text
    assert "支付自动化" not in response.text
    assert "13800001234" not in response.text
    assert "openid" not in response.text.lower()
    assert "unionid" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "access_token" not in response.text
    assert "聚合 ID" not in response.text
    assert "主体 ID" not in response.text
    assert "internal-events-detail-card" not in response.text
    assert "/api/admin/internal-events" not in response.text

    source = Path("aicrm_next/frontend_compat/static/admin_console/admin_execution_ui.js").read_text(encoding="utf-8")
    assert source.count("/api/admin/internal-events?") == 1
    assert 'addEventListener("input"' not in source
    assert "setInterval" not in source

    payment_payload = next_client.get("/api/admin/internal-events", params={"event_section": "payment"}).json()
    questionnaire_payload = next_client.get("/api/admin/internal-events", params={"event_section": "questionnaire"}).json()
    assert payment_payload["total"] == 1
    assert payment_payload["items"][0]["event_type"] == "payment.succeeded"
    assert questionnaire_payload["total"] == 0


def test_internal_event_navigation_entry_is_in_admin_shell(next_client: TestClient) -> None:
    response = next_client.get("/admin/internal-events")

    assert response.status_code == 200
    assert 'href="/admin/internal-events"' in response.text
    assert "事件中心" in response.text


def test_internal_event_page_is_shell_only_and_legacy_query_redirects(monkeypatch, next_client: TestClient) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("internal events page shell must not query the read model")

    monkeypatch.setattr(internal_events_api, "build_events_payload", fail_if_called)
    monkeypatch.setattr(internal_events_api, "build_internal_event_repository", fail_if_called)

    page = next_client.get("/admin/internal-events")
    assert page.status_code == 200
    assert 'data-execution-page="internal-list"' in page.text

    redirect = next_client.get(
        "/admin/internal-events?event_id=iev_legacy_link",
        follow_redirects=False,
    )
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/admin/internal-events/iev_legacy_link"

    detail = next_client.get("/admin/internal-events/iev_legacy_link")
    assert detail.status_code == 200
    assert 'data-execution-page="internal-detail"' in detail.text
    assert 'data-detail-id="iev_legacy_link"' in detail.text


def test_internal_event_list_batches_consumer_run_reads() -> None:
    _seed_page_event()
    delegate = build_internal_event_repository()

    class CountingRepository:
        def __init__(self):
            self.batch_calls = 0

        def __getattr__(self, name):
            return getattr(delegate, name)

        def list_consumer_runs(self, *_args, **_kwargs):
            raise AssertionError("event list must not perform one consumer query per event")

        def list_consumer_runs_for_events(self, event_ids):
            self.batch_calls += 1
            return delegate.list_consumer_runs_for_events(event_ids)

    repository = CountingRepository()
    payload = build_events_payload({}, repository=repository)

    assert payload["ok"] is True
    assert payload["total"] == 1
    assert repository.batch_calls == 1
    assert payload["count_semantics"].startswith("计数来自消费者执行记录")
    assert payload["filter_options"]["event_sections"]
