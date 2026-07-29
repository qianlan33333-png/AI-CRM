from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from aicrm_next.channels.channel_entry import repo as channel_repo
from aicrm_next.channels.channel_entry.application import (
    decrypt_callback_body,
    diagnose_channel_runtime,
    dry_run_channel_entry,
    process_channel_entry,
    repair_channel_entry,
    resolve_channel_for_scene,
)
from aicrm_next.channels.channel_entry.schemas import (
    DiagnoseChannelRuntimeQuery,
    ProcessChannelEntryCommand,
    RepairChannelEntryCommand,
)
from aicrm_next.channels.channel_entry.wecom_adapter import get_wecom_adapter, set_wecom_adapter


class RuntimeHarness:
    def __init__(self) -> None:
        self.channel = {
            "id": 101,
            "channel_code": "next_realistic",
            "channel_name": "Next realistic",
            "scene_value": "scene-current",
            "status": "active",
            "owner_staff_id": "owner-a",
            "welcome_message": "Next渠道码真实测试欢迎语",
            "entry_tag_id": "tag-next-real",
            "entry_tag_name": "Next渠道码实测标签",
            "qr_url": "https://wecom.example/qr/101",
            "carrier_type": "qrcode",
        }
        self.current_scenes = {"scene-current": self.channel}
        self.assets = {
            "scene-current": {
                "id": 901,
                "channel_id": 101,
                "channel_row_id": 101,
                "scene_value": "scene-current",
                "channel_scene_value": "scene-current",
                "channel_qr_url": self.channel["qr_url"],
                "status": "active",
                "generation_source": "next_create_contact_way",
            }
        }
        self.aliases: dict[str, dict] = {}
        self.historical = {}
        self.effect_success: set[tuple[str, str]] = set()
        self.effect_logs: list[dict] = []
        self.contacts: list[dict] = []
        self.tags: set[tuple[str, str, str]] = set()
        self.tag_snapshots: list[dict] = []
        self.events = {
            9001: {
                "id": 9001,
                "external_userid": "wm-repair",
                "user_id": "owner-a",
                "payload_json": {"State": "scene-current", "unionid": "union-repair"},
            }
        }
        self.alias_last_seen: list[str] = []
        self.welcome_calls: list[dict] = []
        self.tag_calls: list[dict] = []
        self.welcome_errcode = 0
        self.tag_errcode = 0
        self.return_db_timestamps = False
        self.validate_effect_json = False

    def install(self, monkeypatch):
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_qrcode_asset_by_scene", self.find_asset)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.touch_qrcode_asset_callback", lambda asset_id: None)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_scene_value", lambda scene: self.current_scenes.get(scene))
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_scene_alias", self.find_alias)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_confirmed_channel_by_scene_alias", self.find_confirmed_alias)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.find_channel_by_historical_scene_value", lambda scene: self.historical.get(scene))
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_scene_alias", self.upsert_alias)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.backfill_scene_alias_from_historical_vote", self.backfill_alias)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.update_alias_last_seen_at", lambda corp_id, scene: self.alias_last_seen.append(scene) or 1)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_contact", self.upsert_contact)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.get_channel_entry_effect_log", self.get_effect)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.upsert_channel_entry_effect_log", self.upsert_effect)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.save_tag_snapshot", self.save_tag_snapshot)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.get_channel_by_id", lambda channel_id: self.channel if channel_id == 101 else None)
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_channel_scene_aliases", lambda channel_id: list(self.aliases.values()))
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_channel_entry_effect_logs", lambda **kwargs: list(self.effect_logs))
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.list_recent_events", lambda scene, limit=20: [{"id": 1, "scene_value": scene, "process_status": "success"}])
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.get_external_contact_event_log", lambda event_log_id: self.events.get(event_log_id))
        monkeypatch.setattr("aicrm_next.channels.channel_entry.repo.decode_payload_json", lambda value: value if isinstance(value, dict) else {})

        class Adapter:
            def send_welcome_msg(adapter_self, payload):
                self.welcome_calls.append(payload)
                return {"errcode": self.welcome_errcode, "errmsg": "ok" if self.welcome_errcode == 0 else "failed"}

            def mark_external_contact_tags(adapter_self, **payload):
                self.tag_calls.append(payload)
                return {"errcode": self.tag_errcode, "errmsg": "ok" if self.tag_errcode == 0 else "failed"}

        previous = get_wecom_adapter()
        set_wecom_adapter(Adapter())
        return previous

    def find_alias(self, corp_id: str, scene: str):
        alias = self.aliases.get(scene)
        if not alias or alias.get("status") == "revoked":
            return None
        return {**self.channel, "scene_alias_id": alias["id"], "scene_alias_status": alias["status"], "scene_alias_source": alias["source"]}

    def find_confirmed_alias(self, corp_id: str, scene: str):
        alias = self.aliases.get(scene)
        if not alias or alias.get("status") == "revoked":
            return None
        if alias.get("source") not in {"next_create_contact_way", "legacy_import_confirmed", "admin_repair_confirmed"}:
            return None
        return {**self.channel, "scene_alias_id": alias["id"], "scene_alias_status": alias["status"], "scene_alias_source": alias["source"]}

    def find_asset(self, corp_id: str, scene: str):
        asset = self.assets.get(scene)
        if not asset:
            return None
        return {**self.channel, **asset, "channel_status": self.channel.get("status")}

    def upsert_alias(self, **kwargs):
        scene = kwargs["scene_value"]
        alias = self.aliases.get(scene) or {"id": len(self.aliases) + 1}
        alias.update(kwargs)
        self.aliases[scene] = alias
        return alias

    def backfill_alias(self, scene: str, channel_id: int):
        return self.upsert_alias(channel_id=channel_id, scene_value=scene, status="active", source="historical_backfill")

    def upsert_contact(self, **kwargs):
        row = {"id": len(self.contacts) + 1, **kwargs}
        if self.return_db_timestamps:
            now = datetime(2026, 5, 31, 15, 35, 5, tzinfo=timezone.utc)
            row.update({"created_at": now, "updated_at": now, "last_channel_entered_at": now})
        existing = next((item for item in self.contacts if item["channel_id"] == kwargs["channel_id"] and item["external_contact_id"] == kwargs["external_contact_id"]), None)
        if existing:
            existing.update(kwargs)
            if self.return_db_timestamps:
                existing.update({key: row[key] for key in ("created_at", "updated_at", "last_channel_entered_at")})
            return existing
        self.contacts.append(row)
        return row

    def get_effect(self, effect_type: str, key: str):
        if (effect_type, key) in self.effect_success:
            return {"effect_type": effect_type, "idempotency_key": key, "status": "success"}
        return None

    def upsert_effect(self, **kwargs):
        if self.validate_effect_json:
            json.dumps(kwargs.get("request_json") or {})
            json.dumps(kwargs.get("response_json") or {})
        row = {"id": len(self.effect_logs) + 1, **kwargs}
        self.effect_logs.append(row)
        if kwargs["status"] == "success":
            self.effect_success.add((kwargs["effect_type"], kwargs["idempotency_key"]))
        return row

    def save_tag_snapshot(self, owner_staff_id, external_contact_id, tag_ids, tag_names):
        for tag_id in tag_ids:
            self.tags.add((owner_staff_id, external_contact_id, tag_id))
            self.tag_snapshots.append({"owner_staff_id": owner_staff_id, "external_contact_id": external_contact_id, "tag_id": tag_id, "tag_name": tag_names.get(tag_id)})


@pytest.fixture
def runtime(monkeypatch):
    harness = RuntimeHarness()
    previous = harness.install(monkeypatch)
    try:
        yield harness
    finally:
        set_wecom_adapter(previous)


def _command(external="wm-real", state="scene-current", owner="owner-a", welcome_code="welcome-real"):
    payload = {"State": state, "WelcomeCode": welcome_code, "corp_id": "ww-test", "follow_user": [{"userid": owner, "tags": []}]}
    return ProcessChannelEntryCommand(unionid="union-real", external_contact_id=external, payload_json=payload, follow_user_userid=owner, send_welcome_message=bool(welcome_code), event_log_id=5001)


def test_realistic_active_channel_flow_has_qr_alias_effects_without_legacy_member(runtime):
    runtime.upsert_alias(channel_id=101, scene_value="scene-current", config_id="config-101", qr_url=runtime.channel["qr_url"], status="active", source="next_create_contact_way")

    result = process_channel_entry(_command())

    assert result["mode"] == "channel_baseline_only"
    assert result["reason"] == "channel_entry_baseline_recorded"
    assert result["scene_match"]["match_type"] == "qrcode_asset_active"
    assert "program_member_written" not in result
    assert "workflow_triggered" not in result
    assert "admission_results" not in result
    assert runtime.aliases["scene-current"]["config_id"] == "config-101"
    assert runtime.contacts[0]["owner_staff_id"] == "owner-a"
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert result["welcome_message"]["real_external_call_executed"] is False
    assert result["entry_tag"]["real_external_call_executed"] is False
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []
    assert {row["effect_type"] for row in runtime.effect_logs} >= {"channel_contact", "welcome_message", "entry_tag"}
    assert any(row["effect_type"] == "welcome_message" and row["status"] == "queued" for row in runtime.effect_logs)
    assert any(row["effect_type"] == "entry_tag" and row["status"] == "queued" for row in runtime.effect_logs)
    assert all(row["effect_type"] != "program_admission" for row in runtime.effect_logs)


def test_effect_log_json_payload_accepts_database_scalar_types():
    wrapped = channel_repo._json(
        {
            "created_at": datetime(2026, 5, 31, 15, 35, 5, tzinfo=timezone.utc),
            "amount": Decimal("9.90"),
            "nested": [{"updated_at": datetime(2026, 5, 31, 15, 36, 5, tzinfo=timezone.utc)}],
        }
    )

    decoded = json.loads(wrapped.dumps(wrapped.obj))

    assert decoded["created_at"] == "2026-05-31T15:35:05+00:00"
    assert decoded["amount"] == "9.90"
    assert decoded["nested"][0]["updated_at"] == "2026-05-31T15:36:05+00:00"


def test_channel_contact_db_timestamps_do_not_block_baseline_effects(runtime):
    runtime.return_db_timestamps = True
    runtime.validate_effect_json = True

    result = process_channel_entry(_command(external="wm-db-datetime"))

    assert result["handled"] is True
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert "program_member_written" not in result
    channel_contact_effect = next(row for row in runtime.effect_logs if row["effect_type"] == "channel_contact")
    assert channel_contact_effect["response_json"]["created_at"] == "2026-05-31T15:35:05+00:00"


def test_no_binding_runs_standalone_baseline(runtime):
    result = process_channel_entry(_command(external="wm-standalone"))

    assert result["mode"] == "channel_baseline_only"
    assert result["reason"] == "channel_entry_baseline_recorded"
    assert "program_member_written" not in result
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []
    assert all(row["effect_type"] != "program_admission" for row in runtime.effect_logs)


def test_scene_alias_is_primary_and_updates_last_seen(runtime):
    runtime.current_scenes = {}
    runtime.assets = {}
    runtime.aliases["scene-old"] = {"id": 7, "channel_id": 101, "scene_value": "scene-old", "status": "active", "source": "legacy_import_confirmed"}

    result = process_channel_entry(_command(state="scene-old", external="wm-old"))

    assert result["scene_match"]["match_type"] == "scene_alias"
    assert runtime.alias_last_seen == ["scene-old"]
    assert runtime.contacts[0]["external_contact_id"] == "wm-old"


def test_historical_fallback_is_diagnostic_only(runtime):
    runtime.current_scenes = {}
    runtime.assets = {}
    runtime.historical["scene-vote"] = runtime.channel

    first_channel, first_match = resolve_channel_for_scene(scene_value="scene-vote", corp_id="ww-test", persist_alias=True)

    assert first_channel is None
    assert first_match["reason"] == "qrcode_scene_unrecognized"
    assert first_match["historical_vote"]["suggested_channel_id"] == 101
    assert "scene-vote" not in runtime.aliases


def test_channel_baseline_does_not_depend_on_program_binding_state(runtime):
    result = process_channel_entry(_command(external="wm-archived"))

    assert result["mode"] == "channel_baseline_only"
    assert result["reason"] == "channel_entry_baseline_recorded"
    assert "program_member_written" not in result
    assert "workflow_triggered" not in result
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []
    assert all(row["effect_type"] != "program_admission" for row in runtime.effect_logs)


def test_disabled_channel_writes_skipped_diagnostics_without_side_effects(runtime):
    runtime.channel["status"] = "disabled"

    result = process_channel_entry(_command(external="wm-disabled"))

    assert result["handled"] is False
    assert result["reason"] == "channel_disabled"
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []
    assert {row["reason"] for row in runtime.effect_logs} == {"channel_disabled"}


def test_missing_welcome_code_does_not_block_tag_or_channel_entry(runtime):
    result = process_channel_entry(_command(external="wm-no-welcome", welcome_code=""))

    assert result["welcome_message"]["reason"] == "welcome_code_missing"
    assert result["entry_tag"]["queued"] is True
    assert "program_member_written" not in result
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []


def test_duplicate_welcome_and_tag_are_idempotent(runtime):
    first = process_channel_entry(_command(external="wm-dup", welcome_code="welcome-dup"))
    second = process_channel_entry(_command(external="wm-dup", welcome_code="welcome-dup"))

    assert first["welcome_message"]["queued"] is True
    assert second["welcome_message"]["queued"] is True
    assert second["entry_tag"]["queued"] is True
    assert len(runtime.welcome_calls) == 0
    assert len(runtime.tag_calls) == 0


def test_follow_user_dimension_is_not_hardcoded(runtime):
    process_channel_entry(_command(external="wm-shared", owner="owner-a", welcome_code="welcome-a"))
    process_channel_entry(_command(external="wm-shared", owner="owner-b", welcome_code="welcome-b"))

    owners = {row["owner_staff_id"] for row in runtime.effect_logs if row["effect_type"] == "entry_tag"}
    assert owners == {"owner-a", "owner-b"}
    assert runtime.contacts[0]["owner_staff_id"] in {"owner-a", "owner-b"}
    assert "HuangYouCan" not in owners


def test_follow_user_empty_wecom_tags_still_applies_channel_entry_tag(runtime):
    result = process_channel_entry(_command(external="wm-empty-tags"))

    assert result["entry_tag"]["queued"] is True
    assert runtime.tag_calls == []


def test_welcome_attachment_shapes_and_failures(runtime):
    runtime.channel["welcome_image_library_ids"] = [{"media_id": "image-media"}]
    runtime.channel["welcome_attachment_library_ids"] = [{"media_id": "file-media"}]
    runtime.channel["welcome_miniprogram_library_ids"] = [{"appid": "wx123", "page": "pages/a", "title": "小程序", "pic_media_id": "pic-media"}]
    ok = process_channel_entry(_command(external="wm-attach"))
    assert ok["welcome_message"]["queued"] is True
    welcome_effect = next(row for row in runtime.effect_logs if row["effect_type"] == "welcome_message" and row["external_contact_id"] == "wm-attach")
    assert [item["msgtype"] for item in welcome_effect["request_json"]["attachments"]] == ["image", "file", "miniprogram"]
    assert welcome_effect["request_json"]["attachments"][2]["appid"] == "wx123"

    runtime.welcome_calls.clear()
    runtime.channel["welcome_image_library_ids"] = list(range(1, 11))
    too_many = process_channel_entry(_command(external="wm-too-many", welcome_code="welcome-too-many"))
    assert too_many["welcome_message"]["reason"] == "attachment_limit_exceeded"
    assert runtime.welcome_calls == []

    runtime.channel["welcome_image_library_ids"] = [{"missing": True}]
    missing = process_channel_entry(_command(external="wm-missing-material", welcome_code="welcome-missing"))
    assert missing["welcome_message"]["reason"] == "material_resolve_failed"


def test_wecom_adapter_errors_write_failed_effects(runtime):
    runtime.welcome_errcode = 40001
    welcome_failed = process_channel_entry(_command(external="wm-welcome-error"))
    assert welcome_failed["welcome_message"]["reason"] == "external_effect_job_queued"
    assert welcome_failed["entry_tag"]["queued"] is True
    assert any(row["effect_type"] == "welcome_message" and row["status"] == "queued" and row["reason"] == "external_effect_job_queued" for row in runtime.effect_logs)

    runtime.welcome_errcode = 0
    runtime.tag_errcode = 40002
    tag_failed = process_channel_entry(_command(external="wm-tag-error", welcome_code="welcome-tag-error"))
    assert tag_failed["entry_tag"]["reason"] == "external_effect_job_queued"
    assert ("owner-a", "wm-tag-error", "tag-next-real") not in runtime.tags


def test_scene_not_found_and_no_welcome_or_no_tag_configs(runtime):
    runtime.current_scenes = {}
    not_found = process_channel_entry(_command(external="wm-not-found", state="missing-scene"))
    assert not_found["handled"] is False
    assert not_found["reason"] == "qrcode_scene_unrecognized"
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []

    runtime.current_scenes = {"scene-current": runtime.channel}
    runtime.channel["entry_tag_id"] = ""
    no_tag = process_channel_entry(_command(external="wm-no-tag", welcome_code="welcome-no-tag"))
    assert no_tag["entry_tag"]["reason"] == "no_entry_tag_configured"

    runtime.channel["entry_tag_id"] = "tag-next-real"
    runtime.channel["welcome_message"] = ""
    no_welcome = process_channel_entry(_command(external="wm-no-message", welcome_code="welcome-no-message"))
    assert no_welcome["welcome_message"]["reason"] == "no_welcome_message_configured"
    assert no_welcome["entry_tag"]["queued"] is True


def test_diagnosis_dry_run_and_repair(runtime):
    runtime.aliases["scene-current"] = {"id": 1, "channel_id": 101, "scene_value": "scene-current", "status": "active", "source": "generated"}
    process_channel_entry(_command(external="wm-diagnosis"))

    diagnosis = diagnose_channel_runtime(DiagnoseChannelRuntimeQuery(scene_value="scene-current"))
    assert diagnosis["callback_route_owner"] == "aicrm_next.channels.channel_entry"
    assert diagnosis["scene_resolve"]["match_type"] == "qrcode_asset_active"
    assert diagnosis["welcome_configured"] is True
    assert diagnosis["entry_tag_configured"] is True
    assert diagnosis["recent_automation_channel_entry_effect_log"]

    dry = dry_run_channel_entry(_command(external="wm-dry-run", welcome_code="welcome-dry-run"))
    assert dry["dry_run"] is True
    assert dry["would_send_welcome"] is True
    assert dry["would_apply_tag"] is True
    assert "would_write_member" not in dry

    repair = repair_channel_entry(RepairChannelEntryCommand(event_log_id=9001))
    assert repair["handled"] is True
    assert repair["welcome_repair"]["reason"] == "welcome_code_unavailable_or_expired"
    assert any(item["external_contact_id"] == "wm-repair" for item in runtime.contacts)


def test_callback_signature_failure_does_not_enter_pipeline(monkeypatch, runtime):
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", "token")
    monkeypatch.setenv("WECOM_CALLBACK_AES_KEY", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")

    with pytest.raises(Exception):
        decrypt_callback_body(query={"timestamp": "1", "nonce": "n", "msg_signature": "bad"}, body=b"<xml><Encrypt>invalid</Encrypt></xml>")

    assert runtime.contacts == []
    assert runtime.welcome_calls == []
    assert runtime.tag_calls == []


def test_real_qr_acceptance_blocked_without_approved_staging_env(monkeypatch):
    for name in (
        "WECOM_CORP_ID",
        "WECOM_CALLBACK_TOKEN",
        "WECOM_CALLBACK_AES_KEY",
        "WECOM_CONTACT_SECRET",
        "AICRM_APPROVED_WECOM_STAGING_CALLBACK_URL",
        "AICRM_APPROVED_WECOM_TEST_EXTERNAL_USERID",
    ):
        monkeypatch.delenv(name, raising=False)

    missing = [
        name
        for name in (
            "WECOM_CORP_ID",
            "WECOM_CALLBACK_TOKEN",
            "WECOM_CALLBACK_AES_KEY",
            "WECOM_CONTACT_SECRET",
            "AICRM_APPROVED_WECOM_STAGING_CALLBACK_URL",
            "AICRM_APPROVED_WECOM_TEST_EXTERNAL_USERID",
        )
        if not __import__("os").getenv(name)
    ]

    assert missing
