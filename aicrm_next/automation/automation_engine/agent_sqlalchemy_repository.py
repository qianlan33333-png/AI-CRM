from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError

from .repo import InMemoryAutomationRepository
from .agents import utc_now_iso
from .agents import AGENT_ROUTE_FAMILY, normalize_agent_create_payload, agent_projection, agent_side_effect_safety


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return deepcopy(default)
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return deepcopy(default)


def _request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _as_mapping(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping or row)


class SqlAlchemyAgentRepository(InMemoryAutomationRepository):
    """Explicit test/staging DB adapter for agent metadata only."""

    source_status = "sql_alchemy_agent_repository"

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def list_agents(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = filters or {}
        agent_type = str(filters.get("agent_type") or "").strip()
        status = str(filters.get("status") or "").strip()
        include_archived = bool(filters.get("include_archived"))
        limit = int(filters.get("limit") or 50)
        offset = int(filters.get("offset") or 0)
        try:
            with self._engine.connect() as conn:
                clauses = []
                params: dict[str, Any] = {"limit": limit, "offset": offset}
                if agent_type:
                    clauses.append("agent_type = :agent_type")
                    params["agent_type"] = agent_type
                if status:
                    clauses.append("status = :status")
                    params["status"] = status
                if not include_archived:
                    clauses.append("(archived_at IS NULL OR archived_at = '')")
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                rows = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM automation_agents
                        {where}
                        ORDER BY sort_order ASC, id ASC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params,
                ).fetchall()
                total = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS total
                        FROM automation_agents
                        {where}
                        """
                    ),
                    {key: value for key, value in params.items() if key not in {"limit", "offset"}},
                ).scalar_one()
            return [self._row_to_projection(_as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"agent repository unavailable: {exc}") from exc

    def create_agent(self, payload: dict[str, Any], *, idempotency_key: str, operator: str) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ContractError("idempotency_key is required")
        operator_id = str(operator or "system").strip() or "system"
        normalized = normalize_agent_create_payload({**payload, "operator": operator_id})
        request_hash = _request_hash(normalized)
        try:
            with self._engine.begin() as conn:
                replay = self._load_idempotency(conn, operator=operator_id, idempotency_key=key)
                if replay:
                    if replay.get("request_hash") != request_hash:
                        raise ContractError("idempotency key conflicts with a different request payload")
                    response = _json_loads(replay.get("response_snapshot"), {})
                    if isinstance(response, dict):
                        response["idempotent_replay"] = True
                        response["source_status"] = self.source_status
                        return response
                self._assert_unique_agent_sql(conn, normalized)
                self._insert_idempotency_pending(conn, operator=operator_id, idempotency_key=key, request_hash=request_hash)
                saved = self._insert_agent(conn, normalized)
                rollback_payload = {
                    "strategy": "archive_created_agent_in_later_approved_phase",
                    "created_agent_id": saved["id"],
                    "agent_code": saved["agent_code"],
                    "delete_approved": False,
                }
                audit_event = self._insert_audit_event(
                    conn,
                    operation="create",
                    operator=operator_id,
                    resource_id=int(saved["id"]),
                    before={},
                    after=saved,
                    request_payload=normalized,
                    rollback_payload=rollback_payload,
                )
                result = {
                    "source_status": self.source_status,
                    "agent": deepcopy(saved),
                    "agents": [deepcopy(saved)],
                    "audit_event": audit_event,
                    "rollback_payload": rollback_payload,
                    "idempotent_replay": False,
                }
                self._mark_idempotency_succeeded(
                    conn,
                    operator=operator_id,
                    idempotency_key=key,
                    resource_id=int(saved["id"]),
                    response_snapshot=result,
                )
                return deepcopy(result)
        except ContractError:
            raise
        except IntegrityError as exc:
            raise ContractError("agent code already exists for workflow") from exc
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"agent repository unavailable: {exc}") from exc

    def list_agent_audit_events(self) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_agent_audit_log
                        WHERE route_family = :route_family
                        ORDER BY created_at DESC, id DESC
                        LIMIT 50
                        """
                    ),
                    {"route_family": AGENT_ROUTE_FAMILY},
                ).fetchall()
            return [self._audit_row_to_event(_as_mapping(row) or {}) for row in rows]
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"agent repository unavailable: {exc}") from exc

    def _load_idempotency(self, conn: Any, *, operator: str, idempotency_key: str) -> dict[str, Any] | None:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM automation_agent_idempotency
                WHERE route_family = :route_family
                  AND operation = 'create'
                  AND operator = :operator
                  AND idempotency_key = :idempotency_key
                """
            ),
            {"route_family": AGENT_ROUTE_FAMILY, "operator": operator, "idempotency_key": idempotency_key},
        ).fetchone()
        return _as_mapping(row)

    def _insert_idempotency_pending(self, conn: Any, *, operator: str, idempotency_key: str, request_hash: str) -> None:
        conn.execute(
            text(
                """
                INSERT INTO automation_agent_idempotency (
                    route_family, operation, operator, idempotency_key, request_hash,
                    response_snapshot, resource_type, resource_id, status, created_at, updated_at
                )
                VALUES (
                    :route_family, 'create', :operator, :idempotency_key, :request_hash,
                    :response_snapshot, 'agent', NULL, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "route_family": AGENT_ROUTE_FAMILY,
                "operator": operator,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "response_snapshot": _json_dumps({}),
            },
        )

    def _mark_idempotency_succeeded(
        self,
        conn: Any,
        *,
        operator: str,
        idempotency_key: str,
        resource_id: int,
        response_snapshot: dict[str, Any],
    ) -> None:
        conn.execute(
            text(
                """
                UPDATE automation_agent_idempotency
                SET response_snapshot = :response_snapshot,
                    resource_id = :resource_id,
                    status = 'succeeded',
                    updated_at = CURRENT_TIMESTAMP
                WHERE route_family = :route_family
                  AND operation = 'create'
                  AND operator = :operator
                  AND idempotency_key = :idempotency_key
                """
            ),
            {
                "route_family": AGENT_ROUTE_FAMILY,
                "operator": operator,
                "idempotency_key": idempotency_key,
                "resource_id": resource_id,
                "response_snapshot": _json_dumps(response_snapshot),
            },
        )

    def _assert_unique_agent_sql(self, conn: Any, normalized: dict[str, Any]) -> None:
        row = conn.execute(
            text(
                """
                SELECT id
                FROM automation_agents
                WHERE LOWER(agent_code) = LOWER(:agent_code)
                LIMIT 1
                """
            ),
            {"agent_code": normalized["agent_code"]},
        ).fetchone()
        if row is not None:
            raise ContractError("agent code already exists")

    def _insert_agent(self, conn: Any, normalized: dict[str, Any]) -> dict[str, Any]:
        params = {
            "program_id": 0,
            "workflow_id": 0,
            "node_id": 0,
            "task_id": 0,
            "agent_code": normalized["agent_code"],
            "agent_name": normalized["agent_name"],
            "agent_type": normalized["agent_type"],
            "status": normalized["status"],
            "sort_order": int(normalized["sort_order"]),
            "metadata_json": _json_dumps(normalized.get("metadata") or {}),
            "config_json": _json_dumps(normalized.get("config") or {}),
            "enabled": bool(normalized["enabled"]),
            "created_by": normalized["created_by"],
            "updated_by": normalized["updated_by"],
        }
        result = conn.execute(
            text(
                """
                INSERT INTO automation_agents (
                    program_id, workflow_id, node_id, task_id, agent_code, agent_name,
                    agent_type, status, sort_order, metadata_json, config_json, enabled,
                    created_by, updated_by, created_at, updated_at
                )
                VALUES (
                    :program_id, :workflow_id, :node_id, :task_id, :agent_code, :agent_name,
                    :agent_type, :status, :sort_order, :metadata_json, :config_json, :enabled,
                    :created_by, :updated_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            params,
        )
        inserted_id = int(result.lastrowid or 0)
        if not inserted_id:
            inserted_id = int(conn.execute(text("SELECT MAX(id) FROM automation_agents")).scalar_one() or 0)
        row = conn.execute(text("SELECT * FROM automation_agents WHERE id = :id"), {"id": inserted_id}).fetchone()
        return self._row_to_projection(_as_mapping(row) or {})

    def _insert_audit_event(
        self,
        conn: Any,
        *,
        operation: str,
        operator: str,
        resource_id: int,
        before: dict[str, Any],
        after: dict[str, Any],
        request_payload: dict[str, Any],
        rollback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        conn.execute(
            text(
                """
                INSERT INTO automation_agent_audit_log (
                    route_family, operation, operator, resource_type, resource_id,
                    before_snapshot, after_snapshot, request_payload, validation_result,
                    rollback_payload, side_effect_safety, created_at
                )
                VALUES (
                    :route_family, :operation, :operator, 'agent', :resource_id,
                    :before_snapshot, :after_snapshot, :request_payload, :validation_result,
                    :rollback_payload, :side_effect_safety, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "route_family": AGENT_ROUTE_FAMILY,
                "operation": operation,
                "operator": operator,
                "resource_id": resource_id,
                "before_snapshot": _json_dumps(before),
                "after_snapshot": _json_dumps(after),
                "request_payload": _json_dumps(request_payload),
                "validation_result": _json_dumps({"ok": True}),
                "rollback_payload": _json_dumps(rollback_payload),
                "side_effect_safety": _json_dumps(agent_side_effect_safety()),
            },
        )
        row = conn.execute(text("SELECT * FROM automation_agent_audit_log WHERE id = (SELECT MAX(id) FROM automation_agent_audit_log)")).fetchone()
        return self._audit_row_to_event(_as_mapping(row) or {})

    def _row_to_projection(self, row: dict[str, Any]) -> dict[str, Any]:
        return agent_projection(
            {
                "id": row.get("id"),
                "agent_code": row.get("agent_code"),
                "agent_name": row.get("agent_name"),
                "agent_type": row.get("agent_type"),
                "status": row.get("status"),
                "sort_order": row.get("sort_order"),
                "metadata": _json_loads(row.get("metadata_json"), {}),
                "config": _json_loads(row.get("config_json"), {}),
                "enabled": row.get("enabled"),
                "created_by": row.get("created_by"),
                "updated_by": row.get("updated_by"),
                "created_at": str(row.get("created_at") or utc_now_iso()),
                "updated_at": str(row.get("updated_at") or utc_now_iso()),
                "archived_at": str(row.get("archived_at") or ""),
            }
        )

    def _audit_row_to_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "route_family": row.get("route_family") or AGENT_ROUTE_FAMILY,
            "operation": row.get("operation") or "",
            "operator": row.get("operator") or "",
            "resource_type": row.get("resource_type") or "agent",
            "resource_id": row.get("resource_id"),
            "before_snapshot": _json_loads(row.get("before_snapshot"), {}),
            "after_snapshot": _json_loads(row.get("after_snapshot"), {}),
            "request_payload": _json_loads(row.get("request_payload"), {}),
            "validation_result": _json_loads(row.get("validation_result"), {}),
            "rollback_payload": _json_loads(row.get("rollback_payload"), {}),
            "side_effect_safety": _json_loads(row.get("side_effect_safety"), agent_side_effect_safety()),
            "created_at": str(row.get("created_at") or utc_now_iso()),
            "external_event_dispatched": False,
        }
