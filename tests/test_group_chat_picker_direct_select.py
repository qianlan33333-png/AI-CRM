from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.automation_engine.group_ops.picker_application import ListGroupChatPickerQuery, SyncGroupChatPickerCommand
from aicrm_next.automation_engine.group_ops.dto import GroupChatPickerSyncRequest
from aicrm_next.automation_engine.group_ops.material_resolver import GroupOpsMaterialResolveError, InMemoryGroupOpsMaterialResolver
from aicrm_next.automation_engine.group_ops.repo import InMemoryGroupOpsRepository
from aicrm_next.media_library.application import EnsureGroupInviteBindingCommand
from aicrm_next.media_library.dto import GroupInviteBindingEnsureRequest
from aicrm_next.media_library.repo import InMemoryMediaLibraryRepository, reset_media_library_fixture_state
from aicrm_next.send_content.application import assert_group_invite_bindings_ready, normalize_send_content_package
from aicrm_next.send_content.repo import InMemorySendContentRepository
from aicrm_next.shared.errors import ContractError


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CHAT_ID = "wrbNXyCwAAm0Vx7_OVQ_-PkT6Exeg8pg"
EXPERIENCE_CHAT_ID = "wrbNXyCwAAnxf9Xlmdxcipk24E-dzAgw"


class FakeGroupInviteAdapter:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.get_calls: list[str] = []

    def create_join_way(self, payload: dict, *, idempotency_key: str = "") -> dict:
        self.create_calls.append({"payload": payload, "idempotency_key": idempotency_key})
        return {
            "ok": True,
            "config_id": f"config-{payload['chat_id_list'][0]}",
            "real_external_call_executed": True,
        }

    def get_join_way(self, config_id: str, *, idempotency_key: str = "") -> dict:
        self.get_calls.append(config_id)
        chat_id = config_id.removeprefix("config-")
        return {
            "ok": True,
            "join_way": {
                "config_id": config_id,
                "scene": 1,
                "chat_id_list": [chat_id],
                "qr_code": f"https://work.weixin.qq.com/gm/{chat_id}",
            },
            "real_external_call_executed": True,
        }


def _group(chat_id: str, name: str, member_count: int) -> dict:
    return {
        "chat_id": chat_id,
        "group_name": name,
        "owner_userid": "HuangYouCan",
        "owner_name": "HuangYouCan",
        "internal_member_count": 1,
        "external_member_count": member_count - 1,
        "status": "active",
    }


class PagedGroupAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = "") -> dict:
        self.calls.append(cursor)
        if not cursor:
            return {
                "ok": True,
                "mode": "production",
                "groups": [_group(FORMAL_CHAT_ID, "老黄的AI+进化同行圈", 115)],
                "next_cursor": "page-2",
                "warnings": [],
            }
        return {
            "ok": True,
            "mode": "production",
            "groups": [_group(EXPERIENCE_CHAT_ID, "老黄的AI+进化同行圈体验版", 172)],
            "next_cursor": "",
            "warnings": [],
        }


def test_picker_syncs_all_cursor_pages_and_preserves_plus_in_search(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_MEDIA_LIBRARY_REPO_BACKEND", "memory")
    reset_media_library_fixture_state()
    repo = InMemoryGroupOpsRepository(seed_groups=False)
    adapter = PagedGroupAdapter()

    result = SyncGroupChatPickerCommand(repo=repo, sync_adapter=adapter)(
        GroupChatPickerSyncRequest(owner_userid="HuangYouCan", limit=100)
    )

    assert result["ok"] is True
    assert adapter.calls == ["", "page-2"]
    by_name = {item["group_name"]: item for item in result["items"]}
    assert by_name["老黄的AI+进化同行圈"]["member_count"] == 115
    assert "老黄的AI+进化同行圈体验版" in by_name
    exact = ListGroupChatPickerQuery(repo)(owner_userid="HuangYouCan", keyword="老黄的AI+进化同行圈")
    assert [item["group_name"] for item in exact["items"]] == ["老黄的AI+进化同行圈", "老黄的AI+进化同行圈体验版"]
    partial = ListGroupChatPickerQuery(repo)(owner_userid="HuangYouCan", keyword="同行圈")
    assert len(partial["items"]) == 2


def test_group_selection_auto_provisions_once_and_keeps_stable_binding_id() -> None:
    repo = InMemoryMediaLibraryRepository()
    adapter = FakeGroupInviteAdapter()
    ensured = EnsureGroupInviteBindingCommand(repo, adapter)(
        GroupInviteBindingEnsureRequest(
            chat_id=FORMAL_CHAT_ID,
            group_name="老黄的AI+进化同行圈",
            owner_userid="HuangYouCan",
            owner_name="HuangYouCan",
            member_count=115,
        )
    )
    ensured_again = EnsureGroupInviteBindingCommand(repo, adapter)(
        GroupInviteBindingEnsureRequest(chat_id=FORMAL_CHAT_ID, group_name="老黄的AI+进化同行圈")
    )
    binding_id = int(ensured["binding_id"])

    assert binding_id == int(ensured_again["binding_id"])
    assert ensured["item"]["binding_status"] == "ready"
    assert ensured["item"]["join_url"].startswith("https://work.weixin.qq.com/gm/")
    assert len(adapter.create_calls) == 1
    assert len(adapter.get_calls) == 1
    assert adapter.create_calls[0]["payload"]["scene"] == 1
    assert adapter.create_calls[0]["payload"]["auto_create_room"] == 0
    package = normalize_send_content_package(
        {"content_text": "欢迎进群", "group_invite_library_ids": [binding_id]},
        require_body=True,
    )
    assert package["group_invite_library_ids"] == [binding_id]

    pending_item = {
        **(repo.get_item("group_invite", str(binding_id)) or {}),
        "join_url": "",
        "binding_status": "pending",
    }
    pending_send_repo = InMemorySendContentRepository(
        {
            "image": [],
            "miniprogram": [],
            "attachment": [],
            "group_invite": [
                {
                    "type": "group_invite",
                    "library_id": binding_id,
                    "title": pending_item["title"],
                    "subtitle": "选用时由系统自动生成邀请",
                    "thumbnail_url": "",
                    "enabled": True,
                    "metadata": {"join_url": "", "binding_status": "pending"},
                }
            ],
        }
    )
    with pytest.raises(ContractError, match="group_invite_not_ready"):
        assert_group_invite_bindings_ready(package, repo=pending_send_repo)
    resolver = InMemoryGroupOpsMaterialResolver(items={"group_invite": {binding_id: pending_item}})
    with pytest.raises(GroupOpsMaterialResolveError, match="group_invite_not_ready"):
        resolver.resolve_content_package_materials(package)

    updated = ensured
    ready_send_repo = InMemorySendContentRepository(
        {
            "image": [],
            "miniprogram": [],
            "attachment": [],
            "group_invite": [
                {
                    "type": "group_invite",
                    "library_id": binding_id,
                    "title": ensured["item"]["title"],
                    "subtitle": "点击卡片直接加入群聊",
                    "thumbnail_url": "",
                    "enabled": True,
                    "metadata": {"join_url": ensured["item"]["join_url"], "binding_status": "ready"},
                }
            ],
        }
    )
    assert_group_invite_bindings_ready(package, repo=ready_send_repo)
    ready_resolver = InMemoryGroupOpsMaterialResolver(items={"group_invite": {binding_id: updated["item"]}})
    attachments, media_ids = ready_resolver.resolve_content_package_materials(package)
    assert media_ids == []
    assert attachments == [
        {
            "msgtype": "link",
            "link": {
                "title": "加入「老黄的AI+进化同行圈」",
                "url": ensured["item"]["join_url"],
                "desc": "点击卡片直接加入群聊",
            },
        }
    ]


def test_group_dissolution_hides_group_and_invalidates_existing_binding(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_MEDIA_LIBRARY_REPO_BACKEND", "memory")
    reset_media_library_fixture_state()
    group_repo = InMemoryGroupOpsRepository(seed_groups=False)
    first = PagedGroupAdapter()
    SyncGroupChatPickerCommand(repo=group_repo, sync_adapter=first)(
        GroupChatPickerSyncRequest(owner_userid="HuangYouCan")
    )

    from aicrm_next.media_library.repo import build_media_library_repository

    media_repo = build_media_library_repository()
    adapter = FakeGroupInviteAdapter()
    binding = EnsureGroupInviteBindingCommand(media_repo, adapter)(
        GroupInviteBindingEnsureRequest(chat_id=FORMAL_CHAT_ID, group_name="老黄的AI+进化同行圈")
    )["item"]

    class ExperienceOnlyAdapter:
        def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = "") -> dict:
            return {"ok": True, "mode": "production", "groups": [_group(EXPERIENCE_CHAT_ID, "老黄的AI+进化同行圈体验版", 172)], "next_cursor": "", "warnings": []}

    after = SyncGroupChatPickerCommand(repo=group_repo, sync_adapter=ExperienceOnlyAdapter())(
        GroupChatPickerSyncRequest(owner_userid="HuangYouCan")
    )
    assert after["sync"]["inactive_count"] == 1
    assert [item["chat_id"] for item in after["items"]] == [EXPERIENCE_CHAT_ID]
    invalid = media_repo.get_item("group_invite", str(binding["id"])) or {}
    assert invalid["binding_status"] == "invalid"


def test_shared_picker_is_a_pure_searchable_list_and_all_surfaces_load_it() -> None:
    picker = (ROOT / "aicrm_next/frontend_compat/static/admin_console/group_chat_picker.js").read_text(encoding="utf-8")
    material_picker = (ROOT / "aicrm_next/frontend_compat/static/admin_console/material_picker.js").read_text(encoding="utf-8")
    templates = [
        ROOT / "aicrm_next/automation_engine/templates/admin_console/channel_code_form.html",
        ROOT / "aicrm_next/frontend_compat/templates/admin_console/cloud_campaigns_workspace.html",
        ROOT / "aicrm_next/frontend_compat/templates/admin_console/hxc_dashboard.html",
        ROOT / "aicrm_next/automation_agents/templates/admin_console/automation_agent_edit.html",
        ROOT / "aicrm_next/automation_engine/group_ops/templates/admin_console/group_ops.html",
    ]

    assert "AICRMGroupChatPicker" in material_picker
    assert "window.AdminApi.requestJson" in picker
    assert "aicrm-group-chat-picker__list" in picker
    assert "aicrm-group-chat-picker__row" in picker
    assert "member_count" in picker
    assert "thumbnail" not in picker.lower()
    assert "客户群</span>" not in picker
    assert "管理群邀请设置" not in picker
    for template in templates:
        source = template.read_text(encoding="utf-8")
        assert "group_chat_picker.js" in source
        assert source.index("group_chat_picker.js") < source.index("material_picker.js")
