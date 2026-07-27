from __future__ import annotations

import time
from urllib.parse import parse_qsl, urlsplit

from aicrm_next.channels.channel_entry import application as channel_application
from aicrm_next.channels.channel_entry.inbox import WeComCallbackInboxWorker, ingest_wecom_callback
from aicrm_next.channels.channel_entry.schemas import ProcessWeComExternalContactEventCommand
from aicrm_next.platform.platform_foundation.webhook_inbox import InMemoryWebhookInboxRepository
from scripts.ops import generate_wecom_callback_sample as generator
from scripts.ops import probe_wecom_callback_pressure as probe


def _set_callback_env(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", "token")
    monkeypatch.setenv("WECOM_CALLBACK_AES_KEY", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")


def test_callback_sample_generator_outputs_valid_encrypted_callback(monkeypatch, tmp_path) -> None:
    _set_callback_env(monkeypatch)
    body_file = tmp_path / "sample.xml"
    url_file = tmp_path / "sample.url"
    metadata_file = tmp_path / "sample.json"
    timestamp = str(int(time.time()))

    payload = generator.run(
        [
            "--callback-base-url",
            "http://127.0.0.1:5002/wecom/external-contact/callback",
            "--body-file",
            str(body_file),
            "--url-file",
            str(url_file),
            "--metadata-file",
            str(metadata_file),
            "--timestamp",
            timestamp,
            "--nonce",
            "nonce-a",
            "--external-userid",
            "wm-a",
            "--user-id",
            "sales-a",
            "--state",
            "scene-a",
        ]
    )

    assert payload["ok"] is True
    assert payload["idempotency_key"] == f"ww-test|change_external_contact|del_external_contact|wm-a|sales-a|{timestamp}||scene-a"
    assert body_file.exists()
    assert url_file.read_text(encoding="utf-8").startswith("http://127.0.0.1:5002/wecom/external-contact/callback?")
    assert "WECOM_CALLBACK_AES_KEY" not in metadata_file.read_text(encoding="utf-8")

    validation = probe.validate_callback_sample(url_file.read_text(encoding="utf-8").strip(), body_file.read_bytes())

    assert validation["ok"] is True
    assert validation["idempotency_key"] == payload["idempotency_key"]
    assert validation["event_summary"]["Event"] == "change_external_contact"
    assert validation["event_summary"]["ChangeType"] == "del_external_contact"


def test_callback_sample_generator_escapes_xml_values(monkeypatch) -> None:
    _set_callback_env(monkeypatch)

    payload = generator.run(["--state", "a&b", "--timestamp", str(int(time.time())), "--nonce", "nonce-a", "--print-body"])
    validation = probe.validate_callback_sample(payload["callback_url"], str(payload["callback_body"]).encode("utf-8"))

    assert validation["ok"] is True


def test_generated_callback_sample_round_trips_through_inbox_worker(monkeypatch) -> None:
    _set_callback_env(monkeypatch)
    payload = generator.run(
        [
            "--callback-base-url",
            "http://127.0.0.1:5002/wecom/external-contact/callback",
            "--timestamp",
            str(int(time.time())),
            "--nonce",
            "nonce-a",
            "--external-userid",
            "wm-a",
            "--user-id",
            "sales-a",
            "--state",
            "scene-a",
            "--print-body",
        ]
    )
    callback_url = str(payload["callback_url"])
    callback_body = str(payload["callback_body"]).encode("utf-8")
    parsed = urlsplit(callback_url)
    query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}

    event_data, plain_xml = channel_application.decrypt_callback_body(query=query, body=callback_body)
    repo = InMemoryWebhookInboxRepository()
    ingest_result = ingest_wecom_callback(
        query=query,
        headers={"Authorization": "secret", "X-Request-Id": "rid-1"},
        body=callback_body,
        event_data=event_data,
        plain_xml=plain_xml,
        route=parsed.path,
        repository=repo,
    )
    processed: list[ProcessWeComExternalContactEventCommand] = []

    def processor(command: ProcessWeComExternalContactEventCommand) -> dict:
        processed.append(command)
        return {
            "handled": False,
            "event_log": {"id": 77},
            "identity_sync": {"status": "skipped", "reason": "non_entry_change_type"},
            "entry_result": {"baseline_effects": {}},
        }

    worker_result = WeComCallbackInboxWorker(repo, processor=processor).run_due(limit=10, dry_run=False)

    assert ingest_result["ok"] is True
    assert ingest_result["duplicate"] is False
    assert ingest_result["idempotency_key"] == payload["idempotency_key"]
    assert worker_result["claimed_count"] == 1
    assert worker_result["succeeded_count"] == 1
    assert len(processed) == 1
    assert processed[0].event_data["ChangeType"] == "del_external_contact"
    assert processed[0].route == "/wecom/external-contact/callback"
    assert repo.rows[0]["status"] == "succeeded"
    assert repo.rows[0]["processing_summary_json"]["handled"] is False
    assert repo.rows[0]["processing_summary_json"]["event_log_id"] == 77
    assert repo.rows[0]["processing_summary_json"]["identity_sync_status"] == "skipped"
    assert repo.rows[0]["processing_summary_json"]["external_effect_job_ids"] == []
