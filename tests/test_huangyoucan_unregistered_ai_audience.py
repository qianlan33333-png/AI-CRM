from __future__ import annotations

import importlib
import os

import pytest
from sqlalchemy import text

from aicrm_next.ai_audience_ops.refresh_intents import AudienceRefreshIntentRepository, AudienceRefreshIntentService
from aicrm_next.ai_audience_ops.sql_linter import lint_sql
from aicrm_next.shared.db_session import get_session_factory


TOKEN = "huangyoucan-unregistered-test-token"
MIGRATION = importlib.import_module("migrations.versions.0057_huangyoucan_unregistered_ai_audience")


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_huangyoucan_unregistered_snapshot_sql_is_catalog_allowed() -> None:
    result = lint_sql(MIGRATION.HUANGYOUCAN_UNREGISTERED_SNAPSHOT_SQL)

    assert result.ok is True
    assert result.errors == []
    assert result.dependencies == [
        "audience_read.huangyoucan_registered_identities_v1",
        "audience_read.wecom_contacts_v1",
    ]


def test_huangyoucan_seed_sql_literal_escapes_runtime_parameter_for_alembic() -> None:
    statement = text(f"SELECT '{MIGRATION._snapshot_sql_literal()}'")

    assert "owner_userid" not in statement._bindparams


@pytest.mark.usefixtures("next_pg_schema")
def test_huangyoucan_unregistered_package_refresh_filters_registered_identities(next_client, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", database_url)

    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                CREATE OR REPLACE VIEW audience_read.huangyoucan_registered_identities_v1 AS
                SELECT 'mobile_hash'::text AS identity_type,
                       md5('13800138001')::text AS identity_value,
                       'test.mobile'::text AS registered_source,
                       CURRENT_TIMESTAMP AS registered_at
                UNION ALL
                SELECT 'unionid'::text AS identity_type,
                       'union_registered'::text AS identity_value,
                       'test.unionid'::text AS registered_source,
                       CURRENT_TIMESTAMP AS registered_at
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO people (id, mobile, third_party_user_id, updated_at)
                VALUES
                    (99001, '13800138000', 'wm_new', CURRENT_TIMESTAMP),
                    (99002, '13800138001', 'wm_mobile_registered', CURRENT_TIMESTAMP),
                    (99003, '13800138002', 'wm_union_registered', CURRENT_TIMESTAMP),
                    (99004, '13800138003', 'wm_other_owner', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO external_contact_bindings (
                    external_userid, person_id, first_owner_userid, last_owner_userid, updated_at
                )
                VALUES
                    ('wm_new', '99001', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_mobile_registered', '99002', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_union_registered', '99003', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_other_owner', '99004', 'OtherOwner', 'OtherOwner', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, unionid, openid, follow_user_userid, name, status, updated_at
                )
                VALUES
                    ('wm_new', 'union_new', '', 'HuangYouCan', '未注册客户', 'active', CURRENT_TIMESTAMP),
                    ('wm_mobile_registered', 'union_mobile_only', '', 'HuangYouCan', '手机号已注册', 'active', CURRENT_TIMESTAMP),
                    ('wm_union_registered', 'union_registered', '', 'HuangYouCan', 'unionid已注册', 'active', CURRENT_TIMESTAMP),
                    ('wm_other_owner', 'union_other', '', 'OtherOwner', '其他账号客户', 'active', CURRENT_TIMESTAMP)
                """
            )
        )
        session.commit()

    create_resp = next_client.post(
        "/api/ai/audience/packages",
        headers=_auth(),
        json={
            "package_key": "test_huangyoucan_unregistered",
            "name": "测试 HuangYouCan 未注册人群",
            "query_mode": "snapshot_current",
            "identity_policy": "external_userid",
            "incremental_enabled": False,
            "daily_enabled": True,
            "daily_refresh_time": "02:00",
            "timezone": "Asia/Shanghai",
            "snapshot_sql_text": MIGRATION.HUANGYOUCAN_UNREGISTERED_SNAPSHOT_SQL,
            "parameters": {"owner_userid": "HuangYouCan"},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    package_id = create_resp.json()["package"]["id"]

    publish_resp = next_client.post(f"/api/ai/audience/packages/{package_id}/publish", headers=_auth(), json={})
    assert publish_resp.status_code == 200, publish_resp.text
    publish_body = publish_resp.json()
    assert publish_body["launch_refresh"]["ok"] is True
    intent = AudienceRefreshIntentRepository().get(int(package_id))
    assert intent is not None
    launched = AudienceRefreshIntentService().process_requested(
        package_id=int(package_id),
        signal_generation=int(intent["signal_generation"]),
    )
    assert launched["entered_count"] == 1

    refresh_resp = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers=_auth(),
        json={"run_type": "daily", "params": {"owner_userid": "HuangYouCan"}},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    body = refresh_resp.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    intent = AudienceRefreshIntentRepository().get(int(package_id))
    assert intent is not None
    body = AudienceRefreshIntentService().process_requested(
        package_id=int(package_id),
        signal_generation=int(intent["signal_generation"]),
    )
    assert body["returned_count"] == 1
    assert body["entered_count"] == 0
    assert body["real_external_call_executed"] is False

    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT identity_type, identity_value, unionid, payload_json
                FROM ai_audience_member_current
                WHERE package_id = :package_id AND status = 'active'
                ORDER BY identity_value
                """
            ),
            {"package_id": package_id},
        ).mappings().all()

    assert [row["identity_type"] for row in rows] == ["unionid"]
    assert [row["identity_value"] for row in rows] == ["union_new"]
    assert [row["unionid"] for row in rows] == ["union_new"]
    assert rows[0]["payload_json"]["external_userid"] == "wm_new"
    assert rows[0]["payload_json"]["registered_mobile_match"] is False
    assert rows[0]["payload_json"]["registered_unionid_match"] is False
