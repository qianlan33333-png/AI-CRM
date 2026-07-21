from __future__ import annotations

from aicrm_next.channel_entry.application import process_channel_entry
from aicrm_next.channel_entry.schemas import ProcessChannelEntryCommand
from aicrm_next.channel_entry.wecom_adapter import get_wecom_adapter, set_wecom_adapter
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_CONTACT_TAG_MARK,
    WECOM_WELCOME_MESSAGE_SEND,
    reset_external_effect_fixture_state,
)


def _patch_repo(monkeypatch, *, channel_status="active", bindings=None):
    channel = {"id": 10, "channel_code": "c", "channel_name": "C", "scene_value": "scene-a", "status": channel_status, "owner_staff_id": "sales", "welcome_message": "hello", "entry_tag_id": "tag-a"}
    calls: list[str] = []
    monkeypatch.setattr("aicrm_next.channel_entry.application.resolve_channel_for_scene", lambda **kwargs: (channel, {"match_type": "current_scene", "matched_scene": "scene-a", "channel_id": 10}))
    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_contact", lambda **kwargs: calls.append("contact") or {"ok": True})
    def fake_upsert_channel_entry_runtime(**kwargs):
        calls.append("runtime_with_identity_effect" if kwargs.get("enqueue_identity_resolution") else "runtime")
        return {
            "ok": True,
            **kwargs,
            "identity_resolution_queue": {
                "external_effect_job_id": 901,
                "execution_id": "exec-identity-901",
                "execution_owner": "external_effect_job",
            },
        }

    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_entry_runtime", fake_upsert_channel_entry_runtime)
    monkeypatch.setattr(
        "aicrm_next.channel_entry.repo.enqueue_channel_entry_identity_resolution",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("identity effect must be persisted atomically with the runtime row")),
    )
    monkeypatch.setattr("aicrm_next.channel_entry.repo.get_channel_entry_effect_log", lambda *args: None)
    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_entry_effect_log", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("aicrm_next.channel_entry.repo.save_tag_snapshot", lambda *args, **kwargs: None)

    class Adapter:
        def send_welcome_msg(self, payload):
            calls.append("welcome")
            return {"errcode": 0}

        def mark_external_contact_tags(self, **payload):
            calls.append("tag")
            return {"errcode": 0}

    previous = get_wecom_adapter()
    set_wecom_adapter(Adapter())
    return calls, previous


def test_active_channel_baseline_emits_only_channel_entry_without_program_admission(monkeypatch):
    reset_external_effect_fixture_state()
    calls, previous = _patch_repo(monkeypatch, bindings=[{"id": 20, "program_id": 30, "program_status": "active"}])
    wakeups: list[tuple[int, str, str]] = []

    def fake_wake(job_id, *, reason, effect_type, adapter_registry=None):
        del adapter_registry
        wakeups.append((int(job_id or 0), reason, effect_type))
        return False

    monkeypatch.setattr("aicrm_next.channel_entry.application.wake_external_effect_job", fake_wake)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-a", external_contact_id="wm-a", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["handled"] is True
    assert result["mode"] == "channel_baseline_only"
    assert result["reason"] == "channel_entry_baseline_recorded"
    assert calls[0] == "contact"
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert result["welcome_message"]["immediate_dispatch_scheduled"] is False
    assert result["welcome_message"]["welcome_effect_cancelled_for_fallback"] is False
    assert result["welcome_message"]["fallback_message"] == {}
    assert result["entry_tag"]["immediate_dispatch_scheduled"] is False
    assert [item[1:] for item in wakeups] == [
        ("channel_entry_welcome_message", "wecom.welcome_message.send"),
        ("channel_entry_tag_mark", "wecom.contact.tag.mark"),
    ]
    assert all(job_id > 0 for job_id, _, _ in wakeups)
    assert "program_member_written" not in result
    assert "admission_results" not in result
    assert "member" not in calls
    assert "legacy_member" not in calls

    welcome_jobs, _ = ExternalEffectService().list_jobs(
        {"effect_type": WECOM_WELCOME_MESSAGE_SEND, "target_id": "union-a"},
        limit=10,
    )
    assert welcome_jobs[0].status == "queued"


def test_archived_program_state_is_not_part_of_channel_baseline(monkeypatch):
    calls, previous = _patch_repo(monkeypatch, bindings=[{"id": 20, "program_id": 30, "program_status": "archived"}])
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-a", external_contact_id="wm-a", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["mode"] == "channel_baseline_only"
    assert result["reason"] == "channel_entry_baseline_recorded"
    assert "program_member_written" not in result
    assert "admission_results" not in result
    assert calls == ["contact"]
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
    assert "member" not in calls


def test_entry_without_unionid_still_queues_external_userid_effects(monkeypatch):
    reset_external_effect_fixture_state()
    runtime_entries: list[dict] = []
    calls, previous = _patch_repo(monkeypatch)
    def fake_upsert_runtime(**kwargs):
        calls.append("runtime_with_identity_effect")
        runtime_entries.append(kwargs)
        return {
            "ok": True,
            **kwargs,
            "identity_resolution_queue": {
                "external_effect_job_id": 902,
                "execution_id": "exec-identity-902",
                "execution_owner": "external_effect_job",
            },
        }

    monkeypatch.setattr("aicrm_next.channel_entry.repo.upsert_channel_entry_runtime", fake_upsert_runtime)
    monkeypatch.setattr("aicrm_next.channel_entry.application.wake_external_effect_job", lambda *args, **kwargs: False)
    try:
        result = process_channel_entry(
            ProcessChannelEntryCommand(
                external_contact_id="wm-no-union",
                payload_json={"State": "scene-a", "WelcomeCode": "wc"},
                send_welcome_message=True,
            )
        )
    finally:
        set_wecom_adapter(previous)

    assert result["handled"] is True
    assert result["mode"] == "channel_runtime_only"
    assert result["reason"] == "channel_entry_runtime_recorded"
    assert runtime_entries[0]["unionid"] == ""
    assert runtime_entries[0]["external_userid"] == "wm-no-union"
    assert result["channel_contact"] == {"attempted": False, "deferred": True, "reason": "identity_pending_unionid"}
    assert result["channel_entry_internal_event"] == {"ok": False, "deferred": True, "reason": "identity_pending_unionid"}
    assert calls == ["runtime_with_identity_effect"]
    assert runtime_entries[0]["enqueue_identity_resolution"] is True
    assert result["runtime_entry"]["identity_resolution_queue"]["execution_owner"] == "external_effect_job"
    assert "contact" not in calls
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True

    welcome_jobs, _ = ExternalEffectService().list_jobs(
        {"effect_type": WECOM_WELCOME_MESSAGE_SEND, "target_id": "wm-no-union"},
        limit=10,
    )
    tag_jobs, _ = ExternalEffectService().list_jobs(
        {"effect_type": WECOM_CONTACT_TAG_MARK, "target_id": "wm-no-union"},
        limit=10,
    )
    assert welcome_jobs[0].target_type == "external_userid"
    assert tag_jobs[0].target_type == "external_userid"
    assert "target_unionid" not in welcome_jobs[0].payload_json
    assert "target_unionid" not in tag_jobs[0].payload_json


def test_tag_idempotency_is_scoped_to_the_relationship_event(monkeypatch):
    reset_external_effect_fixture_state()
    _calls, previous = _patch_repo(monkeypatch)
    monkeypatch.setattr("aicrm_next.channel_entry.application.wake_external_effect_job", lambda *args, **kwargs: False)

    def enter(event_log_id: int):
        return process_channel_entry(
            ProcessChannelEntryCommand(
                external_contact_id="wm-relationship-epoch",
                follow_user_userid="sales",
                payload_json={"corp_id": "ww-test", "State": "scene-a", "WelcomeCode": "wc"},
                send_welcome_message=True,
                event_log_id=event_log_id,
            )
        )

    try:
        first = enter(5101)
        duplicate = enter(5101)
        readded = enter(5102)
    finally:
        set_wecom_adapter(previous)

    assert first["entry_tag"]["external_effect_job_id"] == duplicate["entry_tag"]["external_effect_job_id"]
    assert readded["entry_tag"]["external_effect_job_id"] != first["entry_tag"]["external_effect_job_id"]

    tag_jobs, total = ExternalEffectService().list_jobs(
        {"effect_type": WECOM_CONTACT_TAG_MARK, "target_id": "wm-relationship-epoch"},
        limit=10,
    )
    assert total == 2
    assert len(tag_jobs) == 2
    assert len({job.idempotency_key for job in tag_jobs}) == 2
    assert all("relationship_event:" in job.idempotency_key for job in tag_jobs)


def test_channel_disabled_has_no_baseline_side_effects(monkeypatch):
    calls, previous = _patch_repo(monkeypatch, channel_status="inactive")
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(unionid="union-a", external_contact_id="wm-a", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["handled"] is False
    assert result["mode"] == "channel_disabled"
    assert calls == []


def test_channel_entry_without_unionid_records_runtime_entry(monkeypatch):
    calls, previous = _patch_repo(monkeypatch)
    try:
        result = process_channel_entry(ProcessChannelEntryCommand(external_contact_id="wm-a", payload_json={"State": "scene-a", "WelcomeCode": "wc"}, send_welcome_message=True))
    finally:
        set_wecom_adapter(previous)

    assert result["handled"] is True
    assert result["mode"] == "channel_runtime_only"
    assert result["reason"] == "channel_entry_runtime_recorded"
    assert calls == ["runtime_with_identity_effect"]
    assert result["runtime_entry"]["enqueue_identity_resolution"] is True
    assert result["runtime_entry"]["identity_resolution_queue"]["execution_owner"] == "external_effect_job"
    assert result["channel_contact"] == {"attempted": False, "deferred": True, "reason": "identity_pending_unionid"}
    assert result["welcome_message"]["queued"] is True
    assert result["entry_tag"]["queued"] is True
