from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.platform.shared.repository_provider import RepositoryProviderError

from .agent_runs import agent_run_projection, normalize_agent_run_filters
from .repo import InMemoryAutomationRepository


def _as_mapping(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping or row)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_loads(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SqlAlchemyAgentRunRepository(InMemoryAutomationRepository):
    """Explicit test/staging DB adapter for agent-run metadata reads only."""

    source_status = "sql_alchemy_agent_run_repository"

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def list_agent_runs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        normalized = normalize_agent_run_filters(filters)
        clauses, params = self._filter_clauses(normalized)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params_with_page = {
            **params,
            "limit": normalized["page_size"],
            "offset": normalized["offset"],
        }
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM automation_agent_runs
                        {where}
                        ORDER BY started_at DESC, run_id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params_with_page,
                ).fetchall()
                total = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS total
                        FROM automation_agent_runs
                        {where}
                        """
                    ),
                    params,
                ).scalar_one()
            projected = [
                self._row_to_projection(_as_mapping(row) or {}, visibility=normalized["visibility"])
                for row in rows
            ]
            return projected, int(total or 0), normalized
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"agent-run repository unavailable: {exc}") from exc

    def get_agent_run(self, run_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized = normalize_agent_run_filters(filters)
        clauses, params = self._filter_clauses(normalized)
        clauses.append("run_id = :run_id")
        params["run_id"] = str(run_id or "").strip()
        where = f"WHERE {' AND '.join(clauses)}"
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM automation_agent_runs
                        {where}
                        LIMIT 1
                        """
                    ),
                    params,
                ).fetchone()
            mapping = _as_mapping(row)
            if mapping is None:
                return None
            return self._row_to_projection(mapping, visibility=normalized["visibility"])
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"agent-run repository unavailable: {exc}") from exc

    def _filter_clauses(self, filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for field in (
            "request_id",
            "run_id",
            "agent_code",
            "run_status",
            "trigger_source",
            "unionid",
            "userid",
        ):
            value = str(filters.get(field) or "").strip()
            if value:
                clauses.append(f"{field} = :{field}")
                params[field] = value
        if filters.get("started_after"):
            clauses.append("started_at >= :started_after")
            params["started_after"] = str(filters["started_after"])
        if filters.get("started_before"):
            clauses.append("started_at <= :started_before")
            params["started_before"] = str(filters["started_before"])
        has_error = filters.get("has_error")
        if has_error is not None:
            if _truthy(has_error):
                clauses.append("((error_code IS NOT NULL AND error_code != '') OR (error_message IS NOT NULL AND error_message != ''))")
            else:
                clauses.append("((error_code IS NULL OR error_code = '') AND (error_message IS NULL OR error_message = ''))")
        return clauses, params

    def _row_to_projection(self, row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
        return agent_run_projection(
            {
                "id": row.get("id") or row.get("run_id"),
                "run_id": row.get("run_id") or row.get("id"),
                "request_id": row.get("request_id"),
                "agent_code": row.get("agent_code"),
                "run_status": row.get("run_status") or "completed",
                "trigger_source": row.get("trigger_source") or "fixture",
                "unionid": row.get("unionid"),
                "userid": row.get("userid"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "duration_ms": row.get("duration_ms"),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
                "output_count": row.get("output_count"),
                "metadata": _json_loads(row.get("metadata_json") if "metadata_json" in row else row.get("metadata")),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            },
            visibility=visibility,
        )
