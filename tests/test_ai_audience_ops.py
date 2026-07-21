from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from aicrm_next.ai_audience_ops.event_types import (
    DAILY_REFRESH_CONSUMER,
    DAILY_TICK_EVENT,
    INCREMENTAL_REFRESH_CONSUMER,
    INCREMENTAL_TICK_EVENT,
    MEMBER_EVENT_PREFIX,
    OUTBOUND_EFFECT_CONSUMER,
    RUN_REFRESHED_EVENT,
    SOURCE_CHANGED_EVENT,
    SOURCE_POKE_CONSUMER,
)
from aicrm_next.ai_audience_ops.outbound_service import AudienceOutboundService
from aicrm_next.ai_audience_ops.refresh_intents import AudienceRefreshIntentRepository, AudienceRefreshIntentService
from aicrm_next.ai_audience_ops.repository import build_audience_repository, next_daily_refresh_at
from aicrm_next.ai_audience_ops.scheduler import ai_audience_event_consumer_pairs, emit_due_ticks
from aicrm_next.ai_audience_ops.service import AudiencePackageService
from aicrm_next.ai_audience_ops.sql_catalog import ALLOWED_VIEWS, schema_catalog_payload
from aicrm_next.ai_audience_ops.sql_linter import lint_sql
from aicrm_next.ai_audience_ops.test_agent_service import AudienceTestAgentService, TEST_AGENT_MESSAGE_TEXT
from aicrm_next.ai_audience_ops.webhook_service import AudienceInboundWebhookService
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, WEBHOOK_GENERIC_PUSH, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.shared.db_session import get_session_factory


TOKEN = "ai-audience-test-token"


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _valid_incremental_sql() -> str:
    return """
        SELECT
            identity_type,
            identity_value,
            'questionnaire_submission:' || submission_id::text AS event_source_key,
            payload_json,
            external_userid,
            unionid,
            owner_userid,
            submitted_at AS event_at
        FROM audience_read.questionnaire_submissions_v1
        WHERE questionnaire_id = :questionnaire_id
          AND submitted_at >= :last_watermark_at
          AND submitted_at < :refresh_started_at
    """


def _valid_snapshot_sql(*, include_test_user: bool = True) -> str:
    suffix = "" if include_test_user else " AND 1 = 0"
    return f"""
        SELECT
            'external_userid' AS identity_type,
            wc.external_userid AS identity_value,
            'daily_snapshot:' || wc.external_userid AS event_source_key,
            wc.payload_json AS payload_json,
            wc.external_userid,
            wc.unionid,
            wc.owner_userid,
            wc.updated_at AS event_at
        FROM audience_read.wecom_contacts_v1 wc
        WHERE wc.external_userid = :test_external_userid
        {suffix}
    """


def _external_effect_signature(secret: str, payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _execute_latest_refresh_intent(package_id: int) -> dict[str, Any]:
    intent = AudienceRefreshIntentRepository().get(int(package_id))
    assert intent is not None
    assert intent["status"] in {"waiting", "retry_wait"}
    return AudienceRefreshIntentService().process_requested(
        package_id=int(package_id),
        signal_generation=int(intent["signal_generation"]),
    )


def test_sql_linter_blocks_dangerous_and_non_catalog_sql() -> None:
    assert "keyword_forbidden:drop" in lint_sql("DROP TABLE users").errors
    assert "select_star_forbidden" in lint_sql("SELECT * FROM audience_read.orders_v1").errors
    result = lint_sql(
        "SELECT 'external_userid' AS identity_type, external_userid AS identity_value, external_userid AS event_source_key, '{}'::jsonb AS payload_json FROM public.users"
    )
    assert "dependency_not_allowed:public.users" in result.errors


def test_sql_linter_allows_group_chat_members_catalog_view() -> None:
    sql = """
        SELECT
            'external_userid' AS identity_type,
            external_userid AS identity_value,
            'group_chat_member:' || chat_id || ':' || external_userid AS event_source_key,
            payload_json,
            external_userid,
            owner_userid,
            joined_at AS event_at
        FROM audience_read.group_chat_members_v1
        WHERE chat_id = :chat_id
    """

    result = lint_sql(sql)

    assert result.ok is True
    assert result.errors == []
    assert "audience_read.group_chat_members_v1" in result.dependencies
    assert "audience_read.group_chat_members_v1" in ALLOWED_VIEWS
    assert any(item["name"] == "audience_read.group_chat_members_v1" for item in schema_catalog_payload()["views"])


def test_next_daily_refresh_uses_package_timezone_and_time() -> None:
    after = datetime(2026, 6, 22, 20, 5, tzinfo=timezone.utc)

    assert next_daily_refresh_at("03:00", "Asia/Shanghai", after=after) == datetime(2026, 6, 23, 19, 0, tzinfo=timezone.utc)


def test_ai_audience_scheduler_emits_incremental_every_run_and_daily_only_in_2am_window() -> None:
    class Service:
        def emit_tick(self, tick_type):
            return {"ok": True, "tick_type": tick_type}

    inside_window = datetime(2026, 6, 24, 2, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    outside_window = datetime(2026, 6, 24, 1, 59, tzinfo=ZoneInfo("Asia/Shanghai"))

    from aicrm_next.ai_audience_ops import scheduler as scheduler_module

    original = scheduler_module.AudiencePackageService
    scheduler_module.AudiencePackageService = lambda: Service()
    try:
        due = emit_due_ticks(now=inside_window, daily_refresh_time="02:00", daily_window_minutes=60)
        not_due = emit_due_ticks(now=outside_window, daily_refresh_time="02:00", daily_window_minutes=60)
    finally:
        scheduler_module.AudiencePackageService = original

    assert [item["tick_type"] for item in due["items"]] == ["incremental", "daily"]
    assert due["daily_tick_due"] is True
    assert [item["tick_type"] for item in not_due["items"]] == ["incremental"]
    assert not_due["daily_tick_due"] is False


def test_ai_audience_scheduler_emits_daily_for_launch_refresh_due_outside_2am_window() -> None:
    class Service:
        def emit_tick(self, tick_type):
            return {"ok": True, "tick_type": tick_type}

        def has_launch_refresh_due(self, refresh_kind):
            return refresh_kind == "daily"

    outside_window = datetime(2026, 6, 24, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    from aicrm_next.ai_audience_ops import scheduler as scheduler_module

    original = scheduler_module.AudiencePackageService
    scheduler_module.AudiencePackageService = lambda: Service()
    try:
        due = emit_due_ticks(now=outside_window, daily_refresh_time="02:00", daily_window_minutes=60)
    finally:
        scheduler_module.AudiencePackageService = original

    assert [item["tick_type"] for item in due["items"]] == ["incremental", "daily"]
    assert due["daily_tick_due"] is True
    assert due["daily_tick_window_due"] is False


def test_ai_audience_scheduler_consumer_pairs_cover_source_refresh_and_outbound() -> None:
    pairs = ai_audience_event_consumer_pairs()

    assert f"{SOURCE_CHANGED_EVENT}:{SOURCE_POKE_CONSUMER}" in pairs
    assert f"{INCREMENTAL_TICK_EVENT}:{INCREMENTAL_REFRESH_CONSUMER}" in pairs
    assert f"{DAILY_TICK_EVENT}:{DAILY_REFRESH_CONSUMER}" in pairs
    assert f"{RUN_REFRESHED_EVENT}:{OUTBOUND_EFFECT_CONSUMER}" in pairs
    assert f"{MEMBER_EVENT_PREFIX}entered:{OUTBOUND_EFFECT_CONSUMER}" not in pairs
    assert f"{MEMBER_EVENT_PREFIX}updated:{OUTBOUND_EFFECT_CONSUMER}" not in pairs
    assert f"{MEMBER_EVENT_PREFIX}exited:{OUTBOUND_EFFECT_CONSUMER}" not in pairs


@pytest.mark.usefixtures("next_pg_schema")
@pytest.mark.usefixtures("next_pg_schema")
def test_acquire_due_packages_uses_one_minute_due_window(next_client, monkeypatch) -> None:
    near_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "due_window_near_pkg",
            "name": "近窗口包",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    far_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "due_window_far_pkg",
            "name": "远窗口包",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert near_resp.status_code == 200
    assert far_resp.status_code == 200
    near_id = near_resp.json()["package"]["id"]
    far_id = far_resp.json()["package"]["id"]

    assert next_client.post(f"/api/ai/audience/packages/{near_id}/publish", headers=_auth(), json={}).status_code == 200
    assert next_client.post(f"/api/ai/audience/packages/{far_id}/publish", headers=_auth(), json={}).status_code == 200

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_package
                SET next_incremental_refresh_at = CASE
                    WHEN id = :near_id THEN CURRENT_TIMESTAMP + interval '30 seconds'
                    WHEN id = :far_id THEN CURRENT_TIMESTAMP + interval '90 seconds'
                    ELSE next_incremental_refresh_at
                END,
                    last_incremental_watermark_at = CURRENT_TIMESTAMP - interval '10 minutes',
                    lease_token = '',
                    lease_expires_at = NULL
                WHERE id IN (:near_id, :far_id)
                """
            ),
            {"near_id": near_id, "far_id": far_id},
        )
        session.commit()

    packages = build_audience_repository().acquire_due_packages("incremental", limit=10)
    acquired_ids = {int(package["id"]) for package in packages}

    assert near_id in acquired_ids
    assert far_id not in acquired_ids


def test_publish_requires_sql_for_enabled_refresh_modes() -> None:
    class Repo:
        def get_package(self, package_id):
            return {"id": package_id, "incremental_enabled": True, "daily_enabled": True}

        def get_latest_version(self, package_id):
            return {"id": 9, "incremental_sql_text": _valid_incremental_sql(), "snapshot_sql_text": ""}

        def update_version_validation(self, *args, **kwargs):
            return {}

    result = AudiencePackageService(repository=Repo(), internal_events=object()).publish(1)

    assert result["ok"] is False
    assert result["error"] == "sql_validation_failed"
    assert "snapshot_sql_required" in result["validation_errors"]


@pytest.mark.usefixtures("next_pg_schema")
def test_publish_defaults_to_latest_version(next_client, monkeypatch) -> None:
    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "publish_latest_pkg",
            "name": "发布 latest 测试",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]
    v1_id = create_resp.json()["version"]["id"]

    publish_v1 = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_v1.status_code == 200

    v2_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/versions",
        headers=_auth(),
        json={"incremental_sql_text": _valid_incremental_sql() + "\n-- version 2\n"},
    )
    assert v2_resp.status_code == 200
    v2_id = v2_resp.json()["version"]["id"]

    publish_latest = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_latest.status_code == 200
    assert publish_latest.json()["version"]["id"] == v2_id
    assert publish_latest.json()["package"]["current_version_id"] == v2_id

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.execute(
                text(
                    """
                SELECT id, status
                FROM ai_audience_package_version
                WHERE package_id = :package_id
                ORDER BY id
                """
                ),
                {"package_id": package_id},
            )
            .mappings()
            .all()
        )
    statuses = {int(row["id"]): row["status"] for row in rows}
    assert statuses[v1_id] == "archived"
    assert statuses[v2_id] == "published"


@pytest.mark.usefixtures("next_pg_schema")
def test_publish_can_target_specific_version(next_client, monkeypatch) -> None:
    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "publish_specific_pkg",
            "name": "指定发布测试",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]
    v1_id = create_resp.json()["version"]["id"]
    v2_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/versions",
        headers=_auth(),
        json={"incremental_sql_text": _valid_incremental_sql() + "\n-- version 2\n"},
    )
    assert v2_resp.status_code == 200

    publish_v1 = next_client.post(
        f"/api/ai/audience/packages/{package_id}/publish",
        headers=_auth(),
        json={"version_id": v1_id},
    )
    assert publish_v1.status_code == 200
    assert publish_v1.json()["version"]["id"] == v1_id
    assert publish_v1.json()["package"]["current_version_id"] == v1_id


@pytest.mark.usefixtures("next_pg_schema")
def test_daily_snapshot_publish_latest_version_can_exit_member(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)
    test_user = "wm_daily_exit"

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, unionid, follow_user_userid, name, status, updated_at
                )
                VALUES (:external_userid, 'union_daily_exit', 'HuangYouCan', 'Daily Exit User', 'active', CURRENT_TIMESTAMP)
                """
            ),
            {"external_userid": test_user},
        )
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, profile_json, identity_status, created_at, updated_at
                )
                VALUES (
                    'union_daily_exit',
                    :external_userid,
                    jsonb_build_array(CAST(:external_userid AS text)),
                    '{"name":"Daily Exit User"}'::jsonb,
                    'active',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    profile_json = EXCLUDED.profile_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"external_userid": test_user},
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "daily_snapshot_exit_pkg",
            "name": "Daily exited 测试",
            "query_mode": "hybrid",
            "incremental_enabled": False,
            "daily_enabled": True,
            "snapshot_sql_text": _valid_snapshot_sql(include_test_user=True),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]
    v1_id = create_resp.json()["version"]["id"]

    publish_v1 = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_v1.status_code == 200
    entered_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "daily", "params": {"test_external_userid": test_user}},
    )
    assert entered_resp.status_code == 200
    assert entered_resp.json()["accepted"] is True
    assert _execute_latest_refresh_intent(package_id)["entered_count"] == 1

    v2_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/versions",
        headers=_auth(),
        json={"snapshot_sql_text": _valid_snapshot_sql(include_test_user=False)},
    )
    assert v2_resp.status_code == 200
    v2_id = v2_resp.json()["version"]["id"]

    publish_v2 = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_v2.status_code == 200
    assert publish_v2.json()["package"]["current_version_id"] == v2_id
    exited_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "daily", "params": {"test_external_userid": test_user}},
    )
    assert exited_resp.status_code == 200
    assert exited_resp.json()["accepted"] is True
    assert _execute_latest_refresh_intent(package_id)["exited_count"] == 1

    repeat_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "daily", "params": {"test_external_userid": test_user}},
    )
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["accepted"] is True
    assert _execute_latest_refresh_intent(package_id)["exited_count"] == 0

    with session_factory() as session:
        package_row = (
            session.execute(
                text("SELECT current_version_id FROM ai_audience_package WHERE id = :package_id"),
                {"package_id": package_id},
            )
            .mappings()
            .one()
        )
        version_rows = (
            session.execute(
                text("SELECT id, status FROM ai_audience_package_version WHERE package_id = :package_id ORDER BY id"),
                {"package_id": package_id},
            )
            .mappings()
            .all()
        )
        member_row = (
            session.execute(
                text("SELECT status FROM ai_audience_member_current WHERE package_id = :package_id AND unionid = 'union_daily_exit'"),
                {"package_id": package_id},
            )
            .mappings()
            .one()
        )
    assert package_row["current_version_id"] == v2_id
    assert {int(row["id"]): row["status"] for row in version_rows} == {v1_id: "archived", v2_id: "published"}
    assert member_row["status"] == "exited"


@pytest.mark.usefixtures("next_pg_schema")
def test_publish_auto_refreshes_package_on_first_launch(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, staff_id, submitted_at
                )
                VALUES (202, 'union_launch_refresh_001', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP - interval '1 minute')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES ('union_launch_refresh_001', 'wm_launch_refresh_001', '["wm_launch_refresh_001"]'::jsonb, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "launch_auto_refresh_pkg",
            "name": "上线首刷测试",
            "query_mode": "incremental_event",
            "parameters": {"questionnaire_id": 202},
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]

    publish_resp = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_resp.status_code == 200
    body = publish_resp.json()
    assert body["ok"] is True
    assert body["launch_refresh"]["ok"] is True
    assert body["launch_refresh"]["trigger"] == "package_launch"
    assert body["launch_refresh"]["run_type"] == "incremental"
    assert body["launch_refresh"]["signal_created"] is True
    assert body["launch_refresh"]["real_external_call_executed"] is False
    assert _execute_latest_refresh_intent(package_id)["entered_count"] == 1

    with session_factory() as session:
        member_row = (
            session.execute(
                text(
                    """
                SELECT status
                FROM ai_audience_member_current
                WHERE package_id = :package_id AND unionid = 'union_launch_refresh_001'
                """
                ),
                {"package_id": package_id},
            )
            .mappings()
            .one()
        )
        run_rows = (
            session.execute(
                text(
                    """
                SELECT run_type, status
                FROM ai_audience_package_run
                WHERE package_id = :package_id
                ORDER BY id
                """
                ),
                {"package_id": package_id},
            )
            .mappings()
            .all()
        )
    assert member_row["status"] == "active"
    assert [(row["run_type"], row["status"]) for row in run_rows] == [("incremental", "succeeded")]


@pytest.mark.usefixtures("next_pg_schema")
def test_publish_does_not_repeat_launch_refresh_for_active_package(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, staff_id, submitted_at
                )
                VALUES (203, 'union_launch_refresh_002', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP - interval '1 minute')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES ('union_launch_refresh_002', 'wm_launch_refresh_002', '["wm_launch_refresh_002"]'::jsonb, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "launch_auto_refresh_once_pkg",
            "name": "上线只首刷一次测试",
            "query_mode": "incremental_event",
            "parameters": {"questionnaire_id": 203},
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]

    publish_resp = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_resp.status_code == 200
    assert publish_resp.json()["launch_refresh"]["ok"] is True
    assert _execute_latest_refresh_intent(package_id)["ok"] is True

    version_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/versions",
        headers=_auth(),
        json={"incremental_sql_text": _valid_incremental_sql() + "\n-- v2 active package\n", "parameters": {"questionnaire_id": 203}},
    )
    assert version_resp.status_code == 200

    republish_resp = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert republish_resp.status_code == 200
    assert "launch_refresh" not in republish_resp.json()

    with session_factory() as session:
        run_count = session.execute(
            text("SELECT COUNT(*) FROM ai_audience_package_run WHERE package_id = :package_id"),
            {"package_id": package_id},
        ).scalar_one()
    assert run_count == 1


def test_ai_audience_test_agent_webhook_disabled(next_client, monkeypatch) -> None:
    monkeypatch.delenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", raising=False)

    response = next_client.post("/api/ai/audience/test-agent/webhook", json={})

    assert response.status_code == 404
    assert response.json()["error"] == "test_agent_disabled"


def test_ai_audience_test_agent_service_routes_inbound_without_shared_secrets(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", "test_agent_pkg")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", "wm_test_agent")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", "HuangYouCan")
    inbound_calls = []

    class Repo:
        def get_package_by_key(self, package_key):
            return {"id": 11, "package_key": package_key}

        def list_subscriptions(self, package_id, active_only=True, trigger_event_type=""):
            return [{"id": 21, "package_id": package_id}]

    class Inbound:
        def handle(self, package_key, payload, *, raw_body: bytes):
            inbound_calls.append({"package_key": package_key, "payload": payload, "raw_body": raw_body})
            return {"ok": True, "external_effect_job_id": 7, "record_only": False, "real_external_call_executed": False}

    payload = {
        "event_type": "audience.member.entered",
        "package_key": "test_agent_pkg",
        "member_event_id": 123,
        "member": {"external_userid": "wm_test_agent"},
    }
    service = AudienceTestAgentService(repository=Repo(), inbound_service=Inbound())

    result = service.handle(payload)
    assert result["ok"] is True
    assert result["external_effect_job_id"] == 7
    assert result["sender_userid"] == "HuangYouCan"
    assert inbound_calls[0]["package_key"] == "test_agent_pkg"
    callback = inbound_calls[0]["payload"]
    assert json.loads(inbound_calls[0]["raw_body"]) == callback
    assert callback["external_event_id"] == "self_agent:test_agent_pkg:123"
    assert callback["message"]["text"] == TEST_AGENT_MESSAGE_TEXT
    assert callback["action"] == {
        "type": "send_private_message",
        "target_external_userid": "wm_test_agent",
        "sender_userid": "HuangYouCan",
    }


@pytest.mark.usefixtures("next_pg_schema")
@pytest.mark.usefixtures("next_pg_schema")
def test_ai_audience_test_agent_webhook_business_guards_and_plans_private_message(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", "test_agent_pkg")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", "wm_test_agent")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", "HuangYouCan")
    monkeypatch.delenv("AICRM_AI_AUDIENCE_INBOUND_ACTION_EXECUTE", raising=False)

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "test_agent_pkg",
            "name": "自测 Agent 包",
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]

    sub_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/outbound-subscriptions",
        headers=_auth(),
        json={
            "trigger_event_type": "entered",
            "webhook_url": "https://www.youcangogogo.com/api/ai/audience/test-agent/webhook",
        },
    )
    assert sub_resp.status_code == 200
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('AICRM_AI_AUDIENCE_INBOUND_ACTION_EXECUTE', 'true', CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """
            )
        )
        member_id = session.execute(
            text(
                """
                INSERT INTO ai_audience_member_current (
                    package_id, identity_type, identity_value, unionid, status, event_source_key, payload_hash, payload_json
                )
                VALUES (
                    :package_id, 'external_userid', 'wm_test_agent', '',
                    'active', 'test_agent:member', 'hash-test-agent-member', '{}'::jsonb
                )
                RETURNING id
                """
            ),
            {"package_id": package_id},
        ).scalar_one()
        member_event_id = session.execute(
            text(
                """
                INSERT INTO ai_audience_member_event (
                    package_id, member_current_id, event_type, identity_type, identity_value,
                    unionid, event_source_key, payload_hash, payload_json, idempotency_key, occurred_at
                )
                VALUES (
                    :package_id, :member_id, 'entered', 'external_userid', 'wm_test_agent',
                    '', 'test_agent:member', 'hash-test-agent-event',
                    '{}'::jsonb, 'test_agent_member_event', CURRENT_TIMESTAMP
                )
                RETURNING id
                """
            ),
            {"package_id": package_id, "member_id": member_id},
        ).scalar_one()
        session.commit()

    payload = {
        "event_type": "audience.member.entered",
        "package_key": "test_agent_pkg",
        "package_name": "自测 Agent 包",
        "member_event_id": member_event_id,
        "member": {
            "external_userid": "wm_test_agent",
            "owner_userid": "HuangYouCan",
        },
        "payload": {"test_case": "self_agent"},
        "idempotency_key": "ai_audience_outbound:test:123",
    }
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", "wm_other")
    forbidden_resp = next_client.post(
        "/api/ai/audience/test-agent/webhook",
        json=payload,
    )
    assert forbidden_resp.status_code == 403
    assert forbidden_resp.json()["error"] == "external_userid_not_allowed"
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", "wm_test_agent")

    response = next_client.post(
        "/api/ai/audience/test-agent/webhook",
        json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["external_userid"] == "wm_test_agent"
    assert body["sender_userid"] == "HuangYouCan"
    assert body["simulated_message"] == TEST_AGENT_MESSAGE_TEXT
    assert body["record_only"] is True
    assert body["external_effect_job_id"] is None
    planned = AudienceInboundWebhookService().process_record(
        int(body["inbound_result"]["recorded"]["id"]),
        parent_execution_id=body["inbound_result"]["execution_id"],
    )
    assert planned["ok"] is True
    assert planned["external_effect_job_id"]
    external_effect_job_id = int(planned["external_effect_job_id"])

    jobs, total = ExternalEffectService().list_jobs(
        {
            "effect_type": WECOM_MESSAGE_PRIVATE_SEND,
            "business_type": "ai_audience_inbound_webhook",
            "business_id": f"self_agent:test_agent_pkg:{member_event_id}",
        },
        limit=10,
    )
    assert total == 1
    assert len(jobs) == 1
    assert jobs[0].id == external_effect_job_id
    assert jobs[0].target_id == "wm_test_agent"
    assert jobs[0].payload_json["owner_userid"] == "HuangYouCan"
    assert jobs[0].payload_json["content_text"] == TEST_AGENT_MESSAGE_TEXT

    duplicate_resp = next_client.post(
        "/api/ai/audience/test-agent/webhook",
        json=payload,
    )
    assert duplicate_resp.status_code == 200
    assert duplicate_resp.json()["external_effect_job_id"] == external_effect_job_id
    duplicate_jobs, duplicate_total = ExternalEffectService().list_jobs(
        {
            "effect_type": WECOM_MESSAGE_PRIVATE_SEND,
            "business_type": "ai_audience_inbound_webhook",
            "business_id": f"self_agent:test_agent_pkg:{member_event_id}",
        },
        limit=10,
    )
    assert duplicate_total == 1
    assert duplicate_jobs[0].id == external_effect_job_id


@pytest.mark.usefixtures("next_pg_schema")
def test_create_outbound_subscription_deduplicates_active_target(next_client, monkeypatch) -> None:
    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={"package_key": "subscription_dedupe_pkg", "name": "订阅去重测试"},
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]
    webhook_url = "https://agent.example.test/audience"

    first_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/outbound-subscriptions",
        headers=_auth(),
        json={
            "trigger_event_type": "entered",
            "webhook_url": webhook_url,
            "headers": {"X-Test": "v1"},
        },
    )
    assert first_resp.status_code == 200
    assert first_resp.json()["deduplicated"] is False
    first_id = first_resp.json()["subscription"]["id"]

    second_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/outbound-subscriptions",
        headers=_auth(),
        json={
            "trigger_event_type": "entered",
            "webhook_url": webhook_url,
            "headers": {"X-Test": "v2"},
            "max_attempts": 7,
        },
    )
    assert second_resp.status_code == 200
    assert second_resp.json()["deduplicated"] is True
    assert second_resp.json()["subscription"]["id"] == first_id
    assert "signing_secret" not in second_resp.json()["subscription"]
    assert second_resp.json()["subscription"]["headers_json"] == {"X-Test": "v2"}
    assert second_resp.json()["subscription"]["max_attempts"] == 7

    session_factory = get_session_factory()
    with session_factory() as session:
        active_count = (
            session.execute(
                text(
                    """
                SELECT COUNT(*) AS count
                FROM ai_audience_outbound_subscription
                WHERE package_id = :package_id
                  AND status = 'active'
                  AND trigger_event_type = 'entered'
                  AND target_type = 'webhook'
                  AND webhook_url = :webhook_url
                """
                ),
                {"package_id": package_id, "webhook_url": webhook_url},
            )
            .mappings()
            .one()["count"]
        )
    assert active_count == 1


def test_outbound_planner_deduplicates_historical_duplicate_subscriptions() -> None:
    calls: list[dict[str, Any]] = []

    class Repo:
        def get_member_event(self, member_event_id):
            return {
                "id": member_event_id,
                "package_id": 9,
                "event_type": "entered",
                "identity_type": "external_userid",
                "identity_value": "wm_hist",
                "external_userid": "wm_hist",
                "owner_userid": "HuangYouCan",
                "payload_json": {"case": "historical_duplicate"},
                "internal_event_id": "evt_hist",
            }

        def get_package(self, package_id):
            return {"id": package_id, "package_key": "hist_dup_pkg", "name": "历史重复订阅包"}

        def list_subscriptions(self, package_id, *, active_only=False, trigger_event_type=""):
            duplicate = {
                "package_id": package_id,
                "trigger_event_type": trigger_event_type,
                "target_type": "webhook",
                "webhook_url": "https://agent.example.test/audience",
                "signing_secret": "secret",
                "execution_mode": "execute",
                "requires_approval": False,
                "max_attempts": 5,
            }
            return [{**duplicate, "id": 1}, {**duplicate, "id": 2}]

    class Effects:
        def plan_effect(self, **kwargs):
            calls.append(kwargs)
            return {"id": len(calls), "idempotency_key": kwargs["idempotency_key"]}

    result = AudienceOutboundService(repository=Repo(), external_effects=Effects()).plan_for_member_event(88)

    assert result["ok"] is True
    assert result["planned_count"] == 1
    assert len(calls) == 1
    assert calls[0]["idempotency_key"].startswith("ai_audience_outbound:9:88:entered:")
    assert calls[0]["idempotency_key"] != "ai_audience_outbound:1:88"


@pytest.mark.usefixtures("next_pg_schema")
def test_package_refresh_uses_internal_event_and_external_effect_queue(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", f"{RUN_REFRESHED_EVENT}:{OUTBOUND_EFFECT_CONSUMER}")

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES
                    ('union_ai_audience_001', 'wm_ai_audience_001', '["wm_ai_audience_001"]'::jsonb, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('union_ai_audience_002', 'wm_ai_audience_002', '["wm_ai_audience_002"]'::jsonb, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, staff_id, submitted_at
                )
                VALUES
                    (101, 'union_ai_audience_001', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP - interval '1 minute'),
                    (101, 'union_ai_audience_002', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP - interval '1 minute')
                """
            )
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "q101_submitted_added_wecom",
            "name": "提交 101 问卷且已加微",
            "natural_language_definition": "提交 101 问卷",
            "query_mode": "incremental_event",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]

    publish_resp = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_resp.status_code == 200
    assert publish_resp.json()["ok"] is True

    sub_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/outbound-subscriptions",
        headers=_auth(),
        json={"trigger_event_type": "entered", "webhook_url": "https://agent.example.test/audience", "signing_secret": "outbound-secret"},
    )
    assert sub_resp.status_code == 200

    refresh_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "incremental", "params": {"questionnaire_id": 101}},
    )
    assert refresh_resp.status_code == 200
    body = refresh_resp.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    body = _execute_latest_refresh_intent(package_id)
    assert body["entered_count"] == 2
    assert body["member_event_count"] == 2
    completion_outbox = body["completion"]["completion_event"]
    from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay

    relay = InternalEventOutboxRelay(
        consumer_registry=next_client.app.state.internal_event_consumer_registry,
    )
    relayed = relay.relay_due(limit=100)
    run_event_id = next(
        item["event_id"]
        for item in relayed["items"]
        if item.get("outbox_id") == completion_outbox["outbox_id"]
    )

    with session_factory() as session:
        member_events = (
            session.execute(
                text(
                    """
                SELECT id, internal_event_id
                FROM ai_audience_member_event
                WHERE package_id = :package_id
                ORDER BY id ASC
                """
                ),
                {"package_id": package_id},
            )
            .mappings()
            .all()
        )
    assert len(member_events) == 2
    member_event = member_events[0]
    assert member_event["internal_event_id"]

    worker = InternalEventWorker(
        consumer_registry=next_client.app.state.internal_event_consumer_registry,
    )
    dry_run = worker.dispatch_one_consumer(
        run_event_id,
        OUTBOUND_EFFECT_CONSUMER,
        dry_run=True,
    )
    assert dry_run["ok"] is True
    worker_result = worker.dispatch_one_consumer(
        run_event_id,
        OUTBOUND_EFFECT_CONSUMER,
        dry_run=False,
    )
    assert worker_result["ok"] is True
    assert worker_result["counts"]["succeeded_count"] == 1
    for _ in range(2):
        repeat_result = worker.dispatch_one_consumer(
            run_event_id,
            OUTBOUND_EFFECT_CONSUMER,
            dry_run=False,
            force=True,
            reason="test_repeat_idempotency",
        )
        assert repeat_result["ok"] is True

    effects_resp = next_client.get(f"/api/ai/audience/packages/{package_id}/external-effects", headers=_auth())
    assert effects_resp.status_code == 200
    jobs = effects_resp.json()["external_effect_jobs"]
    assert len(jobs) == 1
    assert jobs[0]["effect_type"] == WEBHOOK_GENERIC_PUSH
    assert jobs[0]["business_type"] == "ai_audience_package_run"
    assert jobs[0]["parent_execution_id"] == completion_outbox["execution_id"]
    assert jobs[0]["lane"] == "outbound_webhook"
    assert jobs[0]["payload_json"]["body"] == ["wm_ai_audience_001", "wm_ai_audience_002"]
    assert jobs[0]["payload_json"]["headers"]["X-AICRM-Package-Key"] == "q101_submitted_added_wecom"
    assert jobs[0]["payload_json"]["headers"]["X-AICRM-Event-Type"] == "audience.incremental.entered"
    assert jobs[0]["payload_json"]["headers"]["X-AICRM-Refresh-Run-Id"]
    assert jobs[0]["payload_json"]["headers"]["X-AICRM-Idempotency-Key"]
    assert "X-AICRM-Signature" not in jobs[0]["payload_json"]["headers"]
    assert "package_key" not in jobs[0]["payload_json"]["body"]
    assert ExternalEffectService().list_attempts(int(jobs[0]["id"])) == []


@pytest.mark.usefixtures("next_pg_schema")
def test_refresh_run_compensates_questionnaire_member_after_late_wecom_identity(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)
    session_factory = get_session_factory()
    unionid = "union_questionnaire_then_wecom_001"
    external_userid = "wm_questionnaire_then_wecom_001"
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, staff_id, submitted_at
                )
                VALUES (991, :unionid, '', '', CURRENT_TIMESTAMP - interval '1 minute')
                """
            ),
            {"unionid": unionid},
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "questionnaire_then_wecom_activation",
            "name": "先填问卷后加企微激活",
            "natural_language_definition": "提交问卷即入包，后置企微身份就绪后补触发。",
            "query_mode": "incremental_event",
            "incremental_sql_text": _valid_incremental_sql(),
        },
    )
    assert create_resp.status_code == 200
    package_id = int(create_resp.json()["package"]["id"])
    assert next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={}).status_code == 200
    assert (
        next_client.post(
            f"/api/ai/audience/packages/{package_id}/outbound-subscriptions",
            headers=_auth(),
            json={"trigger_event_type": "entered", "webhook_url": "https://agent.example.test/audience"},
        ).status_code
        == 200
    )

    first_refresh = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "incremental", "params": {"questionnaire_id": 991}},
    ).json()
    assert first_refresh["accepted"] is True
    first_refresh = _execute_latest_refresh_intent(package_id)
    assert first_refresh["entered_count"] == 1
    first_run_id = int(first_refresh["run"]["id"])
    repo = build_audience_repository()

    deferred = AudienceOutboundService(repository=repo).plan_for_refresh_run(first_run_id)

    assert deferred["ok"] is True
    assert deferred["planned_count"] == 0

    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json,
                    identity_status, created_at, updated_at
                )
                VALUES (
                    :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)),
                    'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"unionid": unionid, "external_userid": external_userid},
        )
        session.commit()

    second_refresh = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "incremental", "params": {"questionnaire_id": 991}},
    ).json()
    assert second_refresh["accepted"] is True
    second_refresh = _execute_latest_refresh_intent(package_id)
    assert second_refresh["entered_count"] == 0
    second_run_id = int(second_refresh["run"]["id"])

    compensated = AudienceOutboundService(repository=repo).plan_for_refresh_run(second_run_id)

    assert compensated["ok"] is True
    assert compensated["planned_count"] == 1
    assert compensated["late_identity_planned_count"] == 1
    assert compensated["source_run_ids"] == [first_run_id]
    job = compensated["external_effect_jobs"][0]
    assert job["business_id"] == str(first_run_id)
    assert job["payload_json"]["body"] == [external_userid]
    assert job["payload_json"]["headers"]["X-AICRM-Refresh-Run-Id"] == str(first_run_id)
    assert job["payload_summary_json"]["late_identity_compensation"] is True

    deduplicated = AudienceOutboundService(repository=repo).plan_for_refresh_run(second_run_id)

    assert deduplicated["planned_count"] == 0
    with session_factory() as session:
        job_count = session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM external_effect_job
                WHERE business_type = 'ai_audience_package_run'
                  AND business_id = :business_id
                """
            ),
            {"business_id": str(first_run_id)},
        ).scalar_one()
    assert job_count == 1


@pytest.mark.usefixtures("next_pg_schema")
def test_source_dirty_emits_existing_internal_event_queue(next_client, monkeypatch) -> None:
    response = next_client.post(
        "/api/ai/audience/source-dirty",
        headers=_auth(),
        json={
            "source_type": "questionnaire_submission",
            "source_key": "questionnaire:101",
            "identity_type": "external_userid",
            "identity_value": "wm_dirty",
            "occurred_at": "2026-06-23T10:00:00+08:00",
            "payload": {"submission_id": 123},
        },
    )
    assert response.status_code == 200
    assert response.json()["event"]["event_type"] == "ai_audience.source.changed"


@pytest.mark.usefixtures("next_pg_schema")
def test_inbound_webhook_requires_hmac_and_records_event(next_client, monkeypatch) -> None:
    secret = "inbound-secret"
    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "agent_callback_pkg",
            "name": "Agent 回调包",
            "inbound_webhook_secret": secret,
        },
    )
    assert create_resp.status_code == 200
    package_id = create_resp.json()["package"]["id"]
    with get_session_factory()() as session:
        member_id = session.execute(
            text(
                """
                INSERT INTO ai_audience_member_current (
                    package_id, identity_type, identity_value, unionid, status, event_source_key, payload_hash, payload_json
                )
                VALUES (
                    :package_id, 'unionid', 'union_agent_callback', 'union_agent_callback',
                    'active', 'agent_callback:member', 'hash-agent-callback', '{}'::jsonb
                )
                RETURNING id
                """
            ),
            {"package_id": package_id},
        ).scalar_one()
        member_event_id = session.execute(
            text(
                """
                INSERT INTO ai_audience_member_event (
                    package_id, member_current_id, event_type, identity_type, identity_value,
                    unionid, event_source_key, payload_hash, payload_json, idempotency_key, occurred_at
                )
                VALUES (
                    :package_id, :member_id, 'entered', 'unionid', 'union_agent_callback',
                    'union_agent_callback', 'agent_callback:member', 'hash-agent-callback',
                    '{}'::jsonb, 'agent_callback_member_event', CURRENT_TIMESTAMP
                )
                RETURNING id
                """
            ),
            {"package_id": package_id, "member_id": member_id},
        ).scalar_one()
        session.commit()
    payload = {
        "external_event_id": "agent_run_abc",
        "member_event_id": member_event_id,
        "status": "generated",
        "message": {"text": "hello"},
        "action": {"type": "record_only"},
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    response = next_client.post(
        "/api/ai/audience/packages/agent_callback_pkg/webhook",
        content=raw,
        headers={"X-AICRM-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["record_only"] is True
