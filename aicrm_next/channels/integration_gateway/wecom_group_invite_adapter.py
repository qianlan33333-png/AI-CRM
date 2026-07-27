from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.shared.wecom_runtime import classify_wecom_provider_error

from .audit import record_audit_event
from .wecom_channel_entry_client import (
    WeComAdapterBlocked,
    WeComApiError,
    build_default_wecom_channel_entry_adapter,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(operation: str, value: str) -> str:
    digest = hashlib.sha256(f"{operation}:{value}".encode("utf-8")).hexdigest()[:24]
    return f"group-invite:{digest}"


class WeComGroupInviteAdapter:
    """Audited gateway for WeCom customer-group join-way APIs."""

    adapter_name = "WeComGroupInviteAdapter"

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client or build_default_wecom_channel_entry_adapter()

    def create_join_way(self, payload: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
        chat_ids = [_text(value) for value in list(payload.get("chat_id_list") or []) if _text(value)]
        key = _text(idempotency_key) or _key("create", "|".join(chat_ids))
        try:
            result = self._client.create_group_join_way(payload)
        except WeComAdapterBlocked as exc:
            return self._failure(
                operation="create_join_way",
                idempotency_key=key,
                error_code=exc.reason,
                error_message=str(exc),
                real_external_call_executed=False,
                missing_config=exc.missing_config,
            )
        except WeComApiError as exc:
            return self._failure(
                operation="create_join_way",
                idempotency_key=key,
                error_code=exc.error_code or "wecom_group_join_way_error",
                error_message=exc.message,
                real_external_call_executed=bool(exc.real_external_call_executed),
                provider_errcode=int(exc.provider_errcode or 0),
                retryable=exc.classification == "retryable",
            )
        config_id = _text((result or {}).get("config_id"))
        if not config_id:
            return self._failure(
                operation="create_join_way",
                idempotency_key=key,
                error_code="wecom_group_join_way_missing_config_id",
                error_message="WeCom did not return config_id",
                real_external_call_executed=True,
            )
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation="create_join_way",
            mode="production",
            idempotency_key=key,
            side_effect_executed=True,
            status="ok",
        )
        return {
            "ok": True,
            "operation": "create_join_way",
            "config_id": config_id,
            "audit_id": audit["audit_id"],
            "side_effect_executed": True,
            "real_external_call_executed": True,
        }

    def get_join_way(self, config_id: str, *, idempotency_key: str = "") -> dict[str, Any]:
        normalized_config_id = _text(config_id)
        key = _text(idempotency_key) or _key("get", normalized_config_id)
        try:
            result = self._client.get_group_join_way(normalized_config_id)
        except WeComAdapterBlocked as exc:
            return self._failure(
                operation="get_join_way",
                idempotency_key=key,
                error_code=exc.reason,
                error_message=str(exc),
                real_external_call_executed=False,
                missing_config=exc.missing_config,
            )
        except WeComApiError as exc:
            return self._failure(
                operation="get_join_way",
                idempotency_key=key,
                error_code=exc.error_code or "wecom_group_join_way_error",
                error_message=exc.message,
                real_external_call_executed=bool(exc.real_external_call_executed),
                provider_errcode=int(exc.provider_errcode or 0),
                retryable=exc.classification == "retryable",
            )
        join_way = (result or {}).get("join_way")
        if not isinstance(join_way, dict):
            join_way = {}
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation="get_join_way",
            mode="production",
            idempotency_key=key,
            side_effect_executed=False,
            status="ok",
        )
        return {
            "ok": True,
            "operation": "get_join_way",
            "config_id": normalized_config_id,
            "join_way": join_way,
            "audit_id": audit["audit_id"],
            "side_effect_executed": False,
            "real_external_call_executed": True,
        }

    def _failure(
        self,
        *,
        operation: str,
        idempotency_key: str,
        error_code: str,
        error_message: str,
        real_external_call_executed: bool,
        provider_errcode: int = 0,
        retryable: bool = False,
        missing_config: list[str] | None = None,
    ) -> dict[str, Any]:
        classification = ""
        if provider_errcode:
            _, classification = classify_wecom_provider_error(provider_errcode=provider_errcode)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode="production" if real_external_call_executed else "disabled",
            idempotency_key=idempotency_key,
            side_effect_executed=real_external_call_executed and operation == "create_join_way",
            status="failed" if real_external_call_executed else "blocked",
            error_code=error_code,
        )
        return {
            "ok": False,
            "operation": operation,
            "audit_id": audit["audit_id"],
            "side_effect_executed": real_external_call_executed and operation == "create_join_way",
            "real_external_call_executed": real_external_call_executed,
            "error_code": error_code,
            "error_message": error_message,
            "provider_errcode": provider_errcode,
            "provider_error_classification": classification,
            "retryable": retryable,
            "missing_config": list(missing_config or []),
        }


def build_wecom_group_invite_adapter() -> WeComGroupInviteAdapter:
    return WeComGroupInviteAdapter()
