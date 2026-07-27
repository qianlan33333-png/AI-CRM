from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting

from .audit import record_audit_event
from .idempotency import get_or_create, make_idempotency_key
from .user_ops_contracts import AdapterMode, Json


VALID_MODES = {"fake", "disabled", "staging", "production"}
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_BATCH_SEND",
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_DEFERRED_JOBS",
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_DND",
        "AICRM_NEXT_ENABLE_REAL_WECOM_DISPATCH",
        "AICRM_NEXT_USER_OPS_BATCH_SEND_MODE",
        "AICRM_NEXT_USER_OPS_DEFERRED_JOBS_MODE",
        "AICRM_NEXT_USER_OPS_DND_MODE",
        "AICRM_NEXT_WECOM_DISPATCH_MODE",
    }
)


def _normalise_mode(value: str | None, *, default: AdapterMode = "fake") -> AdapterMode:
    mode = (value or default).strip().lower()
    if mode not in VALID_MODES:
        return default
    return mode  # type: ignore[return-value]


def _env_true(name: str) -> bool:
    return managed_runtime_bool(name)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_fingerprint(content: str) -> dict[str, Any]:
    normalized = str(content or "")
    return {
        "content_hash": _digest(normalized)[:24],
        "content_preview": normalized[:80],
        "content_length": len(normalized),
    }


def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"secret", "token", "access_token", "client_secret", "app_secret", "credential", "password"}

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key.lower() not in forbidden}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(target)


def _mode_prefix(mode: AdapterMode) -> str:
    return "staging" if mode == "staging" else "fake"


def _base_result(
    *,
    ok: bool,
    adapter: str,
    mode: AdapterMode,
    operation: str,
    idempotency_key: str,
    target: dict[str, Any],
    result: dict[str, Any] | None,
    audit_id: str,
    error_code: str = "",
    error_message: str = "",
) -> Json:
    return {
        "ok": ok,
        "adapter": adapter,
        "mode": mode,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "target": _safe_target(target),
        "result": result or {},
        "audit_id": audit_id,
        "side_effect_executed": False,
        "error_code": error_code,
        "error_message": error_message,
    }


class _GuardedUserOpsAdapter:
    adapter_name = "UserOpsAdapter"
    production_flag = ""

    def __init__(self, mode: AdapterMode | str = "fake") -> None:
        self.mode = _normalise_mode(str(mode), default="fake")

    def _guarded_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any]) -> Json | None:
        if self.mode == "disabled":
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code="adapter_disabled",
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code="adapter_disabled",
                error_message=f"{self.adapter_name} is disabled",
            )
        if self.mode == "production":
            error_code = "production_guard_failed" if not _env_true(self.production_flag) else "production_not_implemented"
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code=error_code,
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code=error_code,
                error_message=f"{self.adapter_name} production mode is not implemented in D7.3",
            )
        return None

    def _successful_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any], factory) -> Json:
        cached = get_or_create(idempotency_key, factory)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=idempotency_key,
            side_effect_executed=False,
            status="ok",
        )
        return _base_result(
            ok=True,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=idempotency_key,
            target=target,
            result=cached,
            audit_id=audit["audit_id"],
        )

    def _audit_only(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"target": _safe_target(target), "result": result or {}})
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="blocked" if error_code else "ok",
            error_code=error_code,
        )
        return _base_result(
            ok=not error_code,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=target,
            result=result or {},
            audit_id=audit["audit_id"],
            error_code=error_code,
            error_message="" if not error_code else "audit recorded as blocked",
        )


class UserOpsDndWriteGateway(_GuardedUserOpsAdapter):
    adapter_name = "UserOpsDndWriteGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_USER_OPS_DND"

    def enable_do_not_disturb(
        self,
        *,
        unionid: str = "",
        external_userid: str = "",
        mobile: str = "",
        owner_userid: str = "",
        record_id: str = "",
        reason_code: str = "",
        reason_text: str = "",
        operator: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        return self._dnd_operation(
            "enable_do_not_disturb",
            is_active=True,
            unionid=unionid,
            external_userid=external_userid,
            mobile=mobile,
            owner_userid=owner_userid,
            record_id=record_id,
            reason_code=reason_code,
            reason_text=reason_text,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def cancel_do_not_disturb(
        self,
        *,
        unionid: str = "",
        external_userid: str = "",
        mobile: str = "",
        owner_userid: str = "",
        record_id: str = "",
        reason_code: str = "",
        reason_text: str = "",
        operator: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        return self._dnd_operation(
            "cancel_do_not_disturb",
            is_active=False,
            unionid=unionid,
            external_userid=external_userid,
            mobile=mobile,
            owner_userid=owner_userid,
            record_id=record_id,
            reason_code=reason_code,
            reason_text=reason_text,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def build_dnd_preview(
        self,
        *,
        action: str,
        external_userid: str = "",
        mobile: str = "",
        owner_userid: str = "",
        record_id: str = "",
        reason_code: str = "",
        reason_text: str = "",
        operator: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "build_dnd_preview"
        target = self._target(
            external_userid=external_userid,
            mobile=mobile,
            owner_userid=owner_userid,
            record_id=record_id,
            reason_code=reason_code,
            reason_text=reason_text,
            operator=operator,
            action=action,
        )
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "preview_id": f"{mode_prefix}_dnd_preview_{_digest(key)[:16]}",
                "action": action,
                "source_status": f"{mode_prefix}_preview",
                "will_apply": False,
            },
        )

    def record_dnd_audit(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _dnd_operation(self, operation: str, *, is_active: bool, unionid: str, external_userid: str, mobile: str, owner_userid: str, record_id: str, reason_code: str, reason_text: str, operator: str, idempotency_key: str | None) -> Json:
        target = self._target(
            unionid=unionid,
            external_userid=external_userid,
            mobile=mobile,
            owner_userid=owner_userid,
            record_id=record_id,
            reason_code=reason_code,
            reason_text=reason_text,
            operator=operator,
            action="enable" if is_active else "cancel",
        )
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "dnd_operation_id": f"{mode_prefix}_dnd_{_digest(key)[:16]}",
                "do_not_disturb": is_active,
                "source_status": mode_prefix,
                "applied": False,
            },
        )

    @staticmethod
    def _target(*, unionid: str = "", external_userid: str, mobile: str, owner_userid: str, record_id: str, reason_code: str, reason_text: str, operator: str, action: str) -> dict[str, Any]:
        return {
            "unionid": unionid,
            "external_userid": external_userid,
            "mobile": mobile,
            "owner_userid": owner_userid,
            "record_id": record_id,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "operator": operator,
            "action": action,
        }


class UserOpsBatchSendGateway(_GuardedUserOpsAdapter):
    adapter_name = "UserOpsBatchSendGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_USER_OPS_BATCH_SEND"

    def build_batch_send_preview(
        self,
        *,
        batch_id: str = "",
        selection_mode: str = "",
        filters: dict[str, Any] | None = None,
        selected_ids: list[int] | None = None,
        excluded_ids: list[int] | None = None,
        content: str = "",
        targets: list[dict[str, Any]] | None = None,
        owner_buckets: list[dict[str, Any]] | None = None,
        include_do_not_disturb: bool = False,
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "build_batch_send_preview"
        target = {
            "batch_id": batch_id,
            "selection_mode": selection_mode,
            "filters": filters or {},
            "selected_ids": sorted(selected_ids or []),
            "excluded_ids": sorted(excluded_ids or []),
            "target_count": len(targets or []),
            "owner_bucket_count": len(owner_buckets or []),
            "include_do_not_disturb": include_do_not_disturb,
            "media_refs": media_refs or [],
            **_content_fingerprint(content),
        }
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "preview_id": f"{mode_prefix}_batch_preview_{_digest(key)[:16]}",
                "source_status": f"{mode_prefix}_preview",
                "eligible_count": len(targets or []),
                "owner_bucket_count": len(owner_buckets or []),
                "will_dispatch": False,
            },
        )

    def execute_batch_send(
        self,
        *,
        batch_id: str = "",
        record_id: str = "",
        content: str = "",
        targets: list[dict[str, Any]] | None = None,
        owner_buckets: list[dict[str, Any]] | None = None,
        operator: str = "",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "execute_batch_send"
        target = {
            "batch_id": batch_id,
            "record_id": record_id,
            "operator": operator,
            "target_count": len(targets or []),
            "owner_bucket_count": len(owner_buckets or []),
            "media_refs": media_refs or [],
            **_content_fingerprint(content),
        }
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        sent_count = sum(int(bucket.get("target_count") or len(bucket.get("external_userids") or [])) for bucket in owner_buckets or [])
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "batch_operation_id": f"{mode_prefix}_batch_execute_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "sent_count": sent_count,
                "task_count": len(owner_buckets or []),
                "dispatched": False,
            },
        )

    def create_send_record(
        self,
        *,
        batch_id: str = "",
        record_id: str = "",
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "create_send_record"
        payload = payload or {}
        target = {
            "batch_id": batch_id,
            "record_id": record_id,
            "selected_count": payload.get("selected_count", 0),
            "eligible_count": payload.get("eligible_count", 0),
            "sent_count": payload.get("sent_count", 0),
            "skipped_count": payload.get("skipped_count", 0),
            "operator": payload.get("operator", ""),
        }
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "record_ref": record_id or f"{mode_prefix}_send_record_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "persisted": False,
            },
        )

    def build_send_result_summary(
        self,
        *,
        batch_id: str = "",
        record_id: str = "",
        task_results: list[dict[str, Any]] | None = None,
        sent_count: int = 0,
        skipped_count: int = 0,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "build_send_result_summary"
        target = {
            "batch_id": batch_id,
            "record_id": record_id,
            "task_count": len(task_results or []),
            "sent_count": sent_count,
            "skipped_count": skipped_count,
        }
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "summary_id": f"{mode_prefix}_send_summary_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "task_count": len(task_results or []),
                "sent_count": sent_count,
                "skipped_count": skipped_count,
                "delivery_status_supported": False,
            },
        )


class WeComMessageDispatchAdapter(_GuardedUserOpsAdapter):
    adapter_name = "WeComMessageDispatchAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_WECOM_DISPATCH"

    def send_private_message(
        self,
        *,
        external_userid: str = "",
        external_userids: list[str] | None = None,
        owner_userid: str = "",
        mobile: str = "",
        record_id: str = "",
        batch_id: str = "",
        content: str = "",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        targets = sorted(external_userids or ([external_userid] if external_userid else []))
        target = self._target(
            send_channel="private",
            external_userid=external_userid,
            external_userids=targets,
            owner_userid=owner_userid,
            mobile=mobile,
            record_id=record_id,
            batch_id=batch_id,
            media_refs=media_refs or [],
            content=content,
        )
        return self._dispatch_operation("send_private_message", target=target, idempotency_key=idempotency_key)

    def send_group_message(
        self,
        *,
        group_chat_id: str = "",
        owner_userid: str = "",
        record_id: str = "",
        batch_id: str = "",
        content: str = "",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        target = self._target(send_channel="group", group_chat_id=group_chat_id, owner_userid=owner_userid, record_id=record_id, batch_id=batch_id, media_refs=media_refs or [], content=content)
        return self._dispatch_operation("send_group_message", target=target, idempotency_key=idempotency_key)

    def send_moment(
        self,
        *,
        owner_userid: str = "",
        record_id: str = "",
        batch_id: str = "",
        content: str = "",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        target = self._target(send_channel="moment", owner_userid=owner_userid, record_id=record_id, batch_id=batch_id, media_refs=media_refs or [], content=content)
        return self._dispatch_operation("send_moment", target=target, idempotency_key=idempotency_key)

    def build_dispatch_preview(
        self,
        *,
        send_channel: str,
        target: dict[str, Any],
        content: str = "",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "build_dispatch_preview"
        dispatch_target = {**target, "send_channel": send_channel, "media_refs": media_refs or [], **_content_fingerprint(content)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=dispatch_target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=dispatch_target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=dispatch_target,
            factory=lambda: {
                "preview_id": f"{mode_prefix}_dispatch_preview_{_digest(key)[:16]}",
                "source_status": f"{mode_prefix}_preview",
                "will_send": False,
            },
        )

    def resolve_dispatch_target(
        self,
        *,
        external_userid: str = "",
        mobile: str = "",
        owner_userid: str = "",
        record_id: str = "",
        batch_id: str = "",
        send_channel: str = "private",
        media_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "resolve_dispatch_target"
        target = {"external_userid": external_userid, "mobile": mobile, "owner_userid": owner_userid, "record_id": record_id, "batch_id": batch_id, "send_channel": send_channel, "media_refs": media_refs or []}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "resolved": bool(external_userid or mobile or owner_userid),
                "source_status": mode_prefix,
                "send_channel": send_channel,
            },
        )

    def record_dispatch_audit(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _dispatch_operation(self, operation: str, *, target: dict[str, Any], idempotency_key: str | None) -> Json:
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        channel = str(target.get("send_channel") or "private")
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "task_id": f"{mode_prefix}_wecom_{channel}_{_digest(key)[:16]}",
                "status": "created",
                "status_label": "已创建任务",
                "error_message": "",
                "dispatch_adapter": "fake_wecom",
                "source_status": mode_prefix,
                "sent": False,
                "target_count": len(target.get("external_userids") or ([] if not target.get("external_userid") else [target.get("external_userid")])),
            },
        )

    @staticmethod
    def _target(**kwargs: Any) -> dict[str, Any]:
        content = str(kwargs.pop("content", ""))
        return {**kwargs, **_content_fingerprint(content)}


class UserOpsDeferredJobGateway(_GuardedUserOpsAdapter):
    adapter_name = "UserOpsDeferredJobGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_USER_OPS_DEFERRED_JOBS"

    def enqueue_deferred_job(
        self,
        *,
        job_id: str = "",
        job_type: str = "",
        run_at: str = "",
        target: dict[str, Any] | None = None,
        payload_summary: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "enqueue_deferred_job"
        job_target = {"job_id": job_id, "job_type": job_type, "run_at": run_at, "target": target or {}, "payload_summary": payload_summary or {}}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=job_target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=job_target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=job_target,
            factory=lambda: {
                "job_ref": job_id or f"{mode_prefix}_deferred_job_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "queued": False,
            },
        )

    def run_due_jobs(
        self,
        *,
        now: str = "",
        limit: int = 100,
        job_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "run_due_jobs"
        target = {"now": now, "limit": limit, "job_ids": sorted(job_ids or [])}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "run_id": f"{mode_prefix}_deferred_run_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "executed_count": 0,
                "jobs_executed": [],
                "executed": False,
            },
        )

    def build_deferred_job_preview(
        self,
        *,
        job_id: str = "",
        job_type: str = "",
        run_at: str = "",
        target: dict[str, Any] | None = None,
        payload_summary: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Json:
        operation = "build_deferred_job_preview"
        job_target = {"job_id": job_id, "job_type": job_type, "run_at": run_at, "target": target or {}, "payload_summary": payload_summary or {}}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=job_target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=job_target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=job_target,
            factory=lambda: {
                "preview_id": f"{mode_prefix}_deferred_preview_{_digest(key)[:16]}",
                "source_status": f"{mode_prefix}_preview",
                "will_enqueue": False,
                "will_execute": False,
            },
        )

    def record_deferred_job_audit(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


def build_user_ops_dnd_gateway() -> UserOpsDndWriteGateway:
    return UserOpsDndWriteGateway(managed_runtime_setting("AICRM_NEXT_USER_OPS_DND_MODE", "fake"))


def build_user_ops_batch_send_gateway() -> UserOpsBatchSendGateway:
    return UserOpsBatchSendGateway(
        managed_runtime_setting("AICRM_NEXT_USER_OPS_BATCH_SEND_MODE", "fake")
    )


def build_wecom_message_dispatch_adapter() -> WeComMessageDispatchAdapter:
    return WeComMessageDispatchAdapter(
        managed_runtime_setting("AICRM_NEXT_WECOM_DISPATCH_MODE", "fake")
    )


def build_user_ops_deferred_job_gateway() -> UserOpsDeferredJobGateway:
    return UserOpsDeferredJobGateway(
        managed_runtime_setting("AICRM_NEXT_USER_OPS_DEFERRED_JOBS_MODE", "fake")
    )
