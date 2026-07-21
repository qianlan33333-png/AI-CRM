"""Repository and dependency continuation for durable welcome-message effects."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD, WECOM_WELCOME_MESSAGE_SEND
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    enqueue_external_effect_settled_rows_in_session,
)
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import fixture_mode


WELCOME_EFFECT_BUSINESS_TYPE = "channel_welcome_effect_graph"
WELCOME_MEDIA_COMPLETION_CONSUMER = "channel_welcome_media_dependency_release"
STATUS_URL_PREFIX = "/api/admin/executions/"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: Any) -> str:
    normalized = _clean(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def _execution_id(kind: str) -> str:
    return f"exe_channel_welcome_{kind}_{uuid4().hex}"


@dataclass(frozen=True)
class WelcomeEffectGraphRequest:
    idempotency_key: str
    channel_id: int
    corp_id: str
    external_userid: str
    follow_user_userid: str
    welcome_code: str
    target_type: str
    target_id: str
    target_payload: dict[str, Any]
    text_content: str
    attachments: tuple[dict[str, Any], ...]
    actor_id: str
    source_event_id: str
    scene_value: str
    source_route: str = "channel_entry.process_channel_entry"
    parent_execution_id: str = ""


class WelcomeEffectGraphRepository(Protocol):
    def plan(self, request: WelcomeEffectGraphRequest) -> dict[str, Any]: ...

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]: ...

    def settle_effect(self, effect_job_id: int, *, status: str, attempt_id: str = "") -> dict[str, Any]: ...

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]: ...


def _response(
    *,
    execution_id: str,
    final_job_id: int,
    upload_job_ids: list[int],
    status: str,
    duplicate: bool,
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "parent_execution_id": "",
        "status": status,
        "duplicate": duplicate,
        "external_effect_job_id": final_job_id,
        "final_effect_job_id": final_job_id,
        "upload_effect_job_ids": upload_job_ids,
        "external_effect_job_ids": [*upload_job_ids, final_job_id],
        "status_url": f"{STATUS_URL_PREFIX}{execution_id}",
        "real_external_call_executed": False,
    }


def _provider_attachment(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize an already provider-ready attachment without doing I/O."""

    msgtype = _clean(item.get("msgtype"))
    if msgtype in {"image", "file"}:
        nested = item.get(msgtype) if isinstance(item.get(msgtype), dict) else {}
        media_id = _clean(nested.get("media_id") or item.get("media_id"))
        if not media_id:
            raise ValueError("welcome provider attachment media_id is required")
        return {"msgtype": msgtype, msgtype: {"media_id": media_id}}
    if msgtype == "miniprogram":
        nested = item.get("miniprogram") if isinstance(item.get("miniprogram"), dict) else item
        appid = _clean(nested.get("appid"))
        page = _clean(nested.get("page") or nested.get("pagepath"))
        title = _clean(nested.get("title"))
        media_id = _clean(nested.get("pic_media_id") or nested.get("thumb_media_id"))
        if not all((appid, page, title, media_id)):
            raise ValueError("welcome provider miniprogram attachment is incomplete")
        return {
            "msgtype": "miniprogram",
            "miniprogram": {"appid": appid, "page": page, "title": title, "pic_media_id": media_id},
        }
    if msgtype == "link":
        nested = item.get("link") if isinstance(item.get("link"), dict) else item
        title = _clean(nested.get("title") or nested.get("name"))
        url = _clean(nested.get("url") or nested.get("join_url"))
        if not title or not url:
            raise ValueError("welcome provider link attachment is incomplete")
        link = {"title": title, "url": url}
        if _clean(nested.get("desc") or nested.get("description")):
            link["desc"] = _clean(nested.get("desc") or nested.get("description"))
        if _clean(nested.get("picurl") or nested.get("pic_url")):
            link["picurl"] = _clean(nested.get("picurl") or nested.get("pic_url"))
        return {"msgtype": "link", "link": link}
    raise ValueError("unsupported welcome attachment msgtype")


def _request_cancel_dispatching_welcome_jobs_in_session(
    session: Session,
    *,
    graph_id: int,
    final_job_id: int,
    actor: str,
    reason: str,
    exclude_job_id: int = 0,
) -> list[int]:
    rows = (
        session.execute(
            sql_text(
                """
                UPDATE external_effect_job job
                SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                    cancel_requested_by = CASE
                        WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                    END,
                    cancel_reason = CASE
                        WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                    END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job.id IN (
                    SELECT prerequisite_effect_job_id
                    FROM channel_welcome_effect_dependency
                    WHERE graph_id = :graph_id
                    UNION
                    SELECT dependent_effect_job_id
                    FROM channel_welcome_effect_dependency
                    WHERE graph_id = :graph_id
                    UNION SELECT :final_job_id
                )
                  AND job.id <> :exclude_job_id
                  AND job.status = 'dispatching'
                  AND job.cancel_requested_at IS NULL
                  AND job.provider_call_started_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM external_effect_attempt attempt
                      WHERE attempt.job_id = job.id
                        AND attempt.provider_call_started_at IS NOT NULL
                  )
                RETURNING job.id
                """
            ),
            {
                "graph_id": int(graph_id),
                "final_job_id": int(final_job_id),
                "exclude_job_id": int(exclude_job_id),
                "actor": _clean(actor),
                "reason": _clean(reason),
            },
        )
        .scalars()
        .all()
    )
    return [int(value) for value in rows]


class SQLAlchemyWelcomeEffectGraphRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def plan(self, request: WelcomeEffectGraphRequest) -> dict[str, Any]:
        if not _clean(request.idempotency_key):
            raise ValueError("welcome effect graph idempotency_key is required")
        if not _clean(request.welcome_code):
            raise ValueError("welcome_code is required")
        with self._session_factory() as session:
            session.execute(
                sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"channel_welcome_effect:{request.idempotency_key}"},
            )
            existing = self._graph_by_key(session, request.idempotency_key)
            if existing:
                result = self._result(session, existing, duplicate=True)
                session.rollback()
                return result

            execution_id = _execution_id("root")
            graph = dict(
                session.execute(
                    sql_text(
                        """
                        INSERT INTO channel_welcome_effect_graph (
                            execution_id, parent_execution_id, idempotency_key, channel_id,
                            status, actor_id,
                            created_at, updated_at
                        ) VALUES (
                            :execution_id, :parent_execution_id, :idempotency_key, :channel_id,
                            'waiting_dependencies', :actor_id,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        ) RETURNING *
                        """
                    ),
                    {
                        "execution_id": execution_id,
                        "parent_execution_id": _clean(request.parent_execution_id),
                        "idempotency_key": _clean(request.idempotency_key),
                        "channel_id": int(request.channel_id or 0),
                        "actor_id": _clean(request.actor_id),
                    },
                )
                .mappings()
                .one()
            )
            prepared, materials = self._prepare_attachments(session, request.attachments)
            final_job = self._plan_final(session, request, execution_id, prepared, held=bool(materials))
            upload_ids: list[int] = []
            for material in materials:
                upload = self._plan_upload(session, request, execution_id, material)
                upload_ids.append(int(upload["id"]))
                session.execute(
                    sql_text(
                        """
                        INSERT INTO channel_welcome_effect_dependency (
                            graph_id, material_key, msgtype, library_kind, library_material_id,
                            upload_kind, attachment_json, prerequisite_effect_job_id,
                            dependent_effect_job_id, status, created_at, updated_at
                        ) VALUES (
                            :graph_id, :material_key, :msgtype, :library_kind, :library_material_id,
                            :upload_kind, CAST(:attachment_json AS jsonb), :upload_job_id,
                            :final_job_id, 'waiting', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "graph_id": int(graph["id"]),
                        "material_key": material["material_key"],
                        "msgtype": material["msgtype"],
                        "library_kind": material["library_kind"],
                        "library_material_id": material["material_id"],
                        "upload_kind": material["upload_kind"],
                        "attachment_json": json.dumps(material["attachment"], ensure_ascii=False, separators=(",", ":")),
                        "upload_job_id": int(upload["id"]),
                        "final_job_id": int(final_job["id"]),
                    },
                )
            graph_status = "waiting_dependencies" if upload_ids else "ready"
            session.execute(
                sql_text(
                    """
                    UPDATE channel_welcome_effect_graph
                    SET final_effect_job_id = :final_job_id, status = :status,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :graph_id
                    """
                ),
                {"final_job_id": int(final_job["id"]), "status": graph_status, "graph_id": int(graph["id"])},
            )
            session.commit()
            return _response(
                execution_id=execution_id,
                final_job_id=int(final_job["id"]),
                upload_job_ids=upload_ids,
                status=graph_status,
                duplicate=False,
            )

    @staticmethod
    def _graph_by_key(session: Session, key: str) -> dict[str, Any] | None:
        row = (
            session.execute(
                sql_text("SELECT * FROM channel_welcome_effect_graph WHERE idempotency_key = :key LIMIT 1"),
                {"key": _clean(key)},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _result(self, session: Session, graph: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        uploads = session.execute(
            sql_text(
                """
                SELECT prerequisite_effect_job_id
                FROM channel_welcome_effect_dependency
                WHERE graph_id = :graph_id ORDER BY id
                """
            ),
            {"graph_id": int(graph["id"])},
        ).scalars().all()
        return _response(
            execution_id=_clean(graph["execution_id"]),
            final_job_id=int(graph.get("final_effect_job_id") or 0),
            upload_job_ids=[int(value) for value in uploads],
            status=_clean(graph["status"]),
            duplicate=duplicate,
        )

    def _prepare_attachments(
        self,
        session: Session,
        attachments: tuple[dict[str, Any], ...],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        prepared: list[dict[str, Any]] = []
        materials_by_key: dict[str, dict[str, Any]] = {}
        for raw in attachments:
            item = dict(raw or {})
            msgtype = _clean(item.get("msgtype"))
            material_id = int(item.get("material_id") or 0)
            if material_id <= 0:
                prepared.append(_provider_attachment(item))
                continue
            if msgtype == "link":
                link = (
                    session.execute(
                        sql_text(
                            """
                            SELECT title, name, description, pic_url, join_url
                            FROM group_invite_library WHERE id = :id AND enabled
                            """
                        ),
                        {"id": material_id},
                    )
                    .mappings()
                    .first()
                )
                if not link:
                    raise ValueError(f"welcome group invite material {material_id} is unavailable")
                prepared.append(
                    _provider_attachment(
                        {
                            "msgtype": "link",
                            "title": link["title"] or link["name"],
                            "url": link["join_url"],
                            "description": link["description"],
                            "pic_url": link["pic_url"],
                        }
                    )
                )
                continue
            library_kind = {"image": "image", "file": "attachment", "miniprogram": "miniprogram"}.get(msgtype)
            upload_kind = {"image": "image", "file": "attachment", "miniprogram": "image"}.get(msgtype)
            if not library_kind or not upload_kind:
                raise ValueError("unsupported unresolved welcome material")
            row = self._material_row(session, library_kind, material_id)
            if not row:
                raise ValueError(f"welcome {library_kind} material {material_id} is unavailable")
            material_key = f"{msgtype}:{material_id}"
            if msgtype == "image":
                attachment = {"msgtype": "image", "image": {"media_dependency_key": material_key}}
            elif msgtype == "file":
                attachment = {"msgtype": "file", "file": {"media_dependency_key": material_key}}
            else:
                appid = _clean(row.get("appid"))
                page = _clean(row.get("pagepath"))
                title = _clean(row.get("title") or row.get("name"))
                if not all((appid, page, title)):
                    raise ValueError(f"welcome miniprogram material {material_id} is incomplete")
                attachment = {
                    "msgtype": "miniprogram",
                    "miniprogram": {
                        "appid": appid,
                        "page": page,
                        "title": title,
                        "pic_media_dependency_key": material_key,
                    },
                }
            prepared.append(json.loads(json.dumps(attachment, ensure_ascii=False)))
            materials_by_key.setdefault(
                material_key,
                {
                    "material_key": material_key,
                    "msgtype": msgtype,
                    "library_kind": library_kind,
                    "material_id": material_id,
                    "upload_kind": upload_kind,
                    "attachment": attachment,
                },
            )
        return prepared, list(materials_by_key.values())

    @staticmethod
    def _material_row(session: Session, kind: str, material_id: int) -> dict[str, Any] | None:
        queries = {
            "image": "SELECT id, enabled FROM image_library WHERE id = :id AND enabled",
            "attachment": "SELECT id, enabled FROM attachment_library WHERE id = :id AND enabled",
            "miniprogram": "SELECT id, name, appid, pagepath, title, enabled FROM miniprogram_library WHERE id = :id AND enabled",
        }
        row = session.execute(sql_text(queries[kind]), {"id": material_id}).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _context(request: WelcomeEffectGraphRequest, execution_id: str) -> CommandContext:
        return CommandContext(
            actor_id=_clean(request.actor_id) or _clean(request.follow_user_userid),
            actor_type="channel_entry",
            request_id=_clean(request.source_event_id),
            trace_id=execution_id,
            source_route=request.source_route,
        )

    def _plan_final(
        self,
        session: Session,
        request: WelcomeEffectGraphRequest,
        execution_id: str,
        attachments: list[dict[str, Any]],
        *,
        held: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "welcome_code": _clean(request.welcome_code),
            "external_userid": _clean(request.external_userid),
            "follow_user_userid": _clean(request.follow_user_userid),
            "channel_id": int(request.channel_id or 0),
            "scene_value": _clean(request.scene_value),
            "welcome_execution_id": execution_id,
            **dict(request.target_payload or {}),
        }
        if _clean(request.text_content):
            payload["text"] = {"content": request.text_content}
        if attachments:
            payload["attachments"] = attachments
        return ExternalEffectService().plan_effect(
            effect_type=WECOM_WELCOME_MESSAGE_SEND,
            adapter_name="wecom_welcome_message",
            operation="send",
            target_type=request.target_type,
            target_id=request.target_id,
            business_type=WELCOME_EFFECT_BUSINESS_TYPE,
            business_id=execution_id,
            source_module="channel_entry.application",
            source_event_id=request.source_event_id,
            source_command_id=f"welcome:{request.source_event_id}",
            idempotency_key=f"{request.idempotency_key}:final",
            context=self._context(request, execution_id),
            payload=payload,
            payload_summary={
                "welcome_execution_id": execution_id,
                "external_userid_present": bool(_clean(request.external_userid)),
                "external_userid_hash": _digest(request.external_userid),
                "follow_user_userid_present": bool(_clean(request.follow_user_userid)),
                "follow_user_userid_hash": _digest(request.follow_user_userid),
                "channel_id": int(request.channel_id or 0),
                "welcome_code_present": True,
                "text_present": bool(_clean(request.text_content)),
                "attachment_count": len(attachments),
                "dependency_count": sum(
                    1 for item in attachments if "dependency_key" in json.dumps(item, ensure_ascii=False)
                ),
            },
            status="planned" if held else "queued",
            scheduled_at=_now(),
            available_at=_now(),
            execution_mode="execute",
            execution_id=_execution_id("send"),
            parent_execution_id=execution_id,
            lane="wecom_interactive",
            ordering_key=f"welcome:{request.corp_id}:{request.external_userid}:{request.follow_user_userid}",
            fairness_key=f"channel:{int(request.channel_id or 0)}",
            connection=session,
        )

    def _plan_upload(
        self,
        session: Session,
        request: WelcomeEffectGraphRequest,
        execution_id: str,
        material: dict[str, Any],
    ) -> dict[str, Any]:
        return ExternalEffectService().plan_effect(
            effect_type=WECOM_MEDIA_UPLOAD,
            adapter_name="wecom_media_upload",
            operation="ensure_temporary_media",
            target_type="media_library_material",
            target_id=f"{material['library_kind']}:{material['material_id']}:{material['upload_kind']}",
            business_type=WELCOME_EFFECT_BUSINESS_TYPE,
            business_id=execution_id,
            source_module="channel_entry.application",
            source_event_id=request.source_event_id,
            source_command_id=f"welcome:{request.source_event_id}:{material['material_key']}",
            idempotency_key=f"{request.idempotency_key}:upload:{material['material_key']}",
            context=self._context(request, execution_id),
            payload={
                "material_kind": material["library_kind"],
                "material_id": int(material["material_id"]),
                "upload_kind": material["upload_kind"],
                "force_refresh": False,
                "welcome_execution_id": execution_id,
                "material_key": material["material_key"],
            },
            payload_summary={
                "welcome_execution_id": execution_id,
                "material_key": material["material_key"],
                "material_kind": material["library_kind"],
                "material_id": int(material["material_id"]),
                "source_payload_persisted": False,
            },
            status="queued",
            scheduled_at=_now(),
            available_at=_now(),
            execution_mode="execute",
            execution_id=_execution_id("upload"),
            parent_execution_id=execution_id,
            lane="wecom_media",
            ordering_key=f"welcome_material:{material['library_kind']}:{material['material_id']}",
            fairness_key=f"channel:{int(request.channel_id or 0)}",
            connection=session,
        )

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]:
        with self._session_factory() as session:
            dependency_row = (
                session.execute(
                    sql_text(
                        """
                        SELECT * FROM channel_welcome_effect_dependency
                        WHERE prerequisite_effect_job_id = :job_id
                        FOR UPDATE
                        """
                    ),
                    {"job_id": int(upload_job_id)},
                )
                .mappings()
                .first()
            )
            if not dependency_row:
                session.rollback()
                return {"ok": True, "applicable": False, "released": False, "reason": "dependency_not_found"}
            dependency = dict(dependency_row)
            upload = dict(
                session.execute(
                    sql_text("SELECT status, last_attempt_id FROM external_effect_job WHERE id = :id FOR UPDATE"),
                    {"id": int(upload_job_id)},
                )
                .mappings()
                .one()
            )
            if upload["status"] != "succeeded":
                session.rollback()
                return {
                    "ok": False,
                    "applicable": True,
                    "released": False,
                    "reason": "upload_not_succeeded",
                    "upload_status": upload["status"],
                }
            effective_attempt = _clean(attempt_id) or _clean(upload.get("last_attempt_id"))
            if effective_attempt:
                succeeded_attempt = session.execute(
                    sql_text(
                        """
                        SELECT 1 FROM external_effect_attempt
                        WHERE job_id = :job_id AND attempt_id = :attempt_id AND status = 'succeeded'
                        """
                    ),
                    {"job_id": int(upload_job_id), "attempt_id": effective_attempt},
                ).scalar_one_or_none()
                if not succeeded_attempt:
                    session.rollback()
                    return {"ok": False, "applicable": True, "released": False, "reason": "attempt_not_succeeded"}
            media_id = self._provider_media_id(session, dependency)
            if not media_id:
                session.rollback()
                return {"ok": False, "applicable": True, "released": False, "reason": "provider_media_id_not_projected"}
            session.execute(
                sql_text(
                    """
                    UPDATE channel_welcome_effect_dependency
                    SET status = 'succeeded', provider_media_id = :media_id,
                        completed_attempt_id = :attempt_id,
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND status IN ('waiting', 'succeeded')
                    """
                ),
                {"media_id": media_id, "attempt_id": effective_attempt, "id": int(dependency["id"])},
            )
            graph = dict(
                session.execute(
                    sql_text("SELECT * FROM channel_welcome_effect_graph WHERE id = :id FOR UPDATE"),
                    {"id": int(dependency["graph_id"])},
                )
                .mappings()
                .one()
            )
            if graph["status"] in {"cancelled", "terminal"}:
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": graph["execution_id"],
                }
            dependencies = [
                dict(row)
                for row in session.execute(
                    sql_text(
                        """
                        SELECT material_key, status, provider_media_id
                        FROM channel_welcome_effect_dependency
                        WHERE graph_id = :graph_id ORDER BY id FOR UPDATE
                        """
                    ),
                    {"graph_id": int(graph["id"])},
                )
                .mappings()
                .all()
            ]
            waiting = [row for row in dependencies if row["status"] != "succeeded"]
            if waiting:
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": "dependencies_waiting",
                    "remaining": len(waiting),
                    "execution_id": graph["execution_id"],
                }
            final = dict(
                session.execute(
                    sql_text("SELECT * FROM external_effect_job WHERE id = :id FOR UPDATE"),
                    {"id": int(graph["final_effect_job_id"])},
                )
                .mappings()
                .one()
            )
            if final["status"] != "planned":
                session.execute(
                    sql_text("UPDATE channel_welcome_effect_graph SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                    {"id": int(graph["id"])},
                )
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": "final_effect_already_released",
                    "execution_id": graph["execution_id"],
                    "final_effect_status": final["status"],
                }
            ready = {_clean(row["material_key"]): _clean(row["provider_media_id"]) for row in dependencies}
            payload = dict(final["payload_json"] or {})
            payload["attachments"] = self._resolve_attachments(list(payload.get("attachments") or []), ready)
            payload_summary = dict(final["payload_summary_json"] or {})
            payload_summary.update({"dependencies_resolved": True, "dependency_count": len(dependencies)})
            released = session.execute(
                sql_text(
                    """
                    UPDATE external_effect_job
                    SET payload_json = CAST(:payload AS jsonb),
                        payload_summary_json = CAST(:summary AS jsonb),
                        status = 'queued', available_at = CURRENT_TIMESTAMP,
                        hold_reason = '', hold_at = NULL,
                        row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND status = 'planned'
                      AND provider_call_started_at IS NULL
                    RETURNING id
                    """
                ),
                {
                    "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "summary": json.dumps(payload_summary, ensure_ascii=False, separators=(",", ":")),
                    "id": int(final["id"]),
                },
            ).scalar_one_or_none()
            if not released:
                session.rollback()
                return {"ok": False, "applicable": True, "released": False, "reason": "final_effect_release_cas_lost"}
            session.execute(
                sql_text("UPDATE channel_welcome_effect_graph SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "applicable": True,
                "released": True,
                "execution_id": graph["execution_id"],
                "final_effect_job_id": int(final["id"]),
            }

    def settle_effect(self, effect_job_id: int, *, status: str, attempt_id: str = "") -> dict[str, Any]:
        terminal_status = _clean(status)
        if terminal_status not in {
            "succeeded",
            "simulated",
            "unknown_after_dispatch",
            "failed_terminal",
            "blocked",
            "cancelled",
        }:
            return {"ok": False, "applicable": False, "reason": "effect_not_terminal"}
        with self._session_factory() as session:
            graph_row = (
                session.execute(
                    sql_text(
                        """
                        SELECT graph.*, dependency.id AS dependency_id
                        FROM channel_welcome_effect_graph graph
                        LEFT JOIN channel_welcome_effect_dependency dependency
                          ON dependency.graph_id = graph.id
                         AND dependency.prerequisite_effect_job_id = :effect_job_id
                        WHERE graph.final_effect_job_id = :effect_job_id
                           OR dependency.prerequisite_effect_job_id = :effect_job_id
                        ORDER BY graph.id DESC LIMIT 1
                        FOR UPDATE OF graph
                        """
                    ),
                    {"effect_job_id": int(effect_job_id)},
                )
                .mappings()
                .first()
            )
            if not graph_row:
                session.rollback()
                return {"ok": True, "applicable": False, "reason": "welcome_graph_not_found"}
            graph = dict(graph_row)
            if _clean(graph["status"]) in {"cancelled", "terminal"}:
                session.rollback()
                return {
                    "ok": True,
                    "applicable": True,
                    "settled": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": graph["execution_id"],
                }

            is_final = int(graph.get("final_effect_job_id") or 0) == int(effect_job_id)
            cancelled_job_ids: list[int] = []
            cancel_requested_job_ids: list[int] = []
            if not is_final:
                session.execute(
                    sql_text(
                        """
                        UPDATE channel_welcome_effect_dependency
                        SET status = :status, completed_attempt_id = :attempt_id,
                            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :dependency_id AND status IN ('waiting', 'failed', 'cancelled')
                        """
                    ),
                    {
                        "status": "cancelled" if terminal_status == "cancelled" else "failed",
                        "attempt_id": _clean(attempt_id),
                        "dependency_id": int(graph["dependency_id"]),
                    },
                )
            if terminal_status != "succeeded":
                cancel_requested_job_ids = _request_cancel_dispatching_welcome_jobs_in_session(
                    session,
                    graph_id=int(graph["id"]),
                    final_job_id=int(graph.get("final_effect_job_id") or 0),
                    exclude_job_id=int(effect_job_id),
                    actor="welcome_graph_settlement",
                    reason="welcome_sibling_terminal",
                )
                cancelled_rows = session.execute(
                        sql_text(
                            """
                            UPDATE external_effect_job job
                            SET status = 'cancelled',
                                cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                                cancel_requested_by = CASE
                                    WHEN cancel_requested_by = '' THEN 'welcome_dependency_settlement'
                                    ELSE cancel_requested_by
                                END,
                                cancel_reason = CASE
                                    WHEN cancel_reason = '' THEN 'welcome_dependency_terminal'
                                    ELSE cancel_reason
                                END,
                                cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                                row_version = row_version + 1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE job.id IN (
                                SELECT dependent_effect_job_id
                                FROM channel_welcome_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION
                                SELECT prerequisite_effect_job_id
                                FROM channel_welcome_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION SELECT :final_job_id
                            )
                              AND job.id <> :effect_job_id
                              AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                              AND job.provider_call_started_at IS NULL
                              AND NOT EXISTS (
                                  SELECT 1 FROM external_effect_attempt attempt
                                  WHERE attempt.job_id = job.id
                                    AND attempt.provider_call_started_at IS NOT NULL
                              )
                            RETURNING job.*
                            """
                        ),
                        {
                            "graph_id": int(graph["id"]),
                            "effect_job_id": int(effect_job_id),
                            "final_job_id": int(graph.get("final_effect_job_id") or 0),
                        },
                    ).mappings().all()
                cancelled_job_ids = enqueue_external_effect_settled_rows_in_session(session, cancelled_rows)
                if is_final:
                    session.execute(
                        sql_text(
                            """
                            UPDATE channel_welcome_effect_dependency
                            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                            WHERE graph_id = :graph_id AND status = 'waiting'
                            """
                        ),
                        {"graph_id": int(graph["id"])},
                    )
            final_boundary = session.execute(
                sql_text(
                    """
                    SELECT provider_call_started_at IS NOT NULL OR EXISTS (
                        SELECT 1 FROM external_effect_attempt attempt
                        WHERE attempt.job_id = external_effect_job.id
                          AND attempt.provider_call_started_at IS NOT NULL
                    ) AS provider_boundary_crossed
                    FROM external_effect_job WHERE id = :job_id
                    """
                ),
                {"job_id": int(graph.get("final_effect_job_id") or 0)},
            ).scalar_one_or_none()
            session.execute(
                sql_text(
                    "UPDATE channel_welcome_effect_graph SET status = 'terminal', updated_at = CURRENT_TIMESTAMP WHERE id = :graph_id"
                ),
                {"graph_id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "applicable": True,
                "settled": True,
                "execution_id": graph["execution_id"],
                "effect_job_id": int(effect_job_id),
                "effect_status": terminal_status,
                "cancelled_job_ids": cancelled_job_ids,
                "cancel_requested_job_ids": cancel_requested_job_ids,
                "provider_boundary_crossed": bool(final_boundary),
            }

    @staticmethod
    def _provider_media_id(session: Session, dependency: dict[str, Any]) -> str:
        queries = {
            "image": "SELECT thumb_media_id FROM image_library WHERE id = :id",
            "attachment": "SELECT media_id FROM attachment_library WHERE id = :id",
            "miniprogram": "SELECT thumb_media_id FROM miniprogram_library WHERE id = :id",
        }
        return _clean(
            session.execute(
                sql_text(queries[_clean(dependency["library_kind"])]),
                {"id": int(dependency["library_material_id"])},
            ).scalar_one_or_none()
        )

    @staticmethod
    def _resolve_attachments(attachments: list[Any], ready: dict[str, str]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for raw in attachments:
            item = json.loads(json.dumps(raw, ensure_ascii=False))
            msgtype = _clean(item.get("msgtype"))
            nested = item.get(msgtype) if isinstance(item.get(msgtype), dict) else {}
            if msgtype in {"image", "file"} and _clean(nested.get("media_dependency_key")):
                key = _clean(nested.pop("media_dependency_key"))
                nested["media_id"] = ready[key]
            elif msgtype == "miniprogram" and _clean(nested.get("pic_media_dependency_key")):
                key = _clean(nested.pop("pic_media_dependency_key"))
                nested["pic_media_id"] = ready[key]
            item[msgtype] = nested
            resolved.append(item)
        encoded = json.dumps(resolved, ensure_ascii=False)
        if "dependency_key" in encoded or '"material_id"' in encoded:
            raise RuntimeError("welcome dependency resolution left an unresolved provider payload")
        return resolved

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        if not _clean(actor) or not _clean(reason):
            raise ValueError("welcome graph cancellation requires actor and reason")
        with self._session_factory() as session:
            graph_row = (
                session.execute(
                    sql_text("SELECT * FROM channel_welcome_effect_graph WHERE execution_id = :execution_id FOR UPDATE"),
                    {"execution_id": _clean(execution_id)},
                )
                .mappings()
                .first()
            )
            if not graph_row:
                session.rollback()
                return {"ok": False, "cancelled": False, "reason": "graph_not_found"}
            graph = dict(graph_row)
            cancel_requested = _request_cancel_dispatching_welcome_jobs_in_session(
                session,
                graph_id=int(graph["id"]),
                final_job_id=int(graph.get("final_effect_job_id") or 0),
                actor=_clean(actor),
                reason=_clean(reason),
            )
            cancelled_rows = session.execute(
                sql_text(
                    """
                    UPDATE external_effect_job job
                    SET status = 'cancelled', cancel_requested_at = CURRENT_TIMESTAMP,
                        cancel_requested_by = :actor, cancel_reason = :reason,
                        cancelled_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE job.id IN (
                        SELECT prerequisite_effect_job_id FROM channel_welcome_effect_dependency WHERE graph_id = :graph_id
                        UNION
                        SELECT dependent_effect_job_id FROM channel_welcome_effect_dependency WHERE graph_id = :graph_id
                        UNION SELECT :final_job_id
                    )
                      AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                      AND job.provider_call_started_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM external_effect_attempt attempt
                          WHERE attempt.job_id = job.id AND attempt.provider_call_started_at IS NOT NULL
                      )
                    RETURNING job.*
                    """
                ),
                {
                    "actor": _clean(actor),
                    "reason": _clean(reason),
                    "graph_id": int(graph["id"]),
                    "final_job_id": int(graph.get("final_effect_job_id") or 0),
                },
            ).mappings().all()
            cancelled = enqueue_external_effect_settled_rows_in_session(session, cancelled_rows)
            session.execute(
                sql_text(
                    """
                    UPDATE channel_welcome_effect_dependency
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                    WHERE graph_id = :graph_id AND status = 'waiting'
                    """
                ),
                {"graph_id": int(graph["id"])},
            )
            session.execute(
                sql_text(
                    """
                    UPDATE channel_welcome_effect_graph
                    SET status = 'cancelled', cancel_reason = :reason,
                        cancelled_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :graph_id
                    """
                ),
                {"reason": _clean(reason), "graph_id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "cancelled": True,
                "execution_id": graph["execution_id"],
                "cancelled_job_ids": [int(value) for value in cancelled],
                "cancel_requested_job_ids": [int(value) for value in cancel_requested],
            }


class InMemoryWelcomeEffectGraphRepository:
    """Fixture-only planner; production always uses the transactional PG graph."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._service = ExternalEffectService(build_external_effect_repository())
        self._graphs: dict[str, dict[str, Any]] = {}

    def plan(self, request: WelcomeEffectGraphRequest) -> dict[str, Any]:
        with self._lock:
            if request.idempotency_key in self._graphs:
                return {**self._graphs[request.idempotency_key], "duplicate": True}
            execution_id = _execution_id("root")
            unresolved = [dict(item) for item in request.attachments if int(item.get("material_id") or 0) > 0]
            ready = [_provider_attachment(dict(item)) for item in request.attachments if int(item.get("material_id") or 0) <= 0]
            final = self._service.plan_effect(
                effect_type=WECOM_WELCOME_MESSAGE_SEND,
                adapter_name="wecom_welcome_message",
                operation="send",
                target_type=request.target_type,
                target_id=request.target_id,
                business_type=WELCOME_EFFECT_BUSINESS_TYPE,
                business_id=execution_id,
                source_module="channel_entry.application",
                source_event_id=request.source_event_id,
                idempotency_key=f"{request.idempotency_key}:final",
                context=CommandContext(actor_id=request.actor_id, actor_type="channel_entry", trace_id=execution_id),
                payload={
                    "welcome_code": request.welcome_code,
                    "external_userid": request.external_userid,
                    "follow_user_userid": request.follow_user_userid,
                    "text": {"content": request.text_content} if request.text_content else {},
                    "attachments": ready,
                },
                payload_summary={"dependency_count": len(unresolved)},
                status="planned" if unresolved else "queued",
                execution_id=_execution_id("send"),
                parent_execution_id=execution_id,
                lane="wecom_interactive",
            )
            uploads: list[int] = []
            for item in unresolved:
                msgtype = _clean(item.get("msgtype"))
                material_id = int(item["material_id"])
                if msgtype == "link":
                    continue
                kind = {"image": "image", "file": "attachment", "miniprogram": "miniprogram"}[msgtype]
                upload = self._service.plan_effect(
                    effect_type=WECOM_MEDIA_UPLOAD,
                    adapter_name="wecom_media_upload",
                    operation="ensure_temporary_media",
                    target_type="media_library_material",
                    target_id=f"{kind}:{material_id}",
                    business_type=WELCOME_EFFECT_BUSINESS_TYPE,
                    business_id=execution_id,
                    source_module="channel_entry.application",
                    source_event_id=request.source_event_id,
                    idempotency_key=f"{request.idempotency_key}:upload:{msgtype}:{material_id}",
                    payload={"material_kind": kind, "material_id": material_id, "upload_kind": "attachment" if msgtype == "file" else "image"},
                    status="queued",
                    execution_id=_execution_id("upload"),
                    parent_execution_id=execution_id,
                    lane="wecom_media",
                )
                uploads.append(int(upload["id"]))
            result = _response(
                execution_id=execution_id,
                final_job_id=int(final["id"]),
                upload_job_ids=uploads,
                status="waiting_dependencies" if unresolved else "ready",
                duplicate=False,
            )
            self._graphs[request.idempotency_key] = result
            return dict(result)

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]:
        del upload_job_id, attempt_id
        return {"ok": False, "applicable": False, "released": False, "reason": "fixture_release_not_supported"}

    def settle_effect(self, effect_job_id: int, *, status: str, attempt_id: str = "") -> dict[str, Any]:
        del attempt_id
        for result in self._graphs.values():
            if int(effect_job_id) not in {
                int(result["final_effect_job_id"]),
                *[int(item) for item in result["upload_effect_job_ids"]],
            }:
                continue
            if result["status"] in {"cancelled", "terminal"}:
                return {
                    "ok": True,
                    "applicable": True,
                    "settled": False,
                    "reason": f"graph_{result['status']}",
                    "execution_id": result["execution_id"],
                }
            result["status"] = "terminal"
            return {
                "ok": True,
                "applicable": True,
                "settled": True,
                "execution_id": result["execution_id"],
                "effect_job_id": int(effect_job_id),
                "effect_status": _clean(status),
                "cancelled_job_ids": [],
                "provider_boundary_crossed": False,
            }
        return {"ok": True, "applicable": False, "reason": "welcome_graph_not_found"}

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        del actor, reason
        for result in self._graphs.values():
            if result["execution_id"] == execution_id:
                result["status"] = "cancelled"
                return {"ok": True, "cancelled": True, "execution_id": execution_id}
        return {"ok": False, "cancelled": False, "reason": "graph_not_found"}


def build_welcome_effect_graph_repository() -> WelcomeEffectGraphRepository:
    if fixture_mode():
        return InMemoryWelcomeEffectGraphRepository()
    return SQLAlchemyWelcomeEffectGraphRepository()


def _matches_welcome_media_upload(job, _dispatch_result) -> bool:
    return (
        job.effect_type == WECOM_MEDIA_UPLOAD
        and job.business_type == WELCOME_EFFECT_BUSINESS_TYPE
        and bool(_clean(job.business_id))
    )


def _release_welcome_after_upload(job, _dispatch_result) -> dict[str, Any]:
    return build_welcome_effect_graph_repository().release_after_upload(
        int(job.id),
        attempt_id=_clean(job.last_attempt_id),
    )


def _matches_welcome_terminal_effect(job, _dispatch_result) -> bool:
    return (
        job.business_type == WELCOME_EFFECT_BUSINESS_TYPE
        and bool(_clean(job.business_id))
        and job.status != "succeeded"
    )


def _settle_welcome_effect_graph(job, _dispatch_result) -> dict[str, Any]:
    return build_welcome_effect_graph_repository().settle_effect(
        int(job.id),
        status=_clean(job.status),
        attempt_id=_clean(job.last_attempt_id),
    )


WELCOME_MEDIA_DEPENDENCY_CONTINUATION = ExternalEffectContinuation(
    name=WELCOME_MEDIA_COMPLETION_CONSUMER,
    matches=_matches_welcome_media_upload,
    run=_release_welcome_after_upload,
)

WELCOME_EFFECT_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="welcome_effect_graph_settlement",
    matches=_matches_welcome_terminal_effect,
    run=_settle_welcome_effect_graph,
)


__all__ = [
    "SQLAlchemyWelcomeEffectGraphRepository",
    "WELCOME_EFFECT_BUSINESS_TYPE",
    "WELCOME_EFFECT_SETTLEMENT_CONTINUATION",
    "WELCOME_MEDIA_DEPENDENCY_CONTINUATION",
    "WelcomeEffectGraphRequest",
    "WelcomeEffectGraphRepository",
    "build_welcome_effect_graph_repository",
]
