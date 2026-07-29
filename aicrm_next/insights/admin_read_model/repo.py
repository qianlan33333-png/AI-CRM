from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.platform.shared.runtime import (
    database_mode,
    production_data_ready,
    raw_database_url,
    runtime_health_state,
)
from aicrm_next.platform.shared.repository_provider import assert_repository_allowed

from .dto import AdminReadDiagnostics
from .errors import AdminReadModelError


class AdminReadRepository(Protocol):
    source_status: str

    @property
    def is_production(self) -> bool: ...

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]: ...

    def count(self, table: str) -> int: ...

    def runtime_health(self) -> dict[str, Any]: ...

    def diagnostics(self) -> AdminReadDiagnostics: ...


def _database_url() -> str:
    return raw_database_url()


class PostgresAdminReadRepository:
    source_status = "production_postgres"

    @property
    def is_production(self) -> bool:
        return True

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise AdminReadModelError("psycopg is required for production admin read model") from exc
        try:
            with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            raise AdminReadModelError(str(exc)) from exc

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = self.rows(query, params)
        return rows[0] if rows else {}

    def count(self, table: str) -> int:
        allowed_tables = {
            "contacts",
            "questionnaire_submissions",
            "wechat_pay_orders",
            "ai_audience_member_current",
            "internal_event",
            "external_effect_job",
            "automation_agent_config",
            "automation_agent_run",
            "automation_agent_output",
            "automation_agent_llm_call_log",
            "archived_messages",
            "sync_runs",
            "outbound_tasks",
            "outbound_webhook_deliveries",
            "reply_message_batch",
            "wecom_external_contact_event_logs",
            "broadcast_jobs",
            "admin_operation_logs",
        }
        if table not in allowed_tables:
            raise AdminReadModelError(f"count table is not allowed: {table}")
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise AdminReadModelError("psycopg is required for production admin read model") from exc
        try:
            with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass(%s) AS table_oid", (table,))
                    exists = cur.fetchone()
                    if not exists or not exists.get("table_oid"):
                        return 0
                    cur.execute(sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table)))
                    row = cur.fetchone()
                    return int((row or {}).get("count") or 0)
        except Exception as exc:
            raise AdminReadModelError(str(exc)) from exc

    def runtime_health(self) -> dict[str, Any]:
        return runtime_health_state()

    def diagnostics(self) -> AdminReadDiagnostics:
        return AdminReadDiagnostics(source_status=self.source_status, details={"database_mode": database_mode()})


class LocalContractAdminReadRepository:
    source_status = "local_contract_probe"

    @property
    def is_production(self) -> bool:
        return False

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return []

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        return {}

    def count(self, table: str) -> int:
        return 0

    def runtime_health(self) -> dict[str, Any]:
        return runtime_health_state()

    def diagnostics(self) -> AdminReadDiagnostics:
        return AdminReadDiagnostics(source_status=self.source_status, details={"database_mode": database_mode()})


def build_admin_read_repository() -> AdminReadRepository:
    if production_data_ready():
        return assert_repository_allowed(PostgresAdminReadRepository(), capability_owner="admin_read_model")
    return assert_repository_allowed(LocalContractAdminReadRepository(), capability_owner="admin_read_model")
