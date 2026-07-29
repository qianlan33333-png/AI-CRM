from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.channels.channel_entry.application import diagnose_channel_runtime
from aicrm_next.channels.channel_entry.schemas import DiagnoseChannelRuntimeQuery
from aicrm_next.channels.channel_entry.wecom_adapter import set_wecom_adapter
from aicrm_next.main import create_app


def test_runtime_diagnosis_route_is_next_native(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.diagnose_channel_runtime", lambda query: {"ok": True, "callback_route_owner": "aicrm_next.channels.channel_entry", "scene": query.scene_value})

    response = TestClient(create_app(), raise_server_exceptions=False).get("/api/admin/channels/runtime-diagnosis?scene_value=s1")

    assert response.status_code == 200
    assert response.json()["callback_route_owner"] == "aicrm_next.channels.channel_entry"


def test_dry_run_and_repair_routes_are_next_native(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.dry_run_channel_entry", lambda command: {"dry_run": True, "would_actions": {}, "external": command.external_contact_id})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.repair_channel_entry", lambda command: {"handled": True, "welcome_repair": {"reason": "welcome_code_unavailable_or_expired"}})
    client = TestClient(create_app(), raise_server_exceptions=False)

    dry = client.post("/api/admin/channels/runtime-diagnosis/dry-run", json={"external_userid": "wm", "scene_value": "s1"})
    repair = client.post("/api/admin/channels/repair-entry", json={"external_userid": "wm", "scene_value": "s1"})

    assert dry.status_code == 200
    assert dry.json()["planned_actions"]["dry_run"] is True
    assert repair.status_code == 200
    assert repair.json()["source"] == "aicrm_next.channels.channel_entry"


def test_runtime_diagnosis_reports_real_adapter_readiness(monkeypatch):
    channel = {
        "id": 10,
        "channel_code": "c",
        "channel_name": "C",
        "scene_value": "scene-a",
        "status": "active",
        "welcome_message": "欢迎加入",
        "entry_tag_id": "tag-a",
    }
    monkeypatch.setenv("AICRM_NEXT_WECOM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.delenv("WECOM_CONTACT_SECRET", raising=False)
    monkeypatch.delenv("WECOM_SECRET", raising=False)
    set_wecom_adapter(None)
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene",
        lambda corp_id, scene: {
            **channel,
            "id": 88,
            "channel_id": 10,
            "channel_row_id": 10,
            "channel_scene_value": "scene-a",
            "channel_status": "active",
            "status": "active",
            "scene_value": scene,
        },
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.touch_qrcode_asset_callback", lambda asset_id: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_channel_scene_aliases", lambda channel_id: [{"id": 1, "scene_value": "scene-a", "source": "current_scene"}])
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_channel_entry_effect_logs", lambda **kwargs: [{"effect_type": "welcome_message", "reason": "missing_wecom_config"}])
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_recent_events", lambda scene, limit=20: [])

    result = diagnose_channel_runtime(DiagnoseChannelRuntimeQuery(scene_value="scene-a"))

    assert result["callback_route_owner"] == "aicrm_next.channels.channel_entry"
    assert result["real_wecom_adapter_enabled"] is False
    assert result["real_wecom_adapter_reason"] == "missing_wecom_config"
    assert result["can_send_welcome"] is False
    assert result["can_mark_tag"] is False
    assert result["can_create_contact_way"] is False
    assert result["missing_config"] == ["WECOM_CONTACT_SECRET"]
    assert "expected_program_admission_result" not in result
