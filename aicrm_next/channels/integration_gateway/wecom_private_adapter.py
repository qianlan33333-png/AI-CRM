from __future__ import annotations

import hashlib
from typing import Any, Callable

from aicrm_next.platform_foundation.external_effects.execution_gates import explicit_wecom_execution_disabled
from aicrm_next.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting
from aicrm_next.shared.wecom_payload_contract import normalize_miniprogram_attachment_payload
from aicrm_next.shared.wecom_runtime import classify_wecom_provider_error

from .audit import record_audit_event
from .wecom_customer_group_client import WeComCustomerGroupClient, WeComCustomerGroupClientError


Json = dict[str, Any]
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE",
        "AICRM_ENABLE_REAL_WECOM_PRIVATE_MESSAGE",
        "AICRM_WECOM_GROUP_ADAPTER_MODE",
        "AICRM_WECOM_PRIVATE_ADAPTER_MODE",
    }
)


def _mode() -> str:
    if explicit_wecom_execution_disabled():
        return "disabled"
    value = (
        managed_runtime_setting("AICRM_WECOM_PRIVATE_ADAPTER_MODE")
        or managed_runtime_setting("AICRM_WECOM_GROUP_ADAPTER_MODE")
    ).strip().lower()
    return value if value in {"disabled", "fake", "staging", "production"} else "disabled"


def _enabled(name: str) -> bool:
    return managed_runtime_bool(name)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(repr(sorted((payload or {}).items())).encode("utf-8")).hexdigest()[:24]


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload or {})
    safe.pop("token", None)
    safe.pop("access_token", None)
    return safe


def _targets(value: Any) -> list[str]:
    return [str(item or "").strip() for item in list(value or []) if str(item or "").strip()]


def _provider_errcode(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_wecom_attachment(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("attachments entries must be objects")
    msgtype = str(item.get("msgtype") or "").strip().lower()
    if msgtype != "miniprogram":
        return dict(item)
    payload = item.get("miniprogram")
    if not isinstance(payload, dict):
        raise ValueError("miniprogram attachments must include miniprogram")
    return {"msgtype": "miniprogram", "miniprogram": normalize_miniprogram_attachment_payload(payload)}


class WeComPrivateMessageAdapter:
    adapter_name = "WeComPrivateMessageAdapter"

    def __init__(self, *, mode: str | None = None, client_factory: Callable[[], Any] | None = None) -> None:
        self.mode = (mode or _mode()).strip().lower()
        if self.mode not in {"disabled", "fake", "staging", "production"}:
            self.mode = "disabled"
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return WeComCustomerGroupClient()

    def create_private_message_task(self, payload: dict[str, Any], *, idempotency_key: str = "") -> Json:
        try:
            normalized = self._build_wecom_payload(payload)
        except ValueError as exc:
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target={},
                result={},
                side_effect_executed=False,
                error_code="validation_failed",
                error_message=str(exc),
            )
        requested_external_userids = _targets(normalized.get("external_userid"))
        target = {
            "sender": normalized.get("sender", ""),
            "requested_external_userids": requested_external_userids,
            "requested_external_userid_count": len(requested_external_userids),
            "exact_target_required": True,
            "official_external_userid_field": "external_userid",
            "payload_hash": _hash_payload(_safe_payload(normalized)),
        }
        if self.mode in {"disabled", "staging"}:
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result={},
                side_effect_executed=False,
                error_code="before_external_call",
                error_message="real WeCom private message creation is disabled",
            )
        if self.mode == "fake":
            return self._result(
                ok=True,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result={"msgid": f"fake_private_msg_{target['payload_hash']}"},
                side_effect_executed=False,
                error_code="",
                error_message="",
                extra={
                    "exact_target_verified": True,
                    "exact_target_verification_source": "fake_adapter_requested_external_userids",
                    "requested_external_userids": requested_external_userids,
                    "wecom_msgid": f"fake_private_msg_{target['payload_hash']}",
                },
            )
        if not (_enabled("AICRM_ENABLE_REAL_WECOM_PRIVATE_MESSAGE") or _enabled("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE")):
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result={},
                side_effect_executed=False,
                error_code="before_external_call",
                error_message="AICRM_ENABLE_REAL_WECOM_PRIVATE_MESSAGE is not enabled",
            )
        try:
            result = self._client().create_group_message_task(normalized)
        except WeComCustomerGroupClientError as exc:
            provider_errcode = _provider_errcode((exc.payload or {}).get("errcode"))
            derived_error_code, classification = classify_wecom_provider_error(
                provider_errcode=provider_errcode,
                transport_error=exc.error_code == "wecom_group_client_http_error",
            )
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result=exc.payload if exc.payload else {},
                side_effect_executed=True,
                error_code=(
                    derived_error_code
                    if provider_errcode or exc.error_code == "wecom_group_client_http_error"
                    else exc.error_code or "wecom_private_message_client_error"
                ),
                error_message=str(exc),
                extra={
                    "provider_errcode": provider_errcode,
                    "provider_error_classification": classification,
                    "retryable": classification == "retryable",
                },
            )
        errcode = int(result.get("errcode") or 0) if isinstance(result, dict) else -1
        msgid = str((result or {}).get("msgid") or "").strip() if isinstance(result, dict) else ""
        fail_list = _targets((result or {}).get("fail_list")) if isinstance(result, dict) else []
        if errcode != 0:
            error_code, classification = classify_wecom_provider_error(provider_errcode=errcode)
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result=result if isinstance(result, dict) else {},
                side_effect_executed=True,
                error_code=error_code,
                error_message=str((result or {}).get("errmsg") or "WeCom private message API failed"),
                extra={
                    "provider_errcode": errcode,
                    "provider_error_classification": classification,
                    "retryable": classification == "retryable",
                },
            )
        if not msgid:
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result=result if isinstance(result, dict) else {},
                side_effect_executed=True,
                error_code="external_call_unknown",
                error_message="WeCom did not return msgid for private message task",
            )
        if fail_list:
            return self._result(
                ok=False,
                operation="create_private_message_task",
                idempotency_key=idempotency_key,
                target=target,
                result=result,
                side_effect_executed=True,
                error_code="private_target_rejected",
                error_message=f"WeCom rejected {len(fail_list)} requested private targets",
                extra={
                    "failed_external_userids": fail_list,
                    "failed_external_userid_count": len(fail_list),
                    "provider_errcode": 0,
                    "provider_error_classification": "terminal",
                    "retryable": False,
                    "wecom_msgid": msgid,
                },
            )
        return self._result(
            ok=True,
            operation="create_private_message_task",
            idempotency_key=idempotency_key,
            target=target,
            result=result,
            side_effect_executed=True,
            error_code="",
            error_message="",
            extra={
                "exact_target_verified": True,
                "exact_target_verification_source": "wecom_add_msg_template.external_userid",
                "requested_external_userids": requested_external_userids,
                "wecom_msgid": msgid,
            },
        )

    def _result(
        self,
        *,
        ok: bool,
        operation: str,
        idempotency_key: str,
        target: dict[str, Any],
        result: dict[str, Any],
        side_effect_executed: bool,
        error_code: str,
        error_message: str,
        extra: dict[str, Any] | None = None,
    ) -> Json:
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=idempotency_key or _hash_payload(target),
            side_effect_executed=side_effect_executed,
            status="ok" if ok else "blocked",
            error_code=error_code,
        )
        return {
            "ok": bool(ok),
            "adapter": self.adapter_name,
            "mode": self.mode,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "target": target,
            "result": result,
            "audit_id": audit["audit_id"],
            "side_effect_executed": bool(side_effect_executed),
            "error_code": error_code,
            "error_message": error_message,
            **dict(extra or {}),
        }

    def _build_wecom_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sender = str((payload or {}).get("sender") or "").strip()
        if not sender:
            raise ValueError("sender is required for WeCom private message")
        external_userids = _targets((payload or {}).get("external_userids"))
        if not external_userids:
            raise ValueError("external_userids is required for WeCom private message")
        result: dict[str, Any] = {
            "chat_type": "single",
            "sender": sender,
            "external_userid": external_userids,
            "allow_select": False,
        }
        text = (payload or {}).get("text")
        if isinstance(text, dict) and str(text.get("content") or "").strip():
            result["text"] = {"content": str(text.get("content") or "").strip()}
        attachments = (payload or {}).get("attachments")
        if isinstance(attachments, list) and attachments:
            result["attachments"] = [_normalize_wecom_attachment(item) for item in attachments]
        if not result.get("text") and not result.get("attachments"):
            raise ValueError("text or attachments is required for WeCom private message")
        return result


def build_wecom_private_message_adapter() -> WeComPrivateMessageAdapter:
    return WeComPrivateMessageAdapter()
