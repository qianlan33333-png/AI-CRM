from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from aicrm_next.extensions.hxc.hxc_dashboard.application import CreateHxcBroadcastTaskCommand
from aicrm_next.extensions.hxc.hxc_dashboard.dto import HxcBroadcastTaskRequest
from aicrm_next.extensions.hxc.hxc_dashboard.repo import InMemoryHxcDashboardBroadcastRepository
from aicrm_next.extensions.hxc.hxc_dashboard.postgres_repo import PostgresHxcDashboardBroadcastRepository
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError


def test_fixture_repo_returns_predictable_audience_preview() -> None:
    repo = InMemoryHxcDashboardBroadcastRepository()

    preview = repo.preview_audience(
        selected_customer_ids=["union_hxc_001", "union_hxc_002", "mobile_only_001"],
        audience_filter={},
        sender_userid="QianLan",
    )

    assert preview["audience_total"] == 3
    assert preview["eligible_count"] == 1
    assert preview["skipped_count"] == 2
    assert preview["skipped_by_reason"] == {
        "do_not_disturb": 1,
        "missing_unionid": 1,
    }
    assert preview["eligible_unionids"] == ["union_hxc_001"]
    assert preview["eligible_external_userids"] == ["ext_hxc_001"]


@pytest.mark.usefixtures("next_pg_schema")
def test_postgres_preview_resolves_hxc_snapshot_unionids_and_dnd() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json,
                    mobile, mobile_normalized, primary_owner_userid
                )
                VALUES
                    ('union_hxc_ready', 'wm_hxc_ready', '["wm_hxc_ready"]'::jsonb, '13900001001', '13900001001', 'QianLan'),
                    ('union_hxc_dnd', 'wm_hxc_dnd', '["wm_hxc_dnd"]'::jsonb, '13900001002', '13900001002', 'QianLan'),
                    ('union_hxc_mobile', 'wm_hxc_mobile', '["wm_hxc_mobile"]'::jsonb, '13900001003', '13900001003', 'QianLan')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO user_ops_hxc_dashboard_snapshot (
                    mobile, phone_match_key, unionid, owner_userid,
                    hxc_member_hit, hxc_user_hit, funnel_state, refreshed_at
                )
                VALUES
                    ('13900001001', '13900001001', 'union_hxc_ready', 'QianLan', true, true, 'member', CURRENT_TIMESTAMP),
                    ('13900001002', '13900001002', 'union_hxc_dnd', 'QianLan', true, true, 'member', CURRENT_TIMESTAMP),
                    ('13900001003', '13900001003', 'union_hxc_mobile', 'QianLan', true, true, 'member', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO user_ops_do_not_disturb_next (unionid, is_active, reason_code)
                VALUES ('union_hxc_dnd', true, 'pytest')
                """
            )
        )
        session.commit()

    repo = PostgresHxcDashboardBroadcastRepository(os.environ["DATABASE_URL"])

    preview = repo.preview_audience(
        selected_customer_ids=["union_hxc_ready", "union_hxc_dnd", "union_hxc_mobile"],
        audience_filter={},
        sender_userid="QianLan",
    )

    assert preview["audience_total"] == 3
    assert preview["eligible_unionids"] == ["union_hxc_ready", "union_hxc_mobile"]
    assert preview["eligible_external_userids"] == ["wm_hxc_ready", "wm_hxc_mobile"]
    assert preview["skipped_by_reason"] == {"do_not_disturb": 1}


class UnavailablePostgresLikeRepo:
    source_status = "production_postgres_hxc_dashboard"

    def preview_audience(self, **kwargs):
        raise RepositoryProviderError("database unavailable")

    def get_task_by_key(self, **kwargs):
        raise RepositoryProviderError("database unavailable")

    def create_task(self, payload):
        raise RepositoryProviderError("database unavailable")


def test_production_repo_unavailable_returns_production_unavailable_without_fake_success() -> None:
    request = HxcBroadcastTaskRequest(
        source_type="hxc_dashboard_broadcast",
        source_id="pytest",
        idempotency_key="postgres-unavailable",
        sender_userid="QianLan",
        selected_customer_ids=["ext_hxc_001"],
        content_package={"content_text": "hello"},
    )

    result = CreateHxcBroadcastTaskCommand(repo=UnavailablePostgresLikeRepo())(request)

    assert result["ok"] is True
    assert result["task"]["status"] == "production_unavailable"
    assert result["task"]["dispatch_status"] == "not_created"
    assert result["task"]["task_id"] == ""
