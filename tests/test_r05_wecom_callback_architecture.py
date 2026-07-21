from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from aicrm_next.channel_entry.callback_ingress import (
    CALLBACK_MAX_BODY_BYTES,
    WeComCallbackIngressValidationError,
    ingest_wecom_external_contact_callback,
)
from aicrm_next.channel_entry.ingress_app import create_wecom_callback_ingress_app
from aicrm_next.channel_entry.inbox import ingest_wecom_callback
from aicrm_next.channel_entry.inbox import WeComCallbackInboxWorker
from aicrm_next.channel_entry.wecom_crypto import WeComCallbackError, validate_callback_timestamp
from aicrm_next.integration_gateway.wecom_channel_entry_client import ProductionWeComAdapter, WeComApiError
from aicrm_next.integration_gateway.wecom_runtime import (
    SingleFlightAccessTokenProvider,
    load_wecom_execution_config,
)
from aicrm_next.platform_foundation.webhook_inbox import InMemoryWebhookInboxRepository
from aicrm_next.platform_foundation.webhook_inbox.repository import PostgresWebhookInboxRepository
from scripts import run_wecom_callback_inbox_worker as worker_entrypoint
from scripts.ops import reconcile_wecom_callback_runtime as reconciliation


ROOT = Path(__file__).resolve().parents[1]


def _event(*, welcome: bool = True) -> dict:
    return {
        "ToUserName": "corp-1",
        "Event": "change_external_contact",
        "ChangeType": "add_external_contact",
        "ExternalUserID": "external-fixture",
        "UserID": "staff-fixture",
        "CreateTime": str(int(time.time())),
        "WelcomeCode": "welcome-fixture" if welcome else "",
        "State": "scene-fixture" if welcome else "",
    }


def test_callback_ack_never_calls_business_processor_or_provider(monkeypatch) -> None:
    provider_calls: list[dict] = []
    repository = InMemoryWebhookInboxRepository()
    app = create_wecom_callback_ingress_app()
    client = TestClient(app, raise_server_exceptions=False)

    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.decrypt_callback_body",
        lambda **kwargs: (_event(), "<xml>plain</xml>"),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.ingest_wecom_callback",
        lambda **kwargs: ingest_wecom_callback(**{**kwargs, "repository": repository}),
    )
    monkeypatch.setattr("aicrm_next.channel_entry.api.encrypted_success_reply", lambda query: "success")
    monkeypatch.setattr(
        "aicrm_next.channel_entry.inbox.process_wecom_external_contact_event",
        lambda command: provider_calls.append({"provider_delay_seconds": 5}) or time.sleep(5),
    )

    latencies: list[float] = []
    for index in range(20):
        started = time.perf_counter()
        response = client.post(
            f"/wecom/external-contact/callback?timestamp={int(time.time())}&nonce=n-{index}&msg_signature=s",
            content=b"<xml>encrypted</xml>",
        )
        latencies.append(time.perf_counter() - started)
        assert response.status_code == 200

    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    assert p95 < 1.0
    assert provider_calls == []
    assert repository.rows[0]["status"] == "received"


def test_callback_ingress_rejects_oversized_body_before_decrypt(monkeypatch) -> None:
    decrypt_calls: list[bool] = []
    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.decrypt_callback_body",
        lambda **kwargs: decrypt_calls.append(True),
    )

    with pytest.raises(WeComCallbackIngressValidationError, match="size limit"):
        ingest_wecom_external_contact_callback(
            query={"timestamp": str(int(time.time()))},
            headers={},
            body=b"x" * (CALLBACK_MAX_BODY_BYTES + 1),
            route="/wecom/external-contact/callback",
        )

    assert decrypt_calls == []


def test_callback_timestamp_window_rejects_expired_and_future_values() -> None:
    now = 1_800_000_000
    validate_callback_timestamp(str(now), now=now)
    with pytest.raises(WeComCallbackError, match="expired"):
        validate_callback_timestamp(str(now - 301), now=now)
    with pytest.raises(WeComCallbackError, match="future"):
        validate_callback_timestamp(str(now + 61), now=now)
    with pytest.raises(WeComCallbackError, match="invalid"):
        validate_callback_timestamp("not-a-number", now=now)


def test_webhook_inbox_prioritizes_fresh_welcome_but_ages_regular_rows_fairly() -> None:
    repository = InMemoryWebhookInboxRepository()
    regular = ingest_wecom_callback(
        query={}, headers={}, body=b"regular", event_data=_event(welcome=False), plain_xml="<xml/>", route="/api/wecom/events", repository=repository
    )
    welcome = ingest_wecom_callback(
        query={}, headers={}, body=b"welcome", event_data=_event(welcome=True), plain_xml="<xml/>", route="/api/wecom/events", repository=repository
    )

    assert [row["id"] for row in repository.preview_due(provider="wecom", limit=2)] == [welcome["id"], regular["id"]]

    repository.rows[0]["received_at"] = datetime.now(timezone.utc) - timedelta(seconds=6)
    assert [row["id"] for row in repository.preview_due(provider="wecom", limit=2)] == [regular["id"], welcome["id"]]


def test_postgres_callback_priority_and_concurrent_duplicate_claim(next_pg_schema) -> None:
    repository = PostgresWebhookInboxRepository(os.environ["DATABASE_URL"])
    regular = ingest_wecom_callback(
        query={}, headers={}, body=b"regular", event_data=_event(welcome=False), plain_xml="<xml/>", route="/api/wecom/events", repository=repository
    )
    welcome = ingest_wecom_callback(
        query={}, headers={}, body=b"welcome", event_data=_event(welcome=True), plain_xml="<xml/>", route="/api/wecom/events", repository=repository
    )

    assert [row["id"] for row in repository.preview_due(provider="wecom", limit=2)] == [welcome["id"], regular["id"]]
    repository.mark_succeeded(int(regular["id"]))
    repository.mark_succeeded(int(welcome["id"]))

    duplicate = ingest_wecom_callback(
        query={},
        headers={},
        body=b"duplicate",
        event_data={**_event(welcome=True), "CreateTime": str(int(time.time()) + 1)},
        plain_xml="<xml/>",
        route="/api/wecom/events",
        repository=repository,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: repository.claim_due(provider="wecom", limit=1, locked_by=f"worker-{worker}"),
                range(2),
            )
        )

    claimed_ids = [int(row["id"]) for batch in claims for row in batch]
    assert claimed_ids == [int(duplicate["id"])]


def test_persistent_worker_drains_until_signal_without_minute_timer() -> None:
    stop_event = threading.Event()

    class FakeWorker:
        calls = 0

        def run_due(self, *, limit: int, dry_run: bool) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"ok": True, "claimed_count": 2, "succeeded_count": 2}
            stop_event.set()
            return {"ok": True, "claimed_count": 0}

    worker = FakeWorker()
    result = worker_entrypoint.run_loop(limit=20, poll_interval=0.05, stop_event=stop_event, worker=worker)

    assert result["ok"] is True
    assert result["mode"] == "persistent"
    assert result["claimed_count"] == 2
    assert result["succeeded_count"] == 2
    assert worker.calls == 2


def test_callback_worker_does_not_retry_typed_terminal_provider_error() -> None:
    repository = InMemoryWebhookInboxRepository()
    ingest_wecom_callback(
        query={}, headers={}, body=b"row", event_data=_event(), plain_xml="<xml/>", route="/api/wecom/events", repository=repository
    )

    class TerminalProviderError(RuntimeError):
        classification = "terminal"
        error_code = "permission_denied"
        retry_after_seconds = None

    result = WeComCallbackInboxWorker(
        repository,
        processor=lambda command: (_ for _ in ()).throw(TerminalProviderError("forbidden")),
    ).run_due(limit=1, dry_run=False)

    assert result["failed_terminal_count"] == 1
    assert repository.rows[0]["status"] == "failed_terminal"
    assert repository.rows[0]["last_error_code"] == "permission_denied"
    assert repository.rows[0]["next_retry_at"] is None


def test_runtime_has_no_callback_inline_or_process_local_executor_boundary() -> None:
    callback_source = (ROOT / "aicrm_next/channel_entry/inbox.py").read_text(encoding="utf-8")
    realtime_source = (ROOT / "aicrm_next/platform_foundation/external_effects/realtime.py").read_text(encoding="utf-8")
    service = (ROOT / "deploy/openclaw-wecom-callback-inbox-worker.service").read_text(encoding="utf-8")

    assert "process_time_sensitive" not in callback_source
    assert "ingress-inline" not in callback_source
    assert "ThreadPoolExecutor" not in realtime_source
    assert "_EXECUTOR.submit" not in realtime_source
    assert "--execute --loop" in service
    assert not (ROOT / "deploy/openclaw-wecom-callback-inbox-worker.timer").exists()


def test_access_token_refresh_is_single_flight_for_concurrent_callers() -> None:
    provider = SingleFlightAccessTokenProvider()
    refresh_count = 0
    refresh_lock = threading.Lock()

    def refresh() -> tuple[str, int]:
        nonlocal refresh_count
        with refresh_lock:
            refresh_count += 1
        time.sleep(0.05)
        return "shared-token", 7200

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _index: provider.get(refresh), range(8)))

    assert tokens == ["shared-token"] * 8
    assert refresh_count == 1


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict:
        return dict(self._payload)


def test_wecom_client_refreshes_expired_token_once_then_succeeds() -> None:
    calls: list[dict] = []

    def request(method, url, **kwargs):
        calls.append({"url": url, "params": dict(kwargs.get("params") or {})})
        if url.endswith("/gettoken"):
            token_number = sum(1 for call in calls if call["url"].endswith("/gettoken"))
            return _Response({"errcode": 0, "access_token": f"token-{token_number}", "expires_in": 7200})
        if len([call for call in calls if call["url"].endswith("/send_welcome_msg")]) == 1:
            return _Response({"errcode": 42001, "errmsg": "expired"})
        return _Response({"errcode": 0, "errmsg": "ok"})

    adapter = ProductionWeComAdapter(corp_id="corp", secret="secret", api_base="https://qy.example", http_request=request)
    result = adapter.send_welcome_msg({"welcome_code": "code"})

    assert result["errcode"] == 0
    assert [call["params"].get("access_token") for call in calls if call["url"].endswith("/send_welcome_msg")] == ["token-1", "token-2"]
    assert len([call for call in calls if call["url"].endswith("/gettoken")]) == 2


def test_wecom_client_classifies_rate_limit_retry_after_and_permission_terminal() -> None:
    responses = iter(
        [
            _Response({"errcode": 0, "access_token": "token", "expires_in": 7200}),
            _Response({"errcode": 0}, status_code=429, headers={"Retry-After": "7"}),
        ]
    )
    adapter = ProductionWeComAdapter(corp_id="corp", secret="secret", api_base="https://qy.example", http_request=lambda *args, **kwargs: next(responses))

    with pytest.raises(WeComApiError) as limited:
        adapter.send_welcome_msg({"welcome_code": "code"})

    assert limited.value.error_code == "rate_limited"
    assert limited.value.classification == "retryable"
    assert limited.value.retry_after_seconds == 7

    permission_responses = iter(
        [
            _Response({"errcode": 0, "access_token": "token", "expires_in": 7200}),
            _Response({"errcode": 48002, "errmsg": "forbidden"}),
        ]
    )
    permission_adapter = ProductionWeComAdapter(
        corp_id="corp",
        secret="secret",
        api_base="https://qy.example",
        http_request=lambda *args, **kwargs: next(permission_responses),
    )
    with pytest.raises(WeComApiError) as denied:
        permission_adapter.send_welcome_msg({"welcome_code": "code"})

    assert denied.value.error_code == "permission_denied"
    assert denied.value.classification == "terminal"


def test_typed_wecom_config_fails_closed_on_legacy_conflict(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_NEXT_WECOM_REAL_CALLS_ENABLED", "false")
    monkeypatch.delenv("AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE", raising=False)

    config = load_wecom_execution_config()

    assert config.execution_mode == "disabled"
    assert config.conflict is True
    assert config.real_calls_enabled is False
    assert "wecom_execution_config_conflict" in config.blocking_reasons
    assert config.diagnostics()["deprecated_settings_delete_after"] == "2026-10-01"


def test_callback_reconciliation_is_count_only_and_reports_static_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(
        reconciliation,
        "read_count_only_inbox_state",
        lambda database_url="": {
            "checked": True,
            "received_count": 1,
            "processing_count": 0,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "dead_letter_count": 0,
            "due_count": 1,
            "duplicate_collapsed_count": 2,
            "oldest_pending_age_seconds": 3,
        },
    )
    monkeypatch.setattr(
        reconciliation,
        "retired_timer_state",
        lambda *, skip_systemctl: {"checked": True, "unit": "retired-timer", "active": False, "status": "inactive"},
    )

    payload = reconciliation.run(["--skip-systemctl"])
    serialized = str(payload)

    assert payload["ok"] is True
    assert payload["unsafe_count"] == 0
    assert payload["pii_included"] is False
    assert payload["static_boundary"]["inline_dispatch_reference_count"] == 0
    assert payload["static_boundary"]["process_local_executor_reference_count"] == 0
    assert payload["static_boundary"]["durable_inbox_runtime_manifest_count"] == 1
    assert payload["static_boundary"]["legacy_persistent_worker_active_count"] == 0
    assert payload["static_boundary"]["legacy_persistent_worker_managed_count"] == 1
    assert "external-fixture" not in serialized
    assert "welcome-fixture" not in serialized
