from __future__ import annotations

from typing import Any


MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "resolve_customer",
        "description": "Resolve a customer by customer_ref, mobile, or external_userid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_ref": {"type": "string"},
                "external_userid": {"type": "string"},
                "include_context": {"type": "boolean"},
                "recent_message_limit": {"type": "integer"},
                "timeline_limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_customer_context",
        "description": "Return customer detail, recent messages, and timeline context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_ref": {"type": "string"},
                "external_userid": {"type": "string"},
                "recent_message_limit": {"type": "integer"},
                "timeline_limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_recent_messages",
        "description": "Return recent single-customer archived messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_ref": {"type": "string"},
                "external_userid": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
)


__all__ = ["MCP_TOOLS"]
