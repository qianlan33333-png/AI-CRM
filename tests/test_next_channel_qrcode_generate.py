from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aicrm_next.channels.channel_entry.application import generate_channel_qrcode
from aicrm_next.channels.channel_entry.schemas import GenerateChannelQrCodeCommand
from aicrm_next.channels.channel_entry.wecom_adapter import get_wecom_adapter, set_wecom_adapter
from aicrm_next.main import create_app


def _iter_api_routes(routes):
    for route in routes:
        original_router = getattr(route, "original_router", None)
        nested = getattr(route, "routes", None) or getattr(original_router, "routes", None)
        if nested:
            yield from _iter_api_routes(nested)
        elif isinstance(route, APIRoute):
            yield route


def test_generate_channel_qrcode_calls_wecom_and_writes_scene_alias(monkeypatch):
    channel = {
        "id": 101,
        "channel_name": "报名渠道",
        "channel_code": "signup",
        "channel_type": "qrcode",
        "carrier_type": "qrcode",
        "scene_value": "old-scene",
        "qr_url": "https://old-qr",
        "owner_staff_id": "HuangYouCan",
        "auto_accept_friend": True,
        "status": "active",
    }
    aliases: list[dict] = []
    effects: list[dict] = []
    updated: dict = {}
    assets: list[dict] = []
    retired: list[int] = []
    payloads: list[dict] = []

    class Adapter:
        def create_contact_way(self, payload):
            payloads.append(payload)
            return {"errcode": 0, "config_id": "cfg-next", "qr_code": "https://wework.qpic.cn/next"}

    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.get_channel_by_id", lambda channel_id: channel if channel_id == 101 else None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_scene_alias", lambda **kwargs: aliases.append(kwargs) or {"id": len(aliases)})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.retire_active_qrcode_assets", lambda channel_id, **kwargs: retired.append((channel_id, kwargs)) or 1)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.insert_qrcode_asset", lambda **kwargs: assets.append(kwargs) or {"id": 55, **kwargs})

    def update_qrcode(**kwargs):
        updated.update(kwargs)
        return {**channel, "scene_value": kwargs["scene_value"], "qr_url": kwargs["qr_url"], "qr_ticket": kwargs["config_id"]}

    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.update_channel_qrcode", update_qrcode)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effects.append(kwargs) or kwargs)
    previous = get_wecom_adapter()
    set_wecom_adapter(Adapter())
    try:
        result = generate_channel_qrcode(GenerateChannelQrCodeCommand(channel_id=101, scene_value="aqr_260531_abcd"))
    finally:
        set_wecom_adapter(previous)

    assert result["ok"] is True
    assert result["source"] == "aicrm_next.channels.channel_entry"
    assert result["route_owner"] == "ai_crm_next"
    assert result["scene_value"] == "aqr_260531_abcd"
    assert result["config_id"] == "cfg-next"
    assert payloads == [{"type": 1, "scene": 2, "style": 1, "skip_verify": True, "state": "aqr_260531_abcd", "user": ["HuangYouCan"]}]
    assert updated == {"channel_id": 101, "scene_value": "aqr_260531_abcd", "qr_url": "https://wework.qpic.cn/next", "config_id": "cfg-next"}
    assert retired == [(101, {"except_asset_id": 55})]
    assert assets[0]["channel_id"] == 101
    assert assets[0]["scene_value"] == "aqr_260531_abcd"
    assert assets[0]["status"] == "active"
    assert result["qrcode_asset_id"] == 55
    assert aliases[0]["scene_value"] == "old-scene"
    assert aliases[0]["status"] == "retired"
    assert aliases[1]["scene_value"] == "aqr_260531_abcd"
    assert aliases[1]["source"] == "next_create_contact_way"
    assert effects[0]["effect_type"] == "qrcode_generate"
    assert effects[0]["status"] == "success"


def test_generate_channel_qrcode_uses_multi_staff_assignees(monkeypatch):
    channel = {
        "id": 102,
        "channel_name": "多客服渠道",
        "channel_code": "multi",
        "channel_type": "qrcode",
        "carrier_type": "qrcode",
        "scene_value": "",
        "qr_url": "",
        "owner_staff_id": "",
        "assignment_mode": "multi_staff",
        "auto_accept_friend": False,
        "status": "active",
    }
    aliases: list[dict] = []
    effects: list[dict] = []
    assets: list[dict] = []
    payloads: list[dict] = []

    class Adapter:
        def create_contact_way(self, payload):
            payloads.append(payload)
            return {"errcode": 0, "config_id": "cfg-multi", "qr_code": "https://wework.qpic.cn/multi"}

    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.get_channel_by_id", lambda channel_id: channel if channel_id == 102 else None)
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.list_channel_assignees",
        lambda channel_id, active_only=True: [
            {"staff_id": "StaffA", "status": "active"},
            {"staff_id": "StaffB", "status": "active"},
        ],
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_scene_alias", lambda **kwargs: aliases.append(kwargs) or {"id": len(aliases)})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.retire_active_qrcode_assets", lambda channel_id, **kwargs: 1)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.insert_qrcode_asset", lambda **kwargs: assets.append(kwargs) or {"id": 56, **kwargs})
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.update_channel_qrcode",
        lambda **kwargs: {**channel, "scene_value": kwargs["scene_value"], "qr_url": kwargs["qr_url"], "qr_ticket": kwargs["config_id"]},
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effects.append(kwargs) or kwargs)
    previous = get_wecom_adapter()
    set_wecom_adapter(Adapter())
    try:
        result = generate_channel_qrcode(GenerateChannelQrCodeCommand(channel_id=102, scene_value="aqr_multi"))
    finally:
        set_wecom_adapter(previous)

    assert result["ok"] is True
    assert result["provider_payload_user_count"] == 2
    assert payloads == [{"type": 2, "scene": 2, "style": 1, "skip_verify": False, "state": "aqr_multi", "user": ["StaffA", "StaffB"]}]
    assert assets[0]["channel_id"] == 102
    assert aliases[0]["scene_value"] == "aqr_multi"
    assert effects[0]["status"] == "success"


def test_generate_qrcode_route_is_next_channel_entry_owned(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.api.generate_channel_qrcode",
        lambda command: {
            "ok": True,
            "channel_id": command.channel_id,
            "scene_value": command.scene_value,
            "config_id": "cfg",
            "qr_url": "https://qr",
            "source": "aicrm_next.channels.channel_entry",
            "route_owner": "ai_crm_next",
        },
    )

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post("/api/admin/channels/7/qrcode/generate", json={"scene_value": "aqr_test"})

    assert response.status_code == 200
    assert response.json()["source"] == "aicrm_next.channels.channel_entry"
    paths = {route.path: route.endpoint.__module__ for route in _iter_api_routes(client.app.routes)}
    assert paths["/api/admin/channels/{channel_id:int}/qrcode/generate"] == "aicrm_next.channels.channel_entry.api"
