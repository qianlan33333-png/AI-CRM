from __future__ import annotations

import os

import pytest

from aicrm_next.crm.customer_tags.local_projection import project_questionnaire_tags
from aicrm_next.internal_event_composition import register_questionnaire_event_consumers
from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.internal_events.consumer_registry import (
    InternalEventConsumerRegistry,
)
from aicrm_next.platform_foundation.internal_events.questionnaire import (
    QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
)
from aicrm_next.platform_foundation.internal_events.repository import (
    SQLAlchemyInternalEventRepository,
)
from aicrm_next.platform_foundation.internal_events.service import InternalEventService
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.shared.db_session import get_engine, get_session_factory


@pytest.fixture()
def database_url() -> str:
    value = os.environ.get("AICRM_TEST_DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL", "").strip()
    assert value
    return value


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


def test_tag_consumer_reloads_identity_after_backfill_and_plans_one_effect(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", QUESTIONNAIRE_SUBMITTED_EVENT_TYPE)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "questionnaire_tag_consumer")
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", "wecom.contact.tag.mark")
    monkeypatch.setenv("AICRM_WECOM_DEFAULT_SENDER_USERID", "owner-r09-backfill")
    monkeypatch.setenv("WECOM_CORP_ID", "corp-r09-backfill")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "secret-r09-backfill")

    with _connect(database_url) as conn:
        questionnaire_id = int(
            conn.execute(
                """
                INSERT INTO questionnaires (slug, name, title, created_at, updated_at)
                VALUES ('r09-backfill', 'R09 backfill', 'R09 backfill', NOW(), NOW())
                RETURNING id
                """
            ).fetchone()["id"]
        )
        submission_id = int(
            conn.execute(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, total_score,
                    final_tags, result_token, submitted_at
                    ) VALUES (%s, '', '', 10, '["tag_r09_backfill"]'::jsonb, 'result-r09-backfill', NOW())
                RETURNING id
                """,
                (questionnaire_id,),
            ).fetchone()["id"]
        )
        conn.execute(
            """
            INSERT INTO wecom_corp_tags (
                tag_id, tag_name, group_id, group_name, order_index,
                raw_payload, synced_at, updated_at
            ) VALUES (
                'tag_r09_backfill', 'R09 backfill', 'group-r09', 'R09', 1,
                '{}'::jsonb, NOW(), NOW()
            )
            """
        )
        conn.commit()

    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repository = SQLAlchemyInternalEventRepository(get_session_factory(database_url))
    service = InternalEventService(repository, registry)
    emitted = service.emit_event(
        event_type=QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
        aggregate_type="questionnaire_submission",
        aggregate_id=str(submission_id),
        payload={
            "questionnaire": {"id": questionnaire_id, "slug": "r09-backfill"},
            "submission": {
                "submission_id": str(submission_id),
                "questionnaire_id": questionnaire_id,
                "final_tags": ["tag_r09_backfill"],
            },
        },
        payload_summary={"submission_id": str(submission_id), "final_tag_count": 1},
        context=CommandContext(trace_id="r09-identity-backfill"),
        idempotency_key=f"questionnaire.submitted:{submission_id}",
    )
    worker = InternalEventWorker(repository, registry)

    waiting = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_tag_consumer"],
    )
    assert waiting["counts"]["failed_retryable_count"] == 1, waiting

    with _connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_openid, openids_json, mobile, mobile_normalized,
                primary_owner_userid, identity_status, created_at, updated_at
            ) VALUES (
                'union-r09-backfill', 'wm-r09-backfill', '["wm-r09-backfill"]'::jsonb,
                '', '[]'::jsonb, '', '', 'owner-r09-backfill', 'active', NOW(), NOW()
            )
            """
        )
        conn.execute(
            """
            UPDATE questionnaire_submissions
            SET unionid = 'union-r09-backfill', follow_user_userid = 'owner-r09-backfill'
            WHERE id = %s
            """,
            (submission_id,),
        )
        conn.commit()

    retried = service.retry_consumer_run(
        emitted["event"]["event_id"],
        "questionnaire_tag_consumer",
        actor_id="r09-test-operator",
        actor_type="test",
        reason="canonical identity backfilled",
    )
    assert retried is not None
    completed = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_tag_consumer"],
    )

    with _connect(database_url) as conn:
        jobs = conn.execute(
            """
            SELECT effect_type, target_type, target_id, business_type, business_id, status
            FROM external_effect_job
            WHERE effect_type = 'wecom.contact.tag.mark'
            """
        ).fetchall()

    assert completed["counts"]["succeeded_count"] == 1, completed
    assert [dict(row) for row in jobs] == [
        {
            "effect_type": "wecom.contact.tag.mark",
            "target_type": "unionid",
            "target_id": "union-r09-backfill",
            "business_type": "questionnaire_submission",
            "business_id": str(submission_id),
            "status": "queued",
        }
    ]


def test_questionnaire_tag_projection_persists_external_effect_lineage(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    with _connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_openid, openids_json, mobile, mobile_normalized,
                primary_owner_userid, identity_status, created_at, updated_at
            ) VALUES (
                'union-r09-projection', 'wm-r09-projection', '["wm-r09-projection"]'::jsonb,
                '', '[]'::jsonb, '', '', 'owner-r09-projection', 'active', NOW(), NOW()
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wecom_corp_tags (
                tag_id, tag_name, group_id, group_name, order_index,
                raw_payload, synced_at, updated_at
            ) VALUES (
                'tag_r09_projection', 'R09 projection', 'group-r09', 'R09', 1,
                '{}'::jsonb, NOW(), NOW()
            )
            """
        )
        conn.commit()

    kwargs = {
        "unionid": "union-r09-projection",
        "external_userid": "wm-r09-projection",
        "owner_userid": "owner-r09-projection",
        "tag_ids": ["tag_r09_projection"],
        "source": "questionnaire_external_effect_success",
        "questionnaire_id": 71,
        "submission_id": "701",
        "idempotency_key": "questionnaire.submitted:701:external-effect:wecom.contact.tag.mark",
        "engine": get_engine(database_url),
    }
    first = project_questionnaire_tags(**kwargs)
    replay = project_questionnaire_tags(**kwargs)

    with _connect(database_url) as conn:
        rows = conn.execute(
            """
            SELECT unionid, userid, tag_id, source, questionnaire_id,
                   submission_id, idempotency_key
            FROM contact_tags
            WHERE unionid = 'union-r09-projection'
            """
        ).fetchall()

    assert first["inserted_count"] == 1
    assert replay["updated_count"] == 1
    assert [dict(row) for row in rows] == [
        {
            "unionid": "union-r09-projection",
            "userid": "owner-r09-projection",
            "tag_id": "tag_r09_projection",
            "source": "questionnaire_external_effect_success",
            "questionnaire_id": "71",
            "submission_id": "701",
            "idempotency_key": "questionnaire.submitted:701:external-effect:wecom.contact.tag.mark",
        }
    ]
