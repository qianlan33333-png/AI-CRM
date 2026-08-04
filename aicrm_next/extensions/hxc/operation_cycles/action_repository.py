from __future__ import annotations

import hashlib
import json
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.runtime import raw_database_url

from .action_dto import (
    OperationCycleActionRequestView,
    OperationRunnerHeartbeatV1,
)
from .domain import OperationCycleConflictError
from .repository import DEFAULT_TENANT_ID


TERMINAL_STATUSES = frozenset({"completed", "failed"})
ACTIVE_STATUSES = frozenset({"queued", "claimed", "thread_bound", "turn_started"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return deepcopy(default)
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return deepcopy(default)


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _request_payload_hash(values: dict[str, Any]) -> str:
    # Creation time is server metadata, not part of the caller's idempotent
    # intent. A retry after a timeout must therefore compare equal even when it
    # reaches the service in a later clock tick.
    return _payload_hash({key: value for key, value in values.items() if key != "created_at"})


def _lease_token() -> str:
    return secrets.token_urlsafe(32)


def _lease_hash(token: str) -> str:
    return hashlib.sha256(_text(token).encode("utf-8")).hexdigest()


def _request_view(row: dict[str, Any]) -> OperationCycleActionRequestView:
    return OperationCycleActionRequestView(
        request_id=_text(row.get("request_id")),
        strategy_key=_text(row.get("strategy_key")),
        run_key=_text(row.get("run_key")),
        action_key=_text(row.get("action_key")),
        action_title=_text(row.get("action_title")),
        strategy_version=int(row.get("strategy_version") or 0),
        context_hash=_text(row.get("context_hash")),
        skill_key=_text(row.get("skill_key")),
        skill_hash=_text(row.get("skill_hash")),
        status=_text(row.get("status")) or "queued",
        parent_request_id=_text(row.get("parent_request_id")),
        thread_id=_text(row.get("thread_id")),
        turn_id=_text(row.get("turn_id")),
        final_result=_json(row.get("final_result_json"), None),
        failure_code=_text(row.get("failure_code")),
        created_at=row.get("created_at") or _utcnow(),
        updated_at=row.get("updated_at") or row.get("created_at") or _utcnow(),
        completed_at=row.get("completed_at"),
    )


class OperationCycleActionRepository(Protocol):
    def heartbeat(
        self,
        payload: OperationRunnerHeartbeatV1,
        *,
        principal_id: str,
        now: datetime,
    ) -> dict[str, Any]: ...

    def select_runner(
        self,
        required_bindings: list[str],
        *,
        now: datetime,
        offline_after_seconds: int,
    ) -> tuple[dict[str, Any] | None, str]: ...

    def create_request(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[OperationCycleActionRequestView, bool]: ...

    def get_request(self, request_id: str) -> OperationCycleActionRequestView | None: ...

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> OperationCycleActionRequestView | None: ...

    def list_strategy_requests(
        self,
        strategy_key: str,
        *,
        limit: int = 100,
    ) -> list[OperationCycleActionRequestView]: ...

    def claim(
        self,
        runner_id: str,
        *,
        principal_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> tuple[OperationCycleActionRequestView | None, str, datetime | None]: ...

    def event_is_replay(
        self,
        request_id: str,
        *,
        event_id: str,
        event_payload: dict[str, Any],
    ) -> bool: ...

    def apply_event(
        self,
        request_id: str,
        *,
        event_id: str,
        lease_token: str,
        event_type: str,
        event_payload: dict[str, Any],
        now: datetime,
    ) -> tuple[OperationCycleActionRequestView, bool]: ...


class InMemoryOperationCycleActionRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._runners: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, str] = {}
        self._events: dict[tuple[str, str], str] = {}

    def heartbeat(
        self,
        payload: OperationRunnerHeartbeatV1,
        *,
        principal_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        with self._lock:
            existing = self._runners.get(payload.runner_id)
            if existing is not None and _text(existing.get("principal_id")) != _text(principal_id):
                raise OperationCycleConflictError("runner_principal_mismatch")
            row = {
                **payload.model_dump(mode="json"),
                "principal_id": _text(principal_id),
                "last_heartbeat_at": now,
                "updated_at": now,
            }
            self._runners[payload.runner_id] = row
            return deepcopy(row)

    def select_runner(
        self,
        required_bindings: list[str],
        *,
        now: datetime,
        offline_after_seconds: int,
    ) -> tuple[dict[str, Any] | None, str]:
        threshold = now - timedelta(seconds=offline_after_seconds)
        rows = sorted(self._runners.values(), key=lambda row: row["last_heartbeat_at"], reverse=True)
        online = [row for row in rows if row["last_heartbeat_at"] >= threshold]
        if not online:
            return None, "runner_offline"
        compatible = [row for row in online if row.get("compatibility_status") == "ready"]
        if not compatible:
            return None, "runner_incompatible"
        if len(compatible) > 1:
            return None, "multiple_runners_online"
        runner = compatible[0]
        if not set(required_bindings).issubset(set(runner.get("binding_keys") or [])):
            return None, "runner_local_bindings_missing"
        return deepcopy(runner), ""

    def create_request(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[OperationCycleActionRequestView, bool]:
        key = _text(idempotency_key)
        request_hash = _request_payload_hash(values)
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id:
                existing = self._requests[existing_id]
                if existing["request_hash"] != request_hash:
                    raise OperationCycleConflictError("action_start_idempotency_mismatch")
                return _request_view(existing), True
            for existing in self._requests.values():
                if (
                    existing["strategy_key"] == values["strategy_key"]
                    and existing["status"] in ACTIVE_STATUSES
                ):
                    return _request_view(existing), True
            now = values.get("created_at") or _utcnow()
            row = {
                **deepcopy(values),
                "idempotency_key": key,
                "request_hash": request_hash,
                "status": "queued",
                "thread_id": "",
                "turn_id": "",
                "lease_token": "",
                "lease_expires_at": None,
                "final_result_json": None,
                "failure_code": "",
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
            self._requests[row["request_id"]] = row
            self._idempotency[key] = row["request_id"]
            return _request_view(row), False

    def get_request(self, request_id: str) -> OperationCycleActionRequestView | None:
        with self._lock:
            row = self._requests.get(_text(request_id))
            return _request_view(deepcopy(row)) if row else None

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> OperationCycleActionRequestView | None:
        with self._lock:
            request_id = self._idempotency.get(_text(idempotency_key))
            row = self._requests.get(request_id or "")
            return _request_view(deepcopy(row)) if row else None

    def list_strategy_requests(
        self,
        strategy_key: str,
        *,
        limit: int = 100,
    ) -> list[OperationCycleActionRequestView]:
        with self._lock:
            rows = [row for row in self._requests.values() if row["strategy_key"] == _text(strategy_key)]
            rows.sort(key=lambda row: row["created_at"], reverse=True)
            return [_request_view(deepcopy(row)) for row in rows[:limit]]

    def claim(
        self,
        runner_id: str,
        *,
        principal_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> tuple[OperationCycleActionRequestView | None, str, datetime | None]:
        with self._lock:
            runner = self._runners.get(_text(runner_id))
            if runner is None or (
                _text(principal_id) and _text(runner.get("principal_id")) != _text(principal_id)
            ):
                raise OperationCycleConflictError("runner_principal_mismatch")
            active = [
                row
                for row in self._requests.values()
                if row.get("runner_id") == _text(runner_id) and row["status"] in ACTIVE_STATUSES - {"queued"}
            ]
            queued = [
                row
                for row in self._requests.values()
                if row.get("runner_id") == _text(runner_id) and row["status"] == "queued"
            ]
            candidates = sorted(active or queued, key=lambda row: row["created_at"])
            if not candidates:
                return None, "", None
            row = candidates[0]
            token = _lease_token()
            expires_at = now + timedelta(seconds=lease_seconds)
            row["lease_token"] = token
            row["lease_expires_at"] = expires_at
            if row["status"] == "queued":
                row["status"] = "claimed"
                row["claimed_at"] = now
            row["updated_at"] = now
            return _request_view(row), token, expires_at

    def event_is_replay(
        self,
        request_id: str,
        *,
        event_id: str,
        event_payload: dict[str, Any],
    ) -> bool:
        key = (_text(request_id), _text(event_id))
        with self._lock:
            existing_hash = self._events.get(key)
        if not existing_hash:
            return False
        if existing_hash != _payload_hash(event_payload):
            raise OperationCycleConflictError("action_event_idempotency_mismatch")
        return True

    def apply_event(
        self,
        request_id: str,
        *,
        event_id: str,
        lease_token: str,
        event_type: str,
        event_payload: dict[str, Any],
        now: datetime,
    ) -> tuple[OperationCycleActionRequestView, bool]:
        payload_hash = _payload_hash(event_payload)
        key = (_text(request_id), _text(event_id))
        with self._lock:
            row = self._requests.get(key[0])
            if row is None:
                raise LookupError("operation_cycle_action_request_not_found")
            existing_hash = self._events.get(key)
            if existing_hash:
                if existing_hash != payload_hash:
                    raise OperationCycleConflictError("action_event_idempotency_mismatch")
                return _request_view(row), True
            if row.get("lease_token") != lease_token or not row.get("lease_expires_at") or row["lease_expires_at"] < now:
                raise OperationCycleConflictError("action_request_lease_invalid")
            self._transition(row, event_type=event_type, payload=event_payload, now=now)
            self._events[key] = payload_hash
            return _request_view(row), False

    @staticmethod
    def _transition(row: dict[str, Any], *, event_type: str, payload: dict[str, Any], now: datetime) -> None:
        status = row["status"]
        if status in TERMINAL_STATUSES:
            raise OperationCycleConflictError("action_request_already_terminal")
        if event_type == "thread_bound":
            if status != "claimed":
                raise OperationCycleConflictError("action_thread_bind_invalid_state")
            row["thread_id"] = _text(payload.get("thread_id"))
            row["status"] = "thread_bound"
        elif event_type == "turn_started":
            if status != "thread_bound" or row.get("thread_id") != _text(payload.get("thread_id")):
                raise OperationCycleConflictError("action_turn_start_invalid_state")
            row["turn_id"] = _text(payload.get("turn_id"))
            row["status"] = "turn_started"
        elif event_type == "completed":
            if status != "turn_started":
                raise OperationCycleConflictError("action_completion_invalid_state")
            row["final_result_json"] = deepcopy(payload.get("result"))
            row["status"] = "completed"
            row["completed_at"] = now
        elif event_type == "failed":
            row["failure_code"] = _text(payload.get("failure_code"))
            row["status"] = "failed"
            row["completed_at"] = now
        else:
            raise ValueError("unsupported_action_event")
        row["updated_at"] = now


class PostgresOperationCycleActionRepository:
    def __init__(self, session_factory=None, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._tenant_id = _text(tenant_id) or DEFAULT_TENANT_ID

    def heartbeat(
        self,
        payload: OperationRunnerHeartbeatV1,
        *,
        principal_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        with self._session_factory.begin() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO operation_cycle_runners (
                        tenant_id, runner_id, principal_id, connector_version, codex_version,
                        app_server_protocol, compatibility_status, binding_keys_json,
                        max_concurrency, last_heartbeat_at, created_at, updated_at
                    ) VALUES (
                        :tenant_id, :runner_id, :principal_id, :connector_version, :codex_version,
                        :app_server_protocol, :compatibility_status, CAST(:binding_keys_json AS jsonb),
                        1, :now, :now, :now
                    ) ON CONFLICT (tenant_id, runner_id) DO UPDATE SET
                        principal_id = EXCLUDED.principal_id,
                        connector_version = EXCLUDED.connector_version,
                        codex_version = EXCLUDED.codex_version,
                        app_server_protocol = EXCLUDED.app_server_protocol,
                        compatibility_status = EXCLUDED.compatibility_status,
                        binding_keys_json = EXCLUDED.binding_keys_json,
                        max_concurrency = 1,
                        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                        updated_at = EXCLUDED.updated_at
                    WHERE operation_cycle_runners.principal_id = EXCLUDED.principal_id
                    RETURNING *
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "runner_id": payload.runner_id,
                    "principal_id": _text(principal_id),
                    "connector_version": payload.connector_version,
                    "codex_version": payload.codex_version,
                    "app_server_protocol": payload.app_server_protocol,
                    "compatibility_status": payload.compatibility_status,
                    "binding_keys_json": _json_dump(payload.binding_keys),
                    "now": now,
                },
            ).mappings().fetchone()
            if row is None:
                raise OperationCycleConflictError("runner_principal_mismatch")
        return dict(row)

    def select_runner(
        self,
        required_bindings: list[str],
        *,
        now: datetime,
        offline_after_seconds: int,
    ) -> tuple[dict[str, Any] | None, str]:
        threshold = now - timedelta(seconds=offline_after_seconds)
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT * FROM operation_cycle_runners
                    WHERE tenant_id = :tenant_id AND last_heartbeat_at >= :threshold
                    ORDER BY last_heartbeat_at DESC, runner_id
                    """
                ),
                {"tenant_id": self._tenant_id, "threshold": threshold},
            ).mappings().all()
        online = [dict(row) for row in rows]
        if not online:
            return None, "runner_offline"
        compatible = [row for row in online if _text(row.get("compatibility_status")) == "ready"]
        if not compatible:
            return None, "runner_incompatible"
        if len(compatible) > 1:
            return None, "multiple_runners_online"
        runner = compatible[0]
        bindings = set(_json(runner.get("binding_keys_json"), []))
        if not set(required_bindings).issubset(bindings):
            return None, "runner_local_bindings_missing"
        runner["binding_keys"] = sorted(bindings)
        return runner, ""

    def create_request(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[OperationCycleActionRequestView, bool]:
        key = _text(idempotency_key)
        request_hash = _request_payload_hash(values)
        with self._session_factory() as session:
            try:
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"operation-action:{self._tenant_id}:{values['strategy_key']}"},
                )
                existing = session.execute(
                    text(
                        "SELECT * FROM operation_cycle_action_requests "
                        "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
                    ),
                    {"tenant_id": self._tenant_id, "idempotency_key": key},
                ).mappings().fetchone()
                if existing:
                    row = dict(existing)
                    if _text(row.get("request_hash")) != request_hash:
                        raise OperationCycleConflictError("action_start_idempotency_mismatch")
                    session.commit()
                    return _request_view(row), True
                active = session.execute(
                    text(
                        """
                        SELECT * FROM operation_cycle_action_requests
                        WHERE tenant_id = :tenant_id AND strategy_key = :strategy_key
                          AND status IN ('queued','claimed','thread_bound','turn_started')
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ),
                    {"tenant_id": self._tenant_id, "strategy_key": values["strategy_key"]},
                ).mappings().fetchone()
                if active:
                    session.commit()
                    return _request_view(dict(active)), True
                row = session.execute(
                    text(
                        """
                        INSERT INTO operation_cycle_action_requests (
                            request_id, tenant_id, strategy_key, run_key, action_key, action_title,
                            strategy_version, context_hash, skill_key, skill_hash, runner_id,
                            status, idempotency_key, request_hash, parent_request_id,
                            created_by, created_at, updated_at
                        ) VALUES (
                            :request_id, :tenant_id, :strategy_key, :run_key, :action_key, :action_title,
                            :strategy_version, :context_hash, :skill_key, :skill_hash, :runner_id,
                            'queued', :idempotency_key, :request_hash, :parent_request_id,
                            :created_by, :created_at, :created_at
                        ) RETURNING *
                        """
                    ),
                    {
                        **values,
                        "tenant_id": self._tenant_id,
                        "idempotency_key": key,
                        "request_hash": request_hash,
                        "parent_request_id": _text(values.get("parent_request_id")) or None,
                    },
                ).mappings().one()
                session.commit()
            except Exception:
                session.rollback()
                raise
        return _request_view(dict(row)), False

    def get_request(self, request_id: str) -> OperationCycleActionRequestView | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    "SELECT * FROM operation_cycle_action_requests "
                    "WHERE tenant_id = :tenant_id AND request_id = :request_id"
                ),
                {"tenant_id": self._tenant_id, "request_id": _text(request_id)},
            ).mappings().fetchone()
        return _request_view(dict(row)) if row else None

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> OperationCycleActionRequestView | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    "SELECT * FROM operation_cycle_action_requests "
                    "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "idempotency_key": _text(idempotency_key),
                },
            ).mappings().fetchone()
        return _request_view(dict(row)) if row else None

    def list_strategy_requests(
        self,
        strategy_key: str,
        *,
        limit: int = 100,
    ) -> list[OperationCycleActionRequestView]:
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT * FROM operation_cycle_action_requests
                    WHERE tenant_id = :tenant_id AND strategy_key = :strategy_key
                    ORDER BY created_at DESC, request_id DESC LIMIT :limit
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "strategy_key": _text(strategy_key),
                    "limit": max(1, min(int(limit), 500)),
                },
            ).mappings().all()
        return [_request_view(dict(row)) for row in rows]

    def claim(
        self,
        runner_id: str,
        *,
        principal_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> tuple[OperationCycleActionRequestView | None, str, datetime | None]:
        token = _lease_token()
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session:
            try:
                row = session.execute(
                    text(
                        """
                        SELECT * FROM operation_cycle_action_requests
                        WHERE tenant_id = :tenant_id AND runner_id = :runner_id
                          AND EXISTS (
                              SELECT 1 FROM operation_cycle_runners runner
                              WHERE runner.tenant_id = operation_cycle_action_requests.tenant_id
                                AND runner.runner_id = operation_cycle_action_requests.runner_id
                                AND runner.principal_id = :principal_id
                          )
                          AND status IN ('claimed','thread_bound','turn_started')
                        ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                        """
                    ),
                    {
                        "tenant_id": self._tenant_id,
                        "runner_id": _text(runner_id),
                        "principal_id": _text(principal_id),
                    },
                ).mappings().fetchone()
                if row is None:
                    row = session.execute(
                        text(
                            """
                            SELECT * FROM operation_cycle_action_requests
                            WHERE tenant_id = :tenant_id AND runner_id = :runner_id
                              AND EXISTS (
                                  SELECT 1 FROM operation_cycle_runners runner
                                  WHERE runner.tenant_id = operation_cycle_action_requests.tenant_id
                                    AND runner.runner_id = operation_cycle_action_requests.runner_id
                                    AND runner.principal_id = :principal_id
                              )
                              AND status = 'queued'
                            ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                            """
                        ),
                        {
                            "tenant_id": self._tenant_id,
                            "runner_id": _text(runner_id),
                            "principal_id": _text(principal_id),
                        },
                    ).mappings().fetchone()
                if row is None:
                    session.commit()
                    return None, "", None
                updated = session.execute(
                    text(
                        """
                        UPDATE operation_cycle_action_requests SET
                            status = CASE WHEN status = 'queued' THEN 'claimed' ELSE status END,
                            claimed_at = COALESCE(claimed_at, :now),
                            lease_token_hash = :lease_token_hash,
                            lease_expires_at = :lease_expires_at,
                            updated_at = :now
                        WHERE request_id = :request_id
                        RETURNING *
                        """
                    ),
                    {
                        "request_id": row["request_id"],
                        "lease_token_hash": _lease_hash(token),
                        "lease_expires_at": expires_at,
                        "now": now,
                    },
                ).mappings().one()
                session.commit()
            except Exception:
                session.rollback()
                raise
        return _request_view(dict(updated)), token, expires_at

    def event_is_replay(
        self,
        request_id: str,
        *,
        event_id: str,
        event_payload: dict[str, Any],
    ) -> bool:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    "SELECT payload_hash FROM operation_cycle_action_request_events "
                    "WHERE request_id = :request_id AND event_id = :event_id"
                ),
                {"request_id": _text(request_id), "event_id": _text(event_id)},
            ).mappings().fetchone()
        if row is None:
            return False
        if _text(row.get("payload_hash")) != _payload_hash(event_payload):
            raise OperationCycleConflictError("action_event_idempotency_mismatch")
        return True

    def apply_event(
        self,
        request_id: str,
        *,
        event_id: str,
        lease_token: str,
        event_type: str,
        event_payload: dict[str, Any],
        now: datetime,
    ) -> tuple[OperationCycleActionRequestView, bool]:
        event_hash = _payload_hash(event_payload)
        with self._session_factory() as session:
            try:
                row = session.execute(
                    text(
                        "SELECT * FROM operation_cycle_action_requests "
                        "WHERE tenant_id = :tenant_id AND request_id = :request_id FOR UPDATE"
                    ),
                    {"tenant_id": self._tenant_id, "request_id": _text(request_id)},
                ).mappings().fetchone()
                if row is None:
                    raise LookupError("operation_cycle_action_request_not_found")
                row = dict(row)
                existing = session.execute(
                    text(
                        "SELECT payload_hash FROM operation_cycle_action_request_events "
                        "WHERE request_id = :request_id AND event_id = :event_id"
                    ),
                    {"request_id": row["request_id"], "event_id": _text(event_id)},
                ).mappings().fetchone()
                if existing:
                    if _text(existing.get("payload_hash")) != event_hash:
                        raise OperationCycleConflictError("action_event_idempotency_mismatch")
                    session.commit()
                    return _request_view(row), True
                if (
                    _text(row.get("lease_token_hash")) != _lease_hash(lease_token)
                    or row.get("lease_expires_at") is None
                    or row["lease_expires_at"] < now
                ):
                    raise OperationCycleConflictError("action_request_lease_invalid")
                mutable = dict(row)
                InMemoryOperationCycleActionRepository._transition(
                    mutable,
                    event_type=event_type,
                    payload=event_payload,
                    now=now,
                )
                updated = session.execute(
                    text(
                        """
                        UPDATE operation_cycle_action_requests SET
                            status = :status, thread_id = :thread_id, turn_id = :turn_id,
                            final_result_json = CAST(:final_result_json AS jsonb),
                            failure_code = :failure_code, completed_at = :completed_at,
                            updated_at = :updated_at
                        WHERE request_id = :request_id RETURNING *
                        """
                    ),
                    {
                        "request_id": row["request_id"],
                        "status": mutable["status"],
                        "thread_id": _text(mutable.get("thread_id")),
                        "turn_id": _text(mutable.get("turn_id")),
                        "final_result_json": _json_dump(mutable.get("final_result_json")),
                        "failure_code": _text(mutable.get("failure_code")),
                        "completed_at": mutable.get("completed_at"),
                        "updated_at": now,
                    },
                ).mappings().one()
                session.execute(
                    text(
                        """
                        INSERT INTO operation_cycle_action_request_events (
                            request_id, event_id, event_type, payload_hash, payload_json, created_at
                        ) VALUES (
                            :request_id, :event_id, :event_type, :payload_hash,
                            CAST(:payload_json AS jsonb), :created_at
                        )
                        """
                    ),
                    {
                        "request_id": row["request_id"],
                        "event_id": _text(event_id),
                        "event_type": event_type,
                        "payload_hash": event_hash,
                        "payload_json": _json_dump(event_payload),
                        "created_at": now,
                    },
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
        return _request_view(dict(updated)), False


def build_operation_cycle_action_repository() -> OperationCycleActionRepository:
    if not _text(raw_database_url()):
        raise RuntimeError("DATABASE_URL is required for operation-cycle actions")
    return PostgresOperationCycleActionRepository()


__all__ = [
    "ACTIVE_STATUSES",
    "InMemoryOperationCycleActionRepository",
    "OperationCycleActionRepository",
    "PostgresOperationCycleActionRepository",
    "TERMINAL_STATUSES",
    "build_operation_cycle_action_repository",
]
