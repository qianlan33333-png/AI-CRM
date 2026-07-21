from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform_foundation.webhook_inbox import InMemoryWebhookInboxRepository
from tests.admin_auth_test_helpers import install_admin_action_tokens


def _seed(repo: InMemoryWebhookInboxRepository, key: str = "event-1") -> dict:
    return repo.ingest(
        provider="wecom",
        event_family="external_contact",
        route="/wecom/external-contact/callback",
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-1",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_event_id=key,
        idempotency_key=key,
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"secret raw body",
        payload_xml="<xml/>",
        payload_json={"ExternalUserID": "wm-a"},
        payload_summary_json={"external_userid": "wm-a"},
        max_attempts=2,
    )


def test_webhook_inbox_admin_metrics_and_items(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    _seed(repo)
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)

    metrics = client.get("/api/admin/webhook-inbox/metrics?provider=wecom")
    items = client.get("/api/admin/webhook-inbox/items?provider=wecom")

    assert metrics.status_code == 200
    assert metrics.json()["queue_metrics"]["due_count"] == 1
    assert metrics.json()["queue_metrics"]["provider_distribution"] == [{"provider": "wecom", "count": 1}]
    assert metrics.json()["queue_metrics"]["route_distribution"] == [{"route": "/wecom/external-contact/callback", "count": 1}]
    assert items.status_code == 200
    body = items.json()
    assert body["items"][0]["idempotency_key"] == "event-1"
    assert body["items"][0]["payload_summary_json"] == {"external_userid": "wm-a"}
    assert "raw_body" not in body["items"][0]


def test_webhook_inbox_admin_filters_incident_window_pending_failed_rows(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    _seed(repo, "pending")
    failed = _seed(repo, "failed")
    succeeded = _seed(repo, "succeeded")
    outside = _seed(repo, "outside")
    incident_at = datetime(2026, 6, 27, 3, 10, tzinfo=timezone.utc)
    repo.rows[0]["received_at"] = incident_at
    repo.rows[1]["received_at"] = incident_at + timedelta(minutes=2)
    repo.rows[2]["received_at"] = incident_at + timedelta(minutes=3)
    repo.rows[3]["received_at"] = datetime(2026, 6, 27, 4, 10, tzinfo=timezone.utc)
    repo.mark_failed_retryable(failed["id"], error_code="RuntimeError", error_message="retry")
    repo.mark_succeeded(succeeded["id"])
    repo.mark_failed_retryable(outside["id"], error_code="RuntimeError", error_message="outside")
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)

    query = "provider=wecom&status=pending_failed&received_from=2026-06-27T11:00&received_to=2026-06-27T11:20"
    metrics = client.get(f"/api/admin/webhook-inbox/metrics?{query}")
    items = client.get(f"/api/admin/webhook-inbox/items?{query}")
    reconciliation = client.get(f"/api/admin/wecom/callback/reconciliation?{query}")

    assert metrics.status_code == 200
    assert metrics.json()["queue_metrics"]["due_count"] == 2
    assert {item["idempotency_key"] for item in items.json()["items"]} == {"pending", "failed"}
    assert {item["idempotency_key"] for item in reconciliation.json()["recent_items"]} == {"pending", "failed"}


def test_webhook_inbox_admin_detail_returns_processing_chain(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    row = _seed(repo)
    repo.mark_succeeded(
        row["id"],
        processing_summary_json={
            "event_log_id": 42,
            "internal_event_id": "iev_42",
            "external_effect_job_ids": [101],
            "handled": True,
        },
    )

    class FakeInternalEvent:
        def __init__(self, payload: dict):
            self.payload = payload

        def to_dict(self) -> dict:
            return dict(self.payload)

    class FakeInternalEventService:
        def get_event(self, event_id: str):
            return FakeInternalEvent({"event_id": event_id, "event_type": "channel_entry.entered", "source_command_id": "42"})

        def list_consumer_runs(self, filters: dict, *, limit: int = 100, offset: int = 0):
            return [FakeInternalEvent({"id": 7, "event_id": filters["event_id"], "consumer_name": "projection", "status": "pending"})], 1

        def list_attempts(self, consumer_run_id: int | None = None, *, event_id: str = ""):
            return [FakeInternalEvent({"id": 8, "attempt_id": "iea_8", "consumer_run_id": 7, "status": "succeeded"})]

    class FakeExternalEffectService:
        def get(self, job_id: int):
            return FakeInternalEvent({"id": int(job_id), "effect_type": "wecom.welcome_message.send", "source_event_id": "42", "status": "queued"})

        def list_jobs(self, filters: dict, *, limit: int = 50, offset: int = 0):
            return [], 0

        def list_attempts(self, job_id: int):
            return [FakeInternalEvent({"id": 9, "attempt_id": "eea_9", "job_id": int(job_id), "status": "blocked"})]

    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api.InternalEventService", FakeInternalEventService)
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api.ExternalEffectService", FakeExternalEffectService)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(f"/api/admin/webhook-inbox/{row['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["item"]["processing_summary_json"]["event_log_id"] == 42
    assert body["processing_chain"]["internal_events"][0]["event_id"] == "iev_42"
    assert body["processing_chain"]["internal_event_consumer_runs"][0]["consumer_name"] == "projection"
    assert body["processing_chain"]["external_effect_jobs"][0]["id"] == 101
    assert body["processing_chain"]["external_effect_attempts"][0]["attempts"][0]["attempt_id"] == "eea_9"


def test_webhook_inbox_admin_retry_and_skip_require_token(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    row = _seed(repo)
    repo.mark_dead_letter(row["id"], error_code="RuntimeError", error_message="boom")
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)
    tokens = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/webhook-inbox/{inbox_id}/retry"),
        ("POST", "/api/admin/webhook-inbox/{inbox_id}/skip"),
    )

    rejected = client.post(f"/api/admin/webhook-inbox/{row['id']}/retry", json={"reason": "manual replay"})
    accepted = client.post(
        f"/api/admin/webhook-inbox/{row['id']}/retry",
        json={"reason": "manual replay"},
        headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/webhook-inbox/{inbox_id}/retry")]},
    )
    skipped = client.post(
        f"/api/admin/webhook-inbox/{row['id']}/skip",
        json={"reason": "operator reviewed"},
        headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/webhook-inbox/{inbox_id}/skip")]},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 422
    assert set(accepted.json()["missing_fields"]) == {"actor", "expected_version"}
    assert skipped.status_code == 422
    assert set(skipped.json()["missing_fields"]) == {"actor", "expected_version"}
    assert repo.get_item(row["id"])["status"] == "dead_letter"


def test_webhook_inbox_admin_dispatch_one_requires_token_and_versioned_command(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    row = _seed(repo)
    calls: list[dict] = []

    class FakeWorker:
        def __init__(self, repository):
            self.repository = repository

        def dispatch_one(self, inbox_id: int, *, dry_run: bool = False, reason: str = "") -> dict:
            calls.append({"inbox_id": int(inbox_id), "dry_run": bool(dry_run), "reason": reason})
            return {"ok": True, "id": int(inbox_id), "status": "succeeded", "dry_run": bool(dry_run)}

    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.app.state.wecom_callback_inbox_worker_factory = FakeWorker
    token = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/webhook-inbox/{inbox_id}/dispatch"),
    )[("POST", "/api/admin/webhook-inbox/{inbox_id}/dispatch")]

    rejected = client.post(f"/api/admin/webhook-inbox/{row['id']}/dispatch", json={"dry_run": False})
    preview = client.post(
        f"/api/admin/webhook-inbox/{row['id']}/dispatch",
        json={"admin_action_token": token},
    )
    executed = client.post(
        f"/api/admin/webhook-inbox/{row['id']}/dispatch",
        json={"dry_run": False, "reason": "manual replay"},
        headers={"X-Admin-Action-Token": token},
    )

    assert rejected.status_code == 401
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert executed.status_code == 422
    assert set(executed.json()["missing_fields"]) == {"actor", "expected_version"}
    assert calls == [
        {"inbox_id": row["id"], "dry_run": True, "reason": "admin_dispatch_one"},
    ]


def test_webhook_inbox_admin_run_due_defaults_to_dry_run(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    _seed(repo)
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/webhook-inbox/run-due"),
    )[("POST", "/api/admin/webhook-inbox/run-due")]

    response = client.post(
        "/api/admin/webhook-inbox/run-due",
        json={"provider": "wecom", "limit": 5},
        headers={"X-Admin-Action-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["due_count"] == 1
    assert repo.rows[0]["status"] == "received"


def test_webhook_inbox_admin_run_due_accepts_admin_action_token(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    _seed(repo)
    monkeypatch.setattr("aicrm_next.platform_foundation.webhook_inbox.api._repo", lambda: repo)
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/webhook-inbox/run-due"),
    )[("POST", "/api/admin/webhook-inbox/run-due")]

    response = client.post(
        "/api/admin/webhook-inbox/run-due",
        json={"provider": "wecom", "limit": 5, "dry_run": True, "admin_action_token": token},
    )

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["due_count"] == 1


def test_webhook_inbox_admin_page_renders_shell_and_api_hooks():
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/admin/webhook-inbox")

    assert response.status_code == 200
    html = response.text
    assert "<h1 class=\"admin-page-title\">Webhook Inbox</h1>" in html
    assert "查看入站回调队列、失败重试、死信与企微回调链路。" in html
    assert "Webhook Inbox" in html
    assert "/api/admin/webhook-inbox/metrics" in html
    assert "/api/admin/webhook-inbox/items" in html
    assert "pending_failed" in html
    assert "name=\"received_from\"" in html
    assert "name=\"received_to\"" in html
    assert "预演单条" in html
    assert "执行单条" in html
    assert "dispatch-preview" in html
    assert "/api/admin/wecom/callback/reconciliation" in html
    assert "processing_chain" in html
    assert "internal_event_consumer_run" in html
    assert "external_effect_attempt" in html
    assert "webhook-inbox-stat-label\">待处理" in html
    assert "webhook-inbox-card-title\">入站回调" in html
    assert "Provider 分布" in html
    assert "Route 分布" in html
    assert "最近错误" in html
    assert "providerDistribution" in html
    assert "routeDistribution" in html
    assert "recentErrors" in html
