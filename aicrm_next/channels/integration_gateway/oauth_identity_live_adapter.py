from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.shared.runtime import production_environment
from aicrm_next.shared.runtime_settings import runtime_bool, runtime_setting

from .oauth_identity_adapter import FakeStubOAuthIdentityAdapter
from .oauth_identity_live_gateway import OAuthIdentityLiveGateway, build_oauth_identity_live_gateway


Json = dict[str, Any]
ADAPTER_MODE = "live_oauth_behind_explicit_flag"
FLAG_ENABLED = "AICRM_OAUTH_IDENTITY_LIVE_ADAPTER_ENABLED"
FLAG_APPROVED = "AICRM_OAUTH_IDENTITY_LIVE_CALLBACK_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_OAUTH_IDENTITY_CONFIG_REVIEWED"
FLAG_APP_ID = "AICRM_OAUTH_IDENTITY_APP_ID"
FLAG_APP_SECRET = "AICRM_OAUTH_IDENTITY_APP_SECRET"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_OAUTH_IDENTITY_APP_ID",
        "AICRM_OAUTH_IDENTITY_APP_SECRET",
        "AICRM_OAUTH_IDENTITY_CONFIG_REVIEWED",
        "AICRM_OAUTH_IDENTITY_LIVE_ADAPTER_ENABLED",
        "AICRM_OAUTH_IDENTITY_LIVE_CALLBACK_APPROVED",
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


def _side_effect_safety(*, live_oauth_call_executed: bool = False, code_exchange_executed: bool = False) -> Json:
    return {
        "live_oauth_call_executed": live_oauth_call_executed,
        "live_callback_processed": live_oauth_call_executed,
        "code_exchange_executed": code_exchange_executed,
        "network_call_executed": live_oauth_call_executed,
        "token_used": code_exchange_executed,
        "app_secret_used": code_exchange_executed,
        "production_session_write_executed": False,
        "production_identity_write_executed": False,
        "outbound_send_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "payment_executed": False,
        "media_upload_executed": False,
        "wecom_live_call_executed": False,
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


class LiveOAuthIdentityAdapter:
    def __init__(self, *, gateway: OAuthIdentityLiveGateway | None = None, confirm_live_oauth_callback: bool = False) -> None:
        self._gateway = gateway or build_oauth_identity_live_gateway()
        self._confirm_live_oauth_callback = confirm_live_oauth_callback
        self._fake_stub = FakeStubOAuthIdentityAdapter()
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def reset_idempotency(self) -> None:
        self._idempotency.clear()

    def build_authorize_url_live(self, *, slug: str, state: str, redirect_uri: str, scope: str = "snsapi_base") -> Json:
        payload = {"operation": "build_authorize_url_live", "slug": slug, "state": state, "redirect_uri": redirect_uri, "scope": scope}
        request_hash = _canonical_hash(payload)
        gate = self._gate_status()
        if not gate["ok"]:
            return self._blocked(gate, request_hash=request_hash)
        result = self._gateway.build_authorize_url_live(slug=slug, state=state, redirect_uri=redirect_uri, scope=scope)
        return self._gateway_response(result=result, operation="build_authorize_url_live", request_hash=request_hash)

    def exchange_code_live(self, *, code: str, state: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        parsed = self._fake_stub.parse_oauth_callback_contract(code=code, state=state, openid="openid_live_placeholder", unionid="")
        request_payload = {"operation": "exchange_code_live", "code_present": bool(code), "state": state, "operator": operator}
        request_hash = _canonical_hash(request_payload)
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {**_base_response(), "ok": False, "result_status": "conflict", "error_code": "duplicate_idempotency_key", "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay
        if not parsed.get("ok"):
            return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": parsed.get("error_code") or "oauth_code_missing"}
        gate = self._gate_status()
        if not gate["ok"]:
            response = self._blocked(gate, state=state, operator=operator, idempotency_key=normalized_key, request_hash=request_hash)
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response
        result = self._gateway.exchange_code_live(code=code, state=state)
        response = self._gateway_response(result=result, operation="exchange_code_live", state=state, operator=operator, idempotency_key=normalized_key, request_hash=request_hash)
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def _gate_status(self) -> Json:
        if not _enabled(FLAG_ENABLED):
            return {"ok": False, "error_code": "live_adapter_not_enabled", "missing_gate": FLAG_ENABLED}
        if not _enabled(FLAG_APPROVED):
            return {"ok": False, "error_code": "live_oauth_callback_not_approved", "missing_gate": FLAG_APPROVED}
        if not _enabled(FLAG_CONFIG_REVIEWED):
            return {"ok": False, "error_code": "oauth_config_missing", "missing_gate": FLAG_CONFIG_REVIEWED}
        if not _present(FLAG_APP_ID) or not _present(FLAG_APP_SECRET):
            return {"ok": False, "error_code": "oauth_config_missing", "missing_gate": "oauth_secret_material"}
        if production_environment() and not _enabled(FLAG_APPROVED):
            return {"ok": False, "error_code": "forbidden_in_production_without_approval", "missing_gate": FLAG_APPROVED}
        if not self._confirm_live_oauth_callback:
            return {"ok": False, "error_code": "live_oauth_callback_not_approved", "missing_gate": "--confirm-live-oauth-callback"}
        return {"ok": True}

    def _blocked(self, gate: Json, **extra: Any) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": gate.get("error_code") or "live_oauth_callback_not_enabled",
            "missing_gate": gate.get("missing_gate") or "",
            "config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "approval_present": _enabled(FLAG_APPROVED),
            **extra,
        }

    def _invalid(self, error_code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": error_code}

    def _gateway_response(self, *, result: Json, operation: str, **extra: Any) -> Json:
        ok = bool(result.get("ok"))
        return {
            **_base_response(live_oauth_call_executed=ok, code_exchange_executed=ok and operation == "exchange_code_live"),
            "ok": ok,
            "result_status": "live_oauth_callback_processed" if ok else str(result.get("result_status") or "blocked"),
            "error_code": "" if ok else str(result.get("error_code") or "oauth_live_call_failed"),
            "operation": operation,
            **extra,
        }


def build_live_oauth_identity_adapter(*, confirm_live_oauth_callback: bool = False) -> LiveOAuthIdentityAdapter:
    return LiveOAuthIdentityAdapter(confirm_live_oauth_callback=confirm_live_oauth_callback)
