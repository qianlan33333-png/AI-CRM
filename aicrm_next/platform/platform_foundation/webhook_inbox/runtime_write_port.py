from __future__ import annotations

from typing import Any, Protocol


class WebhookInboxRuntimeWritePort(Protocol):
    """Public owner boundary for execution-runtime mutations of webhook rows."""

    def make_eligible_now_sqlalchemy(
        self,
        executor: Any,
        *,
        item_id: int,
        expected_status: str,
        expected_version: str,
    ) -> dict[str, Any] | None: ...

    def manual_action_sqlalchemy(
        self,
        executor: Any,
        *,
        action: str,
        item_id: int,
        expected_status: str,
        expected_version: str,
        reason: str,
    ) -> dict[str, Any] | None: ...

    def claim_dbapi(
        self,
        executor: Any,
        *,
        lane: str,
        generation: int,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None: ...

    def recover_expired_dbapi(self, executor: Any, *, lane: str) -> None: ...

    def renew_lease_dbapi(
        self,
        executor: Any,
        *,
        item_id: int,
        lease_token: str,
        generation: int,
        lease_seconds: int,
    ) -> bool: ...


def build_webhook_inbox_runtime_write_port() -> WebhookInboxRuntimeWritePort:
    from .runtime_write_repository import PostgresWebhookInboxRuntimeWriteRepository

    return PostgresWebhookInboxRuntimeWriteRepository()


__all__ = [
    "WebhookInboxRuntimeWritePort",
    "build_webhook_inbox_runtime_write_port",
]
