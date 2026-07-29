from __future__ import annotations

from aicrm_next.channels.channel_entry.application import process_channel_entry, resolve_channel_for_scene
from aicrm_next.channels.channel_entry.schemas import ProcessChannelEntryCommand


def test_callback_state_follows_asset_owner_not_page_context(monkeypatch):
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene",
        lambda corp_id, scene: {
            "id": 31,
            "channel_id": 1,
            "channel_row_id": 1,
            "scene_value": scene,
            "channel_scene_value": "aqr_channel_1",
            "channel_status": "active",
            "status": "active",
            "channel_name": "所有已经报名9.9的",
        },
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.touch_qrcode_asset_callback", lambda asset_id: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: None)

    channel, match = resolve_channel_for_scene(scene_value="aqr_260522_d7fa", corp_id="ww")

    assert channel["id"] == 1
    assert match["match_type"] == "qrcode_asset_active"
    assert match["qrcode_asset_id"] == 31


def test_unrecognized_production_drift_state_sends_no_wrong_welcome(monkeypatch):
    effect_logs: list[dict] = []
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: {"id": 1, "channel_name": "所有已经报名9.9的"})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effect_logs.append(kwargs) or kwargs)

    result = process_channel_entry(
        ProcessChannelEntryCommand(
            external_contact_id="wm-real-drift",
            payload_json={"State": "aqr_260522_d7fa", "WelcomeCode": "welcome"},
            follow_user_userid="HuangYouCan",
            send_welcome_message=True,
        )
    )

    assert result["handled"] is False
    assert result["reason"] == "qrcode_scene_unrecognized"
    assert effect_logs[0]["reason"] == "qrcode_scene_unrecognized"
