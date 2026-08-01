from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform.shared.runtime_settings import runtime_bool, runtime_setting
from aicrm_next.platform.shared.typing import JsonDict


USER_OPS_BATCH_SEND_ROUTE = "/api/admin/user-ops/batch-send/execute"
USER_OPS_BATCH_SEND_BUSINESS_TYPE = "user_ops_batch_send"


def _text(value: object) -> str:
    return str(value or "").strip()


def _hash_payload(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def user_ops_send_disabled() -> bool:
    return runtime_bool("AICRM_USER_OPS_SEND_DISABLE_EXECUTE", False)


def user_ops_send_requires_approval() -> bool:
    return runtime_bool("AICRM_USER_OPS_SEND_REQUIRES_APPROVAL", True)


def user_ops_send_risk_level() -> str:
    return runtime_setting("AICRM_USER_OPS_SEND_RISK_LEVEL", "high") or "high"


def user_ops_send_execution_mode() -> str:
    return runtime_setting("AICRM_USER_OPS_SEND_EXECUTION_MODE", "execute") or "execute"


@dataclass(frozen=True)
class UserOpsSendEffectJobInput:
    record_id: str
    command_id: str
    idempotency_key: str
    operator: str
    source_route: str
    content: str
    media_refs: list[JsonDict] = field(default_factory=list)
    target: JsonDict = field(default_factory=dict)
    requires_approval: bool = True
    execution_mode: str = "execute"
    risk_level: str = "high"


@dataclass(frozen=True)
class UserOpsSendEffectJobResult:
    ok: bool
    job_id: int = 0
    status: str = ""
    idempotency_key: str = ""
    target_unionid: str = ""
    external_userid: str = ""
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "job_id": self.job_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "target_unionid": self.target_unionid,
            "external_userid": self.external_userid,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class UserOpsExternalEffectEnqueueGateway:
    def __init__(self, effect_service: ExternalEffectService | None = None) -> None:
        self._effect_service = effect_service or ExternalEffectService()

    def enqueue_wecom_private_message_jobs(
        self,
        *,
        record_id: str,
        targets: list[JsonDict],
        content: str,
        media_refs: list[JsonDict] | None = None,
        operator: str,
        source_route: str = USER_OPS_BATCH_SEND_ROUTE,
        idempotency_key: str = "",
        command_id: str = "",
        requires_approval: bool | None = None,
        execution_mode: str | None = None,
        risk_level: str | None = None,
    ) -> list[JsonDict]:
        approval_required = user_ops_send_requires_approval() if requires_approval is None else bool(requires_approval)
        mode = _text(execution_mode) or user_ops_send_execution_mode()
        risk = _text(risk_level) or user_ops_send_risk_level()
        return [
            self.enqueue_wecom_private_message_job(
                UserOpsSendEffectJobInput(
                    record_id=record_id,
                    command_id=command_id,
                    idempotency_key=idempotency_key,
                    operator=operator,
                    source_route=source_route,
                    content=content,
                    media_refs=list(media_refs or []),
                    target=target,
                    requires_approval=approval_required,
                    execution_mode=mode,
                    risk_level=risk,
                )
            ).to_dict()
            for target in targets
        ]

    def enqueue_wecom_private_message_job(self, request: UserOpsSendEffectJobInput) -> UserOpsSendEffectJobResult:
        target_unionid = _text(request.target.get("unionid"))
        external_userid = _text(request.target.get("external_userid"))
        owner_userid = _text(request.target.get("owner_userid"))
        target_display_name = _text(
            request.target.get("customer_name")
            or request.target.get("customer_name_snapshot")
            or request.target.get("nickname")
        )
        if not target_unionid:
            return UserOpsSendEffectJobResult(
                ok=False,
                target_unionid=target_unionid,
                external_userid=external_userid,
                error_code="missing_unionid",
                error_message="target unionid is required",
            )
        if not external_userid:
            return UserOpsSendEffectJobResult(
                ok=False,
                target_unionid=target_unionid,
                external_userid=external_userid,
                error_code="missing_external_userid",
                error_message="target external_userid is required",
            )
        if not owner_userid:
            return UserOpsSendEffectJobResult(
                ok=False,
                target_unionid=target_unionid,
                external_userid=external_userid,
                error_code="missing_owner_userid",
                error_message="target owner_userid is required",
            )
        base_key = _text(request.idempotency_key) or _text(request.command_id) or f"{request.record_id}:{_hash_payload(request.content)}"
        job_idempotency_key = f"user_ops_batch_send:{base_key}:{target_unionid}"
        payload = {
            "channel": "wecom_private",
            "source": USER_OPS_BATCH_SEND_BUSINESS_TYPE,
            "record_id": request.record_id,
            "target_unionid": target_unionid,
            "target_display_name": target_display_name,
            "external_userids": [external_userid],
            "owner_userid": owner_userid,
            "content_text": _text(request.content),
            "attachments": [],
            "media_refs": list(request.media_refs or []),
        }
        summary = {
            "source": USER_OPS_BATCH_SEND_BUSINESS_TYPE,
            "record_id": request.record_id,
            "target_unionid": target_unionid,
            "external_userid_count": 1,
            "owner_userid": owner_userid,
            "content_hash": _hash_payload(_text(request.content)),
            "content_text_length": len(_text(request.content)),
            "media_ref_count": len(request.media_refs or []),
            "media_hash": _hash_payload(request.media_refs or []),
            "real_external_call_executed": False,
        }
        job = self._effect_service.plan_effect(
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send_private_message",
            target_type="user_ops_customer",
            target_id=target_unionid,
            payload=payload,
            payload_summary=summary,
            context=CommandContext(
                actor_id=_text(request.operator) or "fixture-admin",
                actor_type="admin",
                source_route=_text(request.source_route) or USER_OPS_BATCH_SEND_ROUTE,
            ),
            business_type=USER_OPS_BATCH_SEND_BUSINESS_TYPE,
            business_id=request.record_id,
            source_module="ops_enrollment",
            source_command_id=request.command_id,
            risk_level=request.risk_level,
            requires_approval=request.requires_approval,
            execution_mode=request.execution_mode,
            idempotency_key=job_idempotency_key,
        )
        return UserOpsSendEffectJobResult(
            ok=True,
            job_id=int(job.get("id") or 0),
            status=_text(job.get("status")),
            idempotency_key=job_idempotency_key,
            target_unionid=target_unionid,
            external_userid=external_userid,
        )


def build_user_ops_external_effect_gateway() -> UserOpsExternalEffectEnqueueGateway:
    return UserOpsExternalEffectEnqueueGateway()
