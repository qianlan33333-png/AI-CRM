from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool

from .external_submit_live_gateway import QuestionnaireExternalSubmitLiveGateway, build_questionnaire_external_submit_live_gateway


Json = dict[str, Any]
REQUIRED_FLAGS = {
    "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_LIVE_ADAPTER_ENABLED": "live_adapter_not_enabled",
    "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_LIVE_CALL_APPROVED": "live_call_not_approved",
    "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_CONFIG_REVIEWED": "questionnaire_config_missing",
    "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_TARGET_POLICY_REVIEWED": "target_policy_not_reviewed",
    "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_NO_PRODUCTION_WRITE_CONFIRMED": "confirm_no_production_write_required",
}
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_LIVE_ADAPTER_ENABLED",
        "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_LIVE_CALL_APPROVED",
        "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_CONFIG_REVIEWED",
        "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_TARGET_POLICY_REVIEWED",
        "AICRM_QUESTIONNAIRE_EXTERNAL_SUBMIT_NO_PRODUCTION_WRITE_CONFIRMED",
    }
)


@dataclass(frozen=True)
class _IdempotencyRecord:
    request_hash: str
    response: Json


def _enabled(name: str) -> bool:
    return managed_runtime_bool(name, False)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(payload: Json) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _redact(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "<redacted>" if len(text) <= 8 else f"{text[:4]}...{text[-4:]}"


def _redact_payload(payload: Json | None) -> Json:
    source = payload if isinstance(payload, dict) else {}
    redacted: Json = {}
    for key, value in sorted(source.items()):
        lowered = str(key).lower()
        if any(token in lowered for token in ("openid", "unionid", "external_userid", "mobile", "token", "secret", "code")):
            redacted[str(key)] = {"redacted": _redact(str(value)), "value_hash": _hash({"value": str(value)})}
        elif isinstance(value, (str, int, float, bool)) or value is None:
            redacted[str(key)] = value
        else:
            redacted[str(key)] = "<redacted_nested>"
    return redacted


def _side_effect_safety(*, provider_call_executed: bool = False) -> Json:
    return {
        "provider_call_executed": provider_call_executed,
        "network_call_executed": provider_call_executed,
        "production_public_submit_write_executed": False,
        "production_identity_write_executed": False,
        "production_tag_write_executed": False,
        "live_oauth_callback_cutover_executed": False,
        "outbound_send_executed": False,
        "batch_tag_write_executed": False,
        "route_owner_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
    }


def _base(**safety: bool) -> Json:
    side_effect_safety = _side_effect_safety(**safety)
    return {
        "adapter_mode": "questionnaire_external_submit_live_adapter_behind_flag",
        **side_effect_safety,
        "side_effect_safety": side_effect_safety,
        "live_call_disabled_by_default": True,
        "redacted_evidence": True,
        "production_success_claimed": False,
        "timestamp": _timestamp(),
    }


class QuestionnaireExternalSubmitLiveAdapter:
    def __init__(self, *, gateway: QuestionnaireExternalSubmitLiveGateway | None = None, confirm_no_production_write: bool = False, confirm_no_outbound_send: bool = False) -> None:
        self._gateway = gateway or build_questionnaire_external_submit_live_gateway()
        self._confirm_no_production_write = confirm_no_production_write
        self._confirm_no_outbound_send = confirm_no_outbound_send
        self._idempotency: dict[str, _IdempotencyRecord] = {}

    def submit_public_live(self, *, slug: str, payload: Json | None, operator: str, idempotency_key: str) -> Json:
        if not str(slug or "").strip():
            return self._invalid("slug_missing")
        payload_redacted = _redact_payload(payload)
        return self._execute(operation="submit_public_live", idempotency_key=idempotency_key, payload={"slug": slug, "payload_redacted": payload_redacted, "operator": operator}, gateway_call=lambda: self._gateway.submit_public_live(slug=str(slug), payload_redacted=payload_redacted))

    def write_identity_mapping_live(self, *, identity: Json | None, operator: str, idempotency_key: str) -> Json:
        identity_redacted = _redact_payload(identity)
        return self._execute(operation="write_identity_mapping_live", idempotency_key=idempotency_key, payload={"identity_redacted": identity_redacted, "operator": operator}, gateway_call=lambda: self._gateway.write_identity_mapping_live(identity_redacted=identity_redacted))

    def write_tag_back_live(self, *, external_userid: str, tag_ids: list[str], operator: str, idempotency_key: str) -> Json:
        if not str(external_userid or "").strip():
            return self._invalid("external_userid_missing")
        normalized = sorted({str(tag).strip() for tag in tag_ids if str(tag).strip()})
        if not normalized:
            return self._invalid("tag_ids_missing")
        return self._execute(operation="write_tag_back_live", idempotency_key=idempotency_key, payload={"external_userid_redacted": _redact(external_userid), "tag_ids": normalized, "operator": operator}, gateway_call=lambda: self._gateway.write_tag_back_live(external_userid_redacted=_redact(external_userid), tag_ids=normalized))

    def _execute(self, *, operation: str, idempotency_key: str, payload: Json, gateway_call: Any) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        request_hash = _hash({"operation": operation, **payload})
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {**_base(), "ok": False, "result_status": "conflict", "error_code": "duplicate_idempotency_key", "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay
        gate = self._gate_status()
        if not gate["ok"]:
            response = {**_base(), "ok": False, "result_status": "blocked", "error_code": gate["error_code"], "missing_gate": gate["missing_gate"], "idempotency_key": normalized_key, "request_hash": request_hash, **payload}
            self._idempotency[normalized_key] = _IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response
        result = gateway_call()
        response = {**_base(provider_call_executed=bool(result.get("provider_call_executed"))), "ok": bool(result.get("ok")), "result_status": str(result.get("result_status") or "blocked"), "error_code": str(result.get("error_code") or "live_gateway_disabled"), "idempotency_key": normalized_key, "request_hash": request_hash, **payload}
        self._idempotency[normalized_key] = _IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def _gate_status(self) -> Json:
        for flag, error_code in REQUIRED_FLAGS.items():
            if not _enabled(flag):
                return {"ok": False, "error_code": error_code, "missing_gate": flag}
        if not self._confirm_no_production_write:
            return {"ok": False, "error_code": "confirm_no_production_write_required", "missing_gate": "confirm_no_production_write"}
        if not self._confirm_no_outbound_send:
            return {"ok": False, "error_code": "confirm_no_outbound_send_required", "missing_gate": "confirm_no_outbound_send"}
        return {"ok": True}

    def _invalid(self, error_code: str) -> Json:
        return {**_base(), "ok": False, "result_status": "invalid", "error_code": error_code}


def build_questionnaire_external_submit_live_adapter(*, confirm_no_production_write: bool = False, confirm_no_outbound_send: bool = False) -> QuestionnaireExternalSubmitLiveAdapter:
    return QuestionnaireExternalSubmitLiveAdapter(confirm_no_production_write=confirm_no_production_write, confirm_no_outbound_send=confirm_no_outbound_send)
