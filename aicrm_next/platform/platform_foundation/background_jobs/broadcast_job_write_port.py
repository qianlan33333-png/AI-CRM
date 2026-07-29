from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


ExecuteReturning = Callable[[str, tuple[Any, ...]], dict[str, Any] | None]


@dataclass(frozen=True)
class BroadcastJobCreate:
    source_type: str
    source_id: str
    source_table: str
    scheduled_for: Any
    priority: int
    batch_key: str
    idempotency_key: str
    target_unionids: tuple[str, ...]
    target_summary: str
    content_type: str
    content_payload: dict[str, Any]
    content_summary: str
    trace_id: str
    created_by: str
    business_domain: str = ""
    channel: str = ""
    target_kind: str = ""
    status: str = "queued"
    requires_approval: bool = False
    retry_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    execution_id: str = ""


class BroadcastJobWritePort(Protocol):
    """Single public mutation boundary for the broadcast job fact."""

    def create_dbapi(self, executor: Any, request: BroadcastJobCreate) -> dict[str, Any] | None: ...

    def set_execution_id_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        execution_id: str,
    ) -> None: ...

    def delegate_external_effect_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        external_effect_job_id: int,
        trace_id: str,
        actor: str,
    ) -> dict[str, Any] | None: ...

    def cancel_campaign_jobs_dbapi(
        self,
        executor: Any,
        *,
        campaign_ids: Sequence[int],
        actor: str,
        reason: str,
        source_type: str,
        source_table: str,
        open_statuses: Sequence[str],
    ) -> list[int]: ...

    def ack_message_batch_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        batch_id: int,
        ack_note: str,
        acked_by: str,
    ) -> dict[str, Any] | None: ...

    def approve_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        job_id: int,
        approved_by: str,
    ) -> dict[str, Any] | None: ...

    def cancel_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        job_id: int,
        cancelled_by: str,
        reason: str,
    ) -> dict[str, Any] | None: ...

    def claim_due_dbapi(
        self,
        executor: Any,
        *,
        limit: int,
        now: Any,
        claim_token: str,
        lease_expires_at: Any,
    ) -> list[dict[str, Any]]: ...

    def begin_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        now: Any,
    ) -> dict[str, Any] | None: ...

    def finalize_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        final_status: str,
        outbound_task_id: int | None,
        sent_count: int,
        failed_count: int,
        failure_type: str,
        error_text: str,
        side_effect_executed: bool,
        provider_result_received: bool,
        result_summary_json: str,
        reconciliation_required: bool,
        external_effect_job_id: int | None,
        retry_delay_seconds: int,
    ) -> dict[str, Any] | None: ...

    def mark_unknown_after_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        error_text: str,
        side_effect_executed: bool,
        provider_result_received: bool,
        result_summary_json: str,
    ) -> dict[str, Any] | None: ...

    def settle_from_external_effect_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        status: str,
        sent_count: int,
        failed_count: int,
        side_effect_executed: bool,
        provider_result_received: bool,
        reconciliation_required: bool,
        result_summary_json: str,
        last_error: str,
    ) -> None: ...


def build_broadcast_job_write_port() -> BroadcastJobWritePort:
    from .broadcast_job_write_repository import PostgresBroadcastJobWriteRepository

    return PostgresBroadcastJobWriteRepository()


__all__ = [
    "BroadcastJobCreate",
    "BroadcastJobWritePort",
    "build_broadcast_job_write_port",
]
