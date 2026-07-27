from __future__ import annotations

from typing import Any, Protocol


class ExternalPushDeliveryProjectionPort(Protocol):
    """Public owner port for the legacy external-push delivery projection."""

    def create_order_delivery_once_dbapi(
        self,
        executor: Any,
        *,
        tenant_id: str,
        config_id: int,
        event_type: str,
        target_id: str,
        order_id: int,
        product_id: int,
        request_url: str,
    ) -> dict[str, Any]: ...

    def create_test_delivery_dbapi(
        self,
        executor: Any,
        *,
        tenant_id: str,
        config_id: int,
        target_id: str,
        product_id: int,
        request_url: str,
    ) -> dict[str, Any]: ...

    def update_delivery_result_dbapi(
        self,
        executor: Any,
        *,
        delivery_id: str,
        status: str,
        attempt_count: int,
        request_url: str,
        request_headers: dict[str, Any],
        request_body: dict[str, Any],
        response_status: int | None,
        response_body: str,
        error_message: str,
        next_retry_at: str | None,
    ) -> dict[str, Any]: ...

    def reconcile_succeeded_dbapi(
        self,
        executor: Any,
        *,
        delivery_id: str,
        external_effect_job_id: int,
        response_status: int | None,
        actor_hash: str,
        reason_hash: str,
    ) -> bool: ...


def build_external_push_delivery_projection_port() -> ExternalPushDeliveryProjectionPort:
    from .repo import DBAPIExternalPushDeliveryProjectionRepository

    return DBAPIExternalPushDeliveryProjectionRepository()


__all__ = [
    "ExternalPushDeliveryProjectionPort",
    "build_external_push_delivery_projection_port",
]
