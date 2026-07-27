from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class AdminAuditRecord:
    operator: str
    action_type: str
    target_type: str
    target_id: str
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None


class AdminAuditPort(Protocol):
    """Public owner boundary for durable administrator operation audit records."""

    def append_dbapi(self, executor: Any, *, record: AdminAuditRecord) -> None: ...

    def append_sqlalchemy(
        self,
        executor: Any,
        *,
        record: AdminAuditRecord,
        dialect_name: str = "postgresql",
    ) -> None: ...


def build_admin_audit_port() -> AdminAuditPort:
    from .repository import PostgresAdminAuditRepository

    return PostgresAdminAuditRepository()


__all__ = ["AdminAuditPort", "AdminAuditRecord", "build_admin_audit_port"]
