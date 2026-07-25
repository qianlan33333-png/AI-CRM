from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from aicrm_next.shared.runtime_settings import runtime_bool

from .openclaw_mcp_ai_assist_live_gateway import OpenClawMcpAiAssistLiveGateway, build_openclaw_mcp_ai_assist_live_gateway


Json = dict[str, Any]
FLAG_ENABLED = "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_ADAPTER_ENABLED"
FLAG_APPROVED = "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_CALL_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_OPENCLAW_MCP_AI_ASSIST_CONFIG_REVIEWED"
FLAG_ENDPOINT_REVIEWED = "AICRM_OPENCLAW_MCP_AI_ASSIST_ENDPOINT_REVIEWED"
FLAG_CREDENTIAL_SOURCE_REVIEWED = "AICRM_OPENCLAW_MCP_AI_ASSIST_CREDENTIAL_SOURCE_REVIEWED"
FLAG_PROMPT_REDACTION_CONFIRMED = "AICRM_OPENCLAW_MCP_AI_ASSIST_PROMPT_REDACTION_CONFIRMED"
FLAG_NO_OUTBOUND_SEND = "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_OUTBOUND_SEND_CONFIRMED"
FLAG_NO_AUTOMATION_EXECUTION = "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_AUTOMATION_EXECUTION_CONFIRMED"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_OPENCLAW_MCP_AI_ASSIST_CONFIG_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_CREDENTIAL_SOURCE_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_ENDPOINT_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_ADAPTER_ENABLED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_CALL_APPROVED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_AUTOMATION_EXECUTION_CONFIRMED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_OUTBOUND_SEND_CONFIRMED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_PROMPT_REDACTION_CONFIRMED",
    }
)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(sk|token|secret|key)-[A-Za-z0-9_-]+", "<redacted_credential>", text)
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"


def redact_payload(value: Json | None) -> Json:
    source = value if isinstance(value, dict) else {}
    redacted: Json = {}
    for key, item in sorted(source.items()):
        lowered = str(key).lower()
        if any(token in lowered for token in ("secret", "token", "credential", "password", "prompt", "content")):
            redacted[str(key)] = {"redacted": "<redacted>", "value_hash": _hash({"value": str(item)})}
        elif isinstance(item, str):
            redacted[str(key)] = {"redacted": redact_text(item), "value_hash": _hash({"value": item})}
        elif isinstance(item, (int, float, bool)) or item is None:
            redacted[str(key)] = item
        else:
            redacted[str(key)] = "<redacted_nested>"
    return redacted


def _side_effect_safety(*, provider_call_executed: bool = False) -> Json:
    return {
        "provider_call_executed": provider_call_executed,
        "network_call_executed": provider_call_executed,
        "real_mcp_call_executed": False,
        "real_openclaw_call_executed": False,
        "real_llm_call_executed": False,
        "deepseek_call_executed": False,
        "tool_execution_without_gate": False,
        "outbound_send_executed": False,
        "timer_execution_executed": False,
        "automation_execution_executed": False,
        "prompt_leak_detected": False,
        "credential_leak_detected": False,
        "production_write_executed": False,
        "route_owner_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
    }


def _base_response(**safety: bool) -> Json:
    side_effect_safety = _side_effect_safety(**safety)
    return {
        "adapter_mode": "openclaw_mcp_ai_assist_live_adapter_behind_explicit_flag",
        **side_effect_safety,
        "live_call_disabled_by_default": True,
        "prompt_redacted": True,
        "credential_redacted": True,
        "production_success_claimed": False,
        "side_effect_safety": side_effect_safety,
        "timestamp": _timestamp(),
    }


class OpenClawMcpAiAssistLiveAdapter:
    def __init__(self, *, gateway: OpenClawMcpAiAssistLiveGateway | None = None, confirm_no_outbound_send: bool = False, confirm_no_automation_execution: bool = False) -> None:
        self._gateway = gateway or build_openclaw_mcp_ai_assist_live_gateway()
        self._confirm_no_outbound_send = confirm_no_outbound_send
        self._confirm_no_automation_execution = confirm_no_automation_execution
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def call_mcp_tool_live(self, *, tool_name: str, arguments: Json | None, operator: str, idempotency_key: str) -> Json:
        if not str(tool_name or "").strip():
            return self._invalid("tool_name_required")
        arguments_redacted = redact_payload(arguments)
        return self._execute(
            operation="call_mcp_tool_live",
            idempotency_key=idempotency_key,
            payload={"tool_name": str(tool_name), "arguments_redacted": arguments_redacted, "operator": operator},
            gateway_call=lambda: self._gateway.call_mcp_tool_live(tool_name=str(tool_name), arguments_redacted=arguments_redacted),
        )

    def push_openclaw_context_live(self, *, member_id: str, context: Json | None, operator: str, idempotency_key: str) -> Json:
        if not str(member_id or "").strip():
            return self._invalid("member_id_required")
        context_redacted = redact_payload(context)
        member_id_redacted = redact_text(member_id)
        return self._execute(
            operation="push_openclaw_context_live",
            idempotency_key=idempotency_key,
            payload={"member_id_redacted": member_id_redacted, "context_redacted": context_redacted, "operator": operator},
            gateway_call=lambda: self._gateway.push_openclaw_context_live(member_id_redacted=member_id_redacted, context_redacted=context_redacted),
        )

    def run_ai_assist_completion_live(self, *, prompt: str, context: Json | None, operator: str, idempotency_key: str) -> Json:
        if not str(prompt or "").strip():
            return self._invalid("prompt_required")
        prompt_redacted = redact_text(prompt)
        context_redacted = redact_payload(context)
        return self._execute(
            operation="run_ai_assist_completion_live",
            idempotency_key=idempotency_key,
            payload={"prompt_text_redacted": prompt_redacted, "context_redacted": context_redacted, "operator": operator},
            gateway_call=lambda: self._gateway.run_ai_assist_completion_live(prompt_redacted=prompt_redacted, context_redacted=context_redacted),
        )

    def _execute(self, *, operation: str, idempotency_key: str, payload: Json, gateway_call: Any) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        request_payload = {"operation": operation, **payload}
        request_hash = _hash(request_payload)
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
            response = self._blocked(gate, idempotency_key=normalized_key, request_hash=request_hash, **request_payload)
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response
        result = gateway_call()
        response = self._gateway_response(result=result, idempotency_key=normalized_key, request_hash=request_hash, **request_payload)
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def _gate_status(self) -> Json:
        required = [
            (FLAG_ENABLED, "live_adapter_not_enabled"),
            (FLAG_APPROVED, "live_call_not_approved"),
            (FLAG_CONFIG_REVIEWED, "adapter_config_missing"),
            (FLAG_ENDPOINT_REVIEWED, "provider_tool_endpoint_not_reviewed"),
            (FLAG_CREDENTIAL_SOURCE_REVIEWED, "credential_source_not_reviewed"),
            (FLAG_PROMPT_REDACTION_CONFIRMED, "prompt_redaction_required"),
            (FLAG_NO_OUTBOUND_SEND, "confirm_no_outbound_send_required"),
            (FLAG_NO_AUTOMATION_EXECUTION, "confirm_no_automation_execution_required"),
        ]
        for flag, error_code in required:
            if not _enabled(flag):
                return {"ok": False, "error_code": error_code, "missing_gate": flag}
        if not self._confirm_no_outbound_send:
            return {"ok": False, "error_code": "confirm_no_outbound_send_required", "missing_gate": "confirm_no_outbound_send"}
        if not self._confirm_no_automation_execution:
            return {"ok": False, "error_code": "confirm_no_automation_execution_required", "missing_gate": "confirm_no_automation_execution"}
        return {"ok": True}

    def _blocked(self, gate: Json, **extra: Any) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": gate.get("error_code") or "live_call_not_enabled",
            "missing_gate": gate.get("missing_gate") or "",
            "live_adapter_enabled": _enabled(FLAG_ENABLED),
            "live_call_approved": _enabled(FLAG_APPROVED),
            "config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "provider_tool_endpoint_reviewed": _enabled(FLAG_ENDPOINT_REVIEWED),
            "credential_source_reviewed": _enabled(FLAG_CREDENTIAL_SOURCE_REVIEWED),
            "prompt_redaction_confirmed": _enabled(FLAG_PROMPT_REDACTION_CONFIRMED),
            **extra,
        }

    def _invalid(self, error_code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": error_code}

    def _gateway_response(self, *, result: Json, **extra: Any) -> Json:
        ok = bool(result.get("ok"))
        return {
            **_base_response(provider_call_executed=bool(result.get("provider_call_executed"))),
            "ok": ok,
            "result_status": "live_operation_completed" if ok else str(result.get("result_status") or "blocked"),
            "error_code": "" if ok else str(result.get("error_code") or "live_gateway_disabled"),
            **extra,
        }


def build_openclaw_mcp_ai_assist_live_adapter(*, confirm_no_outbound_send: bool = False, confirm_no_automation_execution: bool = False) -> OpenClawMcpAiAssistLiveAdapter:
    return OpenClawMcpAiAssistLiveAdapter(confirm_no_outbound_send=confirm_no_outbound_send, confirm_no_automation_execution=confirm_no_automation_execution)
