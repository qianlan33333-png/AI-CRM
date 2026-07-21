from __future__ import annotations

from aicrm_next.channel_entry.application import process_channel_entry
from aicrm_next.channel_entry.schemas import ProcessChannelEntryCommand
from aicrm_next.channel_entry.wecom_adapter import get_wecom_adapter, set_wecom_adapter


def _base(monkeypatch, channel):
    sent = []
    effects = []
    monkeypatch.setattr("aicrm_next.channel_entry.application.resolve_channel_for_scene", lambda **kwargs: (channel, {"match_type": "current_scene", "matched_scene": "scene-a", "channel_id": 10}))
    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_contact", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("aicrm_next.channel_entry.repo.get_channel_entry_effect_log", lambda *args: None)
    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effects.append(kwargs) or {"ok": True})
    monkeypatch.setattr("aicrm_next.channel_entry.repo.save_tag_snapshot", lambda *args, **kwargs: None)

    class WelcomeGraph:
        def plan(self, request):
            return {
                "execution_id": f"welcome-{request.external_userid}",
                "status": "waiting_dependencies" if any(item.get("material_id") for item in request.attachments) else "ready",
                "final_effect_job_id": 9001,
                "external_effect_job_ids": [9001],
                "upload_effect_job_ids": [],
                "status_url": f"/api/admin/executions/welcome-{request.external_userid}",
                "duplicate": False,
            }

    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.build_welcome_effect_graph_repository",
        lambda: WelcomeGraph(),
    )

    class Adapter:
        def send_welcome_msg(self, payload):
            sent.append(payload)
            return {"errcode": 0}

        def mark_external_contact_tags(self, **payload):
            return {"errcode": 0}

    previous = get_wecom_adapter()
    set_wecom_adapter(Adapter())
    return sent, effects, previous


def test_welcome_supports_text_image_file_miniprogram(monkeypatch):
    channel = {"id": 10, "scene_value": "scene-a", "status": "active", "owner_staff_id": "sales", "welcome_message": "hello", "entry_tag_id": "", "welcome_image_library_ids": [1], "welcome_attachment_library_ids": [2], "welcome_miniprogram_library_ids": [3], "welcome_group_invite_library_ids": [4]}
    sent, effects, previous = _base(monkeypatch, channel)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-wm", external_contact_id="wm", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["welcome_message"]["queued"] is True
    assert sent == []
    welcome_effect = next(row for row in effects if row["effect_type"] == "welcome_message")
    assert [item["msgtype"] for item in welcome_effect["request_json"]["attachments"]] == ["image", "file", "miniprogram", "link"]


def test_welcome_renders_customer_name_placeholder_from_identity_name(monkeypatch):
    channel = {"id": 10, "scene_value": "scene-a", "status": "active", "owner_staff_id": "sales", "welcome_message": "你好啊，{{客户名}}测试客户名", "entry_tag_id": ""}
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.resolve_external_contact_customer_name",
        lambda external_userid, **kwargs: "刘惠福",
    )
    sent, effects, previous = _base(monkeypatch, channel)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-wm", external_contact_id="wm", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["welcome_message"]["queued"] is True
    assert sent == []
    welcome_effect = next(row for row in effects if row["effect_type"] == "welcome_message")
    assert welcome_effect["request_json"]["text"]["content"] == "你好啊，刘惠福测试客户名"


def test_welcome_customer_name_placeholder_is_empty_when_identity_name_missing(monkeypatch):
    channel = {"id": 10, "scene_value": "scene-a", "status": "active", "owner_staff_id": "sales", "welcome_message": "你好啊，{{ 客户名 }}", "entry_tag_id": ""}
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.resolve_external_contact_customer_name",
        lambda external_userid, **kwargs: "",
    )
    sent, effects, previous = _base(monkeypatch, channel)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-wm", external_contact_id="wm_external_id", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["welcome_message"]["queued"] is True
    assert sent == []
    welcome_effect = next(row for row in effects if row["effect_type"] == "welcome_message")
    assert welcome_effect["request_json"]["text"]["content"] == "你好啊，"


def test_welcome_attachment_limit_failed(monkeypatch):
    channel = {"id": 10, "scene_value": "scene-a", "status": "active", "owner_staff_id": "sales", "welcome_message": "hello", "entry_tag_id": "", "welcome_image_library_ids": [1, 2, 3, 4], "welcome_attachment_library_ids": [5, 6, 7], "welcome_miniprogram_library_ids": [8, 9, 10]}
    sent, effects, previous = _base(monkeypatch, channel)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-wm", external_contact_id="wm", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["welcome_message"]["sent"] is False
    assert result["welcome_message"]["reason"] == "attachment_limit_exceeded"
    assert sent == []
