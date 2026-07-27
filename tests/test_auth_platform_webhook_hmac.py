from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aicrm_next.platform.platform_foundation.auth_platform.models import WebhookClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.webhook_hmac import WebhookHmacSigner, WebhookHmacVerifier
from tests.webhook_hmac_test_helpers import InMemoryWebhookRepository


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
SECRET = "webhook-test-secret-material-at-least-32-bytes"


def _verifier(*, capabilities=("group_ops_webhook_receive",), cidrs=()):
    client = WebhookClientRecord(
        client_id="group-ops-partner",
        principal_id="api_client:group-ops-partner",
        display_name="Group Ops Partner",
        secret_reference="secretref:file:PARTNER:v1_test",
        capabilities=capabilities,
        allowed_cidrs=cidrs,
        corp_id="corp-test",
        owner_scope={"webhook_key": ["partner-hook"]},
        auth_version=1,
        enabled=True,
    )
    return WebhookHmacVerifier(InMemoryWebhookRepository(client), secret_resolver=lambda _reference: SECRET)


def test_webhook_hmac_binds_timestamp_event_id_raw_body_and_rejects_replay() -> None:
    body = b'{"event":"member_entered"}'
    headers = WebhookHmacSigner(client_id="group-ops-partner", secret=SECRET).sign_headers(
        body=body,
        event_id="partner-event-00000001",
        now=NOW,
    )
    verifier = _verifier()

    verified = verifier.verify(
        headers=headers,
        body=body,
        capability="group_ops_webhook_receive",
        request_id="req-1",
        now=NOW,
    )
    assert verified.ok
    assert verified.context is not None
    assert verified.context.client_id == "group-ops-partner"
    assert verified.context.owner_scope == {"webhook_key": ["partner-hook"]}

    replay = verifier.verify(
        headers=headers,
        body=body,
        capability="group_ops_webhook_receive",
        now=NOW,
    )
    assert replay.error == "webhook_event_replayed"


def test_webhook_hmac_rejects_tampering_expiry_wrong_capability_and_cidr() -> None:
    headers = WebhookHmacSigner(client_id="group-ops-partner", secret=SECRET).sign_headers(
        body=b"{}",
        event_id="partner-event-00000002",
        now=NOW,
    )
    assert (
        _verifier()
        .verify(
            headers=headers,
            body=b'{"tampered":true}',
            capability="group_ops_webhook_receive",
            now=NOW,
        )
        .error
        == "invalid_webhook_signature"
    )
    assert (
        _verifier()
        .verify(
            headers=headers,
            body=b"{}",
            capability="group_ops_webhook_receive",
            now=NOW + timedelta(minutes=6),
        )
        .error
        == "webhook_signature_expired"
    )
    assert (
        _verifier()
        .verify(headers=headers, body=b"{}", capability="automation_agent_webhook_receive", now=NOW)
        .error
        == "invalid_webhook_client"
    )
    assert (
        _verifier(cidrs=("203.0.113.0/24",))
        .verify(
            headers=headers,
            body=b"{}",
            capability="group_ops_webhook_receive",
            source_ip="198.51.100.4",
            now=NOW,
        )
        .error
        == "webhook_client_ip_not_allowed"
    )
