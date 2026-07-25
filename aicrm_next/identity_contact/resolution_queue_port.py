from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class EnqueueIdentityResolutionRequest:
    source_type: str
    source_key: str
    reason: str
    source_route: str
    corp_id: str = ""
    external_userid: str = ""
    openid: str = ""
    mobile: str = ""
    payload_json: dict[str, Any] = field(default_factory=dict)
    parent_execution_id: str = ""


class IdentityResolutionQueuePort(Protocol):
    """Public owner port for durable unresolved-identity intents."""

    def enqueue_dbapi(
        self,
        connection: Any,
        request: EnqueueIdentityResolutionRequest,
    ) -> dict[str, Any]: ...

    def enqueue_sqlalchemy(
        self,
        session: Any,
        request: EnqueueIdentityResolutionRequest,
    ) -> dict[str, Any]: ...


def build_identity_resolution_queue_port() -> IdentityResolutionQueuePort:
    from .resolution_queue_repository import PostgresIdentityResolutionQueueRepository

    return PostgresIdentityResolutionQueueRepository()


__all__ = [
    "EnqueueIdentityResolutionRequest",
    "IdentityResolutionQueuePort",
    "build_identity_resolution_queue_port",
]
