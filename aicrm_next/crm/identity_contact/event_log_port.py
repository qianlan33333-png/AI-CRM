from __future__ import annotations

from typing import Any, Callable, Protocol


class IdentityEventLogPort(Protocol):
    """Public query/command port for the external-contact callback audit log."""

    def log_external_contact_event(
        self,
        *,
        corp_id: str,
        event_type: str,
        change_type: str,
        external_userid: str,
        user_id: str,
        event_time: int,
        event_key: str,
        payload_xml: str,
        payload_json: dict[str, Any],
    ) -> dict[str, Any]: ...

    def get_external_contact_event_log(self, event_log_id: int) -> dict[str, Any] | None: ...

    def mark_event_status(self, event_log_id: int, status: str, error_message: str = "") -> None: ...

    def record_identity_sync_result(
        self,
        event_log_id: int,
        *,
        status: str,
        error_code: str = "",
        error_message: str = "",
        response_json: dict[str, Any] | None = None,
    ) -> None: ...

    def list_recent_events(self, scene_value: str, limit: int = 20) -> list[dict[str, Any]]: ...


def build_identity_event_log_port(
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> IdentityEventLogPort:
    from .event_log_repository import PostgresIdentityEventLogRepository

    return PostgresIdentityEventLogRepository(connection_factory=connection_factory)


__all__ = ["IdentityEventLogPort", "build_identity_event_log_port"]
