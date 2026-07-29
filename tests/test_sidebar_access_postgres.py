from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from aicrm_next.crm.identity_contact.repo import PostgresIdentityRepository
from aicrm_next.crm.sidebar_write.application import (
    SidebarWriteForbiddenError,
    _validate_owner_scope,
)
from aicrm_next.crm.sidebar_write.commands import BindMobileCommand


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def test_postgres_owner_scope_uses_current_relations_and_never_legacy_binding(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as conn:
        conn.execute(
            """
            INSERT INTO external_contact_bindings (
                external_userid, person_id, first_owner_userid, last_owner_userid
            ) VALUES (%s, %s, %s, %s)
            """,
            ("external-r04", "person-r04", "legacy-first", "legacy-last"),
        )
        conn.execute(
            """
            INSERT INTO wecom_external_contact_follow_users (
                external_userid, user_id, relation_status, is_primary
            ) VALUES
                (%s, %s, 'active', TRUE),
                (%s, %s, 'deleted', FALSE)
            """,
            ("external-r04", "active-owner", "external-r04", "deleted-owner"),
        )
        conn.execute(
            """
            INSERT INTO wecom_external_contact_identity_map (
                external_userid, follow_user_userid, status
            ) VALUES (%s, %s, 'deleted')
            """,
            ("external-r04", "deleted-map-owner"),
        )
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_owner_userid, identity_status
            ) VALUES
                (%s, %s, jsonb_build_array(%s::text), %s, 'deleted'),
                (%s, %s, jsonb_build_array(jsonb_build_object('external_userid', %s::text)), %s, 'active'),
                (%s, %s, jsonb_build_array(%s::text), %s, 'pending_merge')
            """,
            (
                "union-r04-deleted",
                "external-r04",
                "external-r04",
                "deleted-canonical-owner",
                "union-r04-active-object",
                "external-r04-primary-other",
                "external-r04",
                "canonical-object-owner",
                "union-r04-pending",
                "external-r04",
                "external-r04",
                "pending-canonical-owner",
            ),
        )

    candidates = PostgresIdentityRepository(_database_url()).list_external_contact_owner_userids(
        "external-r04"
    )

    assert candidates == {"active-owner", "canonical-object-owner"}
    assert "legacy-first" not in candidates
    assert "legacy-last" not in candidates
    assert "deleted-owner" not in candidates
    assert "deleted-map-owner" not in candidates
    assert "deleted-canonical-owner" not in candidates
    assert "pending-canonical-owner" not in candidates


def test_postgres_replay_rechecks_revoked_follow_relation_under_concurrency(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as conn:
        conn.execute(
            """
            INSERT INTO wecom_external_contact_follow_users (
                external_userid, user_id, relation_status, is_primary
            ) VALUES (%s, %s, 'active', TRUE)
            """,
            ("external-r04-replay", "owner-r04"),
        )

    command = BindMobileCommand(
        idempotency_key="r04-replay",
        actor_id="owner-r04",
        actor_type="sidebar_owner",
        external_userid="external-r04-replay",
        payload={"owner_userid": "owner-r04", "mobile": "13800138000"},
        source_route="/api/sidebar/bind-mobile",
    )
    _validate_owner_scope(command)

    with psycopg.connect(_database_url()) as conn:
        conn.execute(
            """
            UPDATE wecom_external_contact_follow_users
            SET relation_status = 'deleted'
            WHERE external_userid = %s AND user_id = %s
            """,
            ("external-r04-replay", "owner-r04"),
        )

    with pytest.raises(SidebarWriteForbiddenError):
        _validate_owner_scope(command)

    def candidates_after_revocation(_: int) -> set[str]:
        return PostgresIdentityRepository(_database_url()).list_external_contact_owner_userids(
            "external-r04-replay"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(candidates_after_revocation, range(32)))

    assert results == [set()] * 32
