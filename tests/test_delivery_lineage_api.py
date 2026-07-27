from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.insights.delivery_lineage.application import (
    delivery_lineage_daily_metrics,
    get_delivery_lineage,
    list_delivery_lineage,
    list_delivery_lineage_by_trace,
    list_delivery_lineage_by_unionid,
)
from aicrm_next.insights.delivery_lineage.dto import DeliveryLineageDailyMetric, DeliveryLineageItem
from aicrm_next.insights.delivery_lineage.repository import InMemoryDeliveryLineageRepository
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    return TestClient(create_app(), raise_server_exceptions=False)


def _repo() -> InMemoryDeliveryLineageRepository:
    return InMemoryDeliveryLineageRepository(
        items=[
            DeliveryLineageItem(
                lineage_id="external_effect:9",
                source_type="group_ops",
                source_id="cmd-1",
                business_domain="group_ops",
                unionid="union-1",
                external_effect_job_id=9,
                external_effect_status="failed_retryable",
                external_effect_attempt_count=2,
                last_error="timeout",
                trace_id="trace-1",
            ),
            DeliveryLineageItem(
                lineage_id="broadcast:7",
                source_type="campaign",
                source_id="campaign-1",
                business_domain="campaign",
                broadcast_job_id=7,
                broadcast_job_status="queued",
                broadcast_event_count=3,
                trace_id="trace-2",
            ),
        ],
        metrics=[
            DeliveryLineageDailyMetric(metric="failed_delivery_daily", day="2026-07-02", value=2),
            DeliveryLineageDailyMetric(metric="blocked_delivery_daily", day="2026-07-02", value=1),
            DeliveryLineageDailyMetric(metric="retryable_effect_daily", day="2026-07-02", value=3),
        ],
    )


def test_delivery_lineage_api_returns_empty_without_database(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/admin/delivery-lineage")
    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.json()["items"] == []

    missing = client.get("/api/admin/delivery-lineage/external_effect:missing")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "delivery_lineage_not_found"

    metrics = client.get("/api/admin/delivery-lineage/metrics/daily")
    assert metrics.status_code == 200
    assert metrics.json()["items"] == []


def test_delivery_lineage_application_filters_safe_public_fields() -> None:
    repo = _repo()

    listing = list_delivery_lineage(repo=repo)
    assert [item["lineage_id"] for item in listing["items"]] == ["external_effect:9", "broadcast:7"]

    detail = get_delivery_lineage("external_effect:9", repo=repo)
    assert detail["item"]["external_effect_status"] == "failed_retryable"
    assert detail["item"]["last_error"] == "timeout"

    by_unionid = list_delivery_lineage_by_unionid("union-1", repo=repo)
    assert [item["lineage_id"] for item in by_unionid["items"]] == ["external_effect:9"]

    by_trace = list_delivery_lineage_by_trace("trace-2", repo=repo)
    assert [item["lineage_id"] for item in by_trace["items"]] == ["broadcast:7"]

    rendered = str(listing) + str(detail)
    for forbidden in ("payload_json", "raw_payload", "request_summary_json", "response_summary_json"):
        assert forbidden not in rendered


def test_delivery_lineage_daily_metrics_expose_dashboard_names() -> None:
    payload = delivery_lineage_daily_metrics(repo=_repo())

    assert payload["days"] == 7
    assert {
        "failed_delivery_daily",
        "blocked_delivery_daily",
        "retryable_effect_daily",
    } == {item["metric"] for item in payload["items"]}
