from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aicrm_next.crm.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.resolver import SQLAlchemyIdentityResolver, resolved_unionids_for_external_userids_with_sqlalchemy
from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import AI_AGENT_GENERATE, ExternalEffectService
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform.platform_foundation.internal_events.outbox import enqueue_internal_event_outbox_batch_in_session
from aicrm_next.platform.shared.db_session import get_session_factory


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_dumps(value: Any) -> str:
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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _agent_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    """Freeze every generation/send field that can change during a long batch."""

    return {
        "agent_code": _text(agent.get("agent_code")),
        "automation_type": _text(agent.get("automation_type")) or "agent",
        "bound_package_key": _text(agent.get("bound_package_key")),
        "published_version": _safe_int(agent.get("published_version")),
        "published_role_prompt": _text(agent.get("published_role_prompt")),
        "published_task_prompt": _text(agent.get("published_task_prompt")),
        "fixed_content_package_json": (
            dict(agent.get("fixed_content_package_json") or {})
            if isinstance(agent.get("fixed_content_package_json"), dict)
            else {}
        ),
        "send_webhook_url": _text(agent.get("send_webhook_url")),
        "need_human_review": bool(agent.get("need_human_review")),
        "status": _text(agent.get("status")),
    }


def _public_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload = dict(row)
    for key, value in list(payload.items()):
        if key.endswith("_json"):
            payload[key] = _json_obj(value)
        elif isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


class AutomationAgentRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def _one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            return _public_row(dict(row)) if row else None

    def _all(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params or {}).mappings().fetchall()
            return [_public_row(dict(row)) or {} for row in rows]

    def _write_one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            session.commit()
            return _public_row(dict(row)) if row else None

    def _write_all(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params or {}).mappings().fetchall()
            session.commit()
            return [_public_row(dict(row)) or {} for row in rows]

    def list_agents(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT a.*,
                   p.name AS bound_package_name,
                   COUNT(*) OVER () AS total_count
            FROM automation_agent_runtime_config a
            LEFT JOIN ai_audience_package p ON p.package_key = a.bound_package_key
            WHERE a.status <> 'archived'
            ORDER BY a.updated_at DESC, a.id DESC
            LIMIT :limit
            """,
            {"limit": max(1, min(int(limit or 200), 200))},
        )

    def get_agent(self, agent_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT a.*, p.name AS bound_package_name
            FROM automation_agent_runtime_config a
            LEFT JOIN ai_audience_package p ON p.package_key = a.bound_package_key
            WHERE a.id = :agent_id
            LIMIT 1
            """,
            {"agent_id": int(agent_id)},
        )

    def get_agent_by_code(self, agent_code: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT a.*, p.name AS bound_package_name
            FROM automation_agent_runtime_config a
            LEFT JOIN ai_audience_package p ON p.package_key = a.bound_package_key
            WHERE a.agent_code = :agent_code
              AND a.status <> 'archived'
            ORDER BY a.id DESC
            LIMIT 1
            """,
            {"agent_code": _text(agent_code)},
        )

    def get_package_by_key(self, package_key: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM ai_audience_package WHERE package_key = :package_key LIMIT 1",
            {"package_key": _text(package_key)},
        )

    def resolve_external_userid_for_unionid(self, unionid: str) -> str:
        with self._session_factory() as session:
            resolution = SQLAlchemyIdentityResolver(session).resolve(ResolvePersonIdentityRequest(unionid=_text(unionid) or None))
        identity = resolution.identity if resolution.status == "resolved" else None
        return _text(identity.external_userid if identity else "")

    def get_bound_audience_context_for_item(self, *, batch_id: str, agent_code: str, external_userid: str) -> dict[str, Any]:
        batch_id = _text(batch_id)
        agent_code = _text(agent_code)
        external_userid = _text(external_userid)
        if not batch_id or not external_userid:
            return {}
        try:
            with self._session_factory() as session:
                batch_row = (
                    session.execute(
                        text(
                            """
                        SELECT *
                        FROM automation_agent_webhook_batch
                        WHERE batch_id = :batch_id
                          AND (:agent_code = '' OR agent_code = :agent_code)
                        LIMIT 1
                        """
                        ),
                        {"batch_id": batch_id, "agent_code": agent_code},
                    )
                    .mappings()
                    .fetchone()
                )
                if not batch_row:
                    return {}
                batch = _public_row(dict(batch_row)) or {}
                package_key = _text(batch.get("bound_package_key"))
                run_id = _text(batch.get("refresh_run_id"))
                source_event_type = _text(batch.get("source_event_type"))
                event_type = source_event_type.rsplit(".", 1)[-1] if source_event_type else ""
                identity_row = (
                    session.execute(
                        text(
                            """
                        SELECT unionid
                        FROM crm_user_identity
                        WHERE primary_external_userid = :external_userid
                           OR external_userids_json @> jsonb_build_array(CAST(:external_userid AS text))
                           OR external_userids_json @> jsonb_build_array(
                               jsonb_build_object('external_userid', CAST(:external_userid AS text))
                           )
                        LIMIT 1
                        """
                        ),
                        {"external_userid": external_userid},
                    )
                    .mappings()
                    .fetchone()
                )
                unionid = _text((identity_row or {}).get("unionid"))
                event_row = None
                if package_key and unionid:
                    event_row = (
                        session.execute(
                            text(
                                """
                            SELECT e.*, p.package_key, p.name AS package_name
                            FROM ai_audience_package p
                            JOIN ai_audience_member_event e ON e.package_id = p.id
                            WHERE p.package_key = :package_key
                              AND e.unionid = :unionid
                              AND (:event_type = '' OR e.event_type = :event_type)
                            ORDER BY
                              CASE WHEN :run_id <> '' AND e.run_id::text = :run_id THEN 0 ELSE 1 END,
                              e.occurred_at DESC,
                              e.id DESC
                            LIMIT 1
                            """
                            ),
                            {
                                "package_key": package_key,
                                "unionid": unionid,
                                "event_type": event_type,
                                "run_id": run_id,
                            },
                        )
                        .mappings()
                        .fetchone()
                    )
                current_row = None
                if package_key and unionid:
                    current_row = (
                        session.execute(
                            text(
                                """
                            SELECT c.*, p.package_key, p.name AS package_name
                            FROM ai_audience_package p
                            JOIN ai_audience_member_current c ON c.package_id = p.id
                            WHERE p.package_key = :package_key
                              AND c.unionid = :unionid
                            ORDER BY c.updated_at DESC, c.id DESC
                            LIMIT 1
                            """
                            ),
                            {"package_key": package_key, "unionid": unionid},
                        )
                        .mappings()
                        .fetchone()
                    )
                return {
                    "batch": batch,
                    "member_event": _public_row(dict(event_row)) if event_row else {},
                    "member_current": _public_row(dict(current_row)) if current_row else {},
                }
        except SQLAlchemyError:
            return {}

    def list_questionnaire_submission_answers(
        self,
        *,
        submission_id: int | str = 0,
        questionnaire_id: int | str = 0,
        external_userid: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_submission_id = _safe_int(submission_id)
        safe_questionnaire_id = _safe_int(questionnaire_id)
        safe_limit = max(1, min(int(limit or 100), 200))
        external_userid = _text(external_userid)
        if safe_submission_id <= 0 and safe_questionnaire_id <= 0 and not external_userid:
            return []
        try:
            identity_clause = ""
            resolved_unionid = ""
            if external_userid:
                identity = self._one(
                    """
                    SELECT unionid
                    FROM crm_user_identity
                    WHERE primary_external_userid = :external_userid
                       OR external_userids_json @> jsonb_build_array(CAST(:external_userid AS text))
                       OR external_userids_json @> jsonb_build_array(
                           jsonb_build_object('external_userid', CAST(:external_userid AS text))
                       )
                    LIMIT 1
                    """,
                    {"external_userid": external_userid},
                )
                resolved_unionid = _text((identity or {}).get("unionid"))
                if not resolved_unionid:
                    return []
                identity_clause = "AND s.unionid = :unionid AND s.unionid <> ''"
            return self._all(
                f"""
                SELECT
                    s.id AS submission_id,
                    s.questionnaire_id,
                    COALESCE(NULLIF(q.title, ''), NULLIF(q.name, ''), q.slug, '未命名问卷') AS questionnaire_title,
                    s.submitted_at,
                    a.question_id,
                    COALESCE(NULLIF(qq.title, ''), NULLIF(a.question_title_snapshot, ''), '未命名问题') AS question,
                    COALESCE(NULLIF(a.question_type, ''), qq.type, '') AS question_type,
                    a.selected_option_texts_snapshot,
                    a.text_value,
                    a.score_contribution,
                    a.selected_option_tags_snapshot
                FROM questionnaire_submissions s
                LEFT JOIN questionnaires q ON q.id = s.questionnaire_id
                LEFT JOIN questionnaire_submission_answers a ON a.submission_id = s.id
                LEFT JOIN questionnaire_questions qq ON qq.id = a.question_id
                WHERE (:submission_id <= 0 OR s.id = :submission_id)
                  AND (:questionnaire_id <= 0 OR s.questionnaire_id = :questionnaire_id)
                  {identity_clause}
                ORDER BY s.submitted_at DESC NULLS LAST, s.id DESC, a.id ASC
                LIMIT :limit
                """,
                {
                    "submission_id": safe_submission_id,
                    "questionnaire_id": safe_questionnaire_id,
                    "unionid": resolved_unionid,
                    "limit": safe_limit,
                },
            )
        except (SQLAlchemyError, ValueError):
            return []

    def create_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._write_one(
            """
            INSERT INTO automation_agent_runtime_config (
                agent_code, agent_name, automation_type, bound_package_key, status,
                draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                draft_version, published_version, fixed_content_package_json, send_webhook_url,
                created_at, updated_at
            ) VALUES (
                :agent_code, :agent_name, :automation_type, :bound_package_key, :status,
                :role_prompt, :task_prompt, :role_prompt, :task_prompt,
                1, 1, CAST(:fixed_content_package_json AS jsonb), :send_webhook_url,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "agent_code": _text(payload.get("agent_code")),
                "agent_name": _text(payload.get("agent_name")),
                "automation_type": _text(payload.get("automation_type")) or "agent",
                "bound_package_key": _text(payload.get("bound_package_key")),
                "status": _text(payload.get("status")) or "active",
                "role_prompt": _text(payload.get("role_prompt")),
                "task_prompt": _text(payload.get("task_prompt")),
                "fixed_content_package_json": _json_dumps(payload.get("fixed_content_package") or {}),
                "send_webhook_url": _text(payload.get("send_webhook_url")),
            },
        )
        if not row:
            raise RuntimeError("automation agent create failed")
        return row

    def update_agent(self, agent_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get_agent(agent_id)
        if not existing:
            return None
        merged = {
            "agent_name": _text(payload.get("agent_name")) if "agent_name" in payload else _text(existing.get("agent_name")),
            "automation_type": _text(payload.get("automation_type")) if "automation_type" in payload else _text(existing.get("automation_type") or "agent"),
            "bound_package_key": _text(payload.get("bound_package_key")) if "bound_package_key" in payload else _text(existing.get("bound_package_key")),
            "status": _text(payload.get("status")) if "status" in payload else _text(existing.get("status")),
            "role_prompt": _text(payload.get("role_prompt")) if "role_prompt" in payload else _text(existing.get("draft_role_prompt")),
            "task_prompt": _text(payload.get("task_prompt")) if "task_prompt" in payload else _text(existing.get("draft_task_prompt")),
            "send_webhook_url": (_text(payload.get("send_webhook_url")) if "send_webhook_url" in payload else _text(existing.get("send_webhook_url"))),
            "fixed_content_package": (
                payload.get("fixed_content_package") if "fixed_content_package" in payload else existing.get("fixed_content_package_json") or {}
            ),
        }
        draft_changed = (
            merged["role_prompt"] != _text(existing.get("draft_role_prompt"))
            or merged["task_prompt"] != _text(existing.get("draft_task_prompt"))
        )
        return self._write_one(
            """
            UPDATE automation_agent_runtime_config
            SET agent_name = :agent_name,
                automation_type = :automation_type,
                bound_package_key = :bound_package_key,
                status = :status,
                draft_role_prompt = :role_prompt,
                draft_task_prompt = :task_prompt,
                draft_version = CASE WHEN :draft_changed THEN draft_version + 1 ELSE draft_version END,
                fixed_content_package_json = CAST(:fixed_content_package_json AS jsonb),
                send_webhook_url = :send_webhook_url,
                archived_at = CASE WHEN :status = 'archived' THEN COALESCE(archived_at, CURRENT_TIMESTAMP) ELSE archived_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :agent_id
            RETURNING *
            """,
            {
                "agent_id": int(agent_id),
                "agent_name": merged["agent_name"],
                "automation_type": merged["automation_type"] or "agent",
                "bound_package_key": merged["bound_package_key"],
                "status": merged["status"] or "active",
                "role_prompt": merged["role_prompt"],
                "task_prompt": merged["task_prompt"],
                "draft_changed": draft_changed,
                "send_webhook_url": merged["send_webhook_url"],
                "fixed_content_package_json": _json_dumps(merged["fixed_content_package"]),
            },
        )

    def publish_agent(self, agent_id: int) -> dict[str, Any] | None:
        return self._write_one(
            """
            UPDATE automation_agent_runtime_config
            SET published_role_prompt = draft_role_prompt,
                published_task_prompt = draft_task_prompt,
                published_version = draft_version,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :agent_id
            RETURNING *
            """,
            {"agent_id": int(agent_id)},
        )

    def set_status(self, agent_id: int, status: str) -> dict[str, Any] | None:
        return self._write_one(
            """
            UPDATE automation_agent_runtime_config
            SET status = :status,
                archived_at = CASE WHEN :status = 'archived' THEN COALESCE(archived_at, CURRENT_TIMESTAMP) ELSE archived_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :agent_id
            RETURNING *
            """,
            {"agent_id": int(agent_id), "status": _text(status)},
        )

    def next_copy_code(self, agent_code: str) -> str:
        base = f"{_text(agent_code)}_copy"
        rows = self._all(
            """
            SELECT agent_code
            FROM automation_agent_runtime_config
            WHERE agent_code LIKE :prefix
            """,
            {"prefix": f"{base}_%"},
        )
        existing = {_text(row.get("agent_code")) for row in rows}
        for index in range(1, 1000):
            candidate = f"{base}_{index:03d}"
            if candidate not in existing:
                return candidate
        raise RuntimeError("agent copy code exhausted")

    def create_batch(
        self,
        *,
        batch_id: str,
        agent: dict[str, Any],
        headers: dict[str, Any],
        payload: Any,
        external_userids: list[str],
        received_count: int,
        idempotency_key: str,
        source_event_type: str,
        refresh_run_id: str,
    ) -> tuple[dict[str, Any], int]:
        with self._session_factory() as session:
            unionids = resolved_unionids_for_external_userids_with_sqlalchemy(session, external_userids)
            deduped_count = len(unionids)
            snapshot = _agent_snapshot(agent)
            row = (
                session.execute(
                    text(
                        """
                    INSERT INTO automation_agent_webhook_batch (
                        batch_id, agent_code, bound_package_key, source_event_type, refresh_run_id,
                        idempotency_key, received_count, deduped_count, accepted_count, status,
                        request_headers_json, request_payload_json,
                        agent_published_version, agent_config_snapshot_json, created_at
                    ) VALUES (
                        :batch_id, :agent_code, :bound_package_key, :source_event_type, :refresh_run_id,
                        :idempotency_key, :received_count, :deduped_count, :accepted_count, 'queued',
                        CAST(:headers_json AS jsonb), CAST(:payload_json AS jsonb),
                        :agent_published_version, CAST(:agent_config_snapshot_json AS jsonb), CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (idempotency_key) WHERE idempotency_key <> ''
                    DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    RETURNING *, (xmax = 0) AS created_on_insert
                    """
                    ),
                    {
                        "batch_id": batch_id,
                        "agent_code": _text(agent.get("agent_code")),
                        "bound_package_key": _text(agent.get("bound_package_key")),
                        "source_event_type": source_event_type,
                        "refresh_run_id": refresh_run_id,
                        "idempotency_key": idempotency_key,
                        "received_count": int(received_count),
                        "deduped_count": deduped_count,
                        "accepted_count": deduped_count,
                        "headers_json": _json_dumps(headers),
                        "payload_json": _json_dumps(payload),
                        "agent_published_version": int(snapshot["published_version"]),
                        "agent_config_snapshot_json": _json_dumps(snapshot),
                    },
                )
                .mappings()
                .one()
            )
            batch = dict(row)
            item_input = [
                {
                    "unionid": unionid,
                    "external_event_id": f"agent:{_text(batch['agent_code'])}:{unionid}:{_text(batch['batch_id'])}",
                }
                for unionid in unionids
            ]
            if item_input and bool(batch.get("created_on_insert")):
                session.execute(
                    text(
                        """
                        WITH input AS (
                            SELECT *
                            FROM jsonb_to_recordset(CAST(:items_json AS jsonb)) AS row(
                                unionid text,
                                external_event_id text
                            )
                        )
                        INSERT INTO automation_agent_webhook_item (
                            batch_id, agent_code, unionid, external_event_id, status, created_at
                        )
                        SELECT
                            :batch_id, :agent_code, unionid, external_event_id,
                            'queued', CURRENT_TIMESTAMP
                        FROM input
                        ON CONFLICT (batch_id, unionid) WHERE unionid <> '' DO UPDATE
                        SET updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "batch_id": _text(batch["batch_id"]),
                        "agent_code": _text(batch.get("agent_code")),
                        "items_json": _json_dumps(item_input),
                    },
                )
            item_rows = (
                session.execute(
                    text(
                        """
                        SELECT id
                        FROM automation_agent_webhook_item
                        WHERE batch_id = :batch_id
                        ORDER BY id ASC
                        """
                    ),
                    {"batch_id": _text(batch["batch_id"])},
                )
                .mappings()
                .all()
            )
            self._enqueue_prepare_events_in_session(
                session,
                batch=batch,
                items=[dict(item) for item in item_rows],
                operator="automation_agent_webhook",
                parent_execution_id="",
            )
            refreshed_batch_row = (
                session.execute(
                    text("SELECT * FROM automation_agent_webhook_batch WHERE batch_id = :batch_id"),
                    {"batch_id": _text(batch["batch_id"])},
                )
                .mappings()
                .one()
            )
            session.commit()
        refreshed_batch = _public_row(dict(refreshed_batch_row)) or _public_row(batch) or {}
        return refreshed_batch, _safe_int(refreshed_batch.get("accepted_count"))

    def _enqueue_prepare_events_in_session(
        self,
        session: Session,
        *,
        batch: dict[str, Any],
        items: list[dict[str, Any]],
        operator: str,
        parent_execution_id: str,
    ) -> list[dict[str, Any]]:
        batch_id = _text(batch.get("batch_id"))
        requests = [
            InternalEventCreateRequest(
                event_type="automation_agent.item.prepare",
                aggregate_type="automation_agent_webhook_item",
                aggregate_id=str(int(item["id"])),
                subject_type="automation_agent_webhook_batch",
                subject_id=batch_id,
                idempotency_key=f"automation_agent.item.prepare:{int(item['id'])}",
                source_module="aicrm_next.extensions.ai.automation_agents",
                source_command_id=batch_id,
                correlation_id=batch_id,
                execution_id=f"exe_automation_agent_item_{int(item['id'])}",
                parent_execution_id=_text(parent_execution_id),
                payload={
                    "item_id": int(item["id"]),
                    "batch_id": batch_id,
                    "agent_code": _text(batch.get("agent_code")),
                    "agent_published_version": _safe_int(batch.get("agent_published_version")),
                },
                payload_summary={
                    "item_id": int(item["id"]),
                    "batch_id": batch_id,
                    "agent_code": _text(batch.get("agent_code")),
                },
                context=CommandContext(
                    actor_id=_text(operator) or "automation_agent_queue_bridge",
                    actor_type="system",
                    source_route="automation_agent.item.prepare",
                ),
            )
            for item in items
        ]
        outboxes = enqueue_internal_event_outbox_batch_in_session(session, requests)
        links = [
            {"item_id": int(item["id"]), "outbox_id": _text(outbox.get("outbox_id"))}
            for item, outbox in zip(items, outboxes, strict=True)
        ]
        if links:
            session.execute(
                text(
                    """
                    WITH input AS (
                        SELECT *
                        FROM jsonb_to_recordset(CAST(:links_json AS jsonb)) AS row(
                            item_id bigint,
                            outbox_id text
                        )
                    )
                    UPDATE automation_agent_webhook_item item
                    SET prepare_outbox_id = input.outbox_id,
                        status = CASE
                            WHEN item.status IN ('queued', 'failed_retryable') THEN 'prepare_queued'
                            ELSE item.status
                        END,
                        prepare_enqueued_at = COALESCE(item.prepare_enqueued_at, CURRENT_TIMESTAMP),
                        state_version = CASE
                            WHEN item.status IN ('queued', 'failed_retryable') THEN item.state_version + 1
                            ELSE item.state_version
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    FROM input
                    WHERE item.id = input.item_id
                      AND item.batch_id = :batch_id
                    """
                ),
                {"batch_id": batch_id, "links_json": _json_dumps(links)},
            )
        session.execute(
            text(
                """
                UPDATE automation_agent_webhook_batch batch
                SET status = CASE WHEN status = 'queued' THEN 'running' ELSE status END,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    prepare_enqueued_at = COALESCE(prepare_enqueued_at, CURRENT_TIMESTAMP),
                    prepare_enqueued_count = (
                        SELECT COUNT(*)
                        FROM automation_agent_webhook_item item
                        WHERE item.batch_id = batch.batch_id
                          AND item.prepare_outbox_id <> ''
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE batch_id = :batch_id
                """
            ),
            {"batch_id": batch_id},
        )
        return outboxes

    def enqueue_batch_item_prepare_events(
        self,
        batch_id: str,
        *,
        operator: str = "automation_agent_queue_bridge",
        parent_execution_id: str = "",
    ) -> dict[str, Any]:
        batch_id = _text(batch_id)
        with self._session_factory() as session:
            batch_row = session.execute(
                text("SELECT * FROM automation_agent_webhook_batch WHERE batch_id = :batch_id FOR UPDATE"),
                {"batch_id": batch_id},
            ).mappings().fetchone()
            if not batch_row:
                return {"ok": False, "batch_id": batch_id, "error": "batch_not_found"}
            item_rows = session.execute(
                text(
                    """
                    SELECT id
                    FROM automation_agent_webhook_item
                    WHERE batch_id = :batch_id
                    ORDER BY id ASC
                    """
                ),
                {"batch_id": batch_id},
            ).mappings().all()
            outboxes = self._enqueue_prepare_events_in_session(
                session,
                batch=dict(batch_row),
                items=[dict(item) for item in item_rows],
                operator=operator,
                parent_execution_id=parent_execution_id,
            )
            session.commit()
        return {
            "ok": True,
            "batch_id": batch_id,
            "item_count": len(item_rows),
            "prepare_event_count": len(outboxes),
            "real_external_call_executed": False,
        }

    def list_queued_items(self, batch_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM automation_agent_webhook_item
            WHERE batch_id = :batch_id
              AND status IN ('queued', 'failed_retryable')
            ORDER BY id ASC
            """,
            {"batch_id": _text(batch_id)},
        )

    def list_items_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM automation_agent_webhook_item
            WHERE batch_id = :batch_id
            ORDER BY id ASC
            """,
            {"batch_id": _text(batch_id)},
        )

    def get_pipeline_item(self, item_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT item.*,
                   batch.agent_published_version,
                   batch.agent_config_snapshot_json,
                   batch.accepted_count AS batch_accepted_count
            FROM automation_agent_webhook_item item
            JOIN automation_agent_webhook_batch batch ON batch.batch_id = item.batch_id
            WHERE item.id = :item_id
            LIMIT 1
            """,
            {"item_id": int(item_id)},
        )

    def claim_item_for_prepare(self, item_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    UPDATE automation_agent_webhook_item
                    SET status = 'preparing',
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        state_version = state_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND status IN ('queued', 'prepare_queued', 'preparing', 'running', 'failed_retryable')
                    RETURNING *
                    """
                ),
                {"item_id": int(item_id)},
            ).mappings().fetchone()
            session.commit()
        if not row:
            existing = self.get_pipeline_item(item_id)
            if existing is not None:
                existing["pipeline_claimed"] = False
            return existing
        claimed = self.get_pipeline_item(item_id)
        if claimed is not None:
            claimed["pipeline_claimed"] = True
        return claimed

    def plan_generation_effect(
        self,
        item_id: int,
        *,
        payload: dict[str, Any],
        payload_summary: dict[str, Any],
        parent_execution_id: str,
        source_event_id: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            item = session.execute(
                text("SELECT * FROM automation_agent_webhook_item WHERE id = :item_id FOR UPDATE"),
                {"item_id": int(item_id)},
            ).mappings().fetchone()
            if not item:
                return {"ok": False, "error": "item_not_found", "item_id": int(item_id)}
            existing_job_id = _safe_int(item.get("generation_effect_job_id"))
            if existing_job_id > 0:
                session.commit()
                return {
                    "ok": True,
                    "deduplicated": True,
                    "item_id": int(item_id),
                    "external_effect_job_id": existing_job_id,
                }
            if _text(item.get("status")) not in {"preparing", "running", "failed_retryable"}:
                session.commit()
                return {
                    "ok": _text(item.get("status")) in {"generation_queued", "generation_succeeded", "send_plan_created"},
                    "deduplicated": True,
                    "item_id": int(item_id),
                    "status": _text(item.get("status")),
                    "error": "item_state_conflict",
                }
            job = ExternalEffectService().plan_effect(
                effect_type=AI_AGENT_GENERATE,
                adapter_name="ai_agent_generation",
                operation="generate",
                target_type="automation_agent_webhook_item",
                target_id=str(int(item_id)),
                payload=payload,
                payload_summary=payload_summary,
                business_type="automation_agent_generation",
                business_id=str(int(item_id)),
                source_module="aicrm_next.extensions.ai.automation_agents",
                source_event_id=_text(source_event_id),
                source_command_id=_text(item.get("batch_id")),
                idempotency_key=(
                    f"automation_agent.generation:{int(item_id)}:"
                    f"v{_safe_int(payload.get('agent_published_version'))}"
                ),
                execution_id="exe_ai_generation_" + uuid4().hex,
                parent_execution_id=_text(parent_execution_id),
                lane="ai_generation",
                ordering_key=f"automation_agent_item:{int(item_id)}",
                fairness_key=f"automation_agent_batch:{_text(item.get('batch_id'))}",
                execution_mode="execute",
                status="queued",
                max_attempts=5,
                context=CommandContext(
                    actor_id="automation_agent_item_prepare_consumer",
                    actor_type="system",
                    source_route="automation_agent.item.prepare",
                ),
                connection=session,
            )
            job_id = _safe_int(job.get("id"))
            updated = session.execute(
                text(
                    """
                    UPDATE automation_agent_webhook_item
                    SET generation_effect_job_id = :job_id,
                        status = 'generation_queued',
                        owner_userid = :owner_userid,
                        context_snapshot_json = CAST(:context_json AS jsonb),
                        prompt_preview = :prompt_preview,
                        prepared_at = COALESCE(prepared_at, CURRENT_TIMESTAMP),
                        state_version = state_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND generation_effect_job_id IS NULL
                      AND status IN ('preparing', 'running', 'failed_retryable')
                    RETURNING batch_id
                    """
                ),
                {
                    "item_id": int(item_id),
                    "job_id": job_id,
                    "owner_userid": _text(payload.get("owner_userid")),
                    "context_json": _json_dumps(payload.get("context_snapshot") or {}),
                    "prompt_preview": _text(payload.get("prompt_preview"))[:2000],
                },
            ).mappings().fetchone()
            if not updated:
                raise RuntimeError("automation agent generation effect link CAS failed")
            session.execute(
                text(
                    """
                    UPDATE automation_agent_webhook_batch
                    SET prepared_count = prepared_count + 1,
                        generation_queued_count = generation_queued_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE batch_id = :batch_id
                    """
                ),
                {"batch_id": _text(updated.get("batch_id"))},
            )
            session.commit()
        return {
            "ok": True,
            "deduplicated": False,
            "item_id": int(item_id),
            "external_effect_job_id": job_id,
            "lane": "ai_generation",
            "real_external_call_executed": False,
        }

    def complete_item_send_plan(
        self,
        item_id: int,
        *,
        owner_userid: str,
        context: dict[str, Any],
        prompt_preview: str,
        raw_output: str,
        content_package: dict[str, Any],
        callback_payload: dict[str, Any],
        callback_response: dict[str, Any],
        generated: bool,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            updated = session.execute(
                text(
                    """
                    UPDATE automation_agent_webhook_item
                    SET owner_userid = :owner_userid,
                        status = 'send_plan_created',
                        context_snapshot_json = CAST(:context_json AS jsonb),
                        prompt_preview = :prompt_preview,
                        raw_agent_output = :raw_output,
                        content_package_json = CAST(:content_package_json AS jsonb),
                        callback_payload_json = CAST(:callback_payload_json AS jsonb),
                        callback_status = 'succeeded',
                        callback_response_json = CAST(:callback_response_json AS jsonb),
                        error_code = '',
                        error_message = '',
                        prepared_at = COALESCE(prepared_at, CURRENT_TIMESTAMP),
                        generation_completed_at = CASE
                            WHEN :generated THEN COALESCE(generation_completed_at, CURRENT_TIMESTAMP)
                            ELSE generation_completed_at
                        END,
                        send_plan_created_at = COALESCE(send_plan_created_at, CURRENT_TIMESTAMP),
                        finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                        state_version = state_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND status <> 'send_plan_created'
                      AND status <> 'failed'
                    RETURNING batch_id, prepared_at, generation_completed_at
                    """
                ),
                {
                    "item_id": int(item_id),
                    "owner_userid": _text(owner_userid),
                    "context_json": _json_dumps(context),
                    "prompt_preview": _text(prompt_preview)[:2000],
                    "raw_output": _text(raw_output),
                    "content_package_json": _json_dumps(content_package),
                    "callback_payload_json": _json_dumps(callback_payload),
                    "callback_response_json": _json_dumps(callback_response),
                    "generated": bool(generated),
                },
            ).mappings().fetchone()
            if updated:
                session.execute(
                    text(
                        """
                        UPDATE automation_agent_webhook_batch
                        SET prepared_count = prepared_count + CASE WHEN :generated THEN 0 ELSE 1 END,
                            generation_succeeded_count = generation_succeeded_count + CASE WHEN :generated THEN 1 ELSE 0 END,
                            send_plan_created_count = send_plan_created_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE batch_id = :batch_id
                        """
                    ),
                    {"batch_id": _text(updated.get("batch_id")), "generated": bool(generated)},
                )
                self._finalize_batch_in_session(session, _text(updated.get("batch_id")))
            session.commit()
        current = self.get_pipeline_item(item_id) or {}
        return {
            "ok": _text(current.get("status")) == "send_plan_created",
            "deduplicated": updated is None,
            "item": current,
        }

    def fail_pipeline_item(
        self,
        item_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
        owner_userid: str = "",
        prompt_preview: str = "",
    ) -> dict[str, Any]:
        status = "failed_retryable" if retryable else "failed"
        with self._session_factory() as session:
            updated = session.execute(
                text(
                    """
                    UPDATE automation_agent_webhook_item
                    SET owner_userid = CASE WHEN :owner_userid <> '' THEN :owner_userid ELSE owner_userid END,
                        status = :status,
                        context_snapshot_json = CASE
                            WHEN CAST(:context_json AS jsonb) <> '{}'::jsonb THEN CAST(:context_json AS jsonb)
                            ELSE context_snapshot_json
                        END,
                        prompt_preview = CASE WHEN :prompt_preview <> '' THEN :prompt_preview ELSE prompt_preview END,
                        error_code = :error_code,
                        error_message = :error_message,
                        finished_at = CASE WHEN :retryable THEN NULL ELSE CURRENT_TIMESTAMP END,
                        state_version = state_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND status NOT IN ('send_plan_created', 'failed')
                    RETURNING batch_id
                    """
                ),
                {
                    "item_id": int(item_id),
                    "owner_userid": _text(owner_userid),
                    "status": status,
                    "context_json": _json_dumps(context or {}),
                    "prompt_preview": _text(prompt_preview)[:2000],
                    "error_code": _text(error_code) or "automation_agent_item_failed",
                    "error_message": _text(error_message)[:1000],
                    "retryable": bool(retryable),
                },
            ).mappings().fetchone()
            if updated and not retryable:
                session.execute(
                    text(
                        """
                        UPDATE automation_agent_webhook_batch
                        SET failed_count = failed_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE batch_id = :batch_id
                        """
                    ),
                    {"batch_id": _text(updated.get("batch_id"))},
                )
                self._finalize_batch_in_session(session, _text(updated.get("batch_id")))
            session.commit()
        return {
            "ok": False,
            "item_id": int(item_id),
            "status": status,
            "error": _text(error_code),
            "detail": _text(error_message)[:1000],
        }

    @staticmethod
    def _finalize_batch_in_session(session: Session, batch_id: str) -> None:
        session.execute(
            text(
                """
                UPDATE automation_agent_webhook_batch
                SET status = CASE
                        WHEN send_plan_created_count + failed_count < accepted_count THEN status
                        WHEN failed_count = 0 THEN 'succeeded'
                        WHEN send_plan_created_count = 0 THEN 'failed'
                        ELSE 'partial_failed'
                    END,
                    finished_at = CASE
                        WHEN send_plan_created_count + failed_count >= accepted_count
                            THEN COALESCE(finished_at, CURRENT_TIMESTAMP)
                        ELSE finished_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE batch_id = :batch_id
                """
            ),
            {"batch_id": _text(batch_id)},
        )

    def mark_batch_status(self, batch_id: str, status: str) -> None:
        self._write_one(
            """
            UPDATE automation_agent_webhook_batch
            SET status = :status,
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                finished_at = CASE WHEN :status IN ('succeeded', 'partial_failed', 'failed') THEN CURRENT_TIMESTAMP ELSE finished_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE batch_id = :batch_id
            RETURNING *
            """,
            {"batch_id": _text(batch_id), "status": _text(status)},
        )

    def update_item(self, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "owner_userid",
            "status",
            "context_snapshot_json",
            "prompt_preview",
            "raw_agent_output",
            "content_package_json",
            "callback_payload_json",
            "callback_status",
            "callback_response_json",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
        }
        columns = [key for key in payload if key in allowed]
        if not columns:
            return self._one("SELECT * FROM automation_agent_webhook_item WHERE id = :id", {"id": int(item_id)}) or {}
        params: dict[str, Any] = {"id": int(item_id)}
        assignments: list[str] = []
        for index, column in enumerate(columns):
            name = f"p{index}"
            value = payload[column]
            if column.endswith("_json"):
                assignments.append(f"{column} = CAST(:{name} AS jsonb)")
                params[name] = _json_dumps(value)
            elif column in {"started_at", "finished_at"} and value == "now":
                assignments.append(f"{column} = CURRENT_TIMESTAMP")
            else:
                assignments.append(f"{column} = :{name}")
                params[name] = value
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        return (
            self._write_one(
                f"UPDATE automation_agent_webhook_item SET {', '.join(assignments)} WHERE id = :id RETURNING *",
                params,
            )
            or {}
        )


def build_automation_agent_repository() -> AutomationAgentRepository:
    return AutomationAgentRepository()
