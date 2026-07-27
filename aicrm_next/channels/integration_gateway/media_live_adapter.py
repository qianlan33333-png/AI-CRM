from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.platform.shared.runtime_settings import runtime_bool, runtime_setting

from .media_live_gateway import MediaUploadLiveGateway, build_media_upload_live_gateway


Json = dict[str, Any]
FLAG_ENABLED = "AICRM_MEDIA_UPLOAD_LIVE_ADAPTER_ENABLED"
FLAG_APPROVED = "AICRM_MEDIA_UPLOAD_LIVE_UPLOAD_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_MEDIA_UPLOAD_CONFIG_REVIEWED"
FLAG_PROVIDER_NAME = "AICRM_MEDIA_UPLOAD_PROVIDER_NAME"
FLAG_PROVIDER_SECRET = "AICRM_MEDIA_UPLOAD_PROVIDER_SECRET"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_MEDIA_UPLOAD_CONFIG_REVIEWED",
        "AICRM_MEDIA_UPLOAD_LIVE_ADAPTER_ENABLED",
        "AICRM_MEDIA_UPLOAD_LIVE_UPLOAD_APPROVED",
        "AICRM_MEDIA_UPLOAD_PROVIDER_NAME",
        "AICRM_MEDIA_UPLOAD_PROVIDER_SECRET",
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


def _hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _side_effect_safety(*, live_provider_upload_executed: bool = False, live_provider_lookup_executed: bool = False) -> Json:
    return {
        "live_provider_upload_executed": live_provider_upload_executed,
        "live_provider_lookup_executed": live_provider_lookup_executed,
        "network_call_executed": live_provider_upload_executed or live_provider_lookup_executed,
        "token_used": False,
        "provider_secret_used": False,
        "public_media_url_published": False,
        "raw_file_exposed": False,
        "destructive_delete_executed": False,
        "db_write_executed": False,
        "route_owner_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "outbound_send_executed": False,
        "payment_executed": False,
        "oauth_callback_executed": False,
        "wecom_live_call_executed": False,
        "openclaw_mcp_executed": False,
        "timer_execution_executed": False,
        "automation_execution_executed": False,
    }


def _base_response(**safety: bool) -> Json:
    side_effect_safety = _side_effect_safety(**safety)
    return {
        "adapter_mode": "media_live_adapter_behind_explicit_flag",
        **side_effect_safety,
        "production_success_claimed": False,
        "side_effect_safety": side_effect_safety,
        "timestamp": _timestamp(),
    }


class MediaUploadLiveAdapter:
    def __init__(self, *, gateway: MediaUploadLiveGateway | None = None, confirm_live_media_upload: bool = False) -> None:
        self._gateway = gateway or build_media_upload_live_gateway()
        self._confirm_live_media_upload = confirm_live_media_upload
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def reset_idempotency(self) -> None:
        self._idempotency.clear()

    def upload_media_live(self, *, data_base64: str, file_name: str, content_type: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        payload = {
            "operation": "upload_media_live",
            "data_present": bool(data_base64),
            "file_name": file_name,
            "content_type": content_type,
            "operator": operator,
        }
        request_hash = _hash(payload)
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {**_base_response(), "ok": False, "result_status": "conflict", "error_code": "duplicate_idempotency_key", "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay
        gate = self._gate_status()
        if not gate["ok"]:
            response = self._blocked(gate, idempotency_key=normalized_key, request_hash=request_hash)
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response
        result = self._gateway.upload_media_live(data_base64=data_base64, file_name=file_name, content_type=content_type)
        response = self._gateway_response(result=result, operation="upload_media_live", idempotency_key=normalized_key, request_hash=request_hash)
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def lookup_media_live(self, *, provider_reference: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        request_hash = _hash({"operation": "lookup_media_live", "provider_reference_present": bool(provider_reference), "operator": operator})
        gate = self._gate_status()
        if not gate["ok"]:
            return self._blocked(gate, idempotency_key=normalized_key, request_hash=request_hash)
        result = self._gateway.lookup_media_live(provider_reference=provider_reference)
        return self._gateway_response(result=result, operation="lookup_media_live", idempotency_key=normalized_key, request_hash=request_hash)

    def _gate_status(self) -> Json:
        if not _enabled(FLAG_ENABLED):
            return {"ok": False, "error_code": "live_adapter_not_enabled", "missing_gate": FLAG_ENABLED}
        if not _enabled(FLAG_APPROVED):
            return {"ok": False, "error_code": "live_upload_not_approved", "missing_gate": FLAG_APPROVED}
        if not _enabled(FLAG_CONFIG_REVIEWED):
            return {"ok": False, "error_code": "media_config_missing", "missing_gate": FLAG_CONFIG_REVIEWED}
        if not _present(FLAG_PROVIDER_NAME) or not _present(FLAG_PROVIDER_SECRET):
            return {"ok": False, "error_code": "media_config_missing", "missing_gate": "provider_secret_material"}
        if not self._confirm_live_media_upload:
            return {"ok": False, "error_code": "live_upload_not_approved", "missing_gate": "--confirm-live-media-upload"}
        return {"ok": True}

    def _blocked(self, gate: Json, **extra: Any) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": gate.get("error_code") or "live_upload_not_enabled",
            "missing_gate": gate.get("missing_gate") or "",
            "config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "approval_present": _enabled(FLAG_APPROVED),
            **extra,
        }

    def _invalid(self, error_code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": error_code}

    def _gateway_response(self, *, result: Json, operation: str, **extra: Any) -> Json:
        ok = bool(result.get("ok"))
        live_upload = ok and operation == "upload_media_live"
        live_lookup = ok and operation == "lookup_media_live"
        return {
            **_base_response(live_provider_upload_executed=live_upload, live_provider_lookup_executed=live_lookup),
            "ok": ok,
            "result_status": "media_live_operation_completed" if ok else str(result.get("result_status") or "blocked"),
            "error_code": "" if ok else str(result.get("error_code") or "media_live_upload_failed"),
            "operation": operation,
            **extra,
        }


def build_media_upload_live_adapter(*, confirm_live_media_upload: bool = False) -> MediaUploadLiveAdapter:
    return MediaUploadLiveAdapter(confirm_live_media_upload=confirm_live_media_upload)
