from __future__ import annotations

from aicrm_next.channels.channel_entry.application import process_channel_entry
from aicrm_next.channels.channel_entry.schemas import ProcessChannelEntryCommand


def test_unknown_scene_does_not_use_live_historical_vote_or_send_effects(monkeypatch):
    calls: list[str] = []
    effect_logs: list[dict] = []
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: {"id": 1, "channel_name": "历史候选"})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_contact", lambda **kwargs: calls.append("contact") or {})
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effect_logs.append(kwargs) or kwargs)

    result = process_channel_entry(
        ProcessChannelEntryCommand(
            external_contact_id="wm-drift",
            payload_json={"State": "aqr_unknown", "WelcomeCode": "welcome"},
            follow_user_userid="HuangYouCan",
            send_welcome_message=True,
        )
    )

    assert result["handled"] is False
    assert result["reason"] == "qrcode_scene_unrecognized"
    assert result["scene_match"]["historical_vote"]["suggested_channel_id"] == 1
    assert calls == []
    assert effect_logs[0]["effect_type"] == "channel_contact"
    assert effect_logs[0]["status"] == "failed"
    assert effect_logs[0]["reason"] == "qrcode_scene_unrecognized"


def test_unacceptable_asset_blocks_side_effects(monkeypatch):
    effect_logs: list[dict] = []
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene",
        lambda corp_id, scene: {"id": 5, "channel_id": 2, "scene_value": scene, "status": "quarantined"},
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: (_ for _ in ()).throw(AssertionError("quarantined asset should stop resolution")))
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: (_ for _ in ()).throw(AssertionError("historical vote should not run")))
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: effect_logs.append(kwargs) or kwargs)

    result = process_channel_entry(
        ProcessChannelEntryCommand(
            external_contact_id="wm-quarantine",
            payload_json={"State": "aqr_bad", "WelcomeCode": "welcome"},
            follow_user_userid="HuangYouCan",
            send_welcome_message=True,
        )
    )

    assert result["handled"] is False
    assert result["reason"] == "qrcode_asset_not_acceptable"
    assert effect_logs[0]["reason"] == "qrcode_asset_not_acceptable"
