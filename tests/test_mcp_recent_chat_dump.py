from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.channels.integration_gateway.mcp import MCP_TOOLS
from aicrm_next.main import create_app


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-mcp-recent-chat-test")
    return TestClient(create_app())


def mcp_call(client: TestClient, name: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_legacy_owner_recent_chat_dump_tool_is_retired_from_current_mcp_surface() -> None:
    names = {tool["name"] for tool in MCP_TOOLS}

    assert "get_owner_recent_chat_dump" not in names
    assert "get_recent_messages" in names


def test_get_recent_messages_returns_next_read_model_context_without_external_calls(monkeypatch) -> None:
    client = make_client(monkeypatch)

    payload = mcp_call(client, "get_recent_messages", {"external_userid": "wx_ext_001", "limit": 2})
    content = payload["result"]["structuredContent"]

    assert content["external_userid"] == "wx_ext_001"
    assert content["route_owner"] == "ai_crm_next"
    assert content["fallback_used"] is False
    assert isinstance(content["messages"], list)
    assert content["adapter_contract"]["customer_context_tool"]["adapter"] == "CustomerContextToolAdapter"
    assert content["side_effect_safety"]["real_mcp_external_call_executed"] is False
    assert content["side_effect_safety"]["real_external_webhook_executed"] is False


def test_legacy_owner_recent_chat_dump_call_returns_structured_error(monkeypatch) -> None:
    client = make_client(monkeypatch)

    payload = mcp_call(client, "get_owner_recent_chat_dump", {"owner_userid": "sales_01"})

    assert payload["error"]["code"] == -32000
    assert "unknown MCP tool" in payload["error"]["message"]
