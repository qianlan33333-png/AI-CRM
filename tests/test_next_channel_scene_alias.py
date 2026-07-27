from __future__ import annotations

from aicrm_next.channels.channel_entry.application import resolve_channel_for_scene


def test_active_qrcode_asset_resolves_channel(monkeypatch):
    touched: list[int] = []
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene",
        lambda corp_id, scene: {
            "id": 10,
            "channel_id": 1,
            "channel_row_id": 1,
            "scene_value": scene,
            "channel_scene_value": scene,
            "status": "active",
            "channel_status": "active",
            "channel_name": "报名渠道",
        },
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.touch_qrcode_asset_callback", lambda asset_id: touched.append(asset_id))
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: (_ for _ in ()).throw(AssertionError("asset should win")))
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: (_ for _ in ()).throw(AssertionError("historical vote is not live path")))

    channel, match = resolve_channel_for_scene(scene_value="scene-current", corp_id="ww")

    assert channel["id"] == 1
    assert match["match_type"] == "qrcode_asset_active"
    assert match["qrcode_asset_id"] == 10
    assert touched == [10]


def test_confirmed_alias_resolves_when_asset_missing(monkeypatch):
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene", lambda corp_id, scene: None)
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias",
        lambda corp_id, scene: {"id": 2, "scene_alias_id": 9, "scene_alias_status": "active", "scene_alias_source": "legacy_import_confirmed"},
    )
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.update_alias_last_seen_at", lambda corp_id, scene: 1)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: (_ for _ in ()).throw(AssertionError("historical vote should not run after confirmed alias")))

    channel, match = resolve_channel_for_scene(scene_value="scene-old", corp_id="ww")

    assert channel["id"] == 2
    assert match["match_type"] == "scene_alias"


def test_historical_vote_is_diagnostic_only(monkeypatch):
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", lambda corp_id, scene: None)
    monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: {"id": 3, "scene_value": "scene-new"})

    channel, match = resolve_channel_for_scene(scene_value="scene-old", corp_id="ww")

    assert channel is None
    assert match["match_type"] == "not_found"
    assert match["reason"] == "qrcode_scene_unrecognized"
    assert match["historical_vote"] == {"suggested_channel_id": 3, "requires_admin_confirmation": True}

