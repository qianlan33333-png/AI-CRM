from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.platform_foundation.external_calls import scrub_summary
from aicrm_next.platform_foundation.external_effects import (
    GROUP_OPS_MESSAGE_LOOPBACK,
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
)
from aicrm_next.platform_foundation.external_effects.models import ExternalEffectAttempt, ExternalEffectJob, public_datetime
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import production_data_ready, production_environment

from .section_mapper import label_for_section, section_for_job
from .status_mapper import standard_push_status
from .sql_read_model import SQLPushCenterReadModel

EFFECTIVE_PENDING = "pending"
EFFECTIVE_RUNNING = "running"
EFFECTIVE_SUCCEEDED = "succeeded"
EFFECTIVE_SENT = "sent"
EFFECTIVE_SIMULATED = "simulated"
EFFECTIVE_UNKNOWN_AFTER_DISPATCH = "unknown_after_dispatch"
EFFECTIVE_FAILED = "failed"
EFFECTIVE_SENT_WITH_SHADOW_WARNING = "sent_with_shadow_warning"
EFFECTIVE_SHADOW_FAILED_NOT_BUSINESS_FAILED = "shadow_failed_not_business_failed"

EFFECTIVE_STATUS_LABELS = {
    EFFECTIVE_PENDING: "待执行",
    EFFECTIVE_RUNNING: "执行中",
    EFFECTIVE_SUCCEEDED: "执行成功",
    EFFECTIVE_SENT: "已发送",
    EFFECTIVE_SIMULATED: "模拟执行",
    EFFECTIVE_UNKNOWN_AFTER_DISPATCH: "结果待核对",
    EFFECTIVE_FAILED: "发送失败",
    EFFECTIVE_SENT_WITH_SHADOW_WARNING: "已发送 · 影子链路异常",
    EFFECTIVE_SHADOW_FAILED_NOT_BUSINESS_FAILED: "影子链路失败，未发现主发送记录",
}

BUSINESS_FAILED_EFFECTIVE_STATUSES = {EFFECTIVE_FAILED}
SENT_EFFECTIVE_STATUSES = {EFFECTIVE_SENT, EFFECTIVE_SENT_WITH_SHADOW_WARNING}
SHADOW_EFFECT_TYPES = {GROUP_OPS_MESSAGE_LOOPBACK, GROUP_OPS_WEBHOOK_ACTION_LOOPBACK}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _is_missing_projection_table(exc: SQLAlchemyError) -> bool:
    message = str(exc).lower()
    return (
        "no such table: broadcast_jobs" in message
        or "no such table: outbound_tasks" in message
        or "undefinedtable" in message
        and ("broadcast_jobs" in message or "outbound_tasks" in message)
    )


def _scrub_nested(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"token", "secret", "password", "authorization", "access_token", "refresh_token"}:
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _scrub_nested(item)
        return result
    if isinstance(value, list):
        return [_scrub_nested(item) for item in value]
    return value


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row or {})
    for key, value in list(payload.items()):
        if key.endswith("_at") or key in {"scheduled_for", "claimed_at", "sent_at", "created_at", "updated_at"}:
            payload[key] = public_datetime(value)
    return payload


def _attempt_item(attempt: ExternalEffectAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "attempt_id": attempt.attempt_id,
        "job_id": attempt.job_id,
        "adapter_name": attempt.adapter_name,
        "adapter_mode": attempt.adapter_mode,
        "operation": attempt.operation,
        "trace_id": attempt.trace_id,
        "request_id": attempt.request_id,
        "status": standard_push_status(attempt.status),
        "effective_status": standard_push_status(attempt.status),
        "raw_status": attempt.status,
        "request_summary": scrub_summary(dict(attempt.request_summary_json or {})),
        "request_summary_json": scrub_summary(dict(attempt.request_summary_json or {})),
        "response_summary": scrub_summary(dict(attempt.response_summary_json or {})),
        "response_summary_json": scrub_summary(dict(attempt.response_summary_json or {})),
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
    }


def external_userid_for_job(job: ExternalEffectJob) -> str:
    if job.target_type in {"external_user", "external_userid", "wecom_external_user"}:
        return _text(job.target_id)
    summary = dict(job.payload_summary_json or {})
    payload = dict(job.payload_json or {})
    for source in (summary, payload):
        value = _text(source.get("external_userid") or source.get("external_user_id"))
        if value:
            return value
        values = source.get("external_userids")
        if isinstance(values, list) and values:
            return _text(values[0])
    return ""


def owner_userid_for_job(job: ExternalEffectJob) -> str:
    summary = dict(job.payload_summary_json or {})
    payload = dict(job.payload_json or {})
    for source in (summary, payload):
        value = _text(source.get("owner_userid") or source.get("sender") or source.get("operator_member_id"))
        if value:
            return value
    return _text(job.actor_id)


def _execution_id(value: ExternalEffectJob | dict[str, Any]) -> str:
    payload = value.to_dict() if isinstance(value, ExternalEffectJob) else dict(value or {})
    return _text(payload.get("parent_execution_id")) or _text(payload.get("execution_id"))


def _external_job_item(job: ExternalEffectJob) -> dict[str, Any]:
    section = section_for_job(job)
    return {
        "id": job.id,
        "record_type": "external_effect_job",
        "source_type": "external_effect_job",
        "section": section,
        "section_label": label_for_section(section),
        "effect_type": job.effect_type,
        "adapter_name": job.adapter_name,
        "operation": job.operation,
        "raw_status": job.status,
        "row_version": job.row_version,
        "execution_mode": job.execution_mode,
        "business_type": job.business_type,
        "business_id": job.business_id,
        "target_type": job.target_type,
        "target_id": job.target_id,
        "external_userid": external_userid_for_job(job),
        "owner_userid": owner_userid_for_job(job),
        "execution_id": job.execution_id,
        "parent_execution_id": job.parent_execution_id,
        "root_execution_id": _execution_id(job),
        "lane": job.lane,
        "available_at": job.available_at,
        "hold_reason": job.hold_reason,
        "policy_version": job.policy_version,
        "source_module": job.source_module,
        "source_route": job.source_route,
        "source_event_id": job.source_event_id,
        "source_command_id": job.source_command_id,
        "trace_id": job.trace_id,
        "request_id": job.request_id,
        "idempotency_key": job.idempotency_key,
        "actor_id": job.actor_id,
        "actor_type": job.actor_type,
        "risk_level": job.risk_level,
        "requires_approval": job.requires_approval,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "last_attempt_id": job.last_attempt_id,
        "last_error_code": job.last_error_code,
        "last_error_message": job.last_error_message,
        "scheduled_at": job.scheduled_at,
        "next_retry_at": job.next_retry_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "executed_at": job.executed_at,
        "cancelled_at": job.cancelled_at,
        "cancel_requested_at": job.cancel_requested_at,
        "cancel_requested_by": job.cancel_requested_by,
        "cancel_reason": job.cancel_reason,
        "payload_summary": scrub_summary(dict(job.payload_summary_json or {})),
        "payload_summary_json": scrub_summary(dict(job.payload_summary_json or {})),
    }


def _broadcast_section(row: dict[str, Any]) -> str:
    source_table = _text(row.get("source_table"))
    source_id = _text(row.get("source_id"))
    channel = _text(row.get("channel"))
    if source_table == "automation_group_ops_plans" or "webhook:" in source_id or source_id.startswith("group_ops:"):
        return "group_ops"
    if channel == "wecom_customer_group":
        return "group_broadcast"
    if channel == "wecom_private":
        return "private_broadcast"
    return "other"


def _broadcast_effect_type(row: dict[str, Any]) -> str:
    section = _broadcast_section(row)
    if section == "group_ops":
        return "broadcast_job.group_ops"
    if section == "group_broadcast":
        return "broadcast_job.group"
    if section == "private_broadcast":
        return "broadcast_job.private"
    return "broadcast_job"


def _business_id_for_broadcast(row: dict[str, Any]) -> str:
    source_id = _text(row.get("source_id"))
    if ":webhook:" in source_id:
        return source_id.split(":webhook:", 1)[0]
    payload = _json_obj(row.get("content_payload"))
    return _text(payload.get("plan_id") or row.get("business_domain") or source_id)


def _target_id_for_broadcast(row: dict[str, Any]) -> str:
    source_id = _text(row.get("source_id"))
    if ":webhook:" in source_id:
        return source_id.rsplit(":webhook:", 1)[-1]
    return _text(row.get("target_summary") or row.get("target_kind"))


def _outbound_task_item(row: dict[str, Any]) -> dict[str, Any]:
    if not row or not row.get("outbound_task_id"):
        return {}
    return _scrub_nested(
        {
            "id": row.get("outbound_task_id"),
            "status": row.get("outbound_task_status"),
            "task_type": row.get("outbound_task_type"),
            "wecom_task_id": row.get("outbound_task_wecom_task_id"),
            "trace_id": row.get("outbound_task_trace_id"),
            "created_at": public_datetime(row.get("outbound_task_created_at")),
            "response_payload": _json_obj(row.get("outbound_task_response_payload")),
        }
    )


def _broadcast_job_item(row: dict[str, Any]) -> dict[str, Any]:
    section = _broadcast_section(row)
    target_id = _target_id_for_broadcast(row)
    public_row = _public_row(row)
    public_row.pop("outbound_task_response_payload", None)
    return {
        **scrub_summary(public_row),
        "id": int(row.get("id") or 0),
        "record_type": "broadcast_job",
        "source_type": "broadcast_job",
        "section": section,
        "section_label": label_for_section(section),
        "effect_type": _broadcast_effect_type(row),
        "adapter_name": "broadcast_queue",
        "operation": "send",
        "raw_status": _text(row.get("status")),
        "business_type": "group_ops_plan" if section == "group_ops" else _text(row.get("business_domain") or row.get("source_type")),
        "business_id": _business_id_for_broadcast(row),
        "target_type": _text(row.get("target_kind") or "broadcast_target"),
        "target_id": target_id,
        "external_userid": "",
        "owner_userid": _text(row.get("created_by")),
        "execution_id": _text(row.get("execution_id")),
        "parent_execution_id": "",
        "root_execution_id": _execution_id(row),
        "source_module": "broadcast_jobs",
        "source_route": "/api/admin/broadcast-jobs",
        "source_event_id": _text(row.get("source_id")),
        "source_command_id": _text(row.get("source_id")),
        "trace_id": _text(row.get("trace_id")),
        "request_id": _text(row.get("trace_id")),
        "idempotency_key": _text(row.get("idempotency_key")),
        "attempt_count": int(row.get("attempt_count") or 0),
        "max_attempts": int(row.get("max_attempts") or 0),
        "last_attempt_id": "",
        "last_error_code": _text(row.get("failure_type")),
        "last_error_message": _text(row.get("last_error")),
        "created_at": public_datetime(row.get("created_at")),
        "updated_at": public_datetime(row.get("updated_at")),
        "scheduled_at": public_datetime(row.get("scheduled_for")),
        "executed_at": public_datetime(row.get("sent_at")),
        "payload_summary_json": scrub_summary(
            {
                "source_id": row.get("source_id"),
                "source_table": row.get("source_table"),
                "target_summary": row.get("target_summary"),
                "target_count": row.get("target_count"),
                "sent_count": row.get("sent_count"),
                "failed_count": row.get("failed_count"),
                "content_summary": row.get("content_summary"),
                "outbound_task_id": row.get("outbound_task_id"),
            }
        ),
        "outbound_task": _outbound_task_item(row),
    }


@dataclass
class ProjectionGroup:
    key: str
    external_effect_jobs: list[dict[str, Any]] = field(default_factory=list)
    external_effect_attempts: list[dict[str, Any]] = field(default_factory=list)
    broadcast_jobs: list[dict[str, Any]] = field(default_factory=list)
    outbound_tasks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_records(self) -> list[dict[str, Any]]:
        return [*self.external_effect_jobs, *self.broadcast_jobs]


class ExternalEffectAdapter:
    def __init__(self, service: ExternalEffectService | None = None) -> None:
        self._service = service or ExternalEffectService()

    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 200) -> list[ExternalEffectJob]:
        jobs, _total = self._service.list_jobs(filters or {}, limit=limit, offset=0)
        return list(jobs)

    def get_job(self, job_id: int) -> ExternalEffectJob | None:
        return self._service.get(job_id)

    def list_attempts(self, job_id: int) -> list[ExternalEffectAttempt]:
        return list(self._service.list_attempts(job_id))

    def list_attempts_for_jobs(self, job_ids: list[int]) -> dict[int, list[ExternalEffectAttempt]]:
        return {int(job_id): list(attempts) for job_id, attempts in self._service.list_attempts_for_jobs(job_ids).items()}


class BroadcastJobAdapter:
    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit or 200), 200))}
        if _text(filters.get("business_id")):
            clauses.append("(bj.source_id = :business_source OR bj.source_id LIKE :business_webhook)")
            params["business_source"] = _text(filters.get("business_id"))
            params["business_webhook"] = f"{_text(filters.get('business_id'))}:webhook:%"
        if _text(filters.get("trace_id")):
            clauses.append("bj.trace_id = :trace_id")
            params["trace_id"] = _text(filters.get("trace_id"))
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        statement = text(
            """
            SELECT
              bj.*,
              ot.id AS outbound_task_id,
              ot.task_type AS outbound_task_type,
              ot.status AS outbound_task_status,
              ot.wecom_task_id AS outbound_task_wecom_task_id,
              ot.response_payload AS outbound_task_response_payload,
              ot.trace_id AS outbound_task_trace_id,
              bj.updated_at AS outbound_task_created_at
            FROM broadcast_jobs bj
            LEFT JOIN outbound_tasks ot ON ot.id = bj.outbound_task_id
            """
            + where_sql
            + " ORDER BY bj.created_at DESC, bj.id DESC LIMIT :limit"
        )
        try:
            with self._session_factory() as session:
                rows = session.execute(statement, params).mappings().fetchall()
                return [dict(row) for row in rows]
        except SQLAlchemyError as exc:
            if _is_missing_projection_table(exc):
                return []
            if production_environment():
                raise
            return []

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        statement = text(
            """
            SELECT
              bj.*,
              ot.id AS outbound_task_id,
              ot.task_type AS outbound_task_type,
              ot.status AS outbound_task_status,
              ot.wecom_task_id AS outbound_task_wecom_task_id,
              ot.response_payload AS outbound_task_response_payload,
              ot.trace_id AS outbound_task_trace_id,
              bj.updated_at AS outbound_task_created_at
            FROM broadcast_jobs bj
            LEFT JOIN outbound_tasks ot ON ot.id = bj.outbound_task_id
            WHERE bj.id = :job_id
            LIMIT 1
            """
        )
        try:
            with self._session_factory() as session:
                row = session.execute(statement, {"job_id": int(job_id)}).mappings().fetchone()
                return dict(row) if row else None
        except SQLAlchemyError as exc:
            if _is_missing_projection_table(exc):
                return None
            if production_environment():
                raise
            return None


class BusinessCorrelationService:
    def keys_for_external_job(self, job: ExternalEffectJob | dict[str, Any]) -> set[str]:
        value = job.to_dict() if isinstance(job, ExternalEffectJob) else dict(job)
        execution_id = _execution_id(value)
        return {f"execution:{execution_id}"} if execution_id else {f"external_effect_job:{int(value.get('id') or 0)}"}

    def keys_for_broadcast_job(self, row: dict[str, Any]) -> set[str]:
        execution_id = _execution_id(row)
        return {f"execution:{execution_id}"} if execution_id else {f"broadcast_job:{int(row.get('id') or 0)}"}


class PushCenterProjectionService:
    def __init__(
        self,
        *,
        external_adapter: ExternalEffectAdapter | None = None,
        broadcast_adapter: BroadcastJobAdapter | None = None,
        correlation: BusinessCorrelationService | None = None,
        sql_read_model: SQLPushCenterReadModel | None = None,
    ) -> None:
        self._external = external_adapter or ExternalEffectAdapter()
        self._broadcast = broadcast_adapter or BroadcastJobAdapter()
        self._correlation = correlation or BusinessCorrelationService()
        self._sql = (
            sql_read_model
            if sql_read_model is not None
            else SQLPushCenterReadModel()
            if external_adapter is None and broadcast_adapter is None and production_data_ready()
            else None
        )

    def list_projections(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if self._sql is not None:
            page = self._sql.query(filters or {}, limit=limit, legacy_offset=offset)
            return page.items, page.total
        records = self._matching_projections(filters or {})
        return self._page(records, limit=limit, offset=offset)

    def query_projections(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        cursor: str = "",
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], list[dict[str, Any]], str, bool]:
        if self._sql is not None:
            page = self._sql.query(filters or {}, limit=limit, cursor=cursor, legacy_offset=offset)
            return page.items, page.total, page.counts, page.sections, page.next_cursor, page.has_more
        records = self._matching_projections(filters or {})
        items, total = self._page(records, limit=limit, offset=offset)
        counts = self._counts_for_records(records)
        return items, total, counts, self._sections_for_counts(counts), "", offset + len(items) < total

    def summary(self, filters: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self._sql is not None:
            page = self._sql.query(filters or {}, limit=1)
            return page.counts, page.sections
        records = self._matching_projections(filters or {})
        counts = self._counts_for_records(records)
        return counts, self._sections_for_counts(counts)

    def _matching_projections(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        groups = self._groups(filters or {})
        records = [self._projection_item(group) for group in groups]
        matched = [item for item in records if self._matches_projection(item, filters or {})]
        matched.sort(key=lambda item: _text(item.get("created_at")), reverse=True)
        return matched

    @staticmethod
    def _page(records: list[dict[str, Any]], *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        start = max(0, int(offset or 0))
        size = max(1, min(int(limit or 50), 200))
        return records[start : start + size], len(records)

    def get_projection(self, projection_id: str) -> dict[str, Any] | None:
        if self._sql is not None:
            return self._sql.get(projection_id)
        kind, raw_id = self._parse_projection_id(projection_id)
        if kind == "external_effect_job":
            job = self._external.get_job(int(raw_id))
            if not job:
                return None
            groups = self._groups_for_seed(external_job=job)
            group = next(
                (
                    item
                    for item in groups
                    if any(int(record.get("id") or 0) == int(job.id) for record in item.external_effect_jobs)
                ),
                None,
            )
            return self._projection_item(group) if group else None
        if kind == "broadcast_job":
            job = self._broadcast.get_job(int(raw_id))
            if not job:
                return None
            groups = self._groups_for_seed(broadcast_job=job)
            group = next(
                (
                    item
                    for item in groups
                    if any(int(record.get("id") or 0) == int(job.get("id") or 0) for record in item.broadcast_jobs)
                ),
                None,
            )
            return self._projection_item(group) if group else None
        return None

    def counts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._sql is not None:
            return self._sql.query(filters or {}, limit=1).counts
        return self._counts_for_records(self._matching_projections(filters or {}))

    @staticmethod
    def _counts_for_records(records: list[dict[str, Any]]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_section: dict[str, int] = {}
        for item in records:
            status = _text(item.get("effective_status"))
            by_status[status] = by_status.get(status, 0) + 1
            section = _text(item.get("section"))
            by_section[section] = by_section.get(section, 0) + 1
        return {
            "total": len(records),
            "by_effective_status": by_status,
            "by_status": by_status,
            "by_section": by_section,
            "pending": by_status.get(EFFECTIVE_PENDING, 0),
            "running": by_status.get(EFFECTIVE_RUNNING, 0),
            "sent": by_status.get(EFFECTIVE_SENT, 0) + by_status.get(EFFECTIVE_SENT_WITH_SHADOW_WARNING, 0),
            "succeeded": by_status.get(EFFECTIVE_SUCCEEDED, 0),
            "failed": by_status.get(EFFECTIVE_FAILED, 0),
            "shadow_warning": by_status.get(EFFECTIVE_SENT_WITH_SHADOW_WARNING, 0) + by_status.get(EFFECTIVE_SHADOW_FAILED_NOT_BUSINESS_FAILED, 0),
        }

    def sections(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        counts = self.counts(filters or {})
        return self._sections_for_counts(counts)

    @staticmethod
    def _sections_for_counts(counts: dict[str, Any]) -> list[dict[str, Any]]:
        from .section_mapper import all_sections

        by_section = dict(counts.get("by_section") or {})
        return [{**section, "count": int(by_section.get(section["key"], 0)), "label": label_for_section(section["key"])} for section in all_sections()]

    def _groups(self, filters: dict[str, Any]) -> list[ProjectionGroup]:
        # This adapter path exists only for fixture/custom-adapter tests.
        # Database-backed runtime always uses SQLPushCenterReadModel and never
        # builds a capped two-sided Python merge.
        external_filters = {
            key: filters.get(key) for key in ("effect_type", "business_type", "business_id", "target_type", "target_id", "trace_id") if _text(filters.get(key))
        }
        external_jobs = self._external.list_jobs(external_filters, limit=200)
        attempts_by_job = self._external.list_attempts_for_jobs([job.id for job in external_jobs])
        broadcast_jobs = self._broadcast.list_jobs(filters, limit=200)
        return self._merge_groups(external_jobs, broadcast_jobs, attempts_by_job=attempts_by_job)

    def _groups_for_seed(self, *, external_job: ExternalEffectJob | None = None, broadcast_job: dict[str, Any] | None = None) -> list[ProjectionGroup]:
        filters: dict[str, Any] = {}
        if external_job:
            filters = {"business_id": external_job.business_id}
        elif broadcast_job:
            filters = {"business_id": _business_id_for_broadcast(broadcast_job)}
        return self._groups(filters)

    def _merge_groups(
        self,
        external_jobs: list[ExternalEffectJob],
        broadcast_jobs: list[dict[str, Any]],
        *,
        attempts_by_job: dict[int, list[ExternalEffectAttempt]],
    ) -> list[ProjectionGroup]:
        groups: list[ProjectionGroup] = []
        key_to_group: dict[str, ProjectionGroup] = {}

        def group_for(keys: set[str]) -> ProjectionGroup:
            existing = next((key_to_group[key] for key in keys if key in key_to_group), None)
            if existing:
                for key in keys:
                    key_to_group[key] = existing
                return existing
            key = sorted(keys)[0] if keys else f"orphan:{len(groups) + 1}"
            group = ProjectionGroup(key=key)
            groups.append(group)
            for item in keys:
                key_to_group[item] = group
            return group

        for job in external_jobs:
            keys = self._correlation.keys_for_external_job(job)
            group = group_for(keys)
            group.external_effect_jobs.append(_external_job_item(job))
            group.external_effect_attempts.extend(_attempt_item(attempt) for attempt in attempts_by_job.get(job.id, []))

        for row in broadcast_jobs:
            keys = self._correlation.keys_for_broadcast_job(row)
            group = group_for(keys)
            group.broadcast_jobs.append(_broadcast_job_item(row))
            outbound_task = _outbound_task_item(row)
            if outbound_task:
                group.outbound_tasks.append(outbound_task)

        return groups

    def _projection_item(self, group: ProjectionGroup) -> dict[str, Any]:
        primary = self._primary_record(group)
        effective_status = self._effective_status(group)
        item = dict(primary)
        item["projection_id"] = self._projection_id(group)
        item["source_record_id"] = item.get("id")
        item["id"] = item["projection_id"]
        item["display_id"] = self._display_id(group)
        item["effective_status"] = effective_status
        item["effective_status_label"] = EFFECTIVE_STATUS_LABELS[effective_status]
        item["status"] = effective_status
        item["status_label"] = EFFECTIVE_STATUS_LABELS[effective_status]
        item["raw_statuses"] = {
            "external_effect_jobs": [job.get("raw_status") for job in group.external_effect_jobs],
            "broadcast_jobs": [job.get("raw_status") for job in group.broadcast_jobs],
        }
        item["linked_record_counts"] = {
            "external_effect_jobs": len(group.external_effect_jobs),
            "external_effect_attempts": len(group.external_effect_attempts),
            "broadcast_jobs": len(group.broadcast_jobs),
            "outbound_tasks": len(group.outbound_tasks),
        }
        item["linked_records"] = {
            "external_effect_jobs": group.external_effect_jobs,
            "external_effect_attempts": group.external_effect_attempts,
            "broadcast_jobs": group.broadcast_jobs,
            "outbound_tasks": group.outbound_tasks,
        }
        return item

    def _primary_record(self, group: ProjectionGroup) -> dict[str, Any]:
        sent = [job for job in group.broadcast_jobs if job.get("raw_status") == "sent"]
        if sent:
            return sent[0]
        if group.broadcast_jobs:
            return group.broadcast_jobs[0]
        if group.external_effect_jobs:
            return group.external_effect_jobs[0]
        return {"record_type": "unknown", "created_at": ""}

    def _effective_status(self, group: ProjectionGroup) -> str:
        primary_sent = any(job.get("raw_status") == "sent" for job in group.broadcast_jobs)
        primary_simulated = any(job.get("raw_status") == "simulated" for job in group.broadcast_jobs)
        primary_unknown = any(job.get("raw_status") == "unknown_after_dispatch" for job in group.broadcast_jobs)
        primary_failed = any(job.get("raw_status") in {"failed", "failed_retryable", "failed_terminal", "blocked", "cancelled"} for job in group.broadcast_jobs)
        primary_running = any(job.get("raw_status") in {"claimed", "running", "dispatching"} for job in group.broadcast_jobs)
        primary_pending = any(job.get("raw_status") in {"queued", "waiting_approval", "pending", "delegated"} for job in group.broadcast_jobs)
        shadow_failed = any(self._is_shadow_failed(job) for job in group.external_effect_jobs)
        external_sent = any(job.get("raw_status") == "succeeded" and not self._is_shadow_job(job) for job in group.external_effect_jobs)
        external_simulated = any(job.get("raw_status") == "simulated" for job in group.external_effect_jobs)
        external_unknown = any(job.get("raw_status") == "unknown_after_dispatch" for job in group.external_effect_jobs)
        external_failed = any(standard_push_status(job.get("raw_status")) == "failed" and not self._is_shadow_job(job) for job in group.external_effect_jobs)
        external_running = any(standard_push_status(job.get("raw_status")) == "running" for job in group.external_effect_jobs)
        external_pending = any(standard_push_status(job.get("raw_status")) == "pending" for job in group.external_effect_jobs)
        if primary_sent and shadow_failed:
            return EFFECTIVE_SENT_WITH_SHADOW_WARNING
        if primary_sent or external_sent:
            return EFFECTIVE_SENT if primary_sent else EFFECTIVE_SUCCEEDED
        if primary_unknown or external_unknown:
            return EFFECTIVE_UNKNOWN_AFTER_DISPATCH
        if primary_simulated or external_simulated:
            return EFFECTIVE_SIMULATED
        if primary_failed or external_failed:
            return EFFECTIVE_FAILED
        if shadow_failed:
            return EFFECTIVE_SHADOW_FAILED_NOT_BUSINESS_FAILED
        if primary_running or external_running:
            return EFFECTIVE_RUNNING
        if primary_pending or external_pending:
            return EFFECTIVE_PENDING
        return EFFECTIVE_FAILED

    def _is_shadow_job(self, job: dict[str, Any]) -> bool:
        return _text(job.get("effect_type")) in SHADOW_EFFECT_TYPES or _text(job.get("execution_mode")) in {"shadow", "plan_only", "execute_dryrun"}

    def _is_shadow_failed(self, job: dict[str, Any]) -> bool:
        return self._is_shadow_job(job) and standard_push_status(job.get("raw_status")) == "failed"

    def _matches_projection(self, item: dict[str, Any], filters: dict[str, Any]) -> bool:
        if _text(filters.get("section")) and _text(item.get("section")) != _text(filters.get("section")):
            return False
        status = _text(filters.get("status"))
        if status and not self._status_matches(_text(item.get("effective_status")), status):
            return False
        for key in (
            "effect_type",
            "business_type",
            "business_id",
            "target_type",
            "target_id",
            "trace_id",
            "idempotency_key",
            "source_module",
            "source_route",
            "external_userid",
            "owner_userid",
        ):
            expected = _text(filters.get(key))
            if expected and expected not in _text(item.get(key)):
                return False
        return True

    def _status_matches(self, effective_status: str, expected: str) -> bool:
        if expected == "succeeded":
            return effective_status == EFFECTIVE_SUCCEEDED
        if expected == "sent":
            return effective_status in SENT_EFFECTIVE_STATUSES
        if expected == "failed":
            return effective_status in BUSINESS_FAILED_EFFECTIVE_STATUSES
        return effective_status == expected

    def _projection_id(self, group: ProjectionGroup) -> str:
        if group.external_effect_jobs:
            return f"external_effect_job:{group.external_effect_jobs[0]['id']}"
        if group.broadcast_jobs:
            return f"broadcast_job:{group.broadcast_jobs[0]['id']}"
        return group.key

    def _display_id(self, group: ProjectionGroup) -> str:
        if group.external_effect_jobs:
            return f"#{group.external_effect_jobs[0]['id']}"
        if group.broadcast_jobs:
            return f"B#{group.broadcast_jobs[0]['id']}"
        return "-"

    def _parse_projection_id(self, projection_id: str) -> tuple[str, str]:
        value = _text(projection_id)
        if ":" in value:
            kind, raw_id = value.split(":", 1)
            return kind, raw_id
        return "external_effect_job", value
