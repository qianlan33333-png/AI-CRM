from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.media_library.dto import GroupInviteUpsertRequest
from aicrm_next.media_library.repo import InMemoryMediaLibraryRepository


VALID_JOIN_URL = "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef"


class FakeGroupInviteAdapter:
    def __init__(self) -> None:
        self.create_calls = 0

    def create_join_way(self, payload: dict, *, idempotency_key: str = "") -> dict:
        self.create_calls += 1
        return {"ok": True, "config_id": f"config-{payload['chat_id_list'][0]}", "real_external_call_executed": True}

    def get_join_way(self, config_id: str, *, idempotency_key: str = "") -> dict:
        chat_id = config_id.removeprefix("config-")
        return {
            "ok": True,
            "join_way": {"config_id": config_id, "chat_id_list": [chat_id], "qr_code": f"https://work.weixin.qq.com/gm/{chat_id}"},
            "real_external_call_executed": True,
        }


def test_group_invite_request_accepts_wecom_gm_url_and_rejects_other_links() -> None:
    request = GroupInviteUpsertRequest(
        name="体验群邀请",
        title="点击加入体验群",
        description="进群领取资料",
        join_url=VALID_JOIN_URL,
        config_id="join-way-1",
        chat_id_list=["wr_group_1"],
    )

    assert request.join_url == VALID_JOIN_URL
    assert request.title == "点击加入体验群"

    with pytest.raises(ValueError, match="work.weixin.qq.com/gm"):
        GroupInviteUpsertRequest(title="错误链接", join_url="https://example.com/group")


def test_group_invite_in_memory_repository_crud() -> None:
    repo = InMemoryMediaLibraryRepository()
    created = repo.save_item(
        "group_invite",
        {
            "name": "体验群邀请",
            "title": "点击加入体验群",
            "description": "进群领取资料",
            "join_url": VALID_JOIN_URL,
            "pic_url": "https://example.com/group-cover.png",
            "config_id": "join-way-1",
            "state": "campaign-a",
            "chat_id_list": ["wr_group_1"],
            "auto_create_room": True,
            "room_base_name": "体验群",
            "room_base_id": 10,
            "enabled": True,
        },
    )

    assert created["join_url"] == VALID_JOIN_URL
    listed = repo.list_items("group_invite", limit=20, offset=0, filters={"enabled_only": True})
    assert any(item["id"] == created["id"] for item in listed["items"])

    updated = repo.save_item("group_invite", {"description": "更新后的描述", "enabled": False}, str(created["id"]))
    assert updated["description"] == "更新后的描述"
    assert updated["enabled"] is False

    deleted = repo.delete_item("group_invite", str(created["id"]))
    assert deleted["deleted"] is True


def test_group_invite_admin_api_and_page_contract() -> None:
    client = TestClient(create_app())

    page = client.get("/admin/group-invite-library")
    assert page.status_code == 200
    assert "群邀请托管" in page.text
    assert "已同步客户群" in page.text
    assert "/api/admin/automation-conversion/group-ops/groups" in page.text
    assert "无需填写链接或参数" in page.text
    assert "首次选用时自动生成" in page.text
    assert "立即生成" not in page.text
    assert "企业微信「加入群聊」链接" not in page.text
    assert "work.weixin.qq.com/gm" not in page.text
    assert "素材名称" not in page.text
    assert "卡片标题" not in page.text
    assert "企微入群方式 config_id" not in page.text
    assert "卡片封面 URL" not in page.text

    created = client.post(
        "/api/admin/group-invite-library",
        json={
            "name": "API 体验群",
            "title": "点击加入 API 体验群",
            "description": "无需扫码",
            "join_url": VALID_JOIN_URL,
            "chat_id_list": ["wr_group_1"],
            "enabled": True,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["ok"] is True
    assert payload["real_external_call_executed"] is False
    item_id = payload["item"]["id"]

    listed = client.get("/api/admin/group-invite-library?enabled_only=false").json()
    assert any(str(item["id"]) == str(item_id) for item in listed["items"])

    updated = client.put(
        f"/api/admin/group-invite-library/{item_id}",
        json={"description": "已更新", "enabled": False},
    ).json()
    assert updated["item"]["description"] == "已更新"

    deleted = client.delete(f"/api/admin/group-invite-library/{item_id}").json()
    assert deleted["deleted"] is True


def test_group_invite_binding_ensure_auto_provisions_and_keeps_update_alias(monkeypatch) -> None:
    adapter = FakeGroupInviteAdapter()
    monkeypatch.setattr(
        "aicrm_next.media_library.application.build_wecom_group_invite_adapter",
        lambda: adapter,
    )
    client = TestClient(create_app())
    payload = {
        "chat_id": "wr_formal_hxc_group",
        "group_name": "老黄的AI+进化同行圈",
        "owner_userid": "HuangYouCan",
        "owner_name": "HuangYouCan",
        "member_count": 115,
    }
    first = client.post("/api/admin/group-invite-bindings/ensure", json=payload)
    second = client.post("/api/admin/group-invite-bindings/ensure", json=payload)

    assert first.status_code == 200
    assert first.json()["binding_status"] == "ready"
    assert first.json()["item"]["join_url"].startswith("https://work.weixin.qq.com/gm/")
    assert first.json()["real_external_call_executed"] is True
    assert first.json()["binding_id"] == second.json()["binding_id"]
    assert adapter.create_calls == 1
    binding_id = first.json()["binding_id"]
    assert client.get(f"/api/admin/group-invite-bindings/{binding_id}").json()["item"]["chat_id"] == payload["chat_id"]

    updated = client.put(
        f"/api/admin/group-invite-bindings/{binding_id}",
        json={"join_url": "https://work.weixin.qq.com/gm/formal-hxc-group", "enabled": True},
    ).json()
    assert updated["binding_status"] == "ready"
    assert updated["item"]["join_url"].endswith("/formal-hxc-group")
    assert client.get(f"/api/admin/group-invite-library/{binding_id}").json()["item"]["binding_status"] == "ready"


def test_group_chat_material_picker_delegates_to_direct_group_picker() -> None:
    material_source = (
        Path(__file__).resolve().parents[1]
        / "aicrm_next/frontend_compat/static/admin_console/material_picker.js"
    ).read_text(encoding="utf-8")
    group_source = (
        Path(__file__).resolve().parents[1]
        / "aicrm_next/frontend_compat/static/admin_console/group_chat_picker.js"
    ).read_text(encoding="utf-8")

    assert "window.AICRMGroupChatPicker.open(options)" in material_source
    assert "/api/admin/automation-conversion/group-ops/group-picker" in group_source
    assert "/api/admin/group-invite-bindings/ensure" in group_source
    assert "管理群邀请设置" not in group_source
