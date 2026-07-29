from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aicrm_next.platform.platform_foundation.webhook_inbox import InMemoryWebhookInboxRepository, WebhookInboxService
from aicrm_next.platform.platform_foundation.webhook_inbox.models import WebhookInboxMetrics


def _ingest(
    repo: InMemoryWebhookInboxRepository,
    key: str = "event-1",
    *,
    provider: str = "wecom",
    event_family: str = "external_contact",
    route: str = "/wecom/external-contact/callback",
) -> dict:
    return repo.ingest(
        provider=provider,
        event_family=event_family,
        route=route,
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-1",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_event_id=key,
        idempotency_key=key,
        raw_query_json={"nonce": "n"},
        raw_headers_json={"user-agent": "wecom"},
        raw_body=b"raw",
        payload_xml="<xml/>",
        payload_json={"ExternalUserID": "wm-a"},
        payload_summary_json={"external_userid": "wm-a"},
        max_attempts=2,
    )


def test_webhook_inbox_ingest_deduplicates_and_updates_metrics():
    repo = InMemoryWebhookInboxRepository()

    first = _ingest(repo)
    duplicate = _ingest(repo)

    assert first["id"] == duplicate["id"]
    assert duplicate["duplicate_count"] == 1
    assert len(repo.rows) == 1

    metrics = repo.queue_metrics({"provider": "wecom"})
    assert metrics["due_count"] == 1
    assert metrics["processing_count"] == 0
    assert metrics["failed_retryable_count"] == 0
    assert metrics["dead_letter_count"] == 0
    assert metrics["status_counts"] == {"received": 1}


def test_webhook_inbox_acquire_due_locks_rows_and_service_returns_items():
    repo = InMemoryWebhookInboxRepository()
    row = _ingest(repo)
    service = WebhookInboxService(repo)

    claimed = service.acquire_due(provider="wecom", limit=10, locked_by="test-worker")

    assert len(claimed) == 1
    assert claimed[0].id == row["id"]
    assert claimed[0].status == "processing"
    metrics = service.queue_metrics({"provider": "wecom"})
    assert metrics.due_count == 0
    assert metrics.processing_count == 1
    assert metrics.status_counts == {"processing": 1}


def test_webhook_inbox_service_item_model_exposes_operational_fields():
    repo = InMemoryWebhookInboxRepository()
    row = _ingest(repo)
    _ingest(repo)
    service = WebhookInboxService(repo)

    preview = service.preview_due(provider="wecom", limit=10)
    claimed = service.acquire_due(provider="wecom", limit=10, locked_by="test-worker")
    failed = service.mark_failed_retryable(
        row["id"],
        error_code="RuntimeError",
        error_message="temporary failure",
        next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert len(preview) == 1
    assert preview[0].raw_query_json == {"nonce": "n"}
    assert preview[0].raw_headers_json == {"user-agent": "wecom"}
    assert preview[0].raw_body == b"raw"
    assert preview[0].payload_summary_json == {"external_userid": "wm-a"}
    assert preview[0].duplicate_count == 1
    assert preview[0].last_seen_at is not None
    assert preview[0].created_at is not None
    assert preview[0].updated_at is not None
    assert claimed[0].locked_by == "test-worker"
    assert claimed[0].locked_at is not None
    assert claimed[0].started_at is not None
    assert failed is not None
    assert failed.status == "failed_retryable"
    assert failed.attempt_count == 1
    assert failed.last_error_code == "RuntimeError"
    assert failed.last_error_message == "temporary failure"
    assert failed.locked_at is None
    assert failed.locked_by == ""
    assert failed.next_retry_at is not None


def test_webhook_inbox_stale_processing_lock_becomes_due_again():
    repo = InMemoryWebhookInboxRepository()
    row = _ingest(repo)
    first_claim = repo.claim_due(provider="wecom", limit=10, locked_by="worker-a")

    assert first_claim[0]["id"] == row["id"]
    assert repo.preview_due(provider="wecom", limit=10) == []

    repo.rows[0]["locked_at"] = datetime.now(timezone.utc) - timedelta(minutes=6)
    metrics = repo.queue_metrics({"provider": "wecom"})
    second_claim = repo.claim_due(provider="wecom", limit=10, locked_by="worker-b")

    assert metrics["due_count"] == 1
    assert second_claim[0]["id"] == row["id"]
    assert second_claim[0]["status"] == "processing"
    assert second_claim[0]["locked_by"] == "worker-b"


def test_webhook_inbox_retry_terminal_and_dead_letter_states_are_metric_visible():
    repo = InMemoryWebhookInboxRepository()
    retryable = _ingest(repo, "retryable")
    terminal = _ingest(repo, "terminal")
    dead = _ingest(repo, "dead")

    repo.mark_failed_retryable(
        retryable["id"],
        error_code="RuntimeError",
        error_message="retry later",
        next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    repo.mark_failed_terminal(terminal["id"], error_code="ValueError", error_message="bad payload")
    repo.mark_dead_letter(dead["id"], error_code="RuntimeError", error_message="too many attempts")

    metrics = repo.queue_metrics({"provider": "wecom"})
    assert metrics["due_count"] == 1
    assert metrics["failed_retryable_count"] == 1
    assert metrics["dead_letter_count"] == 1
    assert metrics["status_counts"] == {
        "dead_letter": 1,
        "failed_retryable": 1,
        "failed_terminal": 1,
    }


def test_webhook_inbox_filters_incident_window_pending_and_failed_rows():
    repo = InMemoryWebhookInboxRepository()
    pending = _ingest(repo, "pending")
    failed = _ingest(repo, "failed")
    succeeded = _ingest(repo, "succeeded")
    outside = _ingest(repo, "outside")

    incident_at = datetime(2026, 6, 27, 3, 10, tzinfo=timezone.utc)
    repo.rows[0]["received_at"] = incident_at
    repo.rows[1]["received_at"] = incident_at + timedelta(minutes=2)
    repo.rows[2]["received_at"] = incident_at + timedelta(minutes=3)
    repo.rows[3]["received_at"] = datetime(2026, 6, 27, 4, 10, tzinfo=timezone.utc)
    repo.mark_failed_retryable(failed["id"], error_code="RuntimeError", error_message="retry")
    repo.mark_succeeded(succeeded["id"])

    filters = {
        "provider": "wecom",
        "status": "pending_failed",
        "received_from": "2026-06-27T11:00",
        "received_to": "2026-06-27T11:20",
    }
    items = repo.list_items(filters, limit=10)
    metrics = repo.queue_metrics(filters)

    assert {item["idempotency_key"] for item in items} == {"pending", "failed"}
    assert metrics["due_count"] == 2
    assert metrics["status_counts"] == {"failed_retryable": 1, "received": 1}


def test_webhook_inbox_metrics_include_distribution_and_recent_errors():
    repo = InMemoryWebhookInboxRepository()
    _ingest(repo, "wecom-a", provider="wecom", route="/wecom/external-contact/callback")
    _ingest(repo, "wecom-b", provider="wecom", route="/api/wecom/events")
    failed = _ingest(repo, "feishu-a", provider="feishu", event_family="oauth", route="/api/feishu/events")
    repo.mark_failed_retryable(failed["id"], error_code="RuntimeError", error_message="adapter timeout")

    metrics = repo.queue_metrics({})
    model = WebhookInboxMetrics.from_payload(metrics)

    assert metrics["provider_distribution"] == [{"provider": "wecom", "count": 2}, {"provider": "feishu", "count": 1}]
    assert {"route": "/wecom/external-contact/callback", "count": 1} in metrics["route_distribution"]
    assert {"route": "/api/wecom/events", "count": 1} in metrics["route_distribution"]
    assert {"route": "/api/feishu/events", "count": 1} in metrics["route_distribution"]
    assert metrics["recent_errors"][0]["error_code"] == "RuntimeError"
    assert metrics["recent_errors"][0]["error_message"] == "adapter timeout"
    assert metrics["recent_errors"][0]["count"] == 1
    assert model.provider_distribution == metrics["provider_distribution"]
    assert model.route_distribution == metrics["route_distribution"]
    assert model.recent_errors[0]["error_code"] == "RuntimeError"
