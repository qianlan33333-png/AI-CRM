from __future__ import annotations

import json
import os

import psycopg

from aicrm_next.extensions.ai.automation_agents.repository import AutomationAgentRepository
from aicrm_next.identity_contact.repo import PostgresIdentityRepository
from aicrm_next.extensions.forms.questionnaire.repo import PostgresQuestionnaireReadRepository
from aicrm_next.shared.db_session import get_session_factory


EXTERNAL_ALIAS = "external-performance-alias"
UNIONID = "union-performance-alias"


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _plan_nodes(payload: object) -> list[dict]:
    value = json.loads(payload) if isinstance(payload, str) else payload
    root = dict(value[0]["Plan"])
    nodes: list[dict] = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.get("Plans") or [])
    return nodes


def test_runtime_alias_reads_support_object_alias_without_array_expansion(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        questionnaire_id = connection.execute(
            "INSERT INTO questionnaires (slug, name, title) VALUES (%s, %s, %s) RETURNING id",
            ("performance-alias", "Performance alias", "Performance alias"),
        ).fetchone()[0]
        submission_id = connection.execute(
            """
            INSERT INTO questionnaire_submissions (questionnaire_id, unionid, submitted_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (questionnaire_id, UNIONID),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO questionnaire_submission_answers (
                submission_id, question_id, question_type, question_title_snapshot, text_value
            ) VALUES (%s, 0, 'textarea', 'Answer', 'value')
            """,
            (submission_id,),
        )
        connection.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_owner_userid, identity_status
            ) VALUES (
                %s, %s,
                jsonb_build_array(jsonb_build_object('external_userid', CAST(%s AS text))),
                %s, 'active'
            )
            """,
            (UNIONID, "other-primary", EXTERNAL_ALIAS, "owner-performance"),
        )
        connection.execute(
            """
            INSERT INTO automation_agent_webhook_batch (
                batch_id, agent_code, bound_package_key, source_event_type, status
            ) VALUES ('batch-performance-alias', 'agent-performance', '', '', 'queued')
            """
        )

    identity_repository = PostgresIdentityRepository(_database_url())
    questionnaire_repository = PostgresQuestionnaireReadRepository(_database_url())
    automation_repository = AutomationAgentRepository(get_session_factory(_database_url()))

    assert identity_repository.list_external_contact_owner_userids(EXTERNAL_ALIAS) == {"owner-performance"}
    submissions, total = questionnaire_repository.list_external_submissions(
        filters={"external_userid": EXTERNAL_ALIAS},
        limit=20,
        offset=0,
    )
    assert total == 1
    assert [item["unionid"] for item in submissions] == [UNIONID]
    answers = automation_repository.list_questionnaire_submission_answers(
        external_userid=EXTERNAL_ALIAS,
        limit=20,
    )
    assert [item["submission_id"] for item in answers] == [submission_id]
    context = automation_repository.get_bound_audience_context_for_item(
        batch_id="batch-performance-alias",
        agent_code="agent-performance",
        external_userid=EXTERNAL_ALIAS,
    )
    assert context["batch"]["batch_id"] == "batch-performance-alias"


def test_external_alias_containment_uses_the_existing_gin_index(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO crm_user_identity (unionid, primary_external_userid, external_userids_json)
            SELECT
                'union-performance-fill-' || value::text,
                'external-performance-fill-' || value::text,
                '[]'::jsonb
            FROM generate_series(1, 20000) AS value
            """
        )
        connection.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json
            ) VALUES (
                %s, %s,
                jsonb_build_array(jsonb_build_object('external_userid', CAST(%s AS text)))
            )
            """,
            (UNIONID, "other-primary", EXTERNAL_ALIAS),
        )
        questionnaire_id = connection.execute(
            "INSERT INTO questionnaires (slug, name, title) VALUES (%s, %s, %s) RETURNING id",
            ("performance-growth", "Performance growth", "Performance growth"),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO questionnaire_submissions (questionnaire_id, unionid, submitted_at)
            SELECT
                %s,
                'union-performance-submission-' || value::text,
                CURRENT_TIMESTAMP - (value * interval '1 second')
            FROM generate_series(1, 100000) AS value
            """,
            (questionnaire_id,),
        )
        connection.execute(
            """
            INSERT INTO questionnaire_submissions (questionnaire_id, unionid, submitted_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            """,
            (questionnaire_id, UNIONID),
        )
        connection.execute("ANALYZE crm_user_identity")
        connection.execute("ANALYZE questionnaire_submissions")
        plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT unionid
            FROM crm_user_identity
            WHERE primary_external_userid = %s
               OR external_userids_json @> jsonb_build_array(CAST(%s AS text))
               OR external_userids_json @> jsonb_build_array(
                    jsonb_build_object('external_userid', CAST(%s AS text))
               )
            """,
            (EXTERNAL_ALIAS, EXTERNAL_ALIAS, EXTERNAL_ALIAS),
        ).fetchone()[0]
        questionnaire_plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT id
            FROM questionnaire_submissions
            WHERE unionid = %s
            ORDER BY submitted_at DESC, id DESC
            LIMIT 20
            """,
            (UNIONID,),
        ).fetchone()[0]

    nodes = _plan_nodes(plan)
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert "idx_crm_user_identity_external_userids_json" in index_names
    assert not any(node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "crm_user_identity" for node in nodes)
    assert max(int(node.get("Actual Rows") or 0) for node in nodes) <= 1

    questionnaire_nodes = _plan_nodes(questionnaire_plan)
    questionnaire_index_names = {str(node.get("Index Name") or "") for node in questionnaire_nodes}
    assert "idx_questionnaire_submissions_unionid_submitted" in questionnaire_index_names
    assert not any(node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "questionnaire_submissions" for node in questionnaire_nodes)
    assert max(int(node.get("Actual Rows") or 0) for node in questionnaire_nodes) <= 20
