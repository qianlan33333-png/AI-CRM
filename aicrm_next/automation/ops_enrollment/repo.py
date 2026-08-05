from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aicrm_next.platform.shared.config import Settings, get_settings
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.repository_provider import assert_repository_allowed
from aicrm_next.platform.shared.runtime import database_mode
from aicrm_next.platform.shared.runtime_settings import startup_environment_setting
from aicrm_next.platform.shared.typing import JsonDict

from .models import (
    user_ops_do_not_disturb_next,
    user_ops_pool_current_next,
    user_ops_send_records_next,
)

AUTO_DND_REASON = {
    "source_type": "auto",
    "source": "auto",
    "reason_code": "signed_paid_course",
    "reason_text": "已报名正价课",
    "reason_label": "已报名正价课",
}

ACTIVATION_LABELS = {
    "activated": "黄小璨已激活",
    "not_activated": "黄小璨未激活",
    "pending_input": "激活待录入",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _status_label(status: str) -> str:
    return {
        "created": "已创建任务",
        "planned": "待审批",
        "queued": "排队中",
        "dispatching": "发送中",
        "partially_succeeded": "部分成功",
        "succeeded": "已成功",
        "failed": "发送失败",
        "blocked": "已阻断",
        "cancelled": "已取消",
    }.get(str(status or "").strip(), str(status or "").strip() or "未知")


def _new_record_key() -> str:
    return f"user_ops_send_{uuid4().hex[:12]}"


def _integrity_constraint_name(exc: IntegrityError) -> str:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return str(getattr(diagnostic, "constraint_name", "") or "")


def _job_id_from_result(job: JsonDict) -> int | None:
    value = job.get("job_id") or job.get("id")
    try:
        job_id = int(value)
    except (TypeError, ValueError):
        return None
    return job_id if job_id > 0 else None


def _external_effect_summary_from_jobs(jobs: list[JsonDict]) -> JsonDict:
    statuses = [str(job.get("status") or "").strip() for job in jobs if bool(job.get("ok", True))]
    planned_count = statuses.count("planned")
    queued_count = statuses.count("queued")
    dispatching_count = statuses.count("dispatching")
    succeeded_count = statuses.count("succeeded")
    failed_count = statuses.count("failed") + statuses.count("failed_retryable") + statuses.count("failed_terminal")
    blocked_count = statuses.count("blocked")
    cancelled_count = statuses.count("cancelled")
    total = len(statuses)
    if total <= 0:
        status = "failed"
    elif succeeded_count == total:
        status = "succeeded"
    elif blocked_count == total:
        status = "blocked"
    elif cancelled_count == total:
        status = "cancelled"
    elif failed_count == total:
        status = "failed"
    elif dispatching_count:
        status = "dispatching"
    elif succeeded_count or failed_count or blocked_count or cancelled_count:
        status = "partially_succeeded"
    elif queued_count:
        status = "queued"
    else:
        status = "planned"
    return {
        "status": status,
        "planned_count": planned_count,
        "queued_count": queued_count,
        "dispatching_count": dispatching_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "cancelled_count": cancelled_count,
        "total_count": total,
        "by_status": {status: statuses.count(status) for status in sorted(set(statuses)) if status},
    }


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _default_pool_rows() -> list[JsonDict]:
    return [
        {
            "id": 1,
            "unionid": "union_ops_001",
            "mobile": "13800138000",
            "external_userid": "wx_ext_001",
            "customer_name": "张小蓝",
            "owner_userid": "ZhaoYanFang",
            "owner_display_name": "赵燕芳",
            "class_term_no": "2026-05-A",
            "class_term_label": "2026 五月 A 班",
            "source_type": "lead_pool",
            "created_at": "2026-05-01T09:00:00+08:00",
            "updated_at": "2026-05-18T10:00:00+08:00",
            "activation_bucket": "activated",
            "tags": ["黄小璨", "已激活"],
            "manual_dnd_reasons": [],
            "auto_dnd_reasons": [],
        },
        {
            "id": 2,
            "unionid": "union_ops_002",
            "mobile": "",
            "external_userid": "wx_ext_002",
            "customer_name": "李未绑",
            "owner_userid": "LiuXiao",
            "owner_display_name": "刘潇",
            "class_term_no": "2026-05-A",
            "class_term_label": "2026 五月 A 班",
            "source_type": "lead_pool",
            "created_at": "2026-05-02T09:00:00+08:00",
            "updated_at": "2026-05-18T10:20:00+08:00",
            "activation_bucket": "pending_input",
            "tags": ["黄小璨", "待录入"],
            "manual_dnd_reasons": [],
            "auto_dnd_reasons": [dict(AUTO_DND_REASON)],
        },
        {
            "id": 3,
            "unionid": "union_ops_003",
            "mobile": "13900139000",
            "external_userid": "",
            "customer_name": "王缺外部联系人",
            "owner_userid": "ZhaoYanFang",
            "owner_display_name": "赵燕芳",
            "class_term_no": "2026-05-B",
            "class_term_label": "2026 五月 B 班",
            "source_type": "questionnaire",
            "created_at": "2026-05-03T09:00:00+08:00",
            "updated_at": "2026-05-18T10:40:00+08:00",
            "activation_bucket": "not_activated",
            "tags": ["未激活"],
            "manual_dnd_reasons": [],
            "auto_dnd_reasons": [],
        },
        {
            "id": 4,
            "unionid": "union_ops_004",
            "mobile": "13700137000",
            "external_userid": "wx_ext_004",
            "customer_name": "陈缺负责人",
            "owner_userid": "",
            "owner_display_name": "",
            "class_term_no": "2026-05-B",
            "class_term_label": "2026 五月 B 班",
            "source_type": "lead_pool",
            "created_at": "2026-05-04T09:00:00+08:00",
            "updated_at": "2026-05-18T11:00:00+08:00",
            "activation_bucket": "not_activated",
            "tags": ["黄小璨", "未激活"],
            "manual_dnd_reasons": [],
            "auto_dnd_reasons": [],
        },
    ]


def _manual_reason(reason_code: str, reason_text: str) -> JsonDict:
    return {
        "source_type": "manual",
        "source": "manual",
        "reason_code": reason_code,
        "reason_text": reason_text,
        "reason_label": reason_text,
    }


def _project_row(row: JsonDict) -> JsonDict:
    projected = deepcopy(row)
    unionid = str(projected.get("unionid") or "").strip()
    external_userid = str(projected.get("external_userid") or "").strip()
    mobile = str(projected.get("mobile") or "").strip()
    customer_name = str(projected.get("customer_name") or projected.get("customer_name_snapshot") or "").strip()
    activation_bucket = str(projected.get("activation_bucket") or "pending_input").strip() or "pending_input"
    reasons = list(projected.pop("auto_dnd_reasons", [])) + list(projected.pop("manual_dnd_reasons", []))
    projected.update(
        {
            "unionid": unionid,
            "customer_name": customer_name,
            "mobile": mobile,
            "external_userid": external_userid,
            "is_added_wecom": bool(external_userid),
            "is_wecom_added": bool(external_userid),
            "is_mobile_bound": bool(mobile),
            "activation_bucket": activation_bucket,
            "activation_bucket_label": ACTIVATION_LABELS.get(activation_bucket, activation_bucket),
            "huangxiaocan_activation_state": activation_bucket,
            "huangxiaocan_activation_state_label": ACTIVATION_LABELS.get(activation_bucket, activation_bucket),
            "do_not_disturb": bool(reasons),
            "do_not_disturb_reasons": reasons,
            "can_open_customer_detail": bool(external_userid),
            "can_batch_send": bool(external_userid),
            "tags": list(projected.get("tags") or []),
        }
    )
    return projected


class UserOpsRepository(Protocol):
    def reset(self) -> None: ...

    def list_rows(self) -> list[JsonDict]: ...

    def find_row(self, *, unionid: str = "") -> JsonDict | None: ...

    def set_do_not_disturb(
        self,
        *,
        unionid: str = "",
        reason_code: str,
        reason_text: str,
        is_active: bool,
        operator: str = "fixture-admin",
    ) -> JsonDict | None: ...

    def create_send_record(self, payload: JsonDict) -> JsonDict: ...

    def create_or_get_send_record_by_idempotency(self, *, idempotency_key: str, payload: JsonDict) -> JsonDict: ...

    def attach_external_effect_jobs(self, record_id: str, jobs: list[JsonDict]) -> JsonDict: ...

    def refresh_send_record_external_effect_status(self, record_id: str, summary: JsonDict) -> JsonDict: ...

    def get_send_record_external_effect_job_ids(self, record_id: str) -> list[int]: ...

    def list_send_records(self) -> list[JsonDict]: ...

    def get_send_record(self, record_id: str) -> JsonDict | None: ...


class InMemoryUserOpsRepository:
    """Default fixture repository; it mirrors the SQL repository contract."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._rows = _default_pool_rows()
        self._send_records: list[JsonDict] = []
        self._next_send_record_id = 1

    def list_rows(self) -> list[JsonDict]:
        return [_project_row(row) for row in self._rows]

    list_pool = list_rows

    def find_row(self, *, unionid: str = "") -> JsonDict | None:
        for row in self._rows:
            if unionid and row.get("unionid") == unionid:
                return row
        return None

    def set_do_not_disturb(
        self,
        *,
        unionid: str = "",
        reason_code: str,
        reason_text: str,
        is_active: bool,
        operator: str = "fixture-admin",
    ) -> JsonDict | None:
        row = self.find_row(unionid=unionid)
        if row is None:
            return None

        manual_reasons = [
            reason
            for reason in list(row.get("manual_dnd_reasons") or [])
            if reason.get("reason_code") != reason_code
        ]
        if is_active:
            manual_reasons.append(_manual_reason(reason_code, reason_text))
        row["manual_dnd_reasons"] = manual_reasons
        row["updated_at"] = _now_iso()
        return _project_row(row)

    def create_send_record(self, payload: JsonDict) -> JsonDict:
        record_id = self._next_send_record_id
        self._next_send_record_id += 1
        created_at = _now_iso()
        execution_backend = str(payload.get("execution_backend") or "legacy_fake")
        record = {
            "id": record_id,
            "record_id": f"user_ops_send_{record_id:04d}",
            "task_type": "user_ops_batch_send",
            "created_at": created_at,
            "updated_at": created_at,
            "idempotency_key": str(payload.get("idempotency_key") or ""),
            "execution_backend": execution_backend,
            "external_effect_job_ids": list(payload.get("external_effect_job_ids") or []),
            "external_effect_status_summary": dict(payload.get("external_effect_status_summary") or {}),
            "external_effect_status_supported": execution_backend == "external_effect_queue",
            "wecom_delivery_status_supported": False,
            "delivery_status_supported": False,
            "planned_count": int(payload.get("planned_count") or 0),
            "queued_count": int(payload.get("queued_count") or 0),
            "dispatching_count": int(payload.get("dispatching_count") or 0),
            "succeeded_count": int(payload.get("succeeded_count") or 0),
            "failed_count": int(payload.get("failed_count") or 0),
            "blocked_count": int(payload.get("blocked_count") or 0),
            "cancelled_count": int(payload.get("cancelled_count") or 0),
            "last_refreshed_at": str(payload.get("last_refreshed_at") or ""),
            **payload,
        }
        self._send_records.insert(0, record)
        return deepcopy(record)

    def create_or_get_send_record_by_idempotency(self, *, idempotency_key: str, payload: JsonDict) -> JsonDict:
        key = str(idempotency_key or "").strip()
        if key:
            for record in self._send_records:
                if str(record.get("idempotency_key") or "") == key:
                    return deepcopy(record)
        enriched = dict(payload)
        enriched["idempotency_key"] = key
        return self.create_send_record(enriched)

    def attach_external_effect_jobs(self, record_id: str, jobs: list[JsonDict]) -> JsonDict:
        record = self._find_send_record(record_id)
        if record is None:
            return {}
        job_ids = [job_id for job in jobs if (job_id := _job_id_from_result(job)) is not None]
        summary = _external_effect_summary_from_jobs(jobs)
        now = _now_iso()
        record.update(
            {
                "external_effect_job_ids": job_ids,
                "external_effect_status_summary": summary,
                "task_results": [deepcopy(job) for job in jobs],
                "status": summary["status"],
                "status_label": _status_label(summary["status"]),
                "sent_count": int(summary.get("succeeded_count") or 0),
                "planned_count": int(summary.get("planned_count") or 0),
                "queued_count": int(summary.get("queued_count") or 0),
                "dispatching_count": int(summary.get("dispatching_count") or 0),
                "succeeded_count": int(summary.get("succeeded_count") or 0),
                "failed_count": int(summary.get("failed_count") or 0),
                "blocked_count": int(summary.get("blocked_count") or 0),
                "cancelled_count": int(summary.get("cancelled_count") or 0),
                "updated_at": now,
                "last_refreshed_at": now,
                "external_effect_status_supported": True,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
            }
        )
        return deepcopy(record)

    def refresh_send_record_external_effect_status(self, record_id: str, summary: JsonDict) -> JsonDict:
        record = self._find_send_record(record_id)
        if record is None:
            return {}
        now = _now_iso()
        status = str(summary.get("status") or record.get("status") or "planned")
        record.update(
            {
                "status": status,
                "status_label": _status_label(status),
                "external_effect_status_summary": dict(summary),
                "task_results": list(summary.get("task_results") or record.get("task_results") or []),
                "sent_count": int(summary.get("succeeded_count") or 0),
                "planned_count": int(summary.get("planned_count") or 0),
                "queued_count": int(summary.get("queued_count") or 0),
                "dispatching_count": int(summary.get("dispatching_count") or 0),
                "succeeded_count": int(summary.get("succeeded_count") or 0),
                "failed_count": int(summary.get("failed_count") or 0),
                "blocked_count": int(summary.get("blocked_count") or 0),
                "cancelled_count": int(summary.get("cancelled_count") or 0),
                "updated_at": now,
                "last_refreshed_at": now,
                "external_effect_status_supported": True,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
            }
        )
        if summary.get("external_effect_job_ids"):
            record["external_effect_job_ids"] = list(summary.get("external_effect_job_ids") or [])
        return deepcopy(record)

    def get_send_record_external_effect_job_ids(self, record_id: str) -> list[int]:
        record = self.get_send_record(record_id)
        if not record:
            return []
        job_ids: list[int] = []
        for item in list(record.get("external_effect_job_ids") or []):
            try:
                job_ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return job_ids

    def list_send_records(self) -> list[JsonDict]:
        if not self._send_records:
            return [
                {
                    "id": 0,
                    "record_id": "fixture_record_001",
                    "task_type": "user_ops_batch_send",
                    "idempotency_key": "",
                    "execution_backend": "legacy_fake",
                    "selected_count": 1,
                    "eligible_count": 1,
                    "sent_count": 1,
                    "planned_count": 0,
                    "queued_count": 0,
                    "dispatching_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "blocked_count": 0,
                    "cancelled_count": 0,
                    "target_unionids": ["union_ops_001"],
                    "skipped_count": 0,
                    "skipped_reasons": {},
                    "external_effect_job_ids": [],
                    "external_effect_status_summary": {},
                    "external_effect_status_supported": False,
                    "wecom_delivery_status_supported": False,
                    "delivery_status_supported": False,
                    "include_do_not_disturb": False,
                    "content_preview": "欢迎继续了解黄小璨课程",
                    "image_count": 0,
                    "sender_userids": ["ZhaoYanFang"],
                    "filter_snapshot": {},
                    "operator": "fixture-admin",
                    "status": "created",
                    "status_label": "已创建任务",
                    "created_at": "2026-05-18T10:30:00+08:00",
                    "last_refreshed_at": "",
                    "task_results": [],
                }
            ]
        return [deepcopy(record) for record in self._send_records]

    def get_send_record(self, record_id: str) -> JsonDict | None:
        for record in self.list_send_records():
            if str(record.get("record_id")) == record_id or str(record.get("id")) == record_id:
                return record
        return None

    def _find_send_record(self, record_id: str) -> JsonDict | None:
        for record in self._send_records:
            if str(record.get("record_id")) == str(record_id) or str(record.get("id")) == str(record_id):
                return record
        return None


class SqlAlchemyUserOpsRepository:
    """PostgreSQL-ready User Ops repository backed by SQLAlchemy Core tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def close(self) -> None:
        try:
            self._session.rollback()
        finally:
            self._session.close()

    def reset(self) -> None:
        self._session.execute(delete(user_ops_send_records_next))
        self._session.execute(delete(user_ops_do_not_disturb_next))
        self._session.execute(delete(user_ops_pool_current_next))
        self.seed_pool_rows(_default_pool_rows())
        self._session.commit()

    def seed_pool_rows(self, rows: list[JsonDict]) -> None:
        for row in rows:
            created_at = _coerce_datetime(row.get("created_at"))
            updated_at = _coerce_datetime(row.get("updated_at"))
            self._session.execute(
                insert(user_ops_pool_current_next).values(
                    id=row["id"],
                    unionid=row.get("unionid") or "",
                    customer_name_snapshot=row.get("customer_name") or row.get("customer_name_snapshot") or "",
                    owner_userid=row.get("owner_userid") or "",
                    owner_display_name=row.get("owner_display_name") or "",
                    class_term_no=row.get("class_term_no") or "",
                    class_term_label=row.get("class_term_label") or "",
                    source_type=row.get("source_type") or "lead_pool",
                    activation_bucket=row.get("activation_bucket") or "pending_input",
                    activation_bucket_label=ACTIVATION_LABELS.get(
                        str(row.get("activation_bucket") or "pending_input"),
                        str(row.get("activation_bucket") or "pending_input"),
                    ),
                    auto_do_not_disturb_reasons_json=list(row.get("auto_dnd_reasons") or []),
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )

    def list_rows(self) -> list[JsonDict]:
        rows = self._session.execute(
            select(user_ops_pool_current_next).order_by(user_ops_pool_current_next.c.id.asc())
        ).mappings()
        return [_project_row(self._row_to_dict(row)) for row in rows]

    list_pool = list_rows

    def find_row(self, *, unionid: str = "") -> JsonDict | None:
        stmt = select(user_ops_pool_current_next)
        if not unionid:
            return None
        stmt = stmt.where(user_ops_pool_current_next.c.unionid == unionid)
        row = self._session.execute(stmt.limit(1)).mappings().first()
        return self._row_to_dict(row) if row else None

    def set_do_not_disturb(
        self,
        *,
        unionid: str = "",
        reason_code: str,
        reason_text: str,
        is_active: bool,
        operator: str = "fixture-admin",
    ) -> JsonDict | None:
        target = self.find_row(unionid=unionid)
        if target is None:
            return None

        now = _now()
        match = self._dnd_match_stmt(target, reason_code=reason_code)
        existing = self._session.execute(match.limit(1)).mappings().first()
        values = {
            "unionid": target.get("unionid") or "",
            "source_type": "manual",
            "reason_code": reason_code,
            "reason_text": reason_text,
            "is_active": bool(is_active),
            "created_by": operator,
            "updated_at": now,
        }
        if existing:
            self._session.execute(
                update(user_ops_do_not_disturb_next)
                .where(user_ops_do_not_disturb_next.c.id == existing["id"])
                .values(**values)
            )
        else:
            self._session.execute(
                insert(user_ops_do_not_disturb_next).values(
                    **values,
                    created_at=now,
                )
            )
        self._session.execute(
            update(user_ops_pool_current_next)
            .where(user_ops_pool_current_next.c.id == target["id"])
            .values(updated_at=now)
        )
        self._session.commit()
        return _project_row(self._row_to_dict(self._fetch_pool_row_by_id(int(target["id"]))))

    def create_send_record(self, payload: JsonDict) -> JsonDict:
        record_key = str(payload.get("record_key") or payload.get("record_id") or _new_record_key())
        self._insert_send_record(record_key=record_key, payload=payload)
        self._session.commit()
        return self.get_send_record(record_key) or {}

    def create_or_get_send_record_by_idempotency(self, *, idempotency_key: str, payload: JsonDict) -> JsonDict:
        key = str(idempotency_key or "").strip()
        if key:
            existing = self._get_send_record_by_idempotency(key)
            if existing is not None:
                return existing
        enriched = dict(payload)
        enriched["idempotency_key"] = key
        record_key = str(enriched.get("record_key") or enriched.get("record_id") or _new_record_key())
        try:
            self._insert_send_record(record_key=record_key, payload=enriched)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if key:
                existing = self._get_send_record_by_idempotency(key)
                if existing is not None:
                    return existing
            if not self._repair_send_record_sequence_after_primary_key_conflict(exc):
                raise
            try:
                self._insert_send_record(record_key=record_key, payload=enriched)
                self._session.commit()
            except IntegrityError:
                self._session.rollback()
                if key:
                    existing = self._get_send_record_by_idempotency(key)
                    if existing is not None:
                        return existing
                raise
        return self.get_send_record(record_key) or {}

    def _repair_send_record_sequence_after_primary_key_conflict(self, exc: IntegrityError) -> bool:
        bind = self._session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return False
        if _integrity_constraint_name(exc) != "user_ops_send_records_next_pkey":
            return False
        self._session.execute(text("LOCK TABLE user_ops_send_records_next IN SHARE ROW EXCLUSIVE MODE"))
        self._session.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('user_ops_send_records_next', 'id')::regclass,
                    GREATEST(
                        (SELECT MAX(id) FROM user_ops_send_records_next),
                        (SELECT last_value FROM user_ops_send_records_next_id_seq)
                    ),
                    TRUE
                )
                """
            )
        )
        return True

    def attach_external_effect_jobs(self, record_id: str, jobs: list[JsonDict]) -> JsonDict:
        record = self.get_send_record(record_id)
        if record is None:
            return {}
        job_ids = [job_id for job in jobs if (job_id := _job_id_from_result(job)) is not None]
        summary = _external_effect_summary_from_jobs(jobs)
        now = _now()
        self._session.execute(
            update(user_ops_send_records_next)
            .where(user_ops_send_records_next.c.record_key == record["record_id"])
            .values(
                external_effect_job_ids_json=job_ids,
                external_effect_status_summary_json=summary,
                task_results_json=[dict(job) for job in jobs],
                sent_count=int(summary.get("succeeded_count") or 0),
                planned_count=int(summary.get("planned_count") or 0),
                queued_count=int(summary.get("queued_count") or 0),
                dispatching_count=int(summary.get("dispatching_count") or 0),
                succeeded_count=int(summary.get("succeeded_count") or 0),
                failed_count=int(summary.get("failed_count") or 0),
                blocked_count=int(summary.get("blocked_count") or 0),
                cancelled_count=int(summary.get("cancelled_count") or 0),
                status=str(summary.get("status") or "planned"),
                status_label=_status_label(str(summary.get("status") or "planned")),
                last_status_sync_at=now,
                last_refreshed_at=now,
            )
        )
        self._session.commit()
        return self.get_send_record(record["record_id"]) or {}

    def refresh_send_record_external_effect_status(self, record_id: str, summary: JsonDict) -> JsonDict:
        record = self.get_send_record(record_id)
        if record is None:
            return {}
        now = _now()
        status = str(summary.get("status") or record.get("status") or "planned")
        values = {
            "external_effect_status_summary_json": dict(summary),
            "task_results_json": list(summary.get("task_results") or record.get("task_results") or []),
            "sent_count": int(summary.get("succeeded_count") or 0),
            "planned_count": int(summary.get("planned_count") or 0),
            "queued_count": int(summary.get("queued_count") or 0),
            "dispatching_count": int(summary.get("dispatching_count") or 0),
            "succeeded_count": int(summary.get("succeeded_count") or 0),
            "failed_count": int(summary.get("failed_count") or 0),
            "blocked_count": int(summary.get("blocked_count") or 0),
            "cancelled_count": int(summary.get("cancelled_count") or 0),
            "status": status,
            "status_label": _status_label(status),
            "last_status_sync_at": now,
            "last_refreshed_at": now,
        }
        if summary.get("external_effect_job_ids"):
            values["external_effect_job_ids_json"] = list(summary.get("external_effect_job_ids") or [])
        self._session.execute(
            update(user_ops_send_records_next)
            .where(user_ops_send_records_next.c.record_key == record["record_id"])
            .values(**values)
        )
        self._session.commit()
        return self.get_send_record(record["record_id"]) or {}

    def get_send_record_external_effect_job_ids(self, record_id: str) -> list[int]:
        record = self.get_send_record(record_id)
        if not record:
            return []
        job_ids: list[int] = []
        for item in list(record.get("external_effect_job_ids") or []):
            try:
                job_ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return job_ids

    def _insert_send_record(self, *, record_key: str, payload: JsonDict) -> None:
        now = _now()
        task_results = list(payload.get("task_results") or [])
        outbound_task_ids = [result.get("task_id") for result in task_results if isinstance(result, dict) and result.get("task_id")]
        status = str(payload.get("status") or "created")
        self._session.execute(
            insert(user_ops_send_records_next).values(
                record_key=record_key,
                idempotency_key=str(payload.get("idempotency_key") or "") or None,
                task_type="user_ops_batch_send",
                execution_backend=str(payload.get("execution_backend") or "legacy_fake"),
                target_source=str(payload.get("target_source") or "").strip(),
                target_source_id=payload.get("target_source_id"),
                outbound_task_ids_json=outbound_task_ids,
                task_results_json=task_results,
                external_effect_job_ids_json=list(payload.get("external_effect_job_ids") or []),
                external_effect_status_summary_json=dict(payload.get("external_effect_status_summary") or {}),
                selected_count=int(payload.get("selected_count") or 0),
                eligible_count=int(payload.get("eligible_count") or 0),
                sent_count=int(payload.get("sent_count") or 0),
                skipped_count=int(payload.get("skipped_count") or 0),
                planned_count=int(payload.get("planned_count") or 0),
                queued_count=int(payload.get("queued_count") or 0),
                dispatching_count=int(payload.get("dispatching_count") or 0),
                succeeded_count=int(payload.get("succeeded_count") or 0),
                failed_count=int(payload.get("failed_count") or 0),
                blocked_count=int(payload.get("blocked_count") or 0),
                cancelled_count=int(payload.get("cancelled_count") or 0),
                skipped_reasons_json=dict(payload.get("skipped_reasons") or payload.get("skipped_by_reason") or {}),
                include_do_not_disturb=bool(payload.get("include_do_not_disturb")),
                target_unionids_json=list(payload.get("target_unionids") or []),
                content_preview=str(payload.get("content_preview") or ""),
                image_count=int(payload.get("image_count") or 0),
                sender_userids_json=list(payload.get("sender_userids") or []),
                filter_snapshot_json=dict(payload.get("filter_snapshot") or {}),
                operator=str(payload.get("operator") or "fixture-admin"),
                status=status,
                status_label=str(payload.get("status_label") or _status_label(status)),
                last_refreshed_at=payload.get("last_refreshed_at"),
                created_at=now,
            )
        )

    def _get_send_record_by_idempotency(self, idempotency_key: str) -> JsonDict | None:
        row = self._session.execute(
            select(user_ops_send_records_next)
            .where(user_ops_send_records_next.c.idempotency_key == idempotency_key)
            .limit(1)
        ).mappings().first()
        return self._record_to_dict(row) if row else None

    def list_send_records(self) -> list[JsonDict]:
        rows = self._session.execute(
            select(user_ops_send_records_next).order_by(user_ops_send_records_next.c.created_at.desc())
        ).mappings()
        return [self._record_to_dict(row) for row in rows]

    def get_send_record(self, record_id: str) -> JsonDict | None:
        stmt = select(user_ops_send_records_next).where(user_ops_send_records_next.c.record_key == record_id)
        if str(record_id).isdigit():
            stmt = select(user_ops_send_records_next).where(user_ops_send_records_next.c.id == int(record_id))
        row = self._session.execute(stmt.limit(1)).mappings().first()
        return self._record_to_dict(row) if row else None

    def _dnd_match_stmt(self, target: JsonDict, *, reason_code: str):
        stmt = select(user_ops_do_not_disturb_next).where(
            user_ops_do_not_disturb_next.c.source_type == "manual",
            user_ops_do_not_disturb_next.c.reason_code == reason_code,
        )
        stmt = stmt.where(user_ops_do_not_disturb_next.c.unionid == target["unionid"])
        return stmt

    def _fetch_pool_row_by_id(self, row_id: int):
        return self._session.execute(
            select(user_ops_pool_current_next).where(user_ops_pool_current_next.c.id == row_id)
        ).mappings().one()

    def _row_to_dict(self, row) -> JsonDict:
        data = dict(row)
        data["created_at"] = _iso(data.get("created_at"))
        data["updated_at"] = _iso(data.get("updated_at"))
        data["auto_dnd_reasons"] = list(data.pop("auto_do_not_disturb_reasons_json") or [])
        data["manual_dnd_reasons"] = self._manual_reasons_for_row(data)
        data["unionid"] = data.get("unionid") or ""
        data["customer_name"] = data.get("customer_name") or data.get("customer_name_snapshot") or ""
        data["mobile"] = data.get("mobile") or ""
        data["external_userid"] = data.get("external_userid") or ""
        data["owner_userid"] = data.get("owner_userid") or ""
        data["class_term_no"] = data.get("class_term_no") or ""
        return data

    def _manual_reasons_for_row(self, row: JsonDict) -> list[JsonDict]:
        stmt = select(user_ops_do_not_disturb_next).where(
            user_ops_do_not_disturb_next.c.source_type == "manual",
            user_ops_do_not_disturb_next.c.is_active.is_(True),
        )
        unionid = str(row.get("unionid") or "")
        if not unionid:
            return []
        stmt = stmt.where(user_ops_do_not_disturb_next.c.unionid == unionid)
        reasons = []
        for item in self._session.execute(stmt.order_by(user_ops_do_not_disturb_next.c.id.asc())).mappings():
            reasons.append(_manual_reason(str(item["reason_code"]), str(item["reason_text"])))
        return reasons

    def _record_to_dict(self, row) -> JsonDict:
        data = dict(row)
        execution_backend = str(data.get("execution_backend") or "legacy_fake")
        return {
            "id": data["id"],
            "record_id": data["record_key"],
            "task_type": data["task_type"],
            "idempotency_key": data.get("idempotency_key") or "",
            "execution_backend": execution_backend,
            "target_source": data.get("target_source") or "",
            "target_source_id": data.get("target_source_id"),
            "selected_count": data["selected_count"],
            "eligible_count": data["eligible_count"],
            "sent_count": data["sent_count"],
            "planned_count": int(data.get("planned_count") or 0),
            "queued_count": int(data.get("queued_count") or 0),
            "dispatching_count": int(data.get("dispatching_count") or 0),
            "succeeded_count": int(data.get("succeeded_count") or 0),
            "failed_count": int(data.get("failed_count") or 0),
            "blocked_count": int(data.get("blocked_count") or 0),
            "cancelled_count": int(data.get("cancelled_count") or 0),
            "target_unionids": data.get("target_unionids_json") or [],
            "skipped_count": data["skipped_count"],
            "skipped_reasons": data.get("skipped_reasons_json") or {},
            "external_effect_job_ids": data.get("external_effect_job_ids_json") or [],
            "external_effect_status_summary": data.get("external_effect_status_summary_json") or {},
            "external_effect_status_supported": execution_backend == "external_effect_queue",
            "wecom_delivery_status_supported": False,
            "delivery_status_supported": False,
            "include_do_not_disturb": data["include_do_not_disturb"],
            "content_preview": data["content_preview"],
            "image_count": data["image_count"],
            "sender_userids": data.get("sender_userids_json") or [],
            "filter_snapshot": data.get("filter_snapshot_json") or {},
            "operator": data["operator"],
            "status": data["status"],
            "status_label": data["status_label"],
            "created_at": _iso(data["created_at"]),
            "last_refreshed_at": _iso(data.get("last_refreshed_at")),
            "task_results": data.get("task_results_json") or [],
        }


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value))
    return _now()


def build_user_ops_repository(
    settings: Settings | None = None,
    *,
    session: Session | None = None,
    engine: Engine | None = None,
) -> UserOpsRepository:
    settings = settings or get_settings()
    backend = resolve_user_ops_repo_backend(settings)
    if backend in {"sql", "sqlalchemy", "postgres", "postgresql"}:
        if session is not None:
            return assert_repository_allowed(SqlAlchemyUserOpsRepository(session), capability_owner="ops_enrollment")
        owned_session = get_session_factory(settings=settings)() if engine is None else Session(bind=engine, future=True)
        return assert_repository_allowed(SqlAlchemyUserOpsRepository(owned_session), capability_owner="ops_enrollment")
    return assert_repository_allowed(InMemoryUserOpsRepository(), capability_owner="ops_enrollment")


FixtureUserOpsRepository = InMemoryUserOpsRepository


def resolve_user_ops_repo_backend(settings: Settings | None = None) -> str:
    configured = startup_environment_setting("USER_OPS_REPO_BACKEND").strip().lower()
    if configured:
        return configured
    settings = settings or get_settings()
    if database_mode() == "postgres":
        return "sqlalchemy"
    return settings.user_ops_repo_backend.strip().lower()
