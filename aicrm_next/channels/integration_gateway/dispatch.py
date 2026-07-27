from __future__ import annotations

from collections.abc import Callable

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.typing import JsonDict

from .mcp_openclaw_adapters import (
    build_customer_context_tool_adapter,
    build_mcp_compatibility_gateway,
    build_mcp_tool_gateway,
    mcp_openclaw_side_effect_safety,
)
from .user_ops_adapters import WeComMessageDispatchAdapter, build_wecom_message_dispatch_adapter


def _looks_like_mobile(value: str) -> bool:
    return value.isdigit() and 8 <= len(value) <= 15


class McpToolDispatcher:
    def __init__(
        self,
        tool_gateway=None,
        customer_context_adapter=None,
        compatibility_gateway=None,
        identity_resolver: Callable[[str], str] | None = None,
        customer_detail_query: Callable[[str], JsonDict] | None = None,
        customer_context_query: Callable[[str, int, int], JsonDict] | None = None,
        recent_messages_query: Callable[[str, int], JsonDict] | None = None,
    ) -> None:
        self._tool_gateway = tool_gateway or build_mcp_tool_gateway()
        self._customer_context_adapter = customer_context_adapter or build_customer_context_tool_adapter()
        self._compatibility_gateway = compatibility_gateway or build_mcp_compatibility_gateway()
        self._identity_resolver = identity_resolver
        self._customer_detail_query = customer_detail_query
        self._customer_context_query = customer_context_query
        self._recent_messages_query = recent_messages_query

    def resolve_external_userid(self, arguments: JsonDict) -> str:
        external_userid = str(arguments.get("external_userid") or "").strip()
        customer_ref = str(arguments.get("customer_ref") or "").strip()
        if external_userid:
            return external_userid
        if not customer_ref:
            raise ContractError("customer_ref or external_userid is required")
        if _looks_like_mobile(customer_ref):
            if self._identity_resolver is None:
                raise ContractError("MCP identity resolver composition is unavailable")
            external_userid = str(self._identity_resolver(customer_ref) or "").strip()
            if not external_userid:
                raise NotFoundError(f"customer not found for mobile: {customer_ref}")
            return external_userid
        return customer_ref

    def dispatch(self, name: str, arguments: JsonDict) -> JsonDict:
        request_id = str(arguments.get("request_id") or "")
        compatibility = self._compatibility_gateway.map_legacy_tool_name(tool_name=name, request_id=request_id)
        mapped_name = str(compatibility.get("result", {}).get("mapped_tool_name") or name)
        payload_mapping = self._compatibility_gateway.map_legacy_payload(tool_name=mapped_name, payload=arguments, request_id=request_id)
        validation = self._tool_gateway.validate_tool_request(tool_name=mapped_name, arguments=arguments, request_id=request_id)
        invocation = self._tool_gateway.invoke_tool(tool_name=mapped_name, arguments=arguments, request_id=request_id)
        if mapped_name == "resolve_customer":
            return self._with_mcp_contracts(
                self._resolve_customer(arguments),
                compatibility=compatibility,
                payload_mapping=payload_mapping,
                validation=validation,
                invocation=invocation,
            )
        if mapped_name == "get_customer_context":
            return self._with_mcp_contracts(
                self._get_customer_context(arguments),
                compatibility=compatibility,
                payload_mapping=payload_mapping,
                validation=validation,
                invocation=invocation,
            )
        if mapped_name == "get_recent_messages":
            return self._with_mcp_contracts(
                self._get_recent_messages(arguments),
                compatibility=compatibility,
                payload_mapping=payload_mapping,
                validation=validation,
                invocation=invocation,
            )
        raise ContractError(f"unknown MCP tool: {mapped_name}")

    def _resolve_customer(self, arguments: JsonDict) -> JsonDict:
        external_userid = self.resolve_external_userid(arguments)
        context_contract = self._customer_context_adapter.resolve_customer(
            external_userid=external_userid,
            customer_ref=str(arguments.get("customer_ref") or ""),
            request_id=str(arguments.get("request_id") or ""),
        )
        if self._customer_detail_query is None:
            raise ContractError("MCP customer detail composition is unavailable")
        detail = self._customer_detail_query(external_userid)
        payload: JsonDict = {
            "external_userid": external_userid,
            "customer": detail["customer"],
            "adapter_contract": {"customer_context_tool": context_contract},
            "side_effect_safety": mcp_openclaw_side_effect_safety(),
        }
        if bool(arguments.get("include_context")):
            payload["context"] = self._get_customer_context(arguments)
        return payload

    def _get_customer_context(self, arguments: JsonDict) -> JsonDict:
        external_userid = self.resolve_external_userid(arguments)
        context_contract = self._customer_context_adapter.get_customer_context(
            external_userid=external_userid,
            request_id=str(arguments.get("request_id") or ""),
        )
        if self._customer_context_query is None:
            raise ContractError("MCP customer context composition is unavailable")
        result = self._customer_context_query(
            external_userid,
            int(arguments.get("recent_message_limit") or 20),
            int(arguments.get("timeline_limit") or 20),
        )
        result.setdefault("adapter_contract", {})["customer_context_tool"] = context_contract
        result["side_effect_safety"] = {**mcp_openclaw_side_effect_safety(), **dict(result.get("side_effect_safety") or {})}
        return result

    def _get_recent_messages(self, arguments: JsonDict) -> JsonDict:
        external_userid = self.resolve_external_userid(arguments)
        recent_contract = self._customer_context_adapter.get_recent_messages(
            external_userid=external_userid,
            request_id=str(arguments.get("request_id") or ""),
        )
        if self._recent_messages_query is None:
            raise ContractError("MCP recent messages composition is unavailable")
        result = self._recent_messages_query(
            external_userid,
            int(arguments.get("limit") or arguments.get("recent_message_limit") or 20),
        )
        result.setdefault("adapter_contract", {})["customer_context_tool"] = recent_contract
        result["side_effect_safety"] = {**mcp_openclaw_side_effect_safety(), **dict(result.get("side_effect_safety") or {})}
        return result

    def _with_mcp_contracts(
        self,
        payload: JsonDict,
        *,
        compatibility: JsonDict,
        payload_mapping: JsonDict,
        validation: JsonDict,
        invocation: JsonDict,
    ) -> JsonDict:
        contracts = dict(payload.get("adapter_contract") or {})
        contracts.update(
            {
                "mcp_tool": invocation,
                "mcp_tool_validation": validation,
                "mcp_compatibility": compatibility,
                "mcp_payload_mapping": payload_mapping,
            }
        )
        payload["adapter_contract"] = contracts
        payload["side_effect_safety"] = {**mcp_openclaw_side_effect_safety(), **dict(payload.get("side_effect_safety") or {})}
        return payload


class DispatchGateway:
    def __init__(self, adapter: WeComMessageDispatchAdapter | None = None) -> None:
        self._adapter = adapter or build_wecom_message_dispatch_adapter()

    def dispatch_user_ops_private_message_batch(
        self,
        *,
        owner_bucket: JsonDict,
        content: str,
        images: list[dict] | None = None,
        attachments: list[dict] | None = None,
    ) -> JsonDict:
        result = self._adapter.send_private_message(
            owner_userid=str(owner_bucket.get("sender_userid") or owner_bucket.get("owner_userid") or ""),
            external_userids=list(owner_bucket.get("external_userids") or []),
            content=content,
            media_refs=[
                *({"kind": "image", "index": index} for index, _ in enumerate(images or [])),
                *({"kind": "attachment", "index": index} for index, _ in enumerate(attachments or [])),
            ],
        )
        if result["ok"]:
            return result["result"]
        return {
            "task_id": "",
            "status": "blocked",
            "status_label": "已阻断",
            "error_message": result["error_message"] or result["error_code"],
            "dispatch_adapter": result["adapter"],
            "sender_userid": str(owner_bucket.get("sender_userid") or owner_bucket.get("owner_userid") or ""),
            "external_userids": list(owner_bucket.get("external_userids") or []),
            "target_count": len(owner_bucket.get("external_userids") or []),
            "content_preview": content[:80],
            "image_count": len(images or []),
            "attachment_count": len(attachments or []),
        }
