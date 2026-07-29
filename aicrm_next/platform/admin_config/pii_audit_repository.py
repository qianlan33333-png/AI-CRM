from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.platform.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)
from aicrm_next.platform.shared.db_session import get_engine
from aicrm_next.platform.shared.pii_audit import PiiAuditEvent


class PiiAuditWriteError(RuntimeError):
    pass


class AdminConfigPiiAuditRepository:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine

    def record_pii_access(self, event: PiiAuditEvent) -> None:
        engine = self._engine or get_engine()
        try:
            with engine.begin() as connection:
                build_admin_audit_port().append_sqlalchemy(
                    connection,
                    dialect_name=engine.dialect.name,
                    record=AdminAuditRecord(
                        operator=event.actor_fingerprint,
                        action_type="pii_access",
                        target_type=event.route_name,
                        target_id=event.resource_fingerprint,
                        before={},
                        after=event.to_dict(),
                    ),
                )
        except SQLAlchemyError:
            raise PiiAuditWriteError("PII audit persistence failed") from None
