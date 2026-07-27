from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.channels.integration_gateway.mcp import MCP_TOOLS
from aicrm_next.main import create_app


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-mcp-business-tools-test")
    return TestClient(create_app())


def mcp_rpc(client: TestClient, method: str, params: dict | None = None) -> dict:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200
    return response.json()


def mcp_call(client: TestClient, name: str, arguments: dict) -> dict:
    return mcp_rpc(client, "tools/call", {"name": name, "arguments": arguments})


def structured(payload: dict) -> dict:
    return payload["result"]["structuredContent"]


def assert_mcp_side_effect_safe(payload: dict) -> None:
    safety = payload["side_effect_safety"]
    assert safety["real_openclaw_call_executed"] is False
    assert safety["real_mcp_external_call_executed"] is False
    assert safety["real_external_webhook_executed"] is False
    assert safety["real_customer_context_write_executed"] is False


def test_mcp_metadata_initialize_and_current_tool_list(monkeypatch) -> None:
    client = make_client(monkeypatch)

    metadata = client.get("/mcp").json()
    initialized = mcp_rpc(client, "initialize")["result"]
    tools = mcp_rpc(client, "tools/list")["result"]

    assert metadata == {"ok": True, "transport": "jsonrpc", "methods": ["initialize", "tools/list", "tools/call"]}
    assert initialized["serverInfo"]["name"] == "aicrm-next"
    assert {tool["name"] for tool in tools["tools"]} == {tool["name"] for tool in MCP_TOOLS}
    assert {tool["name"] for tool in MCP_TOOLS} == {
        "resolve_customer",
        "get_customer_context",
        "get_recent_messages",
    }
    assert tools["adapter_contract"]["mcp_tool"]["result"]["side_effect_safety"]["real_mcp_external_call_executed"] is False


def test_resolve_customer_tool_uses_next_read_model_fixture(monkeypatch) -> None:
    client = make_client(monkeypatch)

    payload = structured(mcp_call(client, "resolve_customer", {"external_userid": "wx_ext_001", "include_context": True}))

    assert payload["external_userid"] == "wx_ext_001"
    assert payload["customer"]["external_userid"] == "wx_ext_001"
    assert payload["adapter_contract"]["customer_context_tool"]["adapter"] == "CustomerContextToolAdapter"
    assert payload["adapter_contract"]["mcp_tool"]["result"]["side_effect_safety"]["real_mcp_external_call_executed"] is False
    assert_mcp_side_effect_safe(payload)


def test_unknown_legacy_mcp_tool_returns_structured_error(monkeypatch) -> None:
    client = make_client(monkeypatch)

    payload = mcp_call(client, "create_private_message_task", {"external_userid": "wx_ext_001"})

    assert payload["error"]["code"] == -32000
    assert "unknown MCP tool" in payload["error"]["message"]


def test_retired_automation_context_mcp_aliases_are_not_mapped(monkeypatch) -> None:
    client = make_client(monkeypatch)

    for name in ("get_automation_context", "member_context", "automation_member_context"):
        payload = mcp_call(client, name, {"member_id": "member_001"})

        assert payload["error"]["code"] == -32000
        assert "unknown MCP tool" in payload["error"]["message"]
