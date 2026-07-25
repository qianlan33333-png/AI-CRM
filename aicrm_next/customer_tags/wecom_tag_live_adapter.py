from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.integration_gateway.wecom_tag_live_gateway import WeComTagLiveGateway, build_wecom_tag_live_gateway
from aicrm_next.shared.runtime_settings import (
    managed_runtime_bool,
    managed_runtime_setting,
    startup_environment_setting,
)


Json = dict[str, Any]
ADAPTER_MODE = "live_behind_explicit_flag"

FLAG_LIVE_ENABLED = "AICRM_WECOM_TAG_LIVE_ADAPTER_ENABLED"
FLAG_LIVE_APPROVED = "AICRM_WECOM_TAG_LIVE_CALL_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_WECOM_TAG_CONFIG_REVIEWED"
FLAG_CORP_ID = "AICRM_WECOM_TAG_CORP_ID"
FLAG_AGENT_SECRET = "AICRM_WECOM_TAG_AGENT_SECRET"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_WECOM_TAG_LIVE_ADAPTER_ENABLED",
        "AICRM_WECOM_TAG_LIVE_CALL_APPROVED",
        "AICRM_WECOM_TAG_CONFIG_REVIEWED",
        "AICRM_WECOM_TAG_CORP_ID",
        "AICRM_WECOM_TAG_AGENT_SECRET",
    }
)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _enabled(name: str) -> bool:
    return managed_runtime_bool(name, False)


def _present(name: str) -> bool:
    return bool(managed_runtime_setting(name).strip())


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_tag_ids(tag_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tag_ids or []:
        tag_id = str(raw or "").strip()
        if not tag_id or tag_id in seen:
            continue
        seen.add(tag_id)
        normalized.append(tag_id)
    return normalized


def _redact_external_userid(external_userid: str) -> str:
    value = str(external_userid or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def _side_effect_safety(*, live_call_executed: bool = False, mark_tag_executed: bool = False, unmark_tag_executed: bool = False, token_used: bool = False, network_call_executed: bool = False) -> Json:
    return {
        "live_call_executed": live_call_executed,
        "mark_tag_executed": mark_tag_executed,
        "unmark_tag_executed": unmark_tag_executed,
        "outbound_send_executed": False,
        "token_used": token_used,
        "network_call_executed": network_call_executed,
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
        "production_tag_write_executed": False,
        "production_success_claimed": False,
        "side_effect_safety": side_effect_safety,
        "timestamp": _timestamp(),
    }


class LiveWeComTagAdapter:
    def __init__(
        self,
        *,
        gateway: WeComTagLiveGateway | None = None,
        confirm_live_wecom_call: bool = False,
    ) -> None:
        self._gateway = gateway or build_wecom_tag_live_gateway()
        self._confirm_live_wecom_call = confirm_live_wecom_call
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def reset_idempotency(self) -> None:
        self._idempotency.clear()

    def list_wecom_tags_live(self) -> Json:
        gate = self._gate_status()
        if not gate["ok"]:
            return self._blocked(gate)
        try:
            result = self._gateway.list_wecom_tags_live()
        except Exception as exc:  # pragma: no cover - exercised by integration evidence only
            return self._live_error(str(exc))
        return {
            **_base_response(live_call_executed=True, token_used=True, network_call_executed=True),
            "ok": True,
            "result_status": "live_list_completed",
            "error_code": "",
            "live_result": result,
            "config_reviewed": True,
            "approval_present": True,
        }

    def mark_tags_live(
        self,
        *,
        external_userid: str,
        tag_ids: list[str],
        operator: str,
        idempotency_key: str,
    ) -> Json:
        return self._tag_operation(
            operation="mark_tags_live",
            external_userid=external_userid,
            tag_ids=tag_ids,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def unmark_tags_live(
        self,
        *,
        external_userid: str,
        tag_ids: list[str],
        operator: str,
        idempotency_key: str,
    ) -> Json:
        return self._tag_operation(
            operation="unmark_tags_live",
            external_userid=external_userid,
            tag_ids=tag_ids,
            operator=operator,
            idempotency_key=idempotency_key,
        )

    def _tag_operation(
        self,
        *,
        operation: str,
        external_userid: str,
        tag_ids: list[str],
        operator: str,
        idempotency_key: str,
    ) -> Json:
        normalized_external_userid = str(external_userid or "").strip()
        normalized_operator = str(operator or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        normalized_tag_ids = _normalize_tag_ids(tag_ids)
        requested_tag_ids = [str(item or "") for item in tag_ids or []]
        if not normalized_external_userid:
            return self._invalid("external_userid_missing")
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        if not normalized_tag_ids or len(normalized_tag_ids) != len([item for item in requested_tag_ids if str(item or "").strip()]):
            return self._invalid("invalid_tag_id")

        payload = {
            "operation": operation,
            "external_userid": normalized_external_userid,
            "tag_ids": normalized_tag_ids,
            "operator": normalized_operator,
        }
        request_hash = _canonical_hash(payload)
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {
                **_base_response(),
                "ok": False,
                "result_status": "conflict",
                "error_code": "duplicate_idempotency_key",
                "external_userid_redacted": _redact_external_userid(normalized_external_userid),
                "requested_tag_ids": requested_tag_ids,
                "normalized_tag_ids": normalized_tag_ids,
                "operator": normalized_operator,
                "idempotency_key": normalized_key,
                "request_hash": request_hash,
                "idempotency_replay": False,
            }
        if existing:
            replay = deepcopy(existing.response)
            replay["idempotency_replay"] = True
            replay["result_status"] = "replay"
            replay["timestamp"] = _timestamp()
            return replay

        gate = self._gate_status()
        if not gate["ok"]:
            blocked = self._blocked(
                gate,
                external_userid=normalized_external_userid,
                requested_tag_ids=requested_tag_ids,
                normalized_tag_ids=normalized_tag_ids,
                operator=normalized_operator,
                idempotency_key=normalized_key,
                request_hash=request_hash,
            )
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(blocked))
            return blocked

        try:
            if operation == "mark_tags_live":
                result = self._gateway.mark_tags_live(
                    external_userid=normalized_external_userid,
                    tag_ids=normalized_tag_ids,
                    operator=normalized_operator,
                )
                safety = {"live_call_executed": True, "mark_tag_executed": True, "token_used": True, "network_call_executed": True}
            else:
                result = self._gateway.unmark_tags_live(
                    external_userid=normalized_external_userid,
                    tag_ids=normalized_tag_ids,
                    operator=normalized_operator,
                )
                safety = {"live_call_executed": True, "unmark_tag_executed": True, "token_used": True, "network_call_executed": True}
        except Exception as exc:  # pragma: no cover - exercised by integration evidence only
            return self._live_error(
                str(exc),
                external_userid=normalized_external_userid,
                requested_tag_ids=requested_tag_ids,
                normalized_tag_ids=normalized_tag_ids,
                operator=normalized_operator,
                idempotency_key=normalized_key,
                request_hash=request_hash,
            )

        response = {
            **_base_response(**safety),
            "ok": True,
            "result_status": "live_tag_operation_completed",
            "error_code": "",
            "external_userid_redacted": _redact_external_userid(normalized_external_userid),
            "requested_tag_ids": requested_tag_ids,
            "normalized_tag_ids": normalized_tag_ids,
            "operator": normalized_operator,
            "idempotency_key": normalized_key,
            "request_hash": request_hash,
            "idempotency_replay": False,
            "live_result": result,
            "config_reviewed": True,
            "approval_present": True,
        }
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def _gate_status(self) -> Json:
        if not _enabled(FLAG_LIVE_ENABLED):
            return {"ok": False, "error_code": "live_adapter_not_enabled", "reason": f"{FLAG_LIVE_ENABLED}=1 is required"}
        if not _enabled(FLAG_LIVE_APPROVED):
            return {"ok": False, "error_code": "live_call_not_approved", "reason": f"{FLAG_LIVE_APPROVED}=1 is required"}
        if not _enabled(FLAG_CONFIG_REVIEWED) or not _present(FLAG_CORP_ID) or not _present(FLAG_AGENT_SECRET):
            return {"ok": False, "error_code": "wecom_config_missing", "reason": "WeCom tag live config review, CorpID, and agent secret are required"}
        if (
            startup_environment_setting("AICRM_NEXT_ENV").strip().lower()
            == "production"
            and not _enabled(FLAG_LIVE_APPROVED)
        ):
            return {"ok": False, "error_code": "forbidden_in_production_without_approval", "reason": "production live call requires explicit approval"}
        if not self._confirm_live_wecom_call:
            return {"ok": False, "error_code": "live_call_not_approved", "reason": "--confirm-live-wecom-call is required for runner-driven execution"}
        return {"ok": True, "error_code": "", "reason": ""}

    def _blocked(self, gate: Json, **evidence: Any) -> Json:
        external_userid = str(evidence.pop("external_userid", "") or "")
        response = {
            **_base_response(),
            "ok": False,
            "result_status": "blocked_not_executed",
            "error_code": gate.get("error_code") or "live_adapter_not_enabled",
            "error_message": gate.get("reason") or "",
            "config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "approval_present": _enabled(FLAG_LIVE_APPROVED),
            **evidence,
        }
        if external_userid:
            response["external_userid_redacted"] = _redact_external_userid(external_userid)
        return response

    def _invalid(self, error_code: str) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "invalid",
            "error_code": error_code,
        }

    def _live_error(self, message: str, **evidence: Any) -> Json:
        external_userid = str(evidence.pop("external_userid", "") or "")
        response = {
            **_base_response(),
            "ok": False,
            "result_status": "live_call_failed",
            "error_code": "wecom_live_call_failed",
            "error_message": message[:500],
            **evidence,
        }
        if external_userid:
            response["external_userid_redacted"] = _redact_external_userid(external_userid)
        return response


def build_live_wecom_tag_adapter(*, confirm_live_wecom_call: bool = False, gateway: WeComTagLiveGateway | None = None) -> LiveWeComTagAdapter:
    return LiveWeComTagAdapter(confirm_live_wecom_call=confirm_live_wecom_call, gateway=gateway)
