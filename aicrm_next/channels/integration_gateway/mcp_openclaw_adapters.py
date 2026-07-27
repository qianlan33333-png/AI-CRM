from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting

from .audit import record_audit_event
from .idempotency import get_or_create, make_idempotency_key
from .mcp_openclaw_contracts import AdapterMode, Json


VALID_MODES = {"fake", "disabled", "staging", "production"}
OPENCLAW_WEBHOOK_PRODUCTION_FLAG = "AICRM_NEXT_ENABLE_REAL_OPENCLAW_WEBHOOK"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_NEXT_CUSTOMER_CONTEXT_TOOL_MODE",
        "AICRM_NEXT_ENABLE_REAL_MCP_TOOLS",
        "AICRM_NEXT_ENABLE_REAL_OPENCLAW_BRIDGE",
        "AICRM_NEXT_MCP_TOOL_MODE",
        "AICRM_NEXT_OPENCLAW_LEGACY_MODE",
    }
)


def _normalise_mode(value: str | None, *, default: AdapterMode = "fake") -> AdapterMode:
    mode = (value or default).strip().lower()
    if mode not in VALID_MODES:
        return default
    return mode  # type: ignore[return-value]


def _env_true(name: str) -> bool:
    return managed_runtime_bool(name)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mode_prefix(mode: AdapterMode) -> str:
    return "staging" if mode == "staging" else "fake"


def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "secret",
        "token",
        "bearer",
        "authorization",
        "access_token",
        "client_secret",
        "app_secret",
        "credential",
        "password",
        "api_key",
        "private_key",
        "webhook_token",
        "mcp_bearer_token",
        "openclaw_token",
    }

    def is_secret_key(key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in forbidden)

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if not is_secret_key(key)}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(target)


def _payload_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    safe = _safe_target(payload or {})
    return {"payload_hash": _digest(repr(sorted(safe.items())))[:24], "payload_keys": sorted(safe.keys())}


def mcp_openclaw_side_effect_safety() -> dict[str, bool]:
    return {
        "real_openclaw_call_executed": False,
        "real_mcp_external_call_executed": False,
        "real_external_webhook_executed": False,
        "real_customer_context_write_executed": False,
        "real_traffic_cutover_executed": False,
    }


def _base_result(
    *,
    ok: bool,
    adapter: str,
    mode: AdapterMode,
    operation: str,
    idempotency_key: str,
    target: dict[str, Any],
    result: dict[str, Any] | None,
    audit_id: str,
    error_code: str = "",
    error_message: str = "",
) -> Json:
    return {
        "ok": ok,
        "adapter": adapter,
        "mode": mode,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "target": _safe_target(target),
        "result": result or {},
        "audit_id": audit_id,
        "side_effect_executed": False,
        "error_code": error_code,
        "error_message": error_message,
    }


class _GuardedMcpOpenClawAdapter:
    adapter_name = "McpOpenClawAdapter"
    production_flag = ""

    def __init__(self, mode: AdapterMode | str = "fake") -> None:
        self.mode = _normalise_mode(str(mode), default="fake")

    def _guarded_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any]) -> Json | None:
        if self.mode == "disabled":
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code="adapter_disabled",
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code="adapter_disabled",
                error_message=f"{self.adapter_name} is disabled",
            )
        if self.mode == "production":
            error_code = "production_guard_failed" if not _env_true(self.production_flag) else "production_not_implemented"
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code=error_code,
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code=error_code,
                error_message=f"{self.adapter_name} production mode is not implemented in D7.7",
            )
        return None

    def _operation(self, operation: str, *, target: dict[str, Any], result_factory, idempotency_key: str | None = None) -> Json:
        safe_target = _safe_target(target)
        key = idempotency_key or make_idempotency_key(operation=operation, payload=safe_target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=safe_target)
        if guarded:
            return guarded
        cached = get_or_create(key, result_factory)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="ok",
        )
        return _base_result(
            ok=True,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=safe_target,
            result=cached,
            audit_id=audit["audit_id"],
        )

    def _audit_only(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        safe_target = _safe_target(target)
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"target": safe_target, "result": result or {}})
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="blocked" if error_code else "ok",
            error_code=error_code,
        )
        return _base_result(
            ok=not error_code,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=safe_target,
            result=result or {},
            audit_id=audit["audit_id"],
            error_code=error_code,
            error_message="" if not error_code else "audit recorded as blocked",
        )


class McpToolGateway(_GuardedMcpOpenClawAdapter):
    adapter_name = "McpToolGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_MCP_TOOLS"

    def list_tools(self, *, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"request_id": request_id}
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            "list_tools",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "tool_catalog_id": f"{mode_prefix}_mcp_tools_{_digest(repr(target))[:12]}",
                "tools": ["resolve_customer", "get_customer_context", "get_recent_messages"],
                "side_effect_safety": mcp_openclaw_side_effect_safety(),
            },
        )

    def invoke_tool(self, *, tool_name: str, arguments: dict[str, Any] | None = None, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id, "arguments_summary": _payload_summary(arguments)}
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            "invoke_tool",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "tool_invocation_id": f"{mode_prefix}_tool_invocation_{_digest(repr(target))[:16]}",
                "tool_name": tool_name,
                "side_effect_safety": mcp_openclaw_side_effect_safety(),
            },
        )

    def build_tool_preview(self, *, tool_name: str, arguments: dict[str, Any] | None = None, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id, "arguments_summary": _payload_summary(arguments)}
        return self._operation(
            "build_tool_preview",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"preview_id": f"{_mode_prefix(self.mode)}_tool_preview_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def validate_tool_request(self, *, tool_name: str, arguments: dict[str, Any] | None = None, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id, "arguments_summary": _payload_summary(arguments)}
        return self._operation(
            "validate_tool_request",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"valid": bool(tool_name), "tool_name": tool_name, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def record_tool_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


class CustomerContextToolAdapter(_GuardedMcpOpenClawAdapter):
    adapter_name = "CustomerContextToolAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_MCP_TOOLS"

    def resolve_customer(self, *, external_userid: str = "", person_id: str = "", customer_ref: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "person_id": person_id, "customer_ref": customer_ref, "request_id": request_id}
        return self._operation(
            "resolve_customer",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"resolved": True, "external_userid": external_userid or customer_ref, "person_id": person_id, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def get_customer_context(self, *, external_userid: str, person_id: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "person_id": person_id, "request_id": request_id, "context_type": "customer_context"}
        return self._operation(
            "get_customer_context",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"context_preview_id": f"{_mode_prefix(self.mode)}_customer_context_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def get_customer_timeline(self, *, external_userid: str, person_id: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "person_id": person_id, "request_id": request_id, "context_type": "customer_timeline"}
        return self._operation(
            "get_customer_timeline",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"timeline_preview_id": f"{_mode_prefix(self.mode)}_customer_timeline_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def get_recent_messages(self, *, external_userid: str, person_id: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "person_id": person_id, "request_id": request_id, "context_type": "recent_messages"}
        return self._operation(
            "get_recent_messages",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"recent_messages_preview_id": f"{_mode_prefix(self.mode)}_recent_messages_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def build_customer_context_preview(self, *, external_userid: str = "", person_id: str = "", context_type: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "person_id": person_id, "context_type": context_type, "request_id": request_id}
        return self._operation(
            "build_customer_context_preview",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"preview_id": f"{_mode_prefix(self.mode)}_customer_preview_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def record_customer_context_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


class OpenClawLegacyBridgeAdapter(_GuardedMcpOpenClawAdapter):
    adapter_name = "OpenClawLegacyBridgeAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_OPENCLAW_BRIDGE"

    def build_openclaw_context_payload(self, *, member_id: str = "", external_userid: str = "", context_type: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"member_id": member_id, "external_userid": external_userid, "context_type": context_type, "payload_summary": _payload_summary(payload_summary)}
        return self._operation(
            "build_openclaw_context_payload",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"payload_preview_id": f"{_mode_prefix(self.mode)}_openclaw_payload_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def push_context_to_openclaw(self, *, member_id: str = "", external_userid: str = "", openclaw_context_id: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"member_id": member_id, "external_userid": external_userid, "openclaw_context_id": openclaw_context_id, "payload_summary": _payload_summary(payload_summary)}
        return self._operation(
            "push_context_to_openclaw",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "bridge_delivery_id": f"{_mode_prefix(self.mode)}_openclaw_bridge_{_digest(repr(target))[:16]}",
                "delivery_status": _mode_prefix(self.mode),
                "side_effect_safety": mcp_openclaw_side_effect_safety(),
            },
        )

    def resolve_legacy_skill_request(self, *, skill_name: str, request_id: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"skill_name": skill_name, "request_id": request_id, "payload_summary": _payload_summary(payload_summary)}
        return self._operation(
            "resolve_legacy_skill_request",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"skill_name": skill_name, "legacy_skill_supported": True, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def build_legacy_bridge_preview(self, *, skill_name: str = "", context_type: str = "", request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"skill_name": skill_name, "context_type": context_type, "request_id": request_id}
        return self._operation(
            "build_legacy_bridge_preview",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"preview_id": f"{_mode_prefix(self.mode)}_legacy_bridge_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def record_openclaw_bridge_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


class McpCompatibilityGateway(_GuardedMcpOpenClawAdapter):
    adapter_name = "McpCompatibilityGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_MCP_TOOLS"

    LEGACY_TOOL_NAMES = {
        "customer_context": "get_customer_context",
        "recent_messages": "get_recent_messages",
    }

    def map_legacy_tool_name(self, *, tool_name: str, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id}
        mapped = self.LEGACY_TOOL_NAMES.get(tool_name, tool_name)
        return self._operation(
            "map_legacy_tool_name",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"tool_name": tool_name, "mapped_tool_name": mapped, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def map_legacy_payload(self, *, tool_name: str, payload: dict[str, Any] | None = None, request_id: str = "", idempotency_key: str | None = None) -> Json:
        safe_payload = _safe_target(payload or {})
        target = {"tool_name": tool_name, "request_id": request_id, "payload_summary": _payload_summary(safe_payload)}
        return self._operation(
            "map_legacy_payload",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"tool_name": tool_name, "payload": safe_payload, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def normalize_tool_response(self, *, tool_name: str, response: dict[str, Any] | None = None, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id, "response_summary": _payload_summary(response)}
        return self._operation(
            "normalize_tool_response",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"tool_name": tool_name, "normalized": True, "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def build_compatibility_preview(self, *, tool_name: str, request_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"tool_name": tool_name, "request_id": request_id}
        return self._operation(
            "build_compatibility_preview",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"preview_id": f"{_mode_prefix(self.mode)}_compat_{_digest(repr(target))[:16]}", "side_effect_safety": mcp_openclaw_side_effect_safety()},
        )

    def record_compatibility_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


def build_mcp_tool_gateway() -> McpToolGateway:
    return McpToolGateway(managed_runtime_setting("AICRM_NEXT_MCP_TOOL_MODE", "fake"))


def build_customer_context_tool_adapter() -> CustomerContextToolAdapter:
    return CustomerContextToolAdapter(
        managed_runtime_setting("AICRM_NEXT_CUSTOMER_CONTEXT_TOOL_MODE", "fake")
    )


def build_openclaw_legacy_bridge_adapter() -> OpenClawLegacyBridgeAdapter:
    return OpenClawLegacyBridgeAdapter(
        managed_runtime_setting("AICRM_NEXT_OPENCLAW_LEGACY_MODE", "fake")
    )


def build_mcp_compatibility_gateway() -> McpCompatibilityGateway:
    return McpCompatibilityGateway(
        managed_runtime_setting("AICRM_NEXT_MCP_TOOL_MODE", "fake")
    )
