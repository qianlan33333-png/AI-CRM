from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


Json = dict[str, Any]
ADAPTER_MODE = "fake_stub"
DETERMINISTIC_OAUTH_EVENTS: list[Json] = [
    {
        "oauth_event_type": "wechat_oauth_callback",
        "slug": "questionnaire-demo-001",
        "state": "questionnaire_demo_001",
        "code": "fake_code_001",
        "openid": "openid_phase5o_0001",
        "unionid": "unionid_phase5o_0001",
        "redirect_uri": "https://example.invalid/api/h5/wechat/oauth/callback",
        "oauth_event_key": "phase5o:wechat_oauth:questionnaire_demo_001:openid_phase5o_0001",
    },
    {
        "oauth_event_type": "wechat_oauth_callback",
        "slug": "questionnaire-demo-002",
        "state": "questionnaire_demo_002",
        "code": "fake_code_002",
        "openid": "openid_phase5o_0002",
        "unionid": "unionid_phase5o_0002",
        "redirect_uri": "https://example.invalid/api/h5/wechat/oauth/callback",
        "oauth_event_key": "phase5o:wechat_oauth:questionnaire_demo_002:openid_phase5o_0002",
    },
]


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= 8:
        return "<redacted>"
    return f"{normalized[:6]}...{normalized[-4:]}"


def _side_effect_safety() -> Json:
    return {
        "live_oauth_call_executed": False,
        "live_callback_processed": False,
        "code_exchange_executed": False,
        "network_call_executed": False,
        "token_used": False,
        "app_secret_used": False,
        "production_session_write_executed": False,
        "production_identity_write_executed": False,
        "outbound_send_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
    }


def _base_response() -> Json:
    return {
        "adapter_mode": ADAPTER_MODE,
        "live_oauth_call_executed": False,
        "live_callback_processed": False,
        "code_exchange_executed": False,
        "network_call_executed": False,
        "token_used": False,
        "app_secret_used": False,
        "app_id_used": False,
        "production_session_write_executed": False,
        "production_identity_write_executed": False,
        "outbound_send_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "production_success_claimed": False,
        "side_effect_safety": _side_effect_safety(),
        "timestamp": _timestamp(),
    }


class FakeStubOAuthIdentityAdapter:
    def __init__(self, events: list[Json] | None = None) -> None:
        self._events = deepcopy(events or DETERMINISTIC_OAUTH_EVENTS)
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._event_keys: dict[str, IdempotencyRecord] = {}

    def reset_state(self) -> None:
        self._idempotency.clear()
        self._event_keys.clear()

    def deterministic_oauth_events(self) -> Json:
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_oauth_events_listed",
            "events": [self._redacted_event(event) for event in self._events],
        }

    def build_oauth_authorize_url_contract(self, *, slug: str, state: str, redirect_uri: str, scope: str = "snsapi_base") -> Json:
        payload = {
            "slug": str(slug or "").strip(),
            "state": str(state or "").strip(),
            "redirect_uri": str(redirect_uri or "").strip(),
            "scope": str(scope or "snsapi_base").strip(),
        }
        missing = [key for key in ("state", "redirect_uri") if not payload[key]]
        request_hash = _canonical_hash(payload)
        if missing:
            return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": "state_missing", "missing_fields": missing, "request_hash": request_hash}
        if not payload["redirect_uri"].startswith(("https://", "http://")):
            return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": "redirect_uri_invalid", "request_hash": request_hash}
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_authorize_url_contract",
            "authorize_url_evidence": {
                "provider": "wechat_mp",
                "state": payload["state"],
                "redirect_uri": payload["redirect_uri"],
                "scope": payload["scope"],
                "slug": payload["slug"],
            },
            "request_hash": request_hash,
        }

    def parse_oauth_callback_contract(self, *, code: str, state: str, openid: str = "", unionid: str = "") -> Json:
        event = {
            "oauth_event_type": "wechat_oauth_callback",
            "code": str(code or "").strip(),
            "state": str(state or "").strip(),
            "openid": str(openid or "").strip(),
            "unionid": str(unionid or "").strip(),
        }
        if not event["state"]:
            return self._error("state_missing")
        if not event["code"]:
            return self._error("oauth_code_missing")
        if not event["openid"]:
            return self._error("openid_missing")
        event["oauth_event_key"] = self._event_key(event)
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_oauth_callback_parsed",
            "event": self._redacted_event(event),
            "request_hash": _canonical_hash(event),
        }

    def normalize_oauth_identity_event(self, event: Json) -> Json:
        normalized = self._coerce_event(event)
        if not normalized["state"]:
            return self._error("state_missing")
        if not normalized["openid"]:
            return self._error("openid_missing")
        if normalized["oauth_event_type"] != "wechat_oauth_callback":
            return self._error("state_invalid")
        if not normalized["oauth_event_key"]:
            normalized["oauth_event_key"] = self._event_key(normalized)
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_oauth_identity_event_normalized",
            "event": self._redacted_event(normalized),
            "request_hash": _canonical_hash(normalized),
        }

    def dry_run_record_oauth_identity(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._dry_run_write_like(operation="dry_run_record_oauth_identity", event=event, operator=operator, idempotency_key=idempotency_key)

    def dry_run_session_identity_evidence(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._dry_run_write_like(operation="dry_run_session_identity_evidence", event=event, operator=operator, idempotency_key=idempotency_key)

    def live_oauth_callback_attempt(self) -> Json:
        return {**_base_response(), "ok": False, "result_status": "blocked", "error_code": "live_oauth_callback_not_enabled"}

    def _dry_run_write_like(self, *, operation: str, event: Json, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        normalized_operator = str(operator or "").strip()
        if not normalized_key:
            return self._error("idempotency_key_required")
        normalized_result = self.normalize_oauth_identity_event(event)
        if not normalized_result.get("ok"):
            return normalized_result
        normalized_event = dict(normalized_result["event"])
        event_key = str(normalized_event["oauth_event_key"])
        request_payload = {
            "operation": operation,
            "oauth_event_key": event_key,
            "openid_redacted": normalized_event["openid_redacted"],
            "unionid_redacted": normalized_event["unionid_redacted"],
            "state": normalized_event["state"],
            "operator": normalized_operator,
        }
        request_hash = _canonical_hash(request_payload)
        existing_key = self._idempotency.get(normalized_key)
        if existing_key and existing_key.request_hash != request_hash:
            return {**_base_response(), "ok": False, "result_status": "conflict", "error_code": "duplicate_oauth_event_key", "oauth_event_key": event_key, "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing_key:
            replay = deepcopy(existing_key.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            return replay
        existing_event = self._event_keys.get(event_key)
        if existing_event:
            replay = deepcopy(existing_event.response)
            replay["result_status"] = "duplicate_event_replay"
            replay["event_key_replay"] = True
            replay["idempotency_key"] = normalized_key
            return replay
        response = {
            **_base_response(),
            "ok": True,
            "result_status": "dry_run_recorded",
            "operation": operation,
            "oauth_event_key": event_key,
            "oauth_event_type": normalized_event["oauth_event_type"],
            "openid_redacted": normalized_event["openid_redacted"],
            "unionid_redacted": normalized_event["unionid_redacted"],
            "state": normalized_event["state"],
            "operator": normalized_operator,
            "idempotency_key": normalized_key,
            "request_hash": request_hash,
        }
        record = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        self._idempotency[normalized_key] = record
        self._event_keys[event_key] = record
        return response

    def _coerce_event(self, event: Json) -> Json:
        raw = dict(event or {})
        return {
            "oauth_event_type": str(raw.get("oauth_event_type") or "wechat_oauth_callback").strip(),
            "slug": str(raw.get("slug") or "").strip(),
            "state": str(raw.get("state") or "").strip(),
            "code": str(raw.get("code") or "").strip(),
            "openid": str(raw.get("openid") or "").strip(),
            "unionid": str(raw.get("unionid") or "").strip(),
            "redirect_uri": str(raw.get("redirect_uri") or "").strip(),
            "oauth_event_key": str(raw.get("oauth_event_key") or "").strip(),
        }

    def _redacted_event(self, event: Json) -> Json:
        normalized = self._coerce_event(event)
        if not normalized["oauth_event_key"] and normalized["state"] and normalized["openid"]:
            normalized["oauth_event_key"] = self._event_key(normalized)
        return {
            "oauth_event_type": normalized["oauth_event_type"],
            "slug": normalized["slug"],
            "state": normalized["state"],
            "oauth_event_key": normalized["oauth_event_key"],
            "openid_redacted": _redact(normalized["openid"]),
            "unionid_redacted": _redact(normalized["unionid"]),
            "redirect_uri_evidence": normalized["redirect_uri"],
        }

    def _event_key(self, event: Json) -> str:
        return f"phase5o:wechat_oauth:{event.get('state', '')}:{event.get('openid', '')}"

    def _error(self, code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid" if code != "live_oauth_callback_not_enabled" else "blocked", "error_code": code}


def build_fake_stub_oauth_identity_adapter() -> FakeStubOAuthIdentityAdapter:
    return FakeStubOAuthIdentityAdapter()
