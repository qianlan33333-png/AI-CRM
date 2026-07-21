from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import secrets
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from aicrm_next.shared.secret_store import FileSecretStore, is_secret_reference

from .context import AuthContext, PrincipalType
from .models import WebhookClientRecord


WEBHOOK_WINDOW = timedelta(minutes=5)
FUTURE_CLOCK_SKEW = timedelta(seconds=60)


class WebhookRepository(Protocol):
    def webhook_client(self, client_id: str) -> WebhookClientRecord | None: ...

    def consume_webhook_event(self, *, client_id: str, event_id_hash: str, expires_at: datetime) -> bool: ...


SecretResolver = Callable[[str], str]


@dataclass(frozen=True)
class WebhookVerification:
    ok: bool
    context: AuthContext | None = None
    error: str = ""


class WebhookHmacVerifier:
    def __init__(self, repository: WebhookRepository, *, secret_resolver: SecretResolver | None = None) -> None:
        self.repository = repository
        self._resolve_secret = secret_resolver or _resolve_secret

    def verify(
        self,
        *,
        headers: Mapping[str, Any],
        body: bytes,
        capability: str,
        source_ip: str = "",
        request_id: str = "",
        now: datetime | None = None,
    ) -> WebhookVerification:
        normalized = {str(key).lower(): str(value).strip() for key, value in headers.items()}
        client_id = normalized.get("x-aicrm-client-id", "")
        timestamp_text = normalized.get("x-aicrm-timestamp", "")
        event_id = normalized.get("x-aicrm-event-id", "")
        signature = normalized.get("x-aicrm-signature", "")
        if not all((client_id, timestamp_text, event_id, signature)):
            return WebhookVerification(ok=False, error="webhook_signature_required")
        try:
            timestamp = datetime.fromtimestamp(int(timestamp_text), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return WebhookVerification(ok=False, error="invalid_webhook_timestamp")
        current = _utc(now or datetime.now(timezone.utc))
        if timestamp > current + FUTURE_CLOCK_SKEW or current - timestamp > WEBHOOK_WINDOW:
            return WebhookVerification(ok=False, error="webhook_signature_expired")
        if len(event_id) < 16 or len(event_id) > 256:
            return WebhookVerification(ok=False, error="invalid_webhook_event_id")
        client = self.repository.webhook_client(client_id)
        if client is None or not client.enabled or capability not in client.capabilities:
            return WebhookVerification(ok=False, error="invalid_webhook_client")
        if not _ip_allowed(source_ip, client.allowed_cidrs):
            return WebhookVerification(ok=False, error="webhook_client_ip_not_allowed")
        try:
            secret = self._resolve_secret(client.secret_reference)
        except Exception:
            return WebhookVerification(ok=False, error="webhook_secret_unavailable")
        expected = hmac.new(secret.encode("utf-8"), canonical_webhook_message(timestamp_text, event_id, body), hashlib.sha256).hexdigest()
        supplied = signature.removeprefix("sha256=").strip().lower()
        if len(supplied) != 64 or not hmac.compare_digest(supplied, expected):
            return WebhookVerification(ok=False, error="invalid_webhook_signature")
        event_hash = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        if not self.repository.consume_webhook_event(
            client_id=client.client_id,
            event_id_hash=event_hash,
            expires_at=timestamp + WEBHOOK_WINDOW,
        ):
            return WebhookVerification(ok=False, error="webhook_event_replayed")
        return WebhookVerification(
            ok=True,
            context=AuthContext(
                principal_type=PrincipalType.API_CLIENT,
                principal_id=client.principal_id,
                client_id=client.client_id,
                corp_id=client.corp_id,
                scopes=("webhook.write",),
                capabilities=client.capabilities,
                owner_scope=client.owner_scope,
                auth_version=client.auth_version,
                request_id=str(request_id or event_id),
            ),
        )


@dataclass(frozen=True)
class WebhookHmacSigner:
    client_id: str
    secret: str

    def __post_init__(self) -> None:
        if not str(self.client_id or "").strip() or len(str(self.secret or "").encode("utf-8")) < 32:
            raise ValueError("webhook HMAC signer configuration is invalid")

    def sign_headers(
        self,
        *,
        body: bytes,
        event_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, str]:
        timestamp = str(int(_utc(now or datetime.now(timezone.utc)).timestamp()))
        actual_event_id = str(event_id or f"evt_{uuid4().hex}").strip()
        if len(actual_event_id) < 16 or len(actual_event_id) > 256:
            raise ValueError("webhook event_id length is invalid")
        signature = hmac.new(
            self.secret.encode("utf-8"),
            canonical_webhook_message(timestamp, actual_event_id, body),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-AICRM-Client-Id": self.client_id,
            "X-AICRM-Timestamp": timestamp,
            "X-AICRM-Event-Id": actual_event_id,
            "X-AICRM-Signature": f"sha256={signature}",
        }


def canonical_webhook_message(timestamp: str, event_id: str, body: bytes) -> bytes:
    return str(timestamp).encode("ascii") + b"\n" + str(event_id).encode("utf-8") + b"\n" + bytes(body or b"")


def issue_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(48)


def runtime_outbound_webhook_signer() -> WebhookHmacSigner | None:
    from aicrm_next.shared.runtime_settings import runtime_setting

    from .repository import PostgresAuthRepository

    client_id = runtime_setting("AICRM_AUTH_OUTBOUND_WEBHOOK_CLIENT_ID")
    if not client_id:
        return None
    client = PostgresAuthRepository().webhook_client(client_id)
    if client is None or not client.enabled:
        return None
    try:
        return WebhookHmacSigner(client_id=client.client_id, secret=_resolve_secret(client.secret_reference))
    except Exception:
        return None


def runtime_outbound_webhook_signer_ready() -> bool:
    return runtime_outbound_webhook_signer() is not None


def _resolve_secret(reference: str) -> str:
    if not is_secret_reference(reference):
        raise ValueError("webhook secret must be stored as a secret reference")
    value = FileSecretStore.from_environment().read(reference).strip()
    if len(value.encode("utf-8")) < 32:
        raise ValueError("webhook secret is invalid")
    return value


def _ip_allowed(source_ip: str, allowed_cidrs: tuple[str, ...]) -> bool:
    if not allowed_cidrs:
        return True
    try:
        address = ipaddress.ip_address(str(source_ip or "").strip())
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("webhook timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
