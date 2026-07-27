from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


Json = dict[str, Any]
ADAPTER_MODE = "fake_stub"

DETERMINISTIC_EVENTS: list[Json] = [
    {
        "event_type": "external_contact",
        "change_type": "add_external_contact",
        "external_userid": "external_userid_phase5i_0001",
        "follow_user_userid": "follow_user_phase5i_001",
        "event_key": "phase5i:external_contact:add:external_userid_phase5i_0001:follow_user_phase5i_001",
    },
    {
        "event_type": "external_contact",
        "change_type": "edit_external_contact",
        "external_userid": "external_userid_phase5i_0002",
        "follow_user_userid": "follow_user_phase5i_001",
        "event_key": "phase5i:external_contact:edit:external_userid_phase5i_0002:follow_user_phase5i_001",
    },
]

SUPPORTED_CHANGE_TYPES = {"add_external_contact", "edit_external_contact", "del_external_contact"}


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_external_userid(external_userid: str) -> str:
    value = str(external_userid or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:8]}...{value[-4:]}"


def _side_effect_safety() -> Json:
    return {
        "live_callback_processed": False,
        "production_write_executed": False,
        "production_contact_write_executed": False,
        "production_identity_mapping_write_executed": False,
        "production_tag_write_executed": False,
        "outbound_send_executed": False,
        "customer_sync_executed": False,
        "token_used": False,
        "aes_key_used": False,
        "decrypt_executed": False,
        "network_call_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
    }


def _base_response() -> Json:
    return {
        "adapter_mode": ADAPTER_MODE,
        "live_callback_processed": False,
        "production_write_executed": False,
        "production_contact_write_executed": False,
        "production_identity_mapping_write_executed": False,
        "production_tag_write_executed": False,
        "outbound_send_executed": False,
        "customer_sync_executed": False,
        "token_used": False,
        "aes_key_used": False,
        "decrypt_executed": False,
        "network_call_executed": False,
        "db_write_executed": False,
        "production_behavior_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "production_success_claimed": False,
        "side_effect_safety": _side_effect_safety(),
        "timestamp": _timestamp(),
    }


class FakeStubWeComContactCallbackAdapter:
    def __init__(self, events: list[Json] | None = None) -> None:
        self._events = deepcopy(events or DETERMINISTIC_EVENTS)
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._event_keys: dict[str, IdempotencyRecord] = {}

    def reset_state(self) -> None:
        self._idempotency.clear()
        self._event_keys.clear()

    def deterministic_events(self) -> Json:
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_events_listed",
            "events": [self._redacted_event(event) for event in self._events],
        }

    def verify_callback_contract(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        values = {
            "signature": str(signature or "").strip(),
            "timestamp": str(timestamp or "").strip(),
            "nonce": str(nonce or "").strip(),
            "echostr": str(echostr or "").strip(),
        }
        missing = [key for key, value in values.items() if not value]
        request_hash = _canonical_hash(values)
        if missing:
            return {
                **_base_response(),
                "ok": False,
                "result_status": "invalid",
                "error_code": "callback_config_missing",
                "missing_fields": missing,
                "request_hash": request_hash,
            }
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_callback_verification_contract",
            "verification_mode": ADAPTER_MODE,
            "request_hash": request_hash,
        }

    def parse_external_contact_event(self, payload: Json) -> Json:
        if not isinstance(payload, dict):
            return self._error("event_type_unsupported")
        event = self._coerce_event(payload)
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_event_parsed",
            "event": self._redacted_event(event),
            "request_hash": _canonical_hash(event),
        }

    def normalize_external_contact_event(self, event: Json) -> Json:
        normalized = self._coerce_event(event)
        if normalized["event_type"] != "external_contact" or normalized["change_type"] not in SUPPORTED_CHANGE_TYPES:
            return {
                **_base_response(),
                "ok": False,
                "result_status": "unsupported",
                "error_code": "event_type_unsupported",
                "event": self._redacted_event(normalized),
            }
        if not normalized["external_userid"]:
            return self._error("external_userid_missing")
        if not normalized["follow_user_userid"]:
            return self._error("follow_user_userid_missing")
        if not normalized["event_key"]:
            normalized["event_key"] = self._event_key(normalized)
        return {
            **_base_response(),
            "ok": True,
            "result_status": "fake_stub_event_normalized",
            "event": self._redacted_event(normalized),
            "request_hash": _canonical_hash(normalized),
        }

    def dry_run_record_contact_event(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._dry_run_write_like(
            operation="dry_run_record_contact_event",
            event=event,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def dry_run_identity_mapping(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._dry_run_write_like(
            operation="dry_run_identity_mapping",
            event=event,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def live_callback_attempt(self) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": "live_callback_not_enabled",
        }

    def _dry_run_write_like(self, *, operation: str, event: Json, operator: str, idempotency_key: str) -> Json:
        normalized_operator = str(operator or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._error("idempotency_key_required")
        normalized = self._coerce_event(event)
        normalized_result = self.normalize_external_contact_event(normalized)
        if not normalized_result.get("ok"):
            return normalized_result
        normalized_event = dict(normalized_result["event"])
        event_key = str(normalized_event.get("event_key") or "").strip()
        if not event_key:
            return self._error("duplicate_event_key")

        request_payload = {
            "operation": operation,
            "event_key": event_key,
            "external_userid_redacted": normalized_event["external_userid_redacted"],
            "follow_user_userid": normalized_event["follow_user_userid"],
            "operator": normalized_operator,
        }
        request_hash = _canonical_hash(request_payload)
        existing_key = self._idempotency.get(normalized_key)
        if existing_key and existing_key.request_hash != request_hash:
            return {
                **_base_response(),
                "ok": False,
                "result_status": "conflict",
                "error_code": "duplicate_event_key",
                "event_key": event_key,
                "operator": normalized_operator,
                "idempotency_key": normalized_key,
                "request_hash": request_hash,
            }
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
            "event_key": event_key,
            "event_type": normalized_event["event_type"],
            "change_type": normalized_event["change_type"],
            "external_userid_redacted": normalized_event["external_userid_redacted"],
            "follow_user_userid": normalized_event["follow_user_userid"],
            "operator": normalized_operator,
            "idempotency_key": normalized_key,
            "request_hash": request_hash,
            "idempotency_replay": False,
            "event_key_replay": False,
        }
        record = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        self._idempotency[normalized_key] = record
        self._event_keys[event_key] = record
        return response

    def _coerce_event(self, event: Json) -> Json:
        source = dict(event or {})
        event_type = str(source.get("event_type") or source.get("Event") or source.get("MsgType") or "external_contact").strip()
        change_type = str(source.get("change_type") or source.get("ChangeType") or source.get("changeType") or "").strip()
        external_userid = str(source.get("external_userid") or source.get("ExternalUserID") or source.get("ExternalUserId") or "").strip()
        follow_user_userid = str(source.get("follow_user_userid") or source.get("UserID") or source.get("userid") or "").strip()
        event_key = str(source.get("event_key") or source.get("EventKey") or source.get("eventKey") or "").strip()
        return {
            "event_type": event_type,
            "change_type": change_type,
            "external_userid": external_userid,
            "follow_user_userid": follow_user_userid,
            "event_key": event_key,
        }

    def _event_key(self, event: Json) -> str:
        return f"phase5i:{event['event_type']}:{event['change_type']}:{event['external_userid']}:{event['follow_user_userid']}"

    def _redacted_event(self, event: Json) -> Json:
        normalized = self._coerce_event(event)
        if not normalized["event_key"] and normalized["external_userid"] and normalized["follow_user_userid"]:
            normalized["event_key"] = self._event_key(normalized)
        return {
            "event_type": normalized["event_type"],
            "change_type": normalized["change_type"],
            "event_key": normalized["event_key"],
            "external_userid_redacted": redact_external_userid(normalized["external_userid"]),
            "follow_user_userid": normalized["follow_user_userid"],
        }

    def _error(self, error_code: str) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked" if error_code == "live_callback_not_enabled" else "invalid",
            "error_code": error_code,
        }


def build_fake_stub_wecom_contact_callback_adapter() -> FakeStubWeComContactCallbackAdapter:
    return FakeStubWeComContactCallbackAdapter()
