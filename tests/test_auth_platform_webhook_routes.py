from __future__ import annotations

import json

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.external_effects import reset_external_effect_fixture_state
from tests.webhook_hmac_test_helpers import install_webhook_hmac_client, signed_headers


def test_group_ops_webhook_requires_registered_hmac_and_rejects_replay(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "shadow")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_group_ops_fixture_state()
    reset_external_effect_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    target = "/api/automation/group-ops/webhooks/daily-lesson-8f3a"
    credentials = install_webhook_hmac_client(
        client,
        capability="group_ops_webhook_receive",
        owner_scope={"webhook_key": ["daily-lesson-8f3a"]},
        client_id="pytest-group-ops-partner",
    )
    body = json.dumps(
        {
            "idempotency_key": "signed-group-ops-event",
            "send_mode": "queued",
            "content": {"text": "signed callback", "attachments": []},
        },
        separators=(",", ":"),
    ).encode()

    unsigned = client.post(target, content=body, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 401
    assert unsigned.json()["error"] == "webhook_signature_required"

    headers = signed_headers(credentials, body=body, event_id="signed-group-ops-event-0001")
    accepted = client.post(target, content=body, headers=headers)
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"

    replay = client.post(target, content=body, headers=headers)
    assert replay.status_code == 401
    assert replay.json()["error"] == "webhook_event_replayed"


def test_group_ops_webhook_hmac_context_is_bound_to_endpoint_resource(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_group_ops_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    target = "/api/automation/group-ops/webhooks/daily-lesson-8f3a"
    credentials = install_webhook_hmac_client(
        client,
        capability="group_ops_webhook_receive",
        owner_scope={"webhook_key": ["different-endpoint"]},
    )
    body = b'{"idempotency_key":"wrong-resource","content":{"text":"blocked"}}'

    response = client.post(target, content=body, headers=signed_headers(credentials, body=body))
    assert response.status_code == 403
    assert response.json()["error"] == "webhook_scope_or_capability_required"
