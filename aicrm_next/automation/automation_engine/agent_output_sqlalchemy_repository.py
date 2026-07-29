from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.platform.shared.repository_provider import RepositoryProviderError

from .agent_outputs import agent_output_projection, normalize_agent_output_filters
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


class SqlAlchemyAgentOutputRepository(InMemoryAutomationRepository):
    """Explicit test/staging DB adapter for agent-output metadata reads only."""

    source_status = "sql_alchemy_agent_output_repository"

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def list_agent_outputs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        normalized = normalize_agent_output_filters(filters)
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
                        FROM automation_agent_outputs
                        {where}
                        ORDER BY created_at DESC, output_id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params_with_page,
                ).fetchall()
                total = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS total
                        FROM automation_agent_outputs
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
            raise RepositoryProviderError(f"agent-output repository unavailable: {exc}") from exc

    def get_agent_output(self, output_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized = normalize_agent_output_filters(filters)
        clauses, params = self._filter_clauses(normalized)
        clauses.append("output_id = :output_id")
        params["output_id"] = str(output_id or "").strip()
        where = f"WHERE {' AND '.join(clauses)}"
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM automation_agent_outputs
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
            raise RepositoryProviderError(f"agent-output repository unavailable: {exc}") from exc

    def _filter_clauses(self, filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for field in ("request_id", "unionid", "userid", "agent_code", "output_type", "applied_status"):
            value = str(filters.get(field) or "").strip()
            if value:
                clauses.append(f"{field} = :{field}")
                params[field] = value
        min_confidence = filters.get("min_confidence")
        if min_confidence is not None:
            clauses.append("confidence >= :min_confidence")
            params["min_confidence"] = float(min_confidence)
        max_confidence = filters.get("max_confidence")
        if max_confidence is not None:
            clauses.append("confidence <= :max_confidence")
            params["max_confidence"] = float(max_confidence)
        has_error = filters.get("has_error")
        if has_error is not None:
            if _truthy(has_error):
                clauses.append("((error_code IS NOT NULL AND error_code != '') OR (error_message IS NOT NULL AND error_message != ''))")
            else:
                clauses.append("((error_code IS NULL OR error_code = '') AND (error_message IS NULL OR error_message = ''))")
        return clauses, params

    def _row_to_projection(self, row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
        return agent_output_projection(
            {
                "id": row.get("id") or row.get("output_id"),
                "output_id": row.get("output_id") or row.get("id"),
                "run_id": row.get("run_id"),
                "request_id": row.get("request_id"),
                "userid": row.get("userid"),
                "unionid": row.get("unionid"),
                "agent_code": row.get("agent_code"),
                "output_type": row.get("output_type") or "metadata",
                "rendered_output_text": row.get("rendered_output_text"),
                "target_agent_code": row.get("target_agent_code"),
                "target_pool": row.get("target_pool"),
                "confidence": row.get("confidence"),
                "reason": row.get("reason"),
                "need_human_review": row.get("need_human_review"),
                "applied_status": row.get("applied_status") or "draft",
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
                "created_at": row.get("created_at"),
            },
            visibility=visibility,
        )
