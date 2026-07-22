from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.integration_gateway.audit import list_audit_events, reset_audit_events
from aicrm_next.integration_gateway.wecom_channel_entry_client import GuardedWeComAdapter
from aicrm_next.integration_gateway.wecom_group_invite_adapter import WeComGroupInviteAdapter
from aicrm_next.media_library.application import EnsureGroupInviteBindingCommand, EnsureGroupInviteBindingReadyCommand
from aicrm_next.media_library.repo import InMemoryMediaLibraryRepository, build_media_library_repository, reset_media_library_fixture_state
from aicrm_next.shared.errors import ContractError


CHAT_ID = "wrbNXyCwAA_auto_join_way"
JOIN_URL = "https://work.weixin.qq.com/gm/auto-join-way"


class RecordingClient:
    def __init__(self) -> None:
        self.create_payloads: list[dict] = []
        self.get_config_ids: list[str] = []

    def create_group_join_way(self, payload: dict) -> dict:
        self.create_payloads.append(payload)
        return {"errcode": 0, "config_id": "cfg-auto"}

    def get_group_join_way(self, config_id: str) -> dict:
        self.get_config_ids.append(config_id)
        return {
            "errcode": 0,
            "join_way": {
                "config_id": config_id,
                "scene": 1,
                "chat_id_list": [CHAT_ID],
                "qr_code": JOIN_URL,
            },
        }


def test_audited_gateway_creates_and_reads_official_group_join_way() -> None:
    reset_audit_events()
    client = RecordingClient()
    gateway = WeComGroupInviteAdapter(client=client)

    created = gateway.create_join_way({"scene": 1, "chat_id_list": [CHAT_ID]}, idempotency_key="binding:1:create")
    loaded = gateway.get_join_way(created["config_id"], idempotency_key="binding:1:get")

    assert created["ok"] is True
    assert created["config_id"] == "cfg-auto"
    assert created["side_effect_executed"] is True
    assert loaded["join_way"]["qr_code"] == JOIN_URL
    assert client.create_payloads == [{"scene": 1, "chat_id_list": [CHAT_ID]}]
    assert client.get_config_ids == ["cfg-auto"]
    events = list_audit_events()
    assert [(event["operation"], event["status"]) for event in events] == [
        ("create_join_way", "ok"),
        ("get_join_way", "ok"),
    ]


def test_guarded_gateway_fails_closed_without_pretending_invite_exists() -> None:
    gateway = WeComGroupInviteAdapter(
        client=GuardedWeComAdapter(contact_way_reason="missing_wecom_config", missing_config=["WECOM_CONTACT_SECRET"])
    )

    result = gateway.create_join_way({"scene": 1, "chat_id_list": [CHAT_ID]})

    assert result["ok"] is False
    assert result["error_code"] == "missing_wecom_config"
    assert result["side_effect_executed"] is False
    assert result["real_external_call_executed"] is False


def test_existing_provider_config_is_retrieved_without_duplicate_create() -> None:
    repo = InMemoryMediaLibraryRepository()
    item = repo.ensure_group_invite_binding({"chat_id": CHAT_ID, "group_name": "自动邀请测试群"})
    repo.save_item("group_invite", {"config_id": "cfg-auto", "state": "aicrm_gi_existing"}, str(item["id"]))
    client = RecordingClient()
    gateway = WeComGroupInviteAdapter(client=client)

    ready = EnsureGroupInviteBindingReadyCommand(repo, gateway)(str(item["id"]))

    assert ready["binding_status"] == "ready"
    assert ready["item"]["join_url"] == JOIN_URL
    assert client.create_payloads == []
    assert client.get_config_ids == ["cfg-auto"]


def test_failed_create_releases_claim_so_next_explicit_retry_can_succeed() -> None:
    class FailingAdapter:
        def create_join_way(self, payload: dict, *, idempotency_key: str = "") -> dict:
            return {"ok": False, "error_code": "rate_limited", "retryable": True, "real_external_call_executed": True}

    repo = InMemoryMediaLibraryRepository()
    item = repo.ensure_group_invite_binding({"chat_id": CHAT_ID, "group_name": "自动邀请测试群"})

    with pytest.raises(ContractError, match="企微接口暂时繁忙"):
        EnsureGroupInviteBindingReadyCommand(repo, FailingAdapter())(str(item["id"]))
    assert repo.get_item("group_invite", str(item["id"]))["config_id"] == ""

    gateway = WeComGroupInviteAdapter(client=RecordingClient())
    result = EnsureGroupInviteBindingCommand(repo, gateway)({"chat_id": CHAT_ID, "group_name": "自动邀请测试群"})
    assert result["binding_status"] == "ready"


def test_provider_target_mismatch_never_marks_binding_ready() -> None:
    class WrongTargetAdapter:
        def create_join_way(self, payload: dict, *, idempotency_key: str = "") -> dict:
            return {"ok": True, "config_id": "cfg-wrong", "real_external_call_executed": True}

        def get_join_way(self, config_id: str, *, idempotency_key: str = "") -> dict:
            return {
                "ok": True,
                "join_way": {"chat_id_list": ["different-chat"], "qr_code": JOIN_URL},
                "real_external_call_executed": True,
            }

    repo = InMemoryMediaLibraryRepository()
    item = repo.ensure_group_invite_binding({"chat_id": CHAT_ID, "group_name": "自动邀请测试群"})

    with pytest.raises(ContractError, match="group_invite_target_mismatch"):
        EnsureGroupInviteBindingReadyCommand(repo, WrongTargetAdapter())(str(item["id"]))
    assert repo.get_item("group_invite", str(item["id"]))["binding_status"] == "pending"


def test_existing_pending_welcome_config_can_be_auto_provisioned_on_save(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_MEDIA_LIBRARY_REPO_BACKEND", "memory")
    reset_media_library_fixture_state()
    repo = build_media_library_repository()
    pending = repo.ensure_group_invite_binding({"chat_id": CHAT_ID, "group_name": "已保存的欢迎语群聊"})
    gateway = WeComGroupInviteAdapter(client=RecordingClient())
    monkeypatch.setattr(
        "aicrm_next.media_library.application.build_wecom_group_invite_adapter",
        lambda: gateway,
    )

    EnsureGroupInviteBindingReadyCommand()(str(pending["id"]))

    ready = repo.get_item("group_invite", str(pending["id"])) or {}
    assert ready["binding_status"] == "ready"
    assert ready["join_url"] == JOIN_URL


def test_channel_save_calls_group_invite_auto_provisioner() -> None:
    source = Path("aicrm_next/automation_engine/channels_api.py").read_text(encoding="utf-8")

    assert "EnsureGroupInviteBindingReadyCommand" in source
    assert 'for group_invite_id in data["welcome_group_invite_library_ids"]' in source
