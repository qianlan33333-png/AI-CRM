from __future__ import annotations

from typing import Any, Protocol


class InternalEventConsumerRunWritePort(Protocol):
    """Public owner boundary for internal-event consumer queue mutations."""

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
        actor_ref_hash: str,
        reason: str,
        attempt_id: str,
    ) -> dict[str, Any] | None: ...

    def quarantine_superseded_signal_owner_sqlalchemy(
        self,
        executor: Any,
        *,
        tenant_id: str,
        idempotency_key: str,
        consumer_name: str,
    ) -> list[dict[str, Any]]: ...

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


def build_internal_event_consumer_run_write_port() -> InternalEventConsumerRunWritePort:
    from .consumer_run_write_repository import PostgresInternalEventConsumerRunWriteRepository

    return PostgresInternalEventConsumerRunWriteRepository()


__all__ = [
    "InternalEventConsumerRunWritePort",
    "build_internal_event_consumer_run_write_port",
]
