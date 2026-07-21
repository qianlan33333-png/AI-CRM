from __future__ import annotations

from contextlib import nullcontext

import pytest

from aicrm_next.channel_entry.identity_bridge import ensure_external_contact_identity_for_sidebar
from aicrm_next.channel_entry.application import process_wecom_external_contact_event
from aicrm_next.channel_entry.schemas import ProcessWeComExternalContactEventCommand
from aicrm_next.channel_entry.wecom_adapter import get_wecom_adapter, set_wecom_adapter
from aicrm_next.shared.postgres_connection import get_db
from scripts.run_identity_mobile_bridge_backfill import run_backfill


def _app_context(app):
    return app.app_context() if hasattr(app, "app_context") else nullcontext()


class DetailAdapter:
    def __init__(self):
        self.profile_updates: list[dict] = []

    def get_external_contact_detail(self, external_userid: str):
        return {
            "errcode": 0,
            "errmsg": "ok",
            "external_contact": {
                "external_userid": external_userid,
                "unionid": "union_bridge_001",
                "openid": "openid_bridge_001",
                "name": "桥接客户",
                "type": 1,
            },
            "follow_user": [
                {
                    "userid": "owner_bridge",
                    "remark": "桥接备注",
                    "description": "",
                    "state": "",
                    "createtime": 1780640000,
                }
            ],
        }

    def update_external_contact_remark(self, payload: dict):
        self.profile_updates.append(dict(payload))
        return {"errcode": 0, "errmsg": "ok"}


def _seed_identity_mobile_candidate(db, *, unionid: str, external_userid: str, mobile: str, owner_userid: str, openid: str = "") -> None:
    db.execute(
        """
        INSERT INTO crm_user_identity (
            unionid, primary_external_userid, primary_openid, primary_owner_userid,
            external_userids_json, openids_json, mobile, mobile_normalized,
            identity_status, created_at, updated_at
        )
        VALUES (
            ?, ?, ?, ?,
            jsonb_build_array(CAST(? AS text)),
            CASE WHEN CAST(? AS text) = '' THEN '[]'::jsonb ELSE jsonb_build_array(CAST(? AS text)) END,
            '', ?, 'active', NOW(), NOW() - INTERVAL '5 minutes'
        )
        ON CONFLICT (unionid) DO UPDATE SET
            primary_external_userid = EXCLUDED.primary_external_userid,
            primary_openid = COALESCE(NULLIF(EXCLUDED.primary_openid, ''), crm_user_identity.primary_openid),
            primary_owner_userid = EXCLUDED.primary_owner_userid,
            external_userids_json = EXCLUDED.external_userids_json,
            openids_json = EXCLUDED.openids_json,
            mobile = '',
            mobile_normalized = EXCLUDED.mobile_normalized,
            identity_status = 'active',
            updated_at = NOW() - INTERVAL '5 minutes'
        """,
        (unionid, external_userid, openid, owner_userid, external_userid, openid, openid, mobile),
    )


def test_next_external_contact_callback_only_plans_identity_provider_work(monkeypatch):
    provider_calls: list[str] = []
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.process_channel_entry",
        lambda command, **kwargs: {"handled": False, "reason": "channel_entry_not_under_test"},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.log_external_contact_event",
        lambda **kwargs: {"id": 501, **kwargs},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_event_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.enqueue_channel_entry_identity_resolution",
        lambda **kwargs: {
            "ok": True,
            "external_effect_job_id": 7001,
            "execution_id": "exe_identity_root",
            "effect_execution_id": "exe_identity_provider",
            "real_external_call_executed": False,
        },
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.sync_external_contact_identity_for_event",
        lambda *args, **kwargs: provider_calls.append("provider") or {},
    )

    result = process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id="ww-bridge",
            event_data={
                "Event": "change_external_contact",
                "ChangeType": "add_external_contact",
                "ExternalUserID": "wm_bridge_001",
                "UserID": "owner_bridge",
                "CreateTime": "1780640000",
            },
            payload_xml="<xml/>",
            route="/wecom/external-contact/callback",
        )
    )

    assert result["identity_sync"]["status"] == "queued"
    assert result["identity_sync"]["external_effect_job_id"] == 7001
    assert result["identity_sync"]["real_external_call_executed"] is False
    assert provider_calls == []


def test_next_external_contact_callback_keeps_entry_success_when_identity_plan_fails(monkeypatch):
    calls = []
    status_updates = []
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.log_external_contact_event",
        lambda **kwargs: {"id": 321, **kwargs},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.enqueue_channel_entry_identity_resolution",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_event_status",
        lambda event_id, status, error_message="": status_updates.append(
            {"event_id": event_id, "status": status, "error_message": error_message}
        ),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.process_channel_entry",
        lambda command, **kwargs: calls.append("channel_entry") or {"handled": True, "reason": "channel_entry_baseline_recorded"},
    )

    result = process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id="ww-bridge",
            event_data={
                "Event": "change_external_contact",
                "ChangeType": "add_external_contact",
                "ExternalUserID": "wm_bridge_failed",
                "UserID": "owner_bridge",
                "State": "scene-a",
                "WelcomeCode": "welcome-failed",
                "CreateTime": "1780640001",
            },
            payload_xml="<xml/>",
            route="/wecom/external-contact/callback",
        )
    )

    assert calls == ["channel_entry"]
    assert result["handled"] is True
    assert result["identity_sync"]["status"] == "failed"
    assert result["identity_sync"]["reason"] == "identity_resolution_effect_planning_failed"
    assert result["identity_sync"]["real_external_call_executed"] is False
    assert status_updates == [
        {
            "event_id": 321,
            "status": "success",
            "error_message": "",
        }
    ]


def test_next_external_contact_callback_reuses_atomic_runtime_identity_plan(monkeypatch):
    calls = []
    status_updates = []
    contacts = []
    internal_events = []
    runtime_updates = []
    effect_logs = []
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.log_external_contact_event",
        lambda **kwargs: {"id": 432, **kwargs},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.sync_external_contact_identity_for_event",
        lambda event, corp_id: calls.append("identity_sync") or {"status": "success", "unionid": "union_bridge_success"},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.process_channel_entry",
        lambda command, **kwargs: calls.append("runtime_entry")
        or {
            "handled": True,
            "mode": "channel_runtime_only",
            "reason": "channel_entry_runtime_recorded",
            "runtime_entry": {
                "identity_resolution_queue": {
                    "ok": True,
                    "external_effect_job_id": 7301,
                    "execution_id": "exe_runtime_identity",
                    "effect_execution_id": "exe_runtime_provider",
                }
            },
        },
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.enqueue_channel_entry_identity_resolution",
        lambda **kwargs: pytest.fail("callback must reuse the runtime transaction's identity effect"),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.resolve_channel_for_scene",
        lambda **kwargs: (
            {"id": 10, "channel_code": "c", "channel_name": "C", "scene_value": "scene-a", "status": "active", "owner_staff_id": "owner_bridge"},
            {"match_type": "current_scene", "matched_scene": "scene-a", "channel_id": 10},
        ),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.upsert_channel_contact",
        lambda **kwargs: contacts.append(kwargs) or {"id": 88, **kwargs},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.upsert_channel_entry_effect_log",
        lambda **kwargs: effect_logs.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.record_identity_sync_result",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_channel_entry_runtime_identity",
        lambda **kwargs: runtime_updates.append(kwargs) or {"status": "success", "updated_count": 1},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_event_status",
        lambda event_id, status, error_message="": status_updates.append(
            {"event_id": event_id, "status": status, "error_message": error_message}
        ),
    )

    class FakeInternalEventService:
        def emit_event(self, **kwargs):
            internal_events.append(kwargs)
            return {"event": {"event_id": "evt-channel-entry"}, "consumer_runs": [{}]}

    monkeypatch.setattr("aicrm_next.channel_entry.application.InternalEventService", FakeInternalEventService)

    result = process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id="ww-bridge",
            event_data={
                "Event": "change_external_contact",
                "ChangeType": "add_external_contact",
                "ExternalUserID": "wm_bridge_success",
                "UserID": "owner_bridge",
                "State": "scene-a",
                "WelcomeCode": "welcome-success",
                "CreateTime": "1780640003",
            },
            payload_xml="<xml/>",
            route="/wecom/external-contact/callback",
        )
    )

    assert calls == ["runtime_entry"]
    assert result["handled"] is True
    assert result["identity_sync"]["status"] == "queued"
    assert result["identity_sync"]["external_effect_job_id"] == 7301
    assert result["identity_sync"]["real_external_call_executed"] is False
    assert contacts == []
    assert internal_events == []
    assert runtime_updates == []
    assert effect_logs == []
    assert status_updates == [{"event_id": 432, "status": "success", "error_message": ""}]


def test_next_external_contact_callback_plans_identity_when_entry_result_has_no_effect(monkeypatch):
    calls = []
    status_updates = []
    runtime_updates = []
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.log_external_contact_event",
        lambda **kwargs: {"id": 654, **kwargs},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.sync_external_contact_identity_for_event",
        lambda event, corp_id: calls.append("identity_sync") or {"status": "pending_identity", "reason": "missing_unionid"},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.enqueue_channel_entry_identity_resolution",
        lambda **kwargs: {
            "ok": True,
            "external_effect_job_id": 7401,
            "execution_id": "exe_callback_identity",
        },
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_event_status",
        lambda event_id, status, error_message="": status_updates.append(
            {"event_id": event_id, "status": status, "error_message": error_message}
        ),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.process_channel_entry",
        lambda command, **kwargs: calls.append("channel_entry") or {"handled": True, "mode": "channel_runtime_only", "reason": "channel_entry_runtime_recorded"},
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.application.repo.mark_channel_entry_runtime_identity",
        lambda **kwargs: runtime_updates.append(kwargs) or {"status": "success", "updated_count": 1},
    )

    result = process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id="ww-bridge",
            event_data={
                "Event": "change_external_contact",
                "ChangeType": "add_external_contact",
                "ExternalUserID": "wm_bridge_pending",
                "UserID": "owner_bridge",
                "State": "scene-a",
                "WelcomeCode": "welcome-pending",
                "CreateTime": "1780640002",
            },
            payload_xml="<xml/>",
            route="/wecom/external-contact/callback",
        )
    )

    assert calls == ["channel_entry"]
    assert result["handled"] is True
    assert result["entry_result"] == {"handled": True, "mode": "channel_runtime_only", "reason": "channel_entry_runtime_recorded"}
    assert result["identity_sync"]["status"] == "queued"
    assert result["identity_sync"]["external_effect_job_id"] == 7401
    assert result["identity_sync"]["real_external_call_executed"] is False
    assert runtime_updates == []
    assert status_updates == [{"event_id": 654, "status": "success", "error_message": ""}]


def test_sidebar_identity_refresh_binds_missing_identity_on_access(app, next_pg_schema):
    previous_adapter = get_wecom_adapter()
    adapter = DetailAdapter()
    set_wecom_adapter(adapter)
    try:
        with _app_context(app):
            db = get_db()
            _seed_identity_mobile_candidate(
                db,
                unionid="union_bridge_001",
                external_userid="wm_bridge_001",
                openid="openid_bridge_001",
                mobile="18565883798",
                owner_userid="owner_bridge",
            )
            db.commit()

        result = ensure_external_contact_identity_for_sidebar(
            external_userid="wm_bridge_001",
            owner_userid="owner_bridge",
            corp_id="ww-bridge",
            min_interval_seconds=60,
        )

        with _app_context(app):
            db = get_db()
            identity = db.execute(
                """
                SELECT primary_external_userid AS external_userid,
                       mobile_normalized AS mobile,
                       primary_owner_userid
                FROM crm_user_identity
                WHERE unionid = ?
                """,
                ("union_bridge_001",),
            ).fetchone()

        assert result["status"] == "skipped"
        assert result["reason"] == "identity_fresh"
        assert result["mobile_bound"] is True
        assert adapter.profile_updates == []
        assert dict(identity) == {
            "external_userid": "wm_bridge_001",
            "mobile": "18565883798",
            "primary_owner_userid": "owner_bridge",
        }
    finally:
        set_wecom_adapter(previous_adapter)


def test_identity_mobile_bridge_backfill_repairs_historical_unbound_rows(app, next_pg_schema):
    with _app_context(app):
        db = get_db()
        db.execute(
            """
            INSERT INTO wecom_external_contact_identity_map (
                corp_id, external_userid, unionid, openid, follow_user_userid, name
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "ww-bridge",
                "wm_bridge_history",
                "union_bridge_history",
                "openid_bridge_history",
                "owner_history",
                "历史桥接客户",
            ),
        )
        _seed_identity_mobile_candidate(
            db,
            unionid="union_bridge_history",
            external_userid="wm_bridge_history",
            openid="openid_bridge_history",
            mobile="18565883799",
            owner_userid="owner_history",
        )
        db.commit()

        dry_run = run_backfill(execute=False, limit=50, external_userids=["wm_bridge_history"])
        executed = run_backfill(execute=True, limit=50, external_userids=["wm_bridge_history"])

        identity = db.execute(
            """
            SELECT primary_external_userid AS external_userid,
                   mobile_normalized AS mobile,
                   primary_owner_userid
            FROM crm_user_identity
            WHERE unionid = ?
            """,
            ("union_bridge_history",),
        ).fetchone()

    assert dry_run["summary"] == {"already_bound": 1}
    assert executed["summary"] == {"already_bound": 1}
    assert executed["results"][0]["questionnaire_backfill"]["reason"] == "questionnaire_submissions_unionid_only"
    assert dict(identity) == {
        "external_userid": "wm_bridge_history",
        "mobile": "18565883799",
        "primary_owner_userid": "owner_history",
    }
