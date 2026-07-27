from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
from psycopg.rows import dict_row

from aicrm_next.crm.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.repo import PostgresIdentityBindingRepository, PostgresIdentityRepository
from aicrm_next.extensions.forms.questionnaire.repo import PostgresQuestionnaireReadRepository
from scripts.ops.check_unionid_identity_cutover import _register_duplicate_alias_conflicts, collect


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _seed_identity(
    *,
    unionid: str,
    external_userid: str,
    openid: str,
    mobile: str = "",
    status: str = "active",
) -> None:
    with psycopg.connect(_database_url()) as conn:
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_openid, openids_json, mobile, mobile_normalized,
                mobile_verified, identity_status
            ) VALUES (
                %s, %s, jsonb_build_array(%s::text),
                %s, jsonb_build_array(%s::text), %s, %s,
                %s, %s
            )
            """,
            (
                unionid,
                external_userid,
                external_userid,
                openid,
                openid,
                mobile,
                mobile,
                bool(mobile),
                status,
            ),
        )


def _counts() -> dict[str, int]:
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        return {
            "people": int(conn.execute("SELECT COUNT(*) AS count FROM people").fetchone()["count"]),
            "bindings": int(conn.execute("SELECT COUNT(*) AS count FROM external_contact_bindings").fetchone()["count"]),
            "queue": int(conn.execute("SELECT COUNT(*) AS count FROM crm_user_identity_resolution_queue").fetchone()["count"]),
            "conflicts": int(conn.execute("SELECT COUNT(*) AS count FROM crm_user_identity_conflicts").fetchone()["count"]),
        }


def test_postgres_resolver_supports_every_alias_and_same_identity_intersection(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-one",
        external_userid="external-r03-one",
        openid="openid-r03-one",
        mobile="13800138001",
    )
    repo = PostgresIdentityRepository(_database_url())

    for request in (
        ResolvePersonIdentityRequest(unionid="union-r03-one"),
        ResolvePersonIdentityRequest(external_userid="external-r03-one"),
        ResolvePersonIdentityRequest(openid="openid-r03-one"),
        ResolvePersonIdentityRequest(mobile="+86 138-0013-8001"),
        ResolvePersonIdentityRequest(
            unionid="union-r03-one",
            external_userid="external-r03-one",
            openid="openid-r03-one",
            mobile="13800138001",
        ),
    ):
        result = repo.resolve_result(request)
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.unionid == "union-r03-one"


def test_postgres_resolver_blocks_cross_field_and_duplicate_alias_conflicts(next_pg_schema) -> None:
    _seed_identity(unionid="union-r03-a", external_userid="external-r03-duplicate", openid="openid-r03-a")
    _seed_identity(unionid="union-r03-b", external_userid="external-r03-duplicate", openid="openid-r03-b")
    repo = PostgresIdentityRepository(_database_url())

    duplicate = repo.resolve_result(ResolvePersonIdentityRequest(external_userid="external-r03-duplicate"))
    disagreement = repo.resolve_result(
        ResolvePersonIdentityRequest(unionid="union-r03-a", openid="openid-r03-b")
    )

    assert duplicate.status == "conflict"
    assert duplicate.reason == "duplicate_alias"
    assert disagreement.status == "conflict"
    assert disagreement.reason == "identity_inputs_disagree"


def test_postgres_resolver_blocks_deleted_canonical_identity(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-deleted",
        external_userid="external-r03-deleted",
        openid="openid-r03-deleted",
        status="deleted",
    )

    result = PostgresIdentityRepository(_database_url()).resolve_result(
        ResolvePersonIdentityRequest(unionid="union-r03-deleted")
    )

    assert result.status == "conflict"
    assert result.reason == "canonical_identity_not_active"


def test_owner_candidates_only_use_columns_guaranteed_by_current_schema(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-owner",
        external_userid="external-r03-owner",
        openid="openid-r03-owner",
    )
    with psycopg.connect(_database_url()) as conn:
        conn.execute(
            """
            INSERT INTO wecom_external_contact_follow_users (
                external_userid, user_id, relation_status, is_primary
            ) VALUES ('external-r03-owner', 'owner-r03', 'active', TRUE)
            """
        )

    owners = PostgresIdentityRepository(_database_url()).list_external_contact_owner_userids(
        "external-r03-owner"
    )

    assert owners == {"owner-r03"}


def test_unresolved_mobile_bind_only_enqueues_and_never_writes_legacy_identity_tables(next_pg_schema) -> None:
    before = _counts()

    result = PostgresIdentityBindingRepository(_database_url()).bind_mobile_to_external_contact(
        external_userid="external-r03-pending",
        mobile="13800138002",
        owner_userid="owner-r03",
        bind_by_userid="operator-r03",
    )
    after = _counts()

    assert result["ok"] is False
    assert result["binding_status"] == "pending"
    assert after["people"] == before["people"]
    assert after["bindings"] == before["bindings"]
    assert after["queue"] == before["queue"] + 1


def test_mobile_alias_owned_by_another_unionid_is_recorded_and_blocked(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-mobile-a",
        external_userid="external-r03-mobile-a",
        openid="openid-r03-mobile-a",
    )
    _seed_identity(
        unionid="union-r03-mobile-b",
        external_userid="external-r03-mobile-b",
        openid="openid-r03-mobile-b",
        mobile="13800138003",
    )
    before = _counts()

    result = PostgresIdentityBindingRepository(_database_url()).bind_mobile_to_external_contact(
        external_userid="external-r03-mobile-a",
        mobile="13800138003",
        force_rebind=True,
    )
    after = _counts()

    assert result["ok"] is False
    assert result["binding_status"] == "conflict"
    assert result["reason"] == "mobile_alias_conflict"
    assert after["conflicts"] == before["conflicts"] + 1
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT mobile_normalized FROM crm_user_identity WHERE unionid = 'union-r03-mobile-a'"
        ).fetchone()
    assert row["mobile_normalized"] == ""


def test_concurrent_same_unionid_mobile_bind_keeps_one_canonical_row(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-concurrent",
        external_userid="external-r03-concurrent",
        openid="openid-r03-concurrent",
    )
    barrier = Barrier(2)

    def bind() -> dict:
        barrier.wait(timeout=5)
        return PostgresIdentityBindingRepository(_database_url()).bind_mobile_to_external_contact(
            external_userid="external-r03-concurrent",
            mobile="13800138004",
            force_rebind=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(bind), pool.submit(bind)]]

    assert all(result["ok"] is True for result in results)
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT unionid, mobile_normalized FROM crm_user_identity WHERE unionid = 'union-r03-concurrent'"
        ).fetchall()
    assert rows == [{"unionid": "union-r03-concurrent", "mobile_normalized": "13800138004"}]


def test_cutover_registers_duplicate_alias_without_selecting_or_mutating_canonical(next_pg_schema) -> None:
    _seed_identity(
        unionid="union-r03-conflict-a",
        external_userid="external-r03-conflict-a",
        openid="openid-r03-conflict-a",
        mobile="13800138005",
    )
    _seed_identity(
        unionid="union-r03-conflict-b",
        external_userid="external-r03-conflict-b",
        openid="openid-r03-conflict-b",
        mobile="13800138005",
    )

    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        before = conn.execute(
            """
            SELECT unionid, mobile_normalized, identity_status
            FROM crm_user_identity
            ORDER BY unionid
            """
        ).fetchall()
        assert _register_duplicate_alias_conflicts(conn) == 1
        payload = collect(conn, release_sha="test-release", phase="preflight", registered_conflict_count=1)
        after = conn.execute(
            """
            SELECT unionid, mobile_normalized, identity_status
            FROM crm_user_identity
            ORDER BY unionid
            """
        ).fetchall()
        assert _register_duplicate_alias_conflicts(conn) == 0

    assert after == before
    assert payload["ok"] is True
    assert payload["counts"]["duplicate_alias_group_count"] == 1
    assert payload["counts"]["blocked_duplicate_alias_group_count"] == 1
    assert payload["counts"]["unregistered_duplicate_alias_group_count"] == 0
    assert payload["registered_conflict_count"] == 1


def test_unresolved_questionnaire_submission_is_saved_and_queued_without_fake_unionid(next_pg_schema) -> None:
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        questionnaire_id = int(
            conn.execute(
                """
                INSERT INTO questionnaires (slug, name, title)
                VALUES ('r03-unresolved', 'R03 unresolved', 'R03 unresolved')
                RETURNING id
                """
            ).fetchone()["id"]
        )

    submission = PostgresQuestionnaireReadRepository(_database_url()).create_submission(
        {
            "questionnaire_id": questionnaire_id,
            "slug": "r03-unresolved",
            "external_userid": "external-r03-unresolved",
            "result_token": "r03-unresolved-result",
        }
    )

    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        stored = conn.execute(
            "SELECT unionid FROM questionnaire_submissions WHERE id = %s",
            (int(submission["id"]),),
        ).fetchone()
        queued = conn.execute(
            """
            SELECT reason, status
            FROM crm_user_identity_resolution_queue
            WHERE source_type = 'questionnaire_submission'
              AND external_userid = 'external-r03-unresolved'
            """
        ).fetchone()

    assert submission["unionid"] == ""
    assert stored == {"unionid": ""}
    assert queued == {"reason": "missing_unionid", "status": "pending"}
