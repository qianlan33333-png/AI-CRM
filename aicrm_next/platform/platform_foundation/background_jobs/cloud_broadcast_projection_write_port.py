from __future__ import annotations

from typing import Any, Protocol


class CloudBroadcastProjectionWritePort(Protocol):
    """Single mutation boundary for cloud-plan recipient delivery projections."""

    def mark_dispatching_dbapi(self, executor: Any, *, job_id: int) -> None: ...

    def mark_delegated_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
    ) -> None: ...

    def finalize_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        status: str,
        last_error: str,
    ) -> None: ...

    def mark_unknown_after_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        last_error: str,
    ) -> None: ...

    def settle_from_external_effect_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        recipient_status: str,
        message_status: str,
        last_error: str,
    ) -> None: ...

    def queue_planned_recipient_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        recipient_id: int,
        job_id: int,
        approved_by: str,
    ) -> None: ...

    def insert_campaign_preparation_projection_sqlalchemy(
        self,
        executor: Any,
        *,
        plan_id: str,
        preparation_id: str,
    ) -> None: ...

    def upsert_agent_recipient_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        unionid: str,
        owner_userid: str,
        display_name: str,
        approval_status: str,
    ) -> dict[str, Any] | None: ...

    def upsert_agent_message_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        recipient_id: int,
        unionid: str,
        content_text: str,
        content_payload_json: str,
    ) -> int: ...

    def approve_recipient_dbapi(
        self,
        executor: Any,
        *,
        recipient_id: int,
        approved_by: str,
        job_id: int | None,
    ) -> dict[str, Any] | None: ...

    def reject_recipient_dbapi(
        self,
        executor: Any,
        *,
        recipient_id: int,
        rejected_by: str,
        reason: str,
    ) -> dict[str, Any] | None: ...

    def update_recipient_message_dbapi(
        self,
        executor: Any,
        *,
        message_id: int,
        content_text: str,
        content_payload_json: str,
        day_offset: int,
        send_time: str,
    ) -> dict[str, Any] | None: ...


def build_cloud_broadcast_projection_write_port() -> CloudBroadcastProjectionWritePort:
    from .cloud_broadcast_projection_write_repository import (
        PostgresCloudBroadcastProjectionWriteRepository,
    )

    return PostgresCloudBroadcastProjectionWriteRepository()


__all__ = [
    "CloudBroadcastProjectionWritePort",
    "build_cloud_broadcast_projection_write_port",
]
