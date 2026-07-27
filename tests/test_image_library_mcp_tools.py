from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.channels.integration_gateway.mcp import MCP_TOOLS
from aicrm_next.main import create_app
from aicrm_next.engagement.media_library.repo import reset_media_library_fixture_state


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdac`\x00\x01\x00\x00\x07\x00\x01\xe9\x15\x08-"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-image-mcp-test")
    reset_media_library_fixture_state()
    return TestClient(create_app())


def assert_json_contract(payload: dict) -> None:
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_legacy_image_library_mcp_tools_are_not_current_mcp_surface() -> None:
    names = {tool["name"] for tool in MCP_TOOLS}

    assert names == {"resolve_customer", "get_customer_context", "get_recent_messages"}
    assert not any(name.startswith("image_library_") for name in names)


def test_image_library_next_api_replaces_legacy_media_mcp_write_contract(monkeypatch) -> None:
    client = make_client(monkeypatch)

    uploaded = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("mcp-replacement.png", BytesIO(TINY_PNG), "image/png")},
        data={"name": "mcp replacement", "tags": "mcp,media"},
    ).json()
    assert_json_contract(uploaded)
    assert uploaded["source_status"] == "local_upload"
    assert uploaded["side_effect_plan"]["external_storage"] == "not_executed"
    assert uploaded["side_effect_plan"]["wecom_media_upload"] == "not_executed"

    facets = client.get("/api/admin/image-library/facets").json()
    assert_json_contract(facets)
    assert {"mcp", "media"} <= set(facets["tags"])


def test_mcp_unknown_legacy_media_tool_returns_structured_error(monkeypatch) -> None:
    client = make_client(monkeypatch)

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "image_library_list", "arguments": {}},
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["error"]["code"] == -32000
    assert "unknown MCP tool" in payload["error"]["message"]
