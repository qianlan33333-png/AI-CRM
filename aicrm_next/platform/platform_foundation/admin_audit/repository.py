from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import text

from .port import AdminAuditRecord


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _params(record: AdminAuditRecord) -> dict[str, str]:
    return {
        "operator": str(record.operator or "").strip(),
        "action_type": str(record.action_type or "").strip(),
        "target_type": str(record.target_type or "").strip(),
        "target_id": str(record.target_id or "").strip(),
        "before_json": _json(record.before),
        "after_json": _json(record.after),
    }


class PostgresAdminAuditRepository:
    """Sole SQL writer for admin_operation_logs; callers own transactions."""

    def append_dbapi(self, executor: Any, *, record: AdminAuditRecord) -> None:
        params = _params(record)
        executor.execute(
            """
            INSERT INTO admin_operation_logs (
                operator, action_type, target_type, target_id,
                before_json, after_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, CURRENT_TIMESTAMP)
            """,
            (
                params["operator"],
                params["action_type"],
                params["target_type"],
                params["target_id"],
                params["before_json"],
                params["after_json"],
            ),
        )

    def append_sqlalchemy(
        self,
        executor: Any,
        *,
        record: AdminAuditRecord,
        dialect_name: str = "postgresql",
    ) -> None:
        json_expression = "CAST(:{name} AS jsonb)" if dialect_name == "postgresql" else ":{name}"
        executor.execute(
            text(
                f"""
                INSERT INTO admin_operation_logs (
                    operator, action_type, target_type, target_id,
                    before_json, after_json, created_at
                )
                VALUES (
                    :operator, :action_type, :target_type, :target_id,
                    {json_expression.format(name="before_json")},
                    {json_expression.format(name="after_json")},
                    CURRENT_TIMESTAMP
                )
                """
            ),
            _params(record),
        )


__all__ = ["PostgresAdminAuditRepository"]
