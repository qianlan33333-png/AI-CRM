from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from aicrm_next.platform.platform_foundation.auth_platform.models import WebhookClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.webhook_hmac import WebhookHmacSigner, WebhookHmacVerifier


class InMemoryWebhookRepository:
    def __init__(self, client: WebhookClientRecord) -> None:
        self.client = client
        self.events: set[tuple[str, str]] = set()

    def webhook_client(self, client_id: str) -> WebhookClientRecord | None:
        return self.client if client_id == self.client.client_id else None

    def consume_webhook_event(self, *, client_id: str, event_id_hash: str, expires_at: datetime) -> bool:
        del expires_at
        key = (client_id, event_id_hash)
        if key in self.events:
            return False
        self.events.add(key)
        return True


@dataclass(frozen=True)
class WebhookHmacCredentials:
    client_id: str
    secret: str


def install_webhook_hmac_client(
    client: TestClient,
    *,
    capability: str,
    owner_scope: dict[str, Any] | None = None,
    client_id: str = "pytest-webhook-client",
) -> WebhookHmacCredentials:
    secret = "pytest-webhook-secret-material-at-least-32-bytes"
    record = WebhookClientRecord(
        client_id=client_id,
        principal_id=f"api_client:{client_id}",
        display_name="Pytest webhook",
        secret_reference="secretref:file:PYTEST:v1_test",
        capabilities=(capability,),
        allowed_cidrs=(),
        corp_id="corp-pytest",
        owner_scope=owner_scope or {},
        auth_version=1,
        enabled=True,
    )
    repository = InMemoryWebhookRepository(record)
    client.app.state.auth_webhook_verifier = WebhookHmacVerifier(repository, secret_resolver=lambda _reference: secret)
    return WebhookHmacCredentials(client_id=client_id, secret=secret)


def signed_headers(
    credentials: WebhookHmacCredentials,
    *,
    body: bytes,
    event_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    headers = WebhookHmacSigner(client_id=credentials.client_id, secret=credentials.secret).sign_headers(
        body=body,
        event_id=event_id or f"pytest-event-{uuid4().hex}",
        now=now,
    )
    return {"Content-Type": "application/json", **headers}


def outbound_webhook_hmac_signer(client_id: str = "pytest-aicrm-outbound") -> WebhookHmacSigner:
    return WebhookHmacSigner(client_id=client_id, secret="pytest-outbound-webhook-secret-material-at-least-32-bytes")
