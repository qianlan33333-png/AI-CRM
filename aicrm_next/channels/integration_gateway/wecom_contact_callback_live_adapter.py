from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.platform.shared.runtime import production_environment
from aicrm_next.platform.shared.runtime_settings import runtime_bool, runtime_setting

from .wecom_contact_callback_adapter import FakeStubWeComContactCallbackAdapter
from .wecom_contact_callback_live_gateway import (
    WeComContactCallbackLiveGateway,
    build_wecom_contact_callback_live_gateway,
)


Json = dict[str, Any]
ADAPTER_MODE = "live_callback_behind_explicit_flag"

FLAG_LIVE_ENABLED = "AICRM_WECOM_CONTACT_CALLBACK_LIVE_ADAPTER_ENABLED"
FLAG_LIVE_APPROVED = "AICRM_WECOM_CONTACT_CALLBACK_LIVE_PROCESSING_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_WECOM_CONTACT_CALLBACK_CONFIG_REVIEWED"
FLAG_CORP_ID = "AICRM_WECOM_CONTACT_CALLBACK_CORP_ID"
FLAG_TOKEN = "AICRM_WECOM_CONTACT_CALLBACK_TOKEN"
FLAG_AES_KEY = "AICRM_WECOM_CONTACT_CALLBACK_AES_KEY"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_WECOM_CONTACT_CALLBACK_AES_KEY",
        "AICRM_WECOM_CONTACT_CALLBACK_CONFIG_REVIEWED",
        "AICRM_WECOM_CONTACT_CALLBACK_CORP_ID",
        "AICRM_WECOM_CONTACT_CALLBACK_LIVE_ADAPTER_ENABLED",
        "AICRM_WECOM_CONTACT_CALLBACK_LIVE_PROCESSING_APPROVED",
        "AICRM_WECOM_CONTACT_CALLBACK_TOKEN",
    }
)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _present(name: str) -> bool:
    return bool(runtime_setting(name))


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _side_effect_safety(*, live_callback_processed: bool = False, decrypt_executed: bool = False) -> Json:
    return {
        "live_callback_processed": live_callback_processed,
        "production_write_executed": False,
        "production_contact_write_executed": False,
        "production_identity_mapping_write_executed": False,
        "production_tag_write_executed": False,
        "outbound_send_executed": False,
        "customer_sync_executed": False,
        "token_used": decrypt_executed,
        "aes_key_used": decrypt_executed,
        "decrypt_executed": decrypt_executed,
        "network_call_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "oauth_callback_executed": False,
        "payment_executed": False,
        "media_upload_executed": False,
        "openclaw_mcp_executed": False,
        "timer_execution_executed": False,
        "automation_execution_executed": False,
    }


def _base_response(**safety: bool) -> Json:
    side_effect_safety = _side_effect_safety(**safety)
    return {
        "adapter_mode": ADAPTER_MODE,
        **side_effect_safety,
        "production_success_claimed": False,
        "side_effect_safety": side_effect_safety,
        "timestamp": _timestamp(),
    }


class LiveWeComContactCallbackAdapter:
    def __init__(
        self,
        *,
        gateway: WeComContactCallbackLiveGateway | None = None,
        confirm_live_wecom_callback: bool = False,
    ) -> None:
        self._gateway = gateway or build_wecom_contact_callback_live_gateway()
        self._confirm_live_wecom_callback = confirm_live_wecom_callback
        self._fake_stub = FakeStubWeComContactCallbackAdapter()
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def reset_idempotency(self) -> None:
        self._idempotency.clear()

    def verify_callback_live(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        payload = {"operation": "verify_callback_live", "signature_present": bool(signature), "timestamp": timestamp, "nonce": nonce, "echostr_present": bool(echostr)}
        request_hash = _canonical_hash(payload)
        gate = self._gate_status(require_idempotency=False)
        if not gate["ok"]:
            return self._blocked(gate, request_hash=request_hash)
        result = self._gateway.verify_callback_live(signature=signature, timestamp=timestamp, nonce=nonce, echostr=echostr)
        return self._gateway_response(result=result, operation="verify_callback_live", request_hash=request_hash)

    def process_external_contact_callback_live(self, *, payload: Json, operator: str, idempotency_key: str) -> Json:
        parsed = self._fake_stub.parse_external_contact_event(payload)
        if not parsed.get("ok"):
            return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": parsed.get("error_code") or "event_type_unsupported"}
        return self.record_contact_event_live(event=payload, operator=operator, idempotency_key=idempotency_key)

    def record_contact_event_live(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._write_like_live_operation(
            operation="record_contact_event_live",
            event=event,
            operator=operator,
            idempotency_key=idempotency_key,
            gateway_method="process_external_contact_event_live",
        )

    def record_identity_mapping_live(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._write_like_live_operation(
            operation="record_identity_mapping_live",
            event=event,
            operator=operator,
            idempotency_key=idempotency_key,
            gateway_method="record_identity_mapping_live",
        )

    def _write_like_live_operation(self, *, operation: str, event: Json, operator: str, idempotency_key: str, gateway_method: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        normalized_operator = str(operator or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        normalized = self._fake_stub.normalize_external_contact_event(event)
        if not normalized.get("ok"):
            return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": normalized.get("error_code")}
        normalized_event = dict(normalized["event"])
        request_payload = {
            "operation": operation,
            "event_key": normalized_event["event_key"],
            "external_userid_redacted": normalized_event["external_userid_redacted"],
            "follow_user_userid": normalized_event["follow_user_userid"],
            "operator": normalized_operator,
        }
        request_hash = _canonical_hash(request_payload)
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {
                **_base_response(),
                "ok": False,
                "result_status": "conflict",
                "error_code": "duplicate_idempotency_key",
                "idempotency_key": normalized_key,
                "request_hash": request_hash,
                "event_key": normalized_event["event_key"],
            }
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay

        gate = self._gate_status(require_idempotency=True)
        if not gate["ok"]:
            response = self._blocked(
                gate,
                event_key=normalized_event["event_key"],
                external_userid_redacted=normalized_event["external_userid_redacted"],
                follow_user_userid=normalized_event["follow_user_userid"],
                operator=normalized_operator,
                idempotency_key=normalized_key,
                request_hash=request_hash,
            )
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response

        gateway = getattr(self._gateway, gateway_method)
        result = gateway(event=normalized_event, operator=normalized_operator)
        response = self._gateway_response(
            result=result,
            operation=operation,
            event_key=normalized_event["event_key"],
            external_userid_redacted=normalized_event["external_userid_redacted"],
            follow_user_userid=normalized_event["follow_user_userid"],
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_hash=request_hash,
        )
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def _gate_status(self, *, require_idempotency: bool) -> Json:
        if not _enabled(FLAG_LIVE_ENABLED):
            return {"ok": False, "error_code": "live_adapter_not_enabled", "missing_gate": FLAG_LIVE_ENABLED}
        if not _enabled(FLAG_LIVE_APPROVED):
            return {"ok": False, "error_code": "live_callback_not_approved", "missing_gate": FLAG_LIVE_APPROVED}
        if not _enabled(FLAG_CONFIG_REVIEWED):
            return {"ok": False, "error_code": "callback_config_missing", "missing_gate": FLAG_CONFIG_REVIEWED}
        if not _present(FLAG_CORP_ID) or not _present(FLAG_TOKEN) or not _present(FLAG_AES_KEY):
            return {"ok": False, "error_code": "callback_config_missing", "missing_gate": "callback_secret_material"}
        if production_environment() and not _enabled(FLAG_LIVE_APPROVED):
            return {"ok": False, "error_code": "forbidden_in_production_without_approval", "missing_gate": FLAG_LIVE_APPROVED}
        if not self._confirm_live_wecom_callback:
            return {"ok": False, "error_code": "live_callback_not_approved", "missing_gate": "--confirm-live-wecom-callback"}
        return {"ok": True, "require_idempotency": require_idempotency}

    def _blocked(self, gate: Json, **extra: Any) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": gate.get("error_code") or "live_callback_not_enabled",
            "missing_gate": gate.get("missing_gate") or "",
            "config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "approval_present": _enabled(FLAG_LIVE_APPROVED),
            **extra,
        }

    def _invalid(self, error_code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": error_code}

    def _gateway_response(self, *, result: Json, operation: str, **extra: Any) -> Json:
        ok = bool(result.get("ok"))
        return {
            **_base_response(live_callback_processed=ok, decrypt_executed=ok),
            "ok": ok,
            "result_status": "live_callback_processed" if ok else str(result.get("result_status") or "blocked"),
            "error_code": "" if ok else str(result.get("error_code") or "wecom_live_callback_failed"),
            "operation": operation,
            **extra,
        }


def build_live_wecom_contact_callback_adapter(*, confirm_live_wecom_callback: bool = False) -> LiveWeComContactCallbackAdapter:
    return LiveWeComContactCallbackAdapter(confirm_live_wecom_callback=confirm_live_wecom_callback)
