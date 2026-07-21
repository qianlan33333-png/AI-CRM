from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_internal_event_outbox_in_session
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.sensitive_data import redact_sensitive_text

from .constants import AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT, AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT
from .event_types import REFRESH_REQUESTED_EVENT, RUN_REFRESHED_EVENT, SOURCE_CHANGED_EVENT
from .refresh_service import AudienceRefreshService


RefreshRunner = Callable[..., dict[str, Any]]
_OPEN_OWNER_STATUSES = frozenset({"waiting", "running", "retry_wait"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _public(row: Any) -> dict[str, Any]:
    payload = dict(row or {})
    for key in ("target_params_json", "running_params_json"):
        if key in payload:
            payload[key] = _json_obj(payload.get(key))
    for key, value in list(payload.items()):
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            payload[key] = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


def _intent_summary(row: Any) -> dict[str, Any]:
    payload = _public(row)
    safe_keys = (
        "package_id",
        "dirty_generation",
        "completed_generation",
        "signal_generation",
        "running_generation",
        "status",
        "target_refresh_kind",
        "running_refresh_kind",
        "execution_id",
        "parent_execution_id",
        "running_execution_id",
        "running_parent_execution_id",
        "lane",
        "available_at",
        "attempt_count",
        "max_attempts",
        "last_run_id",
        "last_error_code",
        "row_version",
        "updated_at",
        "completed_at",
    )
    return {key: payload.get(key) for key in safe_keys if key in payload}


def _bounded_row_limit(value: Any) -> int:
    try:
        parsed = int(value or AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT)
    except (TypeError, ValueError):
        parsed = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT
    return max(1, min(parsed, AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT))


def _refresh_kind(value: Any) -> str:
    normalized = _text(value).lower()
    if normalized in {"daily", "snapshot", "snapshot_current"}:
        return "daily"
    if normalized == "manual":
        return "manual"
    return "incremental"


def _coalesced_kind(current: str, requested: str, *, already_owned: bool) -> str:
    if not already_owned:
        return requested
    if "daily" in {current, requested}:
        return "daily"
    if requested == "manual":
        return "manual"
    return current or requested


def _new_execution_id(prefix: str = "refresh") -> str:
    return f"exe_ai_audience_{prefix}_{uuid4().hex}"


def _safe_error_code(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", _text(value).lower()).strip("_")
    return (normalized or "refresh_failed")[:120]


def _opaque_source_identifier(value: Any) -> str:
    normalized = _text(value)
    if not normalized:
        return ""
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class AudienceRefreshIntentRepository:
    """PostgreSQL fact owner for one coalescing refresh intent per package."""

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def get(self, package_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id"),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            return _public(row) if row else None

    def mark_source_dirty(
        self,
        *,
        source_event_key: str,
        source_type: str,
        source_key: str = "",
        refresh_kind: str = "incremental",
        execution_id: str = "",
        parent_execution_id: str = "",
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self.mark_source_dirty_in_session(
                session,
                source_event_key=source_event_key,
                source_type=source_type,
                source_key=source_key,
                refresh_kind=refresh_kind,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                params=params,
                row_limit=row_limit,
            )
            session.commit()
            return result

    def mark_source_dirty_in_session(
        self,
        session: Session,
        *,
        source_event_key: str,
        source_type: str,
        source_key: str = "",
        refresh_kind: str = "incremental",
        execution_id: str = "",
        parent_execution_id: str = "",
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        normalized_source_type = _text(source_type)
        normalized_event_key = _text(source_event_key)
        if not normalized_source_type or not normalized_event_key:
            raise ValueError("source_type_and_source_event_key_required")
        rows = session.execute(
            text(
                """
                SELECT DISTINCT package.id AS package_id
                FROM ai_audience_package package
                JOIN ai_audience_package_dependency dependency
                  ON dependency.package_id = package.id
                 AND dependency.version_id = package.current_version_id
                WHERE package.status = 'active'
                  AND package.current_version_id IS NOT NULL
                  AND dependency.source_type = :source_type
                  AND (
                    :source_key = ''
                    OR dependency.source_key = ''
                    OR dependency.source_key = :source_key
                  )
                ORDER BY package.id
                """
            ),
            {"source_type": normalized_source_type, "source_key": _text(source_key)},
        ).mappings().all()
        items = [
            self.mark_package_dirty_in_session(
                session,
                package_id=int(row["package_id"]),
                source_event_key=normalized_event_key,
                source_type=normalized_source_type,
                source_key=source_key,
                refresh_kind=refresh_kind,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                params=params,
                row_limit=row_limit,
            )
            for row in rows
        ]
        return {
            "ok": True,
            "source_event_key": _opaque_source_identifier(normalized_event_key),
            "matched_package_count": len(rows),
            "updated_package_count": sum(1 for item in items if not item.get("deduplicated")),
            "deduplicated_package_count": sum(1 for item in items if item.get("deduplicated")),
            "items": items,
            "real_external_call_executed": False,
        }

    def mark_package_dirty(
        self,
        *,
        package_id: int,
        source_event_key: str,
        source_type: str,
        source_key: str = "",
        refresh_kind: str = "incremental",
        execution_id: str = "",
        parent_execution_id: str = "",
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            item = self.mark_package_dirty_in_session(
                session,
                package_id=package_id,
                source_event_key=source_event_key,
                source_type=source_type,
                source_key=source_key,
                refresh_kind=refresh_kind,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                params=params,
                row_limit=row_limit,
            )
            session.commit()
            return item

    def mark_package_dirty_in_session(
        self,
        session: Session,
        *,
        package_id: int,
        source_event_key: str,
        source_type: str,
        source_key: str = "",
        refresh_kind: str = "incremental",
        execution_id: str = "",
        parent_execution_id: str = "",
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        package_id = int(package_id)
        raw_event_key = _text(source_event_key)
        if package_id <= 0 or not raw_event_key:
            raise ValueError("package_id_and_source_event_key_required")
        event_key = _opaque_source_identifier(raw_event_key)
        package = session.execute(
            text(
                """
                SELECT id, status, current_version_id
                FROM ai_audience_package
                WHERE id = :package_id
                FOR SHARE
                """
            ),
            {"package_id": package_id},
        ).mappings().fetchone()
        if not package:
            raise ValueError("package_not_found")
        if _text(package.get("status")) != "active" or not package.get("current_version_id"):
            return {"ok": False, "package_id": package_id, "skipped": "package_not_active", "real_external_call_executed": False}

        root_execution_id = _text(execution_id) or _new_execution_id()
        receipt = session.execute(
            text(
                """
                INSERT INTO ai_audience_refresh_source_receipt (
                    package_id, source_event_key, source_type, source_key,
                    refresh_kind, generation, execution_id, parent_execution_id,
                    created_at
                ) VALUES (
                    :package_id, :source_event_key, :source_type, :source_key,
                    :refresh_kind, 1, :execution_id, :parent_execution_id,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (package_id, source_event_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "package_id": package_id,
                "source_event_key": event_key,
                "source_type": _text(source_type),
                "source_key": _opaque_source_identifier(source_key),
                "refresh_kind": _refresh_kind(refresh_kind),
                "execution_id": root_execution_id,
                "parent_execution_id": _text(parent_execution_id),
            },
        ).mappings().fetchone()
        if not receipt:
            existing = session.execute(
                text(
                    """
                    SELECT generation, execution_id, parent_execution_id
                    FROM ai_audience_refresh_source_receipt
                    WHERE package_id = :package_id AND source_event_key = :source_event_key
                    """
                ),
                {"package_id": package_id, "source_event_key": event_key},
            ).mappings().one()
            intent = session.execute(
                text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id"),
                {"package_id": package_id},
            ).mappings().fetchone()
            return {
                "ok": True,
                "package_id": package_id,
                "generation": int(existing["generation"]),
                "execution_id": _text(existing["execution_id"]),
                "parent_execution_id": _text(existing["parent_execution_id"]),
                "deduplicated": True,
                "intent": _intent_summary(intent) if intent else None,
                "real_external_call_executed": False,
            }

        session.execute(
            text(
                """
                INSERT INTO ai_audience_refresh_intent (package_id)
                VALUES (:package_id)
                ON CONFLICT (package_id) DO NOTHING
                """
            ),
            {"package_id": package_id},
        )
        current = session.execute(
            text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id FOR UPDATE"),
            {"package_id": package_id},
        ).mappings().one()
        current_status = _text(current["status"])
        has_owner = current_status in _OPEN_OWNER_STATUSES and int(current["signal_generation"] or 0) > int(current["completed_generation"] or 0)
        generation = int(current["dirty_generation"] or 0) + 1
        requested_kind = _refresh_kind(refresh_kind)
        has_pending_target = has_owner and (
            current_status != "running"
            or int(current["dirty_generation"] or 0) > int(current["running_generation"] or 0)
        )
        target_kind = _coalesced_kind(
            _text(current["target_refresh_kind"]),
            requested_kind,
            already_owned=has_pending_target,
        )
        preserve_target_payload = has_pending_target and target_kind != requested_kind
        target_params = _json_obj(current.get("target_params_json")) if preserve_target_payload else dict(params or {})
        target_row_limit = int(current.get("target_row_limit") or AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT) if preserve_target_payload else _bounded_row_limit(row_limit)
        should_signal = not has_owner and current_status != "blocked"
        status = "waiting" if should_signal else current_status
        signal_generation = generation if should_signal else int(current["signal_generation"] or 0)
        updated = session.execute(
            text(
                """
                UPDATE ai_audience_refresh_intent
                SET dirty_generation = :generation,
                    signal_generation = :signal_generation,
                    status = :status,
                    target_refresh_kind = :target_refresh_kind,
                    target_params_json = CAST(:target_params_json AS jsonb),
                    target_row_limit = :target_row_limit,
                    execution_id = :execution_id,
                    parent_execution_id = :parent_execution_id,
                    available_at = CASE WHEN :should_signal THEN CURRENT_TIMESTAMP ELSE available_at END,
                    last_source_event_key = :source_event_key,
                    last_error_code = CASE WHEN :should_signal THEN '' ELSE last_error_code END,
                    last_error_message = CASE WHEN :should_signal THEN '' ELSE last_error_message END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE package_id = :package_id
                RETURNING *
                """
            ),
            {
                "package_id": package_id,
                "generation": generation,
                "signal_generation": signal_generation,
                "status": status,
                "target_refresh_kind": target_kind,
                "target_params_json": _json(target_params),
                "target_row_limit": target_row_limit,
                "execution_id": root_execution_id,
                "parent_execution_id": _text(parent_execution_id),
                "should_signal": should_signal,
                "source_event_key": event_key,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE ai_audience_refresh_source_receipt
                SET generation = :generation
                WHERE id = :receipt_id
                """
            ),
            {"generation": generation, "receipt_id": int(receipt["id"])},
        )
        signal = None
        if should_signal:
            signal = self._enqueue_refresh_signal(
                session,
                package_id=package_id,
                generation=generation,
                execution_id=root_execution_id,
                parent_execution_id=parent_execution_id,
                refresh_kind=target_kind,
            )
        return {
            "ok": True,
            "package_id": package_id,
            "generation": generation,
            "execution_id": root_execution_id,
            "parent_execution_id": _text(parent_execution_id),
            "deduplicated": False,
            "signal_created": bool(signal),
            "intent": _intent_summary(updated),
            "signal": _public(signal) if signal else None,
            "real_external_call_executed": False,
        }

    def claim_latest(
        self,
        *,
        package_id: int,
        signal_generation: int,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.execute(
                text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id FOR UPDATE"),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            if not row:
                return {"ok": False, "reason": "intent_not_found", "package_id": int(package_id)}
            status = _text(row["status"])
            dirty_generation = int(row["dirty_generation"] or 0)
            completed_generation = int(row["completed_generation"] or 0)
            if completed_generation >= dirty_generation or status == "idle":
                return {
                    "ok": True,
                    "claimed": False,
                    "reason": "already_completed",
                    "package_id": int(package_id),
                    "dirty_generation": dirty_generation,
                    "completed_generation": completed_generation,
                }
            if status == "running":
                same_run_reclaim = (
                    int(owner_consumer_run_id or 0) > 0
                    and int(row.get("owner_consumer_run_id") or 0) == int(owner_consumer_run_id)
                    and bool(_text(owner_lease_token))
                    and _text(row.get("owner_lease_token")) != _text(owner_lease_token)
                )
                if not same_run_reclaim:
                    return {"ok": True, "claimed": False, "reason": "already_running", "package_id": int(package_id)}
                session.execute(
                    text(
                        """
                        UPDATE ai_audience_refresh_intent
                        SET status = 'retry_wait',
                            running_generation = 0,
                            running_refresh_kind = '',
                            running_params_json = '{}'::jsonb,
                            running_execution_id = '',
                            running_parent_execution_id = '',
                            owner_consumer_run_id = NULL,
                            owner_lease_token = '',
                            last_error_code = 'consumer_lease_reclaimed',
                            last_error_message = 'The owning internal consumer lease was replaced before completion.',
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE package_id = :package_id
                        """
                    ),
                    {"package_id": int(package_id)},
                )
            if status in {"waiting", "retry_wait"}:
                is_available = bool(
                    session.execute(
                        text(
                            """
                            SELECT available_at <= CURRENT_TIMESTAMP
                            FROM ai_audience_refresh_intent
                            WHERE package_id = :package_id
                            """
                        ),
                        {"package_id": int(package_id)},
                    ).scalar_one()
                )
                if not is_available:
                    return {
                        "ok": True,
                        "claimed": False,
                        "reason": "not_available",
                        "package_id": int(package_id),
                        "available_at": _public(row).get("available_at"),
                    }
            if status == "blocked" or int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 0):
                session.execute(
                    text(
                        """
                        UPDATE ai_audience_refresh_intent
                        SET status = 'blocked', row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                        WHERE package_id = :package_id
                        """
                    ),
                    {"package_id": int(package_id)},
                )
                session.commit()
                return {"ok": False, "claimed": False, "reason": "attempt_budget_exhausted", "package_id": int(package_id)}
            claimed = session.execute(
                text(
                    """
                    UPDATE ai_audience_refresh_intent
                    SET status = 'running',
                        running_generation = dirty_generation,
                        running_refresh_kind = target_refresh_kind,
                        running_params_json = target_params_json,
                        running_row_limit = target_row_limit,
                        running_execution_id = execution_id,
                        running_parent_execution_id = parent_execution_id,
                        owner_consumer_run_id = :owner_consumer_run_id,
                        owner_lease_token = :owner_lease_token,
                        attempt_count = attempt_count + 1,
                        available_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE package_id = :package_id
                      AND status IN ('waiting', 'retry_wait')
                    RETURNING *
                    """
                ),
                {
                    "package_id": int(package_id),
                    "owner_consumer_run_id": int(owner_consumer_run_id or 0) or None,
                    "owner_lease_token": _text(owner_lease_token),
                },
            ).mappings().fetchone()
            session.commit()
            if not claimed:
                return {"ok": True, "claimed": False, "reason": "claim_raced", "package_id": int(package_id)}
            result = _public(claimed)
            result.update(
                {
                    "ok": True,
                    "claimed": True,
                    "requested_signal_generation": int(signal_generation or 0),
                    "coalesced_to_latest": int(signal_generation or 0) != int(claimed["running_generation"] or 0),
                }
            )
            return result

    def complete(
        self,
        *,
        package_id: int,
        generation: int,
        result: dict[str, Any],
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            current = session.execute(
                text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id FOR UPDATE"),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            if not current:
                return {"ok": False, "reason": "intent_not_found"}
            owner_mismatch = bool(owner_consumer_run_id or owner_lease_token) and (
                int(current.get("owner_consumer_run_id") or 0) != int(owner_consumer_run_id or 0)
                or _text(current.get("owner_lease_token")) != _text(owner_lease_token)
            )
            if _text(current["status"]) != "running" or int(current["running_generation"] or 0) != int(generation) or owner_mismatch:
                return {"ok": True, "completed": False, "reason": "stale_completion", "intent": _intent_summary(current)}
            run = result.get("run") if isinstance(result.get("run"), dict) else {}
            run_id = int(run.get("id") or result.get("run_id") or 0)
            running_execution_id = _text(current["running_execution_id"]) or _new_execution_id()
            completion_execution_id = _new_execution_id("completion")
            completion = enqueue_internal_event_outbox_in_session(
                session,
                InternalEventCreateRequest(
                    event_type=RUN_REFRESHED_EVENT,
                    aggregate_type="ai_audience_package_run",
                    aggregate_id=str(run_id or f"package-{package_id}-generation-{generation}"),
                    subject_type="ai_audience_package",
                    subject_id=str(int(package_id)),
                    idempotency_key=f"ai_audience.refresh.completed:{int(package_id)}:{int(generation)}",
                    source_module="ai_audience_ops.refresh_intents",
                    payload={
                        "package_id": int(package_id),
                        "generation": int(generation),
                        "run_id": run_id,
                        "run_type": _text(current["running_refresh_kind"]),
                        "returned_count": int(result.get("returned_count") or 0),
                        "entered_count": int(result.get("entered_count") or 0),
                        "updated_count": int(result.get("updated_count") or 0),
                        "exited_count": int(result.get("exited_count") or 0),
                        "member_event_count": int(result.get("member_event_count") or 0),
                    },
                    payload_summary={
                        "package_id": int(package_id),
                        "generation": int(generation),
                        "run_id": run_id,
                        "run_type": _text(current["running_refresh_kind"]),
                        "member_event_count": int(result.get("member_event_count") or 0),
                    },
                    context=CommandContext(
                        actor_id="ai_audience_refresh_intent",
                        actor_type="system",
                        source_route="ai_audience.refresh_intent.complete",
                    ),
                    execution_id=completion_execution_id,
                    parent_execution_id=running_execution_id,
                ),
            )
            dirty_generation = int(current["dirty_generation"] or 0)
            has_continuation = dirty_generation > int(generation)
            next_signal = None
            next_signal_generation = int(current["signal_generation"] or 0)
            next_status = "idle"
            if has_continuation:
                next_status = "waiting"
                next_signal_generation = dirty_generation
                next_signal = self._enqueue_refresh_signal(
                    session,
                    package_id=int(package_id),
                    generation=dirty_generation,
                    execution_id=_text(current["execution_id"]),
                    parent_execution_id=_text(current["parent_execution_id"]),
                    refresh_kind=_text(current["target_refresh_kind"]),
                )
            updated = session.execute(
                text(
                    """
                    UPDATE ai_audience_refresh_intent
                    SET completed_generation = :completed_generation,
                        signal_generation = :signal_generation,
                        running_generation = 0,
                        status = :status,
                        running_refresh_kind = '',
                        running_params_json = '{}'::jsonb,
                        running_execution_id = '',
                        running_parent_execution_id = '',
                        owner_consumer_run_id = NULL,
                        owner_lease_token = '',
                        attempt_count = 0,
                        last_run_id = :last_run_id,
                        last_error_code = '',
                        last_error_message = '',
                        available_at = CURRENT_TIMESTAMP,
                        completed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE package_id = :package_id
                    RETURNING *
                    """
                ),
                {
                    "package_id": int(package_id),
                    "completed_generation": int(generation),
                    "signal_generation": next_signal_generation,
                    "status": next_status,
                    "last_run_id": run_id or None,
                },
            ).mappings().one()
            session.commit()
            return {
                "ok": True,
                "completed": True,
                "generation": int(generation),
                "completion_event": _public(completion),
                "continuation_created": bool(next_signal),
                "continuation_signal": _public(next_signal) if next_signal else None,
                "intent": _intent_summary(updated),
                "real_external_call_executed": False,
            }

    def fail(
        self,
        *,
        package_id: int,
        generation: int,
        error_code: str,
        error_message: str,
        retry_after_seconds: int = 60,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            current = session.execute(
                text("SELECT * FROM ai_audience_refresh_intent WHERE package_id = :package_id FOR UPDATE"),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            if not current:
                return {"ok": False, "reason": "intent_not_found"}
            owner_mismatch = bool(owner_consumer_run_id or owner_lease_token) and (
                int(current.get("owner_consumer_run_id") or 0) != int(owner_consumer_run_id or 0)
                or _text(current.get("owner_lease_token")) != _text(owner_lease_token)
            )
            if _text(current["status"]) != "running" or int(current["running_generation"] or 0) != int(generation) or owner_mismatch:
                return {"ok": True, "failed": False, "reason": "stale_failure", "intent": _intent_summary(current)}
            terminal = int(current["attempt_count"] or 0) >= int(current["max_attempts"] or 0)
            updated = session.execute(
                text(
                    """
                    UPDATE ai_audience_refresh_intent
                    SET status = :status,
                        running_generation = 0,
                        running_refresh_kind = '',
                        running_params_json = '{}'::jsonb,
                        running_execution_id = '',
                        running_parent_execution_id = '',
                        owner_consumer_run_id = NULL,
                        owner_lease_token = '',
                        available_at = CURRENT_TIMESTAMP + (:retry_after_seconds || ' seconds')::interval,
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE package_id = :package_id
                    RETURNING *
                    """
                ),
                {
                    "package_id": int(package_id),
                    "status": "blocked" if terminal else "retry_wait",
                    "retry_after_seconds": max(1, min(int(retry_after_seconds or 60), 86400)),
                    "error_code": _safe_error_code(error_code),
                    "error_message": redact_sensitive_text(error_message)[:1000],
                },
            ).mappings().one()
            session.commit()
            return {
                "ok": not terminal,
                "failed": True,
                "terminal": terminal,
                "intent": _intent_summary(updated),
                "real_external_call_executed": False,
            }

    @staticmethod
    def _enqueue_refresh_signal(
        session: Session,
        *,
        package_id: int,
        generation: int,
        execution_id: str,
        parent_execution_id: str,
        refresh_kind: str,
    ) -> dict[str, Any]:
        return enqueue_internal_event_outbox_in_session(
            session,
            InternalEventCreateRequest(
                event_type=REFRESH_REQUESTED_EVENT,
                aggregate_type="ai_audience_package",
                aggregate_id=str(int(package_id)),
                subject_type="ai_audience_package",
                subject_id=str(int(package_id)),
                idempotency_key=f"ai_audience.refresh.requested:{int(package_id)}:{int(generation)}",
                source_module="ai_audience_ops.refresh_intents",
                payload={
                    "package_id": int(package_id),
                    "generation": int(generation),
                    "refresh_kind": _refresh_kind(refresh_kind),
                },
                payload_summary={
                    "package_id": int(package_id),
                    "generation": int(generation),
                    "refresh_kind": _refresh_kind(refresh_kind),
                    "lane": "internal_general",
                },
                context=CommandContext(
                    actor_id="ai_audience_refresh_intent",
                    actor_type="system",
                    source_route="ai_audience.refresh_intent.request",
                ),
                execution_id=_text(execution_id) or _new_execution_id(),
                parent_execution_id=_text(parent_execution_id),
            ),
        )


class AudienceRefreshIntentService:
    def __init__(
        self,
        repository: AudienceRefreshIntentRepository | None = None,
        refresh_runner: RefreshRunner | None = None,
    ) -> None:
        self._repo = repository or AudienceRefreshIntentRepository()
        self._refresh_runner = refresh_runner or AudienceRefreshService().refresh_package

    def request_source_change(
        self,
        payload: dict[str, Any],
        *,
        source_event_key: str = "",
        execution_id: str = "",
        parent_execution_id: str = "",
        emit_source_audit: bool = False,
    ) -> dict[str, Any]:
        source_type = _text(payload.get("source_type"))
        source_key = _text(payload.get("source_key"))
        event_key = _text(source_event_key) or self._source_event_key(payload)
        source_execution_id = _text(execution_id)
        source_parent_execution_id = source_execution_id or _text(parent_execution_id)
        with self._repo._session_factory() as session:
            result = self._repo.mark_source_dirty_in_session(
                session,
                source_event_key=event_key,
                source_type=source_type,
                source_key=source_key,
                refresh_kind="incremental",
                execution_id="",
                parent_execution_id=source_parent_execution_id,
            )
            audit = None
            if emit_source_audit:
                audit_execution_id = _text(execution_id) or _new_execution_id("source")
                audit = enqueue_internal_event_outbox_in_session(
                    session,
                    InternalEventCreateRequest(
                        event_type=SOURCE_CHANGED_EVENT,
                        aggregate_type="ai_audience_source",
                        aggregate_id=f"{source_type}:{source_key}",
                        subject_type="ai_audience_source",
                        subject_id=source_type,
                        idempotency_key=f"ai_audience.source.changed:{event_key}",
                        source_module="ai_audience_ops.refresh_intents",
                        payload={"source_type": source_type, "source_key": source_key, "source_event_key": event_key},
                        payload_summary={"source_type": source_type, "source_key_present": bool(source_key)},
                        context=CommandContext(
                            actor_id="ai_audience_source_dirty",
                            actor_type="system",
                            source_route="ai_audience.source_dirty",
                        ),
                        execution_id=audit_execution_id,
                        parent_execution_id=_text(parent_execution_id),
                    ),
                )
            session.commit()
        return {
            **result,
            "event": {
                "event_type": SOURCE_CHANGED_EVENT,
                "execution_id": _text((audit or {}).get("execution_id")) or _text(execution_id),
                "parent_execution_id": _text((audit or {}).get("parent_execution_id")) or _text(parent_execution_id),
                "outbox_id": _text((audit or {}).get("outbox_id")),
            }
            if emit_source_audit
            else None,
        }

    def request_package_refresh(
        self,
        package_id: int,
        *,
        refresh_kind: str,
        source_event_key: str = "",
        execution_id: str = "",
        parent_execution_id: str = "",
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        key = _text(source_event_key) or f"manual:{int(package_id)}:{uuid4().hex}"
        return self._repo.mark_package_dirty(
            package_id=int(package_id),
            source_event_key=key,
            source_type="manual_refresh" if _refresh_kind(refresh_kind) == "manual" else f"{_refresh_kind(refresh_kind)}_refresh",
            refresh_kind=refresh_kind,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            params=params,
            row_limit=row_limit,
        )

    def request_due_refreshes(
        self,
        refresh_kind: str,
        *,
        bucket: str = "",
        actor_id: str = "ai_audience_scheduler",
    ) -> dict[str, Any]:
        kind = _refresh_kind(refresh_kind)
        enabled_column = "daily_enabled" if kind == "daily" else "incremental_enabled"
        event_bucket = _text(bucket) or (date.today().isoformat() if kind == "daily" else datetime.now(timezone.utc).isoformat())
        with self._repo._session_factory() as session:
            packages = session.execute(
                text(
                    f"""
                    SELECT id
                    FROM ai_audience_package
                    WHERE status = 'active'
                      AND current_version_id IS NOT NULL
                      AND {enabled_column} = TRUE
                    ORDER BY id
                    """
                )
            ).mappings().all()
            items = [
                self._repo.mark_package_dirty_in_session(
                    session,
                    package_id=int(row["id"]),
                    source_event_key=f"{kind}:{event_bucket}:package:{int(row['id'])}",
                    source_type=f"{kind}_clock_intent",
                    refresh_kind=kind,
                    execution_id=_new_execution_id(),
                    parent_execution_id="",
                )
                for row in packages
            ]
            session.commit()
        return {
            "ok": True,
            "refresh_kind": kind,
            "bucket": event_bucket,
            "candidate_count": len(packages),
            "intent_count": sum(1 for item in items if not item.get("deduplicated")),
            "deduplicated_count": sum(1 for item in items if item.get("deduplicated")),
            "items": items,
            "real_external_call_executed": False,
        }

    def process_requested(
        self,
        *,
        package_id: int,
        signal_generation: int,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        claim = self._repo.claim_latest(
            package_id=int(package_id),
            signal_generation=int(signal_generation),
            owner_consumer_run_id=int(owner_consumer_run_id or 0),
            owner_lease_token=_text(owner_lease_token),
        )
        if not claim.get("claimed"):
            terminal_noop = claim.get("reason") == "already_completed"
            return {**claim, "ok": terminal_noop, "real_external_call_executed": False}
        generation = int(claim["running_generation"])
        run_kind = _text(claim["running_refresh_kind"])
        runner_kind = "daily" if run_kind == "daily" else "incremental"
        try:
            result = self._refresh_runner(
                int(package_id),
                run_type=runner_kind,
                params=dict(claim.get("running_params_json") or {}),
                row_limit=int(claim.get("running_row_limit") or AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT),
                emit_completion_event=False,
            )
        except TypeError as exc:
            if "emit_completion_event" not in str(exc):
                raise
            result = self._refresh_runner(
                int(package_id),
                run_type=runner_kind,
                params=dict(claim.get("running_params_json") or {}),
                row_limit=int(claim.get("running_row_limit") or AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT),
            )
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc))[:1000]
            failure = self._repo.fail(
                package_id=int(package_id),
                generation=generation,
                error_code="refresh_runner_exception",
                error_message=safe_error,
                owner_consumer_run_id=int(owner_consumer_run_id or 0),
                owner_lease_token=_text(owner_lease_token),
            )
            return {
                "ok": False,
                "claimed": True,
                "generation": generation,
                "error": safe_error,
                "failure": failure,
                "real_external_call_executed": False,
            }
        if not result.get("ok"):
            safe_error = redact_sensitive_text(_text(result.get("error")) or "AI Audience refresh failed")[:1000]
            failure = self._repo.fail(
                package_id=int(package_id),
                generation=generation,
                error_code=_text(result.get("error_code")) or "refresh_failed",
                error_message=safe_error,
                owner_consumer_run_id=int(owner_consumer_run_id or 0),
                owner_lease_token=_text(owner_lease_token),
            )
            return {
                **result,
                "error": safe_error,
                "claimed": True,
                "generation": generation,
                "failure": failure,
                "real_external_call_executed": False,
            }
        completion = self._repo.complete(
            package_id=int(package_id),
            generation=generation,
            result=result,
            owner_consumer_run_id=int(owner_consumer_run_id or 0),
            owner_lease_token=_text(owner_lease_token),
        )
        return {
            **result,
            "claimed": True,
            "generation": generation,
            "completion": completion,
            "real_external_call_executed": False,
        }

    @staticmethod
    def _source_event_key(payload: dict[str, Any]) -> str:
        explicit = _text(payload.get("source_event_key") or payload.get("idempotency_key"))
        if explicit:
            return explicit
        parts = (
            _text(payload.get("source_type")),
            _text(payload.get("source_key")),
            _text(payload.get("identity_type")),
            _text(payload.get("identity_value")),
            _text(payload.get("occurred_at")),
        )
        return "api:" + ":".join(parts) if any(parts) else "api:" + uuid4().hex


__all__ = ["AudienceRefreshIntentRepository", "AudienceRefreshIntentService"]
