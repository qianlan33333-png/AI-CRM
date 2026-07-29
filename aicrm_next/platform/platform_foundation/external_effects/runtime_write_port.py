from __future__ import annotations

from typing import Any, Protocol, Sequence


class ExternalEffectRuntimeWritePort(Protocol):
    """Public owner boundary for external-effect job and attempt mutations."""

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
        actor: str,
        reason: str,
    ) -> dict[str, Any] | None: ...

    def request_cancel_dispatching_sqlalchemy(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        exclude_job_id: int,
        actor: str,
        reason: str,
    ) -> list[int]: ...

    def cancel_pre_provider_sqlalchemy(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        exclude_job_id: int,
        actor: str,
        reason: str,
    ) -> list[dict[str, Any]]: ...

    def release_planned_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        payload_json: str,
        payload_summary_json: str,
        available_at_mode: str,
    ) -> int | None: ...

    def block_planned_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        error_code: str,
        error_message: str,
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
        test_only: bool,
    ) -> dict[str, Any] | None: ...

    def recover_expired_dbapi(
        self,
        executor: Any,
        *,
        lane: str,
    ) -> list[dict[str, Any]]: ...

    def renew_lease_dbapi(
        self,
        executor: Any,
        *,
        item_id: int,
        lease_token: str,
        generation: int,
        lease_seconds: int,
    ) -> bool: ...

    def adopt_pre_provider_dbapi(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        generation: int,
        source_policy_version: str,
        target_policy_version: str,
    ) -> list[int]: ...

    def apply_history_freeze_dbapi(
        self,
        executor: Any,
        *,
        freeze_revision: str,
        cutoff_at: Any,
    ) -> None: ...


def build_external_effect_runtime_write_port() -> ExternalEffectRuntimeWritePort:
    from .runtime_write_repository import PostgresExternalEffectRuntimeWriteRepository

    return PostgresExternalEffectRuntimeWriteRepository()


__all__ = [
    "ExternalEffectRuntimeWritePort",
    "build_external_effect_runtime_write_port",
]
