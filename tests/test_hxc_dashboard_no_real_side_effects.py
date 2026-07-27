from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "hxc-dashboard-no-side-effects-test")
    reset_hxc_safe_mode_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_all_hxc_side_effect_routes_expose_false_execution_flags(monkeypatch) -> None:
    client = _client(monkeypatch)
    responses = [
        client.post("/api/admin/hxc-dashboard/refresh", json={"trigger_source": "pytest"}),
        client.post("/api/admin/hxc-dashboard/refresh-directory", json={}),
        client.post("/api/admin/hxc-dashboard/broadcast", json={"external_userids": ["wx_ext_001"], "content": "hello"}),
    ]

    for response in responses:
        assert response.status_code == 200
        body = response.json()
        assert body["fallback_used"] is False
        assert body["real_external_call_executed"] is False
        assert body["hxc_refresh_executed"] is False
        assert body["directory_sync_executed"] is False
        assert body["hxc_broadcast_executed"] is False
        assert body["wecom_send_executed"] is False
        assert body["wecom_api_called"] is False
        assert body["external_call_attempt"]["status"] == "blocked"


def test_hxc_next_module_has_no_direct_legacy_or_external_call_markers() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "aicrm_next/extensions/hxc/hxc_dashboard").glob("*.py"))
    forbidden_parts = [
        "forward_to_" + "legacy_flask",
        "wecom_" + "ability_service",
        "refresh_hxc_" + "dashboard_snapshot",
        "sync_admin_" + "wecom_directory_members",
        "broadcast_to_" + "filtered_users",
        "requests.",
        "http" + "x.",
        "Open" + "Claw",
        "WeCom" + "Client",
        "access_" + "token",
    ]

    assert [marker for marker in forbidden_parts if marker in source] == []
