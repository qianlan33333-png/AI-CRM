from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from aicrm_next.channel_entry.callback_ingress import ingest_wecom_external_contact_callback
from aicrm_next.channel_entry.callback_processor import process_wecom_callback_payload
from aicrm_next.channel_entry.callback_worker import WeComCallbackWorker
from aicrm_next.channel_entry.inbox import WeComCallbackInboxWorker, ingest_wecom_callback
from aicrm_next.channel_entry.schemas import ProcessWeComExternalContactEventCommand
from aicrm_next.channel_entry import application as channel_application
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.webhook_inbox import InMemoryWebhookInboxRepository
from scripts import run_wecom_callback_inbox_worker as callback_worker_entrypoint


def _event(change_type: str = "add_external_contact") -> dict:
    return {
        "ToUserName": "corp-1",
        "Event": "change_external_contact",
        "ChangeType": change_type,
        "ExternalUserID": "wm-a",
        "UserID": "sales-a",
        "CreateTime": "1782530000",
        "WelcomeCode": "welcome-a",
        "State": "scene-a",
    }


def test_callback_post_enqueues_and_acks_without_processing(monkeypatch):
    calls: list[dict] = []
    client = TestClient(create_app(), raise_server_exceptions=False)

    monkeypatch.setattr("aicrm_next.channel_entry.api.encrypted_success_reply", lambda query: "success")
    monkeypatch.setattr("aicrm_next.channel_entry.api.ingest_wecom_external_contact_callback", lambda **kwargs: calls.append(kwargs) or {"ok": True, "id": 1})
    monkeypatch.setattr(
        channel_application,
        "process_wecom_external_contact_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("must not run inline")),
    )

    response = client.post(
        "/wecom/external-contact/callback?timestamp=1&nonce=n&msg_signature=s",
        content=b"<xml>encrypted</xml>",
        headers={"X-Test-Header": "seen"},
    )

    assert response.status_code == 200
    assert response.text == "success"
    assert len(calls) == 1
    assert calls[0]["body"] == b"<xml>encrypted</xml>"
    assert calls[0]["route"] == "/wecom/external-contact/callback"


def test_callback_post_returns_400_when_verification_or_decrypt_fails(monkeypatch):
    client = TestClient(create_app(), raise_server_exceptions=False)
    monkeypatch.setattr(
        "aicrm_next.channel_entry.api.ingest_wecom_external_contact_callback",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("bad signature")),
    )

    response = client.post("/wecom/external-contact/callback?timestamp=1&nonce=n&msg_signature=bad", content=b"bad")

    assert response.status_code == 400
    assert "bad signature" in response.text


def test_callback_post_returns_503_when_inbox_write_fails(monkeypatch):
    client = TestClient(create_app(), raise_server_exceptions=False)

    monkeypatch.setattr(
        "aicrm_next.channel_entry.api.ingest_wecom_external_contact_callback",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    response = client.post("/wecom/external-contact/callback?timestamp=1&nonce=n&msg_signature=s", content=b"body")

    assert response.status_code == 503
    assert "webhook ingress unavailable" in response.text


def test_callback_ingress_decrypts_and_ingests_webhook_inbox(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    calls: list[dict] = []

    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.decrypt_callback_body",
        lambda *, query, body: calls.append({"query": query, "body": body}) or (_event(), "<xml>plain</xml>"),
    )

    result = ingest_wecom_external_contact_callback(
        query={"timestamp": "1", "nonce": "n", "msg_signature": "s"},
        headers={"Cookie": "hidden", "User-Agent": "wecom"},
        body=b"<xml>encrypted</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )

    assert result["ok"] is True
    assert result["duplicate"] is False
    assert calls == [{"query": {"timestamp": "1", "nonce": "n", "msg_signature": "s"}, "body": b"<xml>encrypted</xml>"}]
    assert repo.rows[0]["payload_json"]["ExternalUserID"] == "wm-a"
    assert "cookie" not in repo.rows[0]["raw_headers_json"]


def test_callback_ingress_uses_durable_only_ack_boundary(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.decrypt_callback_body",
        lambda *, query, body: (_event(), "<xml>plain</xml>"),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.ingest_wecom_callback",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "id": 1, "ack_boundary": "durable_inbox_only"},
    )

    result = ingest_wecom_external_contact_callback(
        query={"timestamp": "1", "nonce": "n", "msg_signature": "s"},
        headers={},
        body=b"<xml>encrypted</xml>",
        route="/wecom/external-contact/callback",
    )

    assert result["ok"] is True
    assert result["ack_boundary"] == "durable_inbox_only"
    assert "process_time_sensitive" not in calls[0]


def test_callback_ingress_validation_error_is_returned_as_400(monkeypatch):
    client = TestClient(create_app(), raise_server_exceptions=False)

    monkeypatch.setattr(
        "aicrm_next.channel_entry.callback_ingress.decrypt_callback_body",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("decrypt failed")),
    )

    response = client.post("/wecom/external-contact/callback?timestamp=1&nonce=n&msg_signature=bad", content=b"bad")

    assert response.status_code == 400
    assert "decrypt failed" in response.text


def test_ingest_wecom_callback_deduplicates_by_event_key():
    repo = InMemoryWebhookInboxRepository()

    first = ingest_wecom_callback(
        query={"nonce": "n1"},
        headers={"Authorization": "secret", "User-Agent": "wecom"},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )
    duplicate = ingest_wecom_callback(
        query={"nonce": "n2"},
        headers={"User-Agent": "wecom"},
        body=b"raw-2",
        event_data=_event(),
        plain_xml="<xml>plain-2</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )

    assert first["ok"] is True
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["duplicate_count"] == 1
    assert len(repo.rows) == 1
    assert "authorization" not in repo.rows[0]["raw_headers_json"]


def test_ingest_time_sensitive_welcome_callback_stays_durable_until_worker_claim(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    processed: list[ProcessWeComExternalContactEventCommand] = []

    monkeypatch.setattr(
        "aicrm_next.channel_entry.inbox.process_wecom_external_contact_event",
        lambda command: processed.append(command) or (_ for _ in ()).throw(AssertionError("ingress must not process callback")),
    )

    result = ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )

    assert result["ack_boundary"] == "durable_inbox_only"
    assert result["status"] == "received"
    assert processed == []
    assert repo.rows[0]["status"] == "received"
    assert repo.rows[0]["locked_at"] is None
    assert len(repo.preview_due(provider="wecom", limit=10)) == 1


def test_ingest_non_welcome_callback_stays_durable(monkeypatch):
    repo = InMemoryWebhookInboxRepository()

    monkeypatch.setattr(
        "aicrm_next.channel_entry.inbox.process_wecom_external_contact_event",
        lambda command: (_ for _ in ()).throw(AssertionError("non-welcome callback must remain async")),
    )

    result = ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data={**_event(), "WelcomeCode": ""},
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )

    assert result["ack_boundary"] == "durable_inbox_only"
    assert result["status"] == "received"
    assert repo.rows[0]["status"] == "received"
    assert len(repo.preview_due(provider="wecom", limit=10)) == 1


def test_wecom_callback_inbox_worker_processes_claimed_rows():
    repo = InMemoryWebhookInboxRepository()
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )
    processed: list[ProcessWeComExternalContactEventCommand] = []

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        processed.append(command)
        return {
            "handled": True,
            "event_log": {"id": 42},
            "identity_sync": {
                "status": "success",
                "external_effect_job_id": 100,
            },
            "entry_result": {
                "mode": "channel_baseline_only",
                "reason": "channel_entry_baseline_recorded",
                "channel_entry_internal_event": {"event_id": "iev_42", "consumer_run_count": 2},
                "baseline_effects": {
                    "welcome_message": {"external_effect_job_id": 101},
                    "entry_tag": {"external_effect_job_id": 102},
                },
            },
        }

    result = WeComCallbackInboxWorker(repo, processor=processor).run_due(limit=10, dry_run=False)

    assert result["claimed_count"] == 1
    assert result["succeeded_count"] == 1
    assert repo.rows[0]["status"] == "succeeded"
    assert repo.rows[0]["processing_summary_json"]["event_log_id"] == 42
    assert repo.rows[0]["processing_summary_json"]["internal_event_id"] == "iev_42"
    assert repo.rows[0]["processing_summary_json"]["external_effect_job_ids"] == [101, 102, 100]
    assert result["items"][0]["internal_event_id"] == "iev_42"
    assert processed[0].event_data["ExternalUserID"] == "wm-a"


def test_wecom_callback_inbox_worker_dry_run_only_previews_due_rows():
    repo = InMemoryWebhookInboxRepository()
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        raise AssertionError("dry-run must not dispatch callback payloads")

    result = WeComCallbackInboxWorker(repo, processor=processor).run_due(limit=10, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["due_count"] == 1
    assert result["items"][0]["status"] == "received"
    assert repo.rows[0]["status"] == "received"
    assert repo.rows[0]["locked_at"] is None
    assert repo.rows[0]["locked_by"] == ""


def test_wecom_callback_worker_entrypoint_defaults_to_dry_run_and_requires_execute(monkeypatch):
    calls: list[dict] = []

    def fake_run(*, limit: int | None = None, dry_run: bool = True) -> dict:
        calls.append({"limit": limit, "dry_run": dry_run})
        return {"ok": True, "dry_run": dry_run, "limit": limit}

    monkeypatch.setattr(callback_worker_entrypoint, "run", fake_run)
    monkeypatch.setattr(callback_worker_entrypoint, "print_json", lambda payload: None)
    monkeypatch.delenv("AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE", raising=False)
    monkeypatch.delenv("AICRM_WECOM_CALLBACK_INBOX_WORKER_MAX_EXECUTE_BATCH_SIZE", raising=False)

    preview_exit_code = callback_worker_entrypoint.main(["--limit", "7"])
    blocked_exit_code = callback_worker_entrypoint.main(["--limit", "7", "--execute"])
    monkeypatch.setenv("AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE", "1")
    too_large_exit_code = callback_worker_entrypoint.main(["--limit", "21", "--execute"])
    execute_exit_code = callback_worker_entrypoint.main(["--limit", "7", "--execute"])

    assert preview_exit_code == 0
    assert blocked_exit_code == 1
    assert too_large_exit_code == 1
    assert execute_exit_code == 0
    assert calls == [
        {"limit": 7, "dry_run": True},
        {"limit": 7, "dry_run": False},
    ]


def test_wecom_callback_worker_alias_and_processor_boundary(monkeypatch):
    repo = InMemoryWebhookInboxRepository()
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )
    processed: list[ProcessWeComExternalContactEventCommand] = []

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        processed.append(command)
        return {
            "handled": True,
            "event_log": {"id": 43},
            "identity_sync": {"status": "success"},
            "entry_result": {"mode": "channel_baseline_only", "baseline_effects": {}},
        }

    monkeypatch.setattr("aicrm_next.channel_entry.callback_processor.process_wecom_external_contact_event", processor)

    preview = WeComCallbackWorker(repo).preview_due(limit=10)
    processor_result = process_wecom_callback_payload(repo.rows[0])
    worker_result = WeComCallbackWorker(repo, processor=processor).run_due(limit=10, dry_run=False)

    assert preview["due_count"] == 1
    assert processor_result["event_log"]["id"] == 43
    assert worker_result["succeeded_count"] == 1
    assert processed[0].event_data["ExternalUserID"] == "wm-a"


def test_wecom_callback_inbox_worker_dispatch_one_by_id():
    repo = InMemoryWebhookInboxRepository()
    ingest_result = ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )
    processed: list[ProcessWeComExternalContactEventCommand] = []

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        processed.append(command)
        return {
            "handled": True,
            "event_log": {"id": 84},
            "identity_sync": {"status": "success"},
            "entry_result": {
                "mode": "channel_baseline_only",
                "channel_entry_internal_event": {"event_id": "iev_84", "consumer_run_count": 1},
                "baseline_effects": {"welcome_message": {"external_effect_job_id": 201}},
            },
        }

    worker = WeComCallbackInboxWorker(repo, processor=processor)
    preview = worker.dispatch_one(int(ingest_result["id"]), dry_run=True)
    result = worker.dispatch_one(int(ingest_result["id"]))
    repeated = worker.dispatch_one(int(ingest_result["id"]))

    assert preview["ok"] is True
    assert preview["dry_run"] is True
    assert repo.rows[0]["status"] == "succeeded"
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["event_log_id"] == 84
    assert result["internal_event_id"] == "iev_84"
    assert result["external_effect_job_ids"] == [201]
    assert repo.rows[0]["processing_summary_json"]["internal_event_id"] == "iev_84"
    assert repeated["ok"] is False
    assert repeated["error"] == "webhook_inbox_item_not_dispatchable"
    assert len(processed) == 1


def test_wecom_callback_inbox_worker_dispatch_one_replays_dead_letter():
    repo = InMemoryWebhookInboxRepository()
    row = repo.upsert_received(
        provider="wecom",
        event_family="external_contact",
        route="/wecom/external-contact/callback",
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-1",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_event_id="event-1",
        idempotency_key="event-1",
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"",
        payload_xml="<xml/>",
        payload_json=_event(),
        payload_summary_json={},
        max_attempts=2,
    )
    repo.mark_dead_letter(row["id"], error_code="RuntimeError", error_message="old failure")

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        return {
            "handled": False,
            "event_log": {"id": 85},
            "identity_sync": {"status": "skipped"},
            "entry_result": {"mode": "noop", "baseline_effects": {}},
        }

    result = WeComCallbackInboxWorker(repo, processor=processor).dispatch_one(row["id"], reason="manual replay")

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert repo.rows[0]["status"] == "succeeded"
    assert repo.rows[0]["last_error_code"] == ""
    assert repo.rows[0]["processing_summary_json"]["event_log_id"] == 85


def test_wecom_callback_inbox_worker_retries_then_dead_letters():
    repo = InMemoryWebhookInboxRepository()
    row = repo.upsert_received(
        provider="wecom",
        event_family="external_contact",
        route="/wecom/external-contact/callback",
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-1",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_event_id="event-1",
        idempotency_key="event-1",
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"",
        payload_xml="<xml/>",
        payload_json=_event(),
        payload_summary_json={},
        max_attempts=1,
    )
    assert row["status"] == "received"

    def broken_processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        raise RuntimeError("boom")

    result = WeComCallbackInboxWorker(repo, processor=broken_processor).run_due(limit=10, dry_run=False)

    assert result["claimed_count"] == 1
    assert result["dead_letter_count"] == 1
    assert repo.rows[0]["status"] == "dead_letter"
    assert repo.rows[0]["attempt_count"] == 1
    assert repo.rows[0]["last_error_code"] == "RuntimeError"


def test_wecom_callback_worker_reclaims_stale_processing_after_crash():
    repo = InMemoryWebhookInboxRepository()
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"raw",
        event_data=_event(),
        plain_xml="<xml>plain</xml>",
        route="/wecom/external-contact/callback",
        repository=repo,
    )
    claimed = repo.claim_due(provider="wecom", limit=1, locked_by="crashed-worker")
    assert claimed[0]["status"] == "processing"
    repo.rows[0]["locked_at"] = datetime.now(timezone.utc) - timedelta(minutes=6)

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        return {
            "handled": True,
            "event_log": {"id": 86},
            "identity_sync": {"status": "success"},
            "entry_result": {"mode": "channel_baseline_only", "baseline_effects": {}},
        }

    result = WeComCallbackWorker(repo, processor=processor).run_due(limit=1, dry_run=False)

    assert result["claimed_count"] == 1
    assert result["succeeded_count"] == 1
    assert result["items"][0]["event_log_id"] == 86
    assert repo.rows[0]["status"] == "succeeded"


def test_non_entry_callback_event_does_not_run_identity_sync_or_channel_entry(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        channel_application.repo,
        "log_external_contact_event",
        lambda **kwargs: {"id": 42, **kwargs},
    )
    monkeypatch.setattr(channel_application.repo, "mark_event_status", lambda *args, **kwargs: calls.append("mark"))
    monkeypatch.setattr(channel_application.repo, "record_identity_sync_result", lambda *args, **kwargs: calls.append("identity_diag"))
    monkeypatch.setattr(
        channel_application,
        "sync_external_contact_identity_for_event",
        lambda *args, **kwargs: calls.append("identity_sync") or {"status": "success"},
    )
    monkeypatch.setattr(
        channel_application,
        "process_channel_entry",
        lambda *args, **kwargs: calls.append("process_channel_entry") or {"handled": True},
    )

    result = channel_application.process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id="corp-1",
            event_data=_event(change_type="del_external_contact"),
            payload_xml="<xml/>",
            route="/wecom/external-contact/callback",
        )
    )

    assert result["handled"] is False
    assert result["identity_sync"] == {"status": "skipped", "reason": "non_entry_change_type"}
    assert calls == ["identity_diag", "mark"]
