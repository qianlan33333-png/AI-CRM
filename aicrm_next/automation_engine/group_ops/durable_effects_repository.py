from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    GROUP_OPS_MESSAGE_LOOPBACK,
    WECOM_MEDIA_UPLOAD,
    WECOM_MESSAGE_GROUP_SEND,
)
from aicrm_next.platform_foundation.external_effects.models import public_datetime
from aicrm_next.platform_foundation.external_effects.repo import (
    ExternalEffectRepository,
    build_external_effect_repository,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    enqueue_external_effect_settled_rows_in_session,
)
from aicrm_next.platform_foundation.external_effects.test_receiver import (
    TEST_RECEIVER_PATH_PREFIX,
    canonical_payload_hash,
)
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import fixture_mode

from .domain import clean_text, mask_sensitive_payload
from .effect_graph_lifecycle_repository import (
    InMemoryGroupOpsEffectGraphLifecycleMixin,
    SQLAlchemyGroupOpsEffectGraphLifecycleMixin,
    request_cancel_dispatching_group_ops_jobs_in_session,
)
from .external_effects import content_payload_summary, parse_external_effect_scheduled_at


STATUS_URL_PREFIX = "/api/admin/executions/"
GROUP_OPS_EFFECT_BUSINESS_TYPE = "group_ops_effect_graph"
MEDIA_PREPARATION_LEAD = timedelta(hours=12)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_hash(value: Any, *, length: int = 32) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _execution_id(prefix: str = "exe_group_ops") -> str:
    return f"{prefix}_{uuid4().hex}"


def _media_upload_available_at(scheduled_at: datetime) -> datetime:
    """Open the media lane shortly before the business send is due.

    WeCom temporary media expires after a few days.  Uploading at plan-create
    time can therefore make a future send deterministically stale.  The queue
    row keeps the business ``scheduled_at`` while ``available_at`` controls
    when an idle media slot may actually claim the upload.
    """

    return max(_utcnow(), scheduled_at - MEDIA_PREPARATION_LEAD)


@dataclass(frozen=True)
class GroupOpsEffectMaterial:
    material_key: str
    role: str
    file_name: str
    content_type: str
    file_bytes: bytes = b""
    attachment_payload: dict[str, Any] = field(default_factory=dict)
    library_kind: str = "image"
    library_material_id: int = 0
    upload_kind: str = "image"


@dataclass(frozen=True)
class GroupOpsEffectGraphRequest:
    idempotency_key: str
    source_kind: str
    plan_id: int
    chat_ids: list[str]
    content_payload: dict[str, Any]
    content_summary: str
    actor_id: str
    owner_userid: str
    source_module: str
    source_route: str
    source_command_id: str
    node_id: int = 0
    source_event_id: str = ""
    parent_execution_id: str = ""
    scheduled_at: datetime | str | None = None
    version_fingerprint: str = ""
    webhook_key: str = ""
    materials: tuple[GroupOpsEffectMaterial, ...] = ()
    test_loopback: bool = False
    test_receiver_base_url: str = ""
    test_receiver_response_status: int = 200

    def normalized_scheduled_at(self) -> datetime:
        if isinstance(self.scheduled_at, datetime):
            value = self.scheduled_at
        else:
            value = parse_external_effect_scheduled_at(self.scheduled_at)
        value = value or _utcnow()
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class GroupOpsEffectGraphRepository(Protocol):
    def plan(self, request: GroupOpsEffectGraphRequest) -> dict[str, Any]: ...

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]: ...

    def settle_effect(self, effect_job_id: int, *, status: str, attempt_id: str = "") -> dict[str, Any]: ...

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]: ...

    def cancel_plan(
        self,
        plan_id: int,
        *,
        actor: str,
        reason: str,
        node_id: int | None = None,
    ) -> dict[str, Any]: ...


def _material_dependency_key(attachment: dict[str, Any]) -> str:
    msgtype = clean_text(attachment.get("msgtype"))
    nested = attachment.get(msgtype) if isinstance(attachment.get(msgtype), dict) else {}
    return clean_text(nested.get("media_dependency_key") or nested.get("pic_media_dependency_key"))


def _material_dependency_attachment(material: GroupOpsEffectMaterial) -> dict[str, Any]:
    if material.role in {"card_cover", "miniprogram"}:
        card = dict(material.attachment_payload or {})
        card["pic_media_dependency_key"] = material.material_key
        return {"msgtype": "miniprogram", "miniprogram": card}
    if material.role == "file":
        return {
            "msgtype": "file",
            "file": {"media_dependency_key": material.material_key},
        }
    return {
        "msgtype": "image",
        "image": {"media_dependency_key": material.material_key},
    }


def materialize_group_ops_content_dependencies(
    content_payload: dict[str, Any],
    materials: tuple[GroupOpsEffectMaterial, ...],
) -> dict[str, Any]:
    payload = dict(content_payload or {})
    attachments = [dict(item) for item in list(payload.get("attachments") or []) if isinstance(item, dict)]
    dependency_keys = {_material_dependency_key(item) for item in attachments}
    dependency_keys.discard("")
    for material in materials:
        if material.material_key in dependency_keys:
            continue
        attachments.append(_material_dependency_attachment(material))
        dependency_keys.add(material.material_key)
    payload["attachments"] = attachments
    return payload


def _final_effect_payload(
    request: GroupOpsEffectGraphRequest,
    *,
    content_payload: dict[str, Any],
    execution_id: str,
) -> dict[str, Any]:
    return {
        "plan_id": int(request.plan_id),
        "node_id": int(request.node_id or 0),
        "group_ops_execution_id": execution_id,
        "chat_ids": list(dict.fromkeys(clean_text(item) for item in request.chat_ids if clean_text(item))),
        "content_summary": clean_text(request.content_summary)[:500],
        "content_payload": mask_sensitive_payload(content_payload),
        "operator_member_id": clean_text(request.actor_id),
        "owner_userid": clean_text(request.owner_userid) or clean_text(request.actor_id),
        "webhook_key": clean_text(request.webhook_key),
        "mention_all": False,
        "is_mention_all": False,
        "wecom_send_executed": False,
    }


def _effect_response(
    *,
    execution_id: str,
    source_version: int,
    final_job_id: int,
    upload_job_ids: list[int],
    status: str,
    duplicate: bool,
) -> dict[str, Any]:
    job_ids = [*upload_job_ids, int(final_job_id)]
    return {
        "execution_id": execution_id,
        "parent_execution_id": "",
        "source_version": int(source_version),
        "status": status,
        "duplicate": bool(duplicate),
        "external_effect_job_ids": job_ids,
        "job_ids": job_ids,
        "upload_effect_job_ids": upload_job_ids,
        "final_effect_job_id": int(final_job_id),
        "status_url": f"{STATUS_URL_PREFIX}{execution_id}",
        "real_external_call_executed": False,
    }


class SQLAlchemyGroupOpsEffectGraphRepository(SQLAlchemyGroupOpsEffectGraphLifecycleMixin):
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def plan(self, request: GroupOpsEffectGraphRequest) -> dict[str, Any]:
        if not clean_text(request.idempotency_key):
            raise ValueError("group ops effect graph idempotency_key is required")
        if request.source_kind not in {"direct_send", "plan_node", "trusted_webhook", "webhook_action"}:
            raise ValueError("unsupported group ops effect graph source_kind")
        scheduled_at = request.normalized_scheduled_at()
        version_fingerprint = clean_text(request.version_fingerprint) or _stable_hash(
            {
                "plan_id": request.plan_id,
                "node_id": request.node_id,
                "content": request.content_payload,
                "materials": [
                    {
                        "key": item.material_key,
                        "role": item.role,
                        "sha256": hashlib.sha256(item.file_bytes).hexdigest() if item.file_bytes else "",
                        "library_kind": item.library_kind,
                        "library_material_id": item.library_material_id,
                        "attachment": item.attachment_payload,
                    }
                    for item in request.materials
                ],
            }
        )
        with self._session_factory() as session:
            if request.source_kind == "plan_node":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": f"group_ops_plan_scope:{int(request.plan_id)}"},
                )
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"group_ops_effect_graph:{clean_text(request.idempotency_key)}"},
            )
            existing = self._graph_by_idempotency(session, request.idempotency_key)
            if existing:
                result = self._result(session, existing, duplicate=True)
                session.rollback()
                return result

            source_version = self._source_version(
                session,
                request=request,
                version_fingerprint=version_fingerprint,
            )
            execution_id = _execution_id()
            row = (
                session.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_effect_graph (
                            execution_id, parent_execution_id, idempotency_key, source_kind,
                            plan_id, node_id, source_version, version_fingerprint,
                            scheduled_at, status, actor_id, created_at, updated_at
                        ) VALUES (
                            :execution_id, :parent_execution_id, :idempotency_key, :source_kind,
                            :plan_id, :node_id, :source_version, :version_fingerprint,
                            :scheduled_at, 'waiting_dependencies', :actor_id,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "execution_id": execution_id,
                        "parent_execution_id": clean_text(request.parent_execution_id),
                        "idempotency_key": clean_text(request.idempotency_key),
                        "source_kind": request.source_kind,
                        "plan_id": int(request.plan_id or 0),
                        "node_id": int(request.node_id or 0),
                        "source_version": source_version,
                        "version_fingerprint": version_fingerprint,
                        "scheduled_at": scheduled_at,
                        "actor_id": clean_text(request.actor_id),
                    },
                )
                .mappings()
                .one()
            )
            graph = dict(row)
            graph_id = int(graph["id"])
            content_payload = materialize_group_ops_content_dependencies(request.content_payload, request.materials)
            final_job = self._plan_final_effect(
                session,
                request=request,
                execution_id=execution_id,
                source_version=source_version,
                content_payload=content_payload,
                held=bool(request.materials),
            )
            upload_job_ids: list[int] = []
            for material in request.materials:
                material_row = self._persist_material(
                    session,
                    graph_id=graph_id,
                    execution_id=execution_id,
                    material=material,
                )
                upload_job = self._plan_upload_effect(
                    session,
                    request=request,
                    execution_id=execution_id,
                    source_version=source_version,
                    material=material,
                    library_material_id=int(material_row["library_material_id"]),
                )
                upload_job_ids.append(int(upload_job["id"]))
                session.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_effect_dependency (
                            graph_id, material_id, prerequisite_effect_job_id,
                            dependent_effect_job_id, status, created_at, updated_at
                        ) VALUES (
                            :graph_id, :material_id, :prerequisite_effect_job_id,
                            :dependent_effect_job_id, 'waiting', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "graph_id": graph_id,
                        "material_id": int(material_row["id"]),
                        "prerequisite_effect_job_id": int(upload_job["id"]),
                        "dependent_effect_job_id": int(final_job["id"]),
                    },
                )
            graph_status = "waiting_dependencies" if upload_job_ids else "ready"
            graph = dict(
                session.execute(
                    text(
                        """
                        UPDATE automation_group_ops_effect_graph
                        SET final_effect_job_id = :final_effect_job_id,
                            status = :status,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :graph_id
                        RETURNING *
                        """
                    ),
                    {
                        "final_effect_job_id": int(final_job["id"]),
                        "status": graph_status,
                        "graph_id": graph_id,
                    },
                )
                .mappings()
                .one()
            )
            session.commit()
            return _effect_response(
                execution_id=execution_id,
                source_version=source_version,
                final_job_id=int(final_job["id"]),
                upload_job_ids=upload_job_ids,
                status=graph_status,
                duplicate=False,
            )

    def _graph_by_idempotency(self, session: Session, idempotency_key: str) -> dict[str, Any] | None:
        row = (
            session.execute(
                text("SELECT * FROM automation_group_ops_effect_graph WHERE idempotency_key = :idempotency_key LIMIT 1"),
                {"idempotency_key": clean_text(idempotency_key)},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _source_version(
        self,
        session: Session,
        *,
        request: GroupOpsEffectGraphRequest,
        version_fingerprint: str,
    ) -> int:
        if request.source_kind != "plan_node":
            return 1
        lock_key = f"group_ops_plan_node:{int(request.plan_id)}:{int(request.node_id)}"
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"), {"lock_key": lock_key})
        latest = (
            session.execute(
                text(
                    """
                    SELECT source_version, version_fingerprint
                    FROM automation_group_ops_effect_graph
                    WHERE source_kind = 'plan_node'
                      AND plan_id = :plan_id AND node_id = :node_id
                    ORDER BY source_version DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"plan_id": int(request.plan_id), "node_id": int(request.node_id)},
            )
            .mappings()
            .first()
        )
        if latest and clean_text(latest["version_fingerprint"]) == version_fingerprint:
            return int(latest["source_version"])
        source_version = int((latest or {}).get("source_version") or 0) + 1
        if latest:
            self._supersede_old_plan_version(
                session,
                plan_id=int(request.plan_id),
                node_id=int(request.node_id),
                keep_fingerprint=version_fingerprint,
                actor=clean_text(request.actor_id),
            )
        return source_version

    def _supersede_old_plan_version(
        self,
        session: Session,
        *,
        plan_id: int,
        node_id: int,
        keep_fingerprint: str,
        actor: str,
    ) -> None:
        old_graphs = (
            session.execute(
                text(
                    """
                    SELECT id, final_effect_job_id
                    FROM automation_group_ops_effect_graph
                    WHERE source_kind = 'plan_node'
                      AND plan_id = :plan_id AND node_id = :node_id
                      AND version_fingerprint <> :keep_fingerprint
                      AND status IN ('waiting_dependencies', 'ready')
                    FOR UPDATE
                    """
                ),
                {
                    "plan_id": plan_id,
                    "node_id": node_id,
                    "keep_fingerprint": keep_fingerprint,
                },
            )
            .mappings()
            .all()
        )
        for graph in old_graphs:
            request_cancel_dispatching_group_ops_jobs_in_session(
                session,
                graph_id=int(graph["id"]),
                final_effect_job_id=int(graph["final_effect_job_id"] or 0),
                actor=actor or "group_ops_plan_editor",
                reason="group_ops_plan_version_superseded",
            )
            cancelled_rows = session.execute(
                text(
                    """
                    UPDATE external_effect_job job
                    SET status = 'cancelled',
                        cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                        cancel_requested_by = CASE WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by END,
                        cancel_reason = CASE WHEN cancel_reason = '' THEN 'group_ops_plan_version_superseded' ELSE cancel_reason END,
                        cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job.id IN (
                        SELECT dependent_effect_job_id
                        FROM automation_group_ops_effect_dependency
                        WHERE graph_id = :graph_id
                        UNION
                        SELECT prerequisite_effect_job_id
                        FROM automation_group_ops_effect_dependency
                        WHERE graph_id = :graph_id
                        UNION
                        SELECT :final_effect_job_id
                    )
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
                    "final_effect_job_id": int(graph["final_effect_job_id"] or 0),
                    "actor": actor or "group_ops_plan_editor",
                },
            ).mappings().all()
            enqueue_external_effect_settled_rows_in_session(session, cancelled_rows)
            session.execute(
                text(
                    """
                    UPDATE automation_group_ops_effect_graph
                    SET status = 'superseded', superseded_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :graph_id
                    """
                ),
                {"graph_id": int(graph["id"])},
            )

    def _plan_final_effect(
        self,
        session: Session,
        *,
        request: GroupOpsEffectGraphRequest,
        execution_id: str,
        source_version: int,
        content_payload: dict[str, Any],
        held: bool,
    ) -> dict[str, Any]:
        payload = _final_effect_payload(request, content_payload=content_payload, execution_id=execution_id)
        effect_type = WECOM_MESSAGE_GROUP_SEND
        adapter_name = "wecom_group_message"
        operation = "send_group_message"
        lane = "wecom_bulk"
        if request.test_loopback:
            effect_type = GROUP_OPS_MESSAGE_LOOPBACK
            adapter_name = "outbound_webhook"
            operation = "post"
            lane = "outbound_webhook"
            body = {
                "synthetic": True,
                "source": request.source_module,
                "effect_type": effect_type,
                "plan_id": int(request.plan_id),
                "node_id": int(request.node_id or 0),
                "chat_ids": list(payload["chat_ids"]),
                "content_summary": clean_text(request.content_summary)[:500],
                "content_payload": content_payload_summary(content_payload),
                "test_only": True,
            }
            base_url = clean_text(request.test_receiver_base_url)
            if not base_url:
                raise ValueError("test_receiver_base_url is required for Group Ops loopback")
            payload.update(
                {
                    "webhook_url": f"{base_url.rstrip('/')}{TEST_RECEIVER_PATH_PREFIX}",
                    "body": body,
                    "receiver_response_status": int(request.test_receiver_response_status or 200),
                    "test_receiver_expires_at": public_datetime(_utcnow() + timedelta(hours=12)),
                    "execution_scope": "test_loopback",
                    "is_test": True,
                    "expected_payload_hash": canonical_payload_hash(body),
                }
            )
        planned = ExternalEffectService().plan_effect(
            effect_type=effect_type,
            adapter_name=adapter_name,
            operation=operation,
            target_type="group_ops_execution",
            target_id=execution_id,
            business_type=GROUP_OPS_EFFECT_BUSINESS_TYPE,
            business_id=execution_id,
            payload=payload,
            payload_summary={
                "group_ops_execution_id": execution_id,
                "plan_id": int(request.plan_id),
                "node_id": int(request.node_id or 0),
                "source_version": int(source_version),
                "chat_count": len(payload["chat_ids"]),
                "content_payload": content_payload_summary(content_payload),
                "dependency_count": len(request.materials),
            },
            context=CommandContext(
                actor_id=clean_text(request.actor_id) or "group_ops",
                actor_type="system",
                request_id=request.idempotency_key,
                trace_id=execution_id,
                source_route=request.source_route,
            ),
            source_module=request.source_module,
            source_event_id=request.source_event_id,
            source_command_id=request.source_command_id,
            risk_level="medium",
            execution_mode="execute",
            status="planned" if held else "queued",
            scheduled_at=request.normalized_scheduled_at(),
            idempotency_key=f"{request.idempotency_key}:final:v{source_version}",
            execution_id=_execution_id("exe_group_ops_send"),
            parent_execution_id=execution_id,
            lane=lane,
            ordering_key=f"group_ops:{int(request.plan_id)}:{int(request.node_id or 0)}",
            fairness_key=f"group_ops_plan:{int(request.plan_id)}",
            connection=session,
        )
        return dict(planned)

    def _persist_material(
        self,
        session: Session,
        *,
        graph_id: int,
        execution_id: str,
        material: GroupOpsEffectMaterial,
    ) -> dict[str, Any]:
        library_material_id = int(material.library_material_id or 0)
        library_kind = clean_text(material.library_kind) or "image"
        if library_material_id <= 0:
            if not material.file_bytes:
                raise ValueError("group ops effect material requires source bytes or library_material_id")
            library_row = (
                session.execute(
                    text(
                        """
                        INSERT INTO image_library (
                            name, file_name, source, source_url, data_base64, mime_type,
                            file_size, enabled, description, category, created_at, updated_at
                        ) VALUES (
                            :name, :file_name, 'group_ops_effect_graph', '', :data_base64,
                            :mime_type, :file_size, TRUE,
                            :description, 'group_ops_ephemeral', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "name": f"Group Ops {material.role} {execution_id}",
                        "file_name": clean_text(material.file_name) or "group-ops-image",
                        "data_base64": base64.b64encode(bytes(material.file_bytes)).decode("ascii"),
                        "mime_type": clean_text(material.content_type) or "image/png",
                        "file_size": len(material.file_bytes),
                        "description": f"durable source for {execution_id}:{material.material_key}",
                    },
                )
                .mappings()
                .one()
            )
            library_material_id = int(library_row["id"])
            library_kind = "image"
        material_row = (
            session.execute(
                text(
                    """
                    INSERT INTO automation_group_ops_effect_material (
                        graph_id, material_key, role, library_kind, library_material_id,
                        metadata_json, created_at, updated_at
                    ) VALUES (
                        :graph_id, :material_key, :role, :library_kind, :library_material_id,
                        CAST(:metadata_json AS jsonb), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING *
                    """
                ),
                {
                    "graph_id": graph_id,
                    "material_key": material.material_key,
                    "role": material.role,
                    "library_kind": library_kind,
                    "library_material_id": library_material_id,
                    "metadata_json": json.dumps(
                        mask_sensitive_payload(material.attachment_payload),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
            .mappings()
            .one()
        )
        return dict(material_row)

    def _plan_upload_effect(
        self,
        session: Session,
        *,
        request: GroupOpsEffectGraphRequest,
        execution_id: str,
        source_version: int,
        material: GroupOpsEffectMaterial,
        library_material_id: int,
    ) -> dict[str, Any]:
        business_due_at = request.normalized_scheduled_at()
        upload_available_at = _media_upload_available_at(business_due_at)
        planned = ExternalEffectService().plan_effect(
            effect_type=WECOM_MEDIA_UPLOAD,
            adapter_name="wecom_media_upload",
            operation="refresh_temporary_media",
            target_type="media_library_material",
            target_id=(f"{clean_text(material.library_kind) or 'image'}:{library_material_id}:{clean_text(material.upload_kind) or 'image'}"),
            business_type=GROUP_OPS_EFFECT_BUSINESS_TYPE,
            business_id=execution_id,
            payload={
                "material_kind": clean_text(material.library_kind) or "image",
                "material_id": int(library_material_id),
                "upload_kind": clean_text(material.upload_kind) or "image",
                "force_refresh": True,
                "group_ops_execution_id": execution_id,
                "material_key": material.material_key,
            },
            payload_summary={
                "group_ops_execution_id": execution_id,
                "material_key": material.material_key,
                "material_kind": clean_text(material.library_kind) or "image",
                "material_id": int(library_material_id),
                "source_payload_persisted": True,
                "source_payload_redacted": True,
            },
            context=CommandContext(
                actor_id=clean_text(request.actor_id) or "group_ops",
                actor_type="system",
                request_id=request.idempotency_key,
                trace_id=execution_id,
                source_route=request.source_route,
            ),
            source_module=request.source_module,
            source_event_id=request.source_event_id,
            source_command_id=f"{request.source_command_id}:{material.material_key}",
            risk_level="low",
            execution_mode="execute",
            status="queued",
            scheduled_at=business_due_at,
            available_at=upload_available_at,
            idempotency_key=f"{request.idempotency_key}:upload:{material.material_key}:v{source_version}",
            execution_id=_execution_id("exe_group_ops_upload"),
            parent_execution_id=execution_id,
            lane="wecom_media",
            ordering_key=f"group_ops_material:{execution_id}:{material.material_key}",
            fairness_key=f"group_ops_plan:{int(request.plan_id)}",
            connection=session,
        )
        return dict(planned)

    def _result(self, session: Session, graph: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        uploads = (
            session.execute(
                text(
                    """
                SELECT prerequisite_effect_job_id
                FROM automation_group_ops_effect_dependency
                WHERE graph_id = :graph_id
                ORDER BY id ASC
                """
                ),
                {"graph_id": int(graph["id"])},
            )
            .scalars()
            .all()
        )
        return _effect_response(
            execution_id=clean_text(graph["execution_id"]),
            source_version=int(graph["source_version"]),
            final_job_id=int(graph["final_effect_job_id"] or 0),
            upload_job_ids=[int(item) for item in uploads],
            status=clean_text(graph["status"]),
            duplicate=duplicate,
        )

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]:
        with self._session_factory() as session:
            dependency = (
                session.execute(
                    text(
                        """
                        SELECT dependency.*, material.material_key, material.library_kind,
                               material.library_material_id, material.role, material.metadata_json
                        FROM automation_group_ops_effect_dependency dependency
                        JOIN automation_group_ops_effect_material material ON material.id = dependency.material_id
                        WHERE dependency.prerequisite_effect_job_id = :upload_job_id
                        FOR UPDATE OF dependency
                        """
                    ),
                    {"upload_job_id": int(upload_job_id)},
                )
                .mappings()
                .first()
            )
            if not dependency:
                session.rollback()
                return {"ok": True, "applicable": False, "released": False, "reason": "dependency_not_found"}
            dependency = dict(dependency)
            upload = (
                session.execute(
                    text("SELECT status, last_attempt_id FROM external_effect_job WHERE id = :job_id FOR UPDATE"),
                    {"job_id": int(upload_job_id)},
                )
                .mappings()
                .one()
            )
            if clean_text(upload["status"]) != "succeeded":
                session.rollback()
                return {
                    "ok": False,
                    "applicable": True,
                    "released": False,
                    "reason": "upload_not_succeeded",
                    "upload_status": clean_text(upload["status"]),
                }
            media_id = self._provider_media_id(session, dependency)
            if not media_id:
                session.rollback()
                return {
                    "ok": False,
                    "applicable": True,
                    "released": False,
                    "reason": "provider_media_id_not_projected",
                }
            effective_attempt_id = clean_text(attempt_id) or clean_text(upload["last_attempt_id"])
            session.execute(
                text(
                    """
                    UPDATE automation_group_ops_effect_dependency
                    SET status = 'succeeded', provider_media_id = :provider_media_id,
                        completed_attempt_id = :attempt_id,
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :dependency_id AND status IN ('waiting', 'succeeded')
                    """
                ),
                {
                    "provider_media_id": media_id,
                    "attempt_id": effective_attempt_id,
                    "dependency_id": int(dependency["id"]),
                },
            )
            graph = dict(
                session.execute(
                    text("SELECT * FROM automation_group_ops_effect_graph WHERE id = :graph_id FOR UPDATE"),
                    {"graph_id": int(dependency["graph_id"])},
                )
                .mappings()
                .one()
            )
            if graph["status"] in {"superseded", "cancelled", "terminal"}:
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": graph["execution_id"],
                }
            dependencies = [
                dict(item)
                for item in session.execute(
                    text(
                        """
                        SELECT dependency.status, dependency.provider_media_id, material.material_key
                        FROM automation_group_ops_effect_dependency dependency
                        JOIN automation_group_ops_effect_material material ON material.id = dependency.material_id
                        WHERE dependency.graph_id = :graph_id
                        ORDER BY dependency.id ASC
                        FOR UPDATE OF dependency
                        """
                    ),
                    {"graph_id": int(graph["id"])},
                )
                .mappings()
                .all()
            ]
            if any(item["status"] != "succeeded" for item in dependencies):
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": "dependencies_waiting",
                    "execution_id": graph["execution_id"],
                    "remaining": len([item for item in dependencies if item["status"] != "succeeded"]),
                }
            final_job = dict(
                session.execute(
                    text("SELECT * FROM external_effect_job WHERE id = :job_id FOR UPDATE"),
                    {"job_id": int(graph["final_effect_job_id"])},
                )
                .mappings()
                .one()
            )
            if final_job["status"] != "planned":
                session.execute(
                    text("UPDATE automation_group_ops_effect_graph SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE id = :graph_id"),
                    {"graph_id": int(graph["id"])},
                )
                session.commit()
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": "final_effect_already_released",
                    "execution_id": graph["execution_id"],
                    "final_effect_status": final_job["status"],
                }
            ready_by_key = {item["material_key"]: item["provider_media_id"] for item in dependencies}
            payload = dict(final_job["payload_json"] or {})
            content_payload = dict(payload.get("content_payload") or {})
            content_payload["attachments"] = self._resolve_attachments(
                list(content_payload.get("attachments") or []),
                ready_by_key,
            )
            payload["content_payload"] = content_payload
            payload_summary = dict(final_job["payload_summary_json"] or {})
            payload_summary["dependencies_resolved"] = True
            payload_summary["dependency_count"] = len(dependencies)
            released = session.execute(
                text(
                    """
                    UPDATE external_effect_job
                    SET payload_json = CAST(:payload_json AS jsonb),
                        payload_summary_json = CAST(:payload_summary_json AS jsonb),
                        status = 'queued', available_at = scheduled_at,
                        hold_reason = '', hold_at = NULL,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id
                      AND status = 'planned'
                      AND provider_call_started_at IS NULL
                    RETURNING id
                    """
                ),
                {
                    "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "payload_summary_json": json.dumps(payload_summary, ensure_ascii=False, separators=(",", ":")),
                    "job_id": int(final_job["id"]),
                },
            ).scalar_one_or_none()
            if not released:
                session.rollback()
                return {
                    "ok": False,
                    "applicable": True,
                    "released": False,
                    "reason": "final_effect_release_cas_lost",
                    "execution_id": graph["execution_id"],
                }
            session.execute(
                text("UPDATE automation_group_ops_effect_graph SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE id = :graph_id"),
                {"graph_id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "applicable": True,
                "released": True,
                "execution_id": graph["execution_id"],
                "final_effect_job_id": int(final_job["id"]),
            }

    @staticmethod
    def _provider_media_id(session: Session, dependency: dict[str, Any]) -> str:
        library_kind = clean_text(dependency.get("library_kind")) or "image"
        material_id = int(dependency["library_material_id"])
        query_by_kind = {
            "image": "SELECT thumb_media_id FROM image_library WHERE id = :material_id",
            "attachment": "SELECT media_id FROM attachment_library WHERE id = :material_id",
            "miniprogram": "SELECT thumb_media_id FROM miniprogram_library WHERE id = :material_id",
        }
        return clean_text(
            session.execute(
                text(query_by_kind[library_kind]),
                {"material_id": material_id},
            ).scalar_one_or_none()
        )

    @staticmethod
    def _resolve_attachments(
        attachments: list[Any],
        ready_by_key: dict[str, str],
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            current = json.loads(json.dumps(item, ensure_ascii=False))
            msgtype = clean_text(current.get("msgtype"))
            nested = current.get(msgtype) if isinstance(current.get(msgtype), dict) else {}
            if msgtype == "image" and clean_text(nested.get("media_dependency_key")):
                key = clean_text(nested.pop("media_dependency_key"))
                nested["media_id"] = ready_by_key[key]
            if msgtype == "file" and clean_text(nested.get("media_dependency_key")):
                key = clean_text(nested.pop("media_dependency_key"))
                nested["media_id"] = ready_by_key[key]
            if msgtype == "miniprogram" and clean_text(nested.get("pic_media_dependency_key")):
                key = clean_text(nested.pop("pic_media_dependency_key"))
                nested["pic_media_id"] = ready_by_key[key]
            current[msgtype] = nested
            resolved.append(current)
        return resolved

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        with self._session_factory() as session:
            graph = (
                session.execute(
                    text("SELECT * FROM automation_group_ops_effect_graph WHERE execution_id = :execution_id FOR UPDATE"),
                    {"execution_id": clean_text(execution_id)},
                )
                .mappings()
                .first()
            )
            if not graph:
                session.rollback()
                return {"ok": False, "cancelled": False, "reason": "graph_not_found"}
            graph = dict(graph)
            cancel_requested = request_cancel_dispatching_group_ops_jobs_in_session(
                session,
                graph_id=int(graph["id"]),
                final_effect_job_id=int(graph["final_effect_job_id"] or 0),
                actor=clean_text(actor),
                reason=clean_text(reason),
            )
            cancelled_rows = (
                session.execute(
                    text(
                        """
                    UPDATE external_effect_job job
                    SET status = 'cancelled', cancel_requested_at = CURRENT_TIMESTAMP,
                        cancel_requested_by = :actor, cancel_reason = :reason,
                        cancelled_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE job.id IN (
                        SELECT prerequisite_effect_job_id FROM automation_group_ops_effect_dependency WHERE graph_id = :graph_id
                        UNION
                        SELECT dependent_effect_job_id FROM automation_group_ops_effect_dependency WHERE graph_id = :graph_id
                        UNION
                        SELECT :final_effect_job_id
                    )
                      AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                      AND job.provider_call_started_at IS NULL
                    RETURNING job.*
                    """
                    ),
                    {
                        "graph_id": int(graph["id"]),
                        "final_effect_job_id": int(graph["final_effect_job_id"] or 0),
                        "actor": clean_text(actor),
                        "reason": clean_text(reason),
                    },
                )
                .mappings()
                .all()
            )
            updated = enqueue_external_effect_settled_rows_in_session(session, cancelled_rows)
            session.execute(
                text(
                    """
                    UPDATE automation_group_ops_effect_graph
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :graph_id
                    """
                ),
                {"graph_id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "cancelled": True,
                "execution_id": graph["execution_id"],
                "cancelled_job_ids": [int(item) for item in updated],
                "cancel_requested_job_ids": [int(item) for item in cancel_requested],
            }

class InMemoryGroupOpsEffectGraphRepository(InMemoryGroupOpsEffectGraphLifecycleMixin):
    def __init__(self, external_effect_repo: ExternalEffectRepository | None = None) -> None:
        self._effect_repo = external_effect_repo or build_external_effect_repository()
        self._service = ExternalEffectService(self._effect_repo)
        self._lock = RLock()
        self._graphs: dict[str, dict[str, Any]] = {}
        self._by_execution: dict[str, dict[str, Any]] = {}
        self._upload_to_graph: dict[int, tuple[str, str]] = {}
        self._plan_versions: dict[tuple[int, int], dict[str, Any]] = {}

    def reset(self, external_effect_repo: ExternalEffectRepository | None = None) -> None:
        with self._lock:
            if external_effect_repo is not None:
                self._effect_repo = external_effect_repo
                self._service = ExternalEffectService(external_effect_repo)
            self._graphs.clear()
            self._by_execution.clear()
            self._upload_to_graph.clear()
            self._plan_versions.clear()

    def plan(self, request: GroupOpsEffectGraphRequest) -> dict[str, Any]:
        with self._lock:
            existing = self._graphs.get(clean_text(request.idempotency_key))
            if existing:
                return {**existing["response"], "duplicate": True}
            execution_id = _execution_id()
            source_version = 1
            if request.source_kind == "plan_node":
                version_key = (int(request.plan_id), int(request.node_id))
                fingerprint = clean_text(request.version_fingerprint) or _stable_hash(request.content_payload)
                latest = self._plan_versions.get(version_key)
                if latest and latest["fingerprint"] == fingerprint:
                    source_version = int(latest["version"])
                else:
                    source_version = int((latest or {}).get("version") or 0) + 1
                    for old_execution_id in list((latest or {}).get("execution_ids") or []):
                        self._supersede(old_execution_id, actor=clean_text(request.actor_id))
                    self._plan_versions[version_key] = {
                        "fingerprint": fingerprint,
                        "version": source_version,
                        "execution_ids": [],
                    }
            content_payload = materialize_group_ops_content_dependencies(request.content_payload, request.materials)
            payload = _final_effect_payload(request, content_payload=content_payload, execution_id=execution_id)
            effect_type = WECOM_MESSAGE_GROUP_SEND
            adapter_name = "wecom_group_message"
            operation = "send_group_message"
            lane = "wecom_bulk"
            if request.test_loopback:
                effect_type = GROUP_OPS_MESSAGE_LOOPBACK
                adapter_name = "outbound_webhook"
                operation = "post"
                lane = "outbound_webhook"
                body = {
                    "synthetic": True,
                    "source": request.source_module,
                    "effect_type": effect_type,
                    "plan_id": int(request.plan_id),
                    "node_id": int(request.node_id or 0),
                    "chat_ids": list(payload["chat_ids"]),
                    "content_summary": clean_text(request.content_summary)[:500],
                    "content_payload": content_payload_summary(content_payload),
                    "test_only": True,
                }
                base_url = clean_text(request.test_receiver_base_url)
                if not base_url:
                    raise ValueError("test_receiver_base_url is required for Group Ops loopback")
                payload.update(
                    {
                        "webhook_url": f"{base_url.rstrip('/')}{TEST_RECEIVER_PATH_PREFIX}",
                        "body": body,
                        "receiver_response_status": int(request.test_receiver_response_status or 200),
                        "test_receiver_expires_at": public_datetime(_utcnow() + timedelta(hours=12)),
                        "execution_scope": "test_loopback",
                        "is_test": True,
                        "expected_payload_hash": canonical_payload_hash(body),
                    }
                )
            final = self._service.plan_effect(
                effect_type=effect_type,
                adapter_name=adapter_name,
                operation=operation,
                target_type="group_ops_execution",
                target_id=execution_id,
                business_type=GROUP_OPS_EFFECT_BUSINESS_TYPE,
                business_id=execution_id,
                payload=payload,
                payload_summary={
                    "group_ops_execution_id": execution_id,
                    "chat_count": len(payload["chat_ids"]),
                    "dependency_count": len(request.materials),
                    "content_payload": content_payload_summary(content_payload),
                },
                context=CommandContext(
                    actor_id=request.actor_id,
                    actor_type="system",
                    request_id=request.idempotency_key,
                    trace_id=execution_id,
                    source_route=request.source_route,
                ),
                source_module=request.source_module,
                source_event_id=request.source_event_id,
                source_command_id=request.source_command_id,
                status="planned" if request.materials else "queued",
                scheduled_at=request.normalized_scheduled_at(),
                idempotency_key=f"{request.idempotency_key}:final:v1",
                execution_id=_execution_id("exe_group_ops_send"),
                parent_execution_id=execution_id,
                lane=lane,
            )
            uploads: list[int] = []
            dependencies: dict[str, dict[str, Any]] = {}
            business_due_at = request.normalized_scheduled_at()
            upload_available_at = _media_upload_available_at(business_due_at)
            for index, material in enumerate(request.materials, start=1):
                upload = self._service.plan_effect(
                    effect_type=WECOM_MEDIA_UPLOAD,
                    adapter_name="wecom_media_upload",
                    operation="refresh_temporary_media",
                    target_type="group_ops_fixture_material",
                    target_id=f"{execution_id}:{index}",
                    business_type=GROUP_OPS_EFFECT_BUSINESS_TYPE,
                    business_id=execution_id,
                    payload={
                        "group_ops_execution_id": execution_id,
                        "material_key": material.material_key,
                        "source_payload_persisted": True,
                    },
                    payload_summary={
                        "group_ops_execution_id": execution_id,
                        "material_key": material.material_key,
                        "source_payload_redacted": True,
                    },
                    context=CommandContext(
                        actor_id=request.actor_id,
                        actor_type="system",
                        request_id=request.idempotency_key,
                        trace_id=execution_id,
                        source_route=request.source_route,
                    ),
                    source_module=request.source_module,
                    source_event_id=request.source_event_id,
                    source_command_id=f"{request.source_command_id}:{material.material_key}",
                    status="queued",
                    scheduled_at=business_due_at,
                    available_at=upload_available_at,
                    idempotency_key=f"{request.idempotency_key}:upload:{material.material_key}:v1",
                    execution_id=_execution_id("exe_group_ops_upload"),
                    parent_execution_id=execution_id,
                    lane="wecom_media",
                )
                upload_id = int(upload["id"])
                uploads.append(upload_id)
                dependencies[material.material_key] = {"job_id": upload_id, "status": "waiting", "media_id": ""}
                self._upload_to_graph[upload_id] = (execution_id, material.material_key)
            response = _effect_response(
                execution_id=execution_id,
                source_version=source_version,
                final_job_id=int(final["id"]),
                upload_job_ids=uploads,
                status="waiting_dependencies" if uploads else "ready",
                duplicate=False,
            )
            graph = {
                "request": request,
                "response": response,
                "dependencies": dependencies,
                "status": response["status"],
            }
            self._graphs[request.idempotency_key] = graph
            self._by_execution[execution_id] = graph
            if request.source_kind == "plan_node":
                self._plan_versions[(int(request.plan_id), int(request.node_id))]["execution_ids"].append(execution_id)
            return dict(response)

    def _supersede(self, execution_id: str, *, actor: str) -> None:
        graph = self._by_execution.get(execution_id)
        if not graph or graph["status"] not in {"waiting_dependencies", "ready"}:
            return
        for job_id in graph["response"]["job_ids"]:
            job = self._service.get(int(job_id))
            if not job or job.provider_call_started_at:
                continue
            self._service.cancel(
                int(job_id),
                actor=actor or "group_ops_plan_editor",
                reason="group_ops_plan_version_superseded",
                expected_version=job.row_version,
            )
        graph["status"] = "superseded"
        graph["response"]["status"] = "superseded"

    def release_after_upload(self, upload_job_id: int, *, attempt_id: str = "") -> dict[str, Any]:
        del attempt_id
        with self._lock:
            pointer = self._upload_to_graph.get(int(upload_job_id))
            if not pointer:
                return {"ok": True, "applicable": False, "released": False, "reason": "dependency_not_found"}
            execution_id, material_key = pointer
            graph = self._by_execution[execution_id]
            if graph["status"] in {"superseded", "cancelled", "terminal"}:
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": execution_id,
                }
            dependency = graph["dependencies"][material_key]
            if dependency["status"] == "waiting":
                return {
                    "ok": False,
                    "applicable": True,
                    "released": False,
                    "reason": "fixture_media_id_required",
                }
            remaining = [item for item in graph["dependencies"].values() if item["status"] != "succeeded"]
            if remaining:
                return {
                    "ok": True,
                    "applicable": True,
                    "released": False,
                    "reason": "dependencies_waiting",
                    "remaining": len(remaining),
                }
            final_id = int(graph["response"]["final_effect_job_id"])
            final = self._service.get(final_id)
            if final and final.status == "planned":
                self._service.enqueue(final_id)
                graph["status"] = "ready"
                graph["response"]["status"] = "ready"
                return {
                    "ok": True,
                    "applicable": True,
                    "released": True,
                    "execution_id": execution_id,
                    "final_effect_job_id": final_id,
                }
            return {
                "ok": True,
                "applicable": True,
                "released": False,
                "reason": "final_effect_already_released",
                "execution_id": execution_id,
            }

    def complete_fixture_upload(self, upload_job_id: int, *, media_id: str) -> dict[str, Any]:
        pointer = self._upload_to_graph[int(upload_job_id)]
        graph = self._by_execution[pointer[0]]
        graph["dependencies"][pointer[1]].update({"status": "succeeded", "media_id": clean_text(media_id)})
        return self.release_after_upload(upload_job_id)

    def fail_fixture_upload(self, upload_job_id: int, *, status: str = "failed") -> None:
        pointer = self._upload_to_graph[int(upload_job_id)]
        self._by_execution[pointer[0]]["dependencies"][pointer[1]]["status"] = status

    def cancel(self, execution_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        with self._lock:
            graph = self._by_execution.get(clean_text(execution_id))
            if not graph:
                return {"ok": False, "cancelled": False, "reason": "graph_not_found"}
            cancelled: list[int] = []
            for job_id in graph["response"]["job_ids"]:
                job = self._service.get(int(job_id))
                if not job:
                    continue
                if self._service.cancel(
                    int(job_id),
                    actor=clean_text(actor),
                    reason=clean_text(reason),
                    expected_version=job.row_version,
                ):
                    cancelled.append(int(job_id))
            graph["status"] = "cancelled"
            graph["response"]["status"] = "cancelled"
            return {
                "ok": True,
                "cancelled": True,
                "execution_id": execution_id,
                "cancelled_job_ids": cancelled,
            }


_MEMORY_GRAPH_REPOSITORY = InMemoryGroupOpsEffectGraphRepository()


def build_group_ops_effect_graph_repository(
    *,
    external_effect_repo: ExternalEffectRepository | None = None,
) -> GroupOpsEffectGraphRepository:
    if fixture_mode():
        current_effect_repo = external_effect_repo or build_external_effect_repository()
        if _MEMORY_GRAPH_REPOSITORY._effect_repo is not current_effect_repo:
            _MEMORY_GRAPH_REPOSITORY.reset(current_effect_repo)
        return _MEMORY_GRAPH_REPOSITORY
    return SQLAlchemyGroupOpsEffectGraphRepository()


def reset_group_ops_effect_graph_fixture_state(
    external_effect_repo: ExternalEffectRepository | None = None,
) -> None:
    _MEMORY_GRAPH_REPOSITORY.reset(external_effect_repo)


def plan_trusted_group_ops_bundle(
    *,
    plan: dict[str, Any],
    event_id: int,
    request_idempotency: str,
    chat_ids: list[str],
    content_payload: dict[str, Any],
    scheduled_at: Any,
    test_loopback: bool = False,
    test_receiver_base_url: str = "",
    test_receiver_response_status: int = 200,
) -> dict[str, Any]:
    return build_group_ops_effect_graph_repository().plan(
        GroupOpsEffectGraphRequest(
            idempotency_key=f"group-ops-legacy-bundle:{int(plan['id'])}:{int(event_id)}:{request_idempotency}",
            source_kind="trusted_webhook",
            plan_id=int(plan["id"]),
            chat_ids=chat_ids,
            content_payload=content_payload,
            content_summary=(content_payload.get("text") or {}).get("content", "") or f"{len(content_payload.get('attachments') or [])} attachments",
            actor_id=clean_text(plan.get("owner_userid")),
            owner_userid=clean_text(plan.get("owner_userid")),
            webhook_key=clean_text(plan.get("webhook_key")),
            source_module="automation_engine.group_ops.legacy_bundle",
            source_route="/api/automation/group-ops/webhooks/{webhook_key}",
            source_event_id=str(event_id),
            source_command_id=f"{int(plan['id'])}:webhook:{int(event_id)}",
            scheduled_at=scheduled_at,
            version_fingerprint=f"trusted-webhook:{int(event_id)}",
            test_loopback=bool(test_loopback),
            test_receiver_base_url=clean_text(test_receiver_base_url),
            test_receiver_response_status=int(test_receiver_response_status or 200),
        )
    )


__all__ = [
    "GROUP_OPS_EFFECT_BUSINESS_TYPE",
    "GroupOpsEffectGraphRepository",
    "GroupOpsEffectGraphRequest",
    "GroupOpsEffectMaterial",
    "InMemoryGroupOpsEffectGraphRepository",
    "SQLAlchemyGroupOpsEffectGraphRepository",
    "build_group_ops_effect_graph_repository",
    "reset_group_ops_effect_graph_fixture_state",
    "plan_trusted_group_ops_bundle",
]
