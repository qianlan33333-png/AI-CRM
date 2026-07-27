from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from typing import Any, Callable

import pytest

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.internal_events.questionnaire import (
    build_questionnaire_submitted_event_request,
)
from aicrm_next.extensions.forms.questionnaire import repo as questionnaire_repo
from aicrm_next.extensions.forms.questionnaire.reconciliation import QuestionnaireRadarReconciliationService
from aicrm_next.extensions.forms.questionnaire.repo import PostgresQuestionnaireReadRepository


@pytest.fixture()
def database_url() -> str:
    value = os.environ.get("AICRM_TEST_DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL", "").strip()
    assert value
    return value


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


@contextmanager
def _insert_failure(database_url: str, *, table: str, suffix: str):
    from psycopg import sql

    function_name = f"r09_fail_{suffix}"
    trigger_name = f"r09_fail_{suffix}_trigger"
    with _connect(database_url) as conn:
        conn.execute(
            sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS {}").format(
                sql.Identifier(function_name),
                sql.Literal(f"BEGIN RAISE EXCEPTION 'injected {table} insert failure'; END"),
            )
        )
        conn.execute(
            sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}()").format(
                sql.Identifier(trigger_name),
                sql.Identifier(table),
                sql.Identifier(function_name),
            )
        )
        conn.commit()
    try:
        yield
    finally:
        with _connect(database_url) as conn:
            conn.execute(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                    sql.Identifier(trigger_name),
                    sql.Identifier(table),
                )
            )
            conn.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(sql.Identifier(function_name)))
            conn.commit()


def _seed_questionnaire(database_url: str, *, suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with _connect(database_url) as conn:
        questionnaire = dict(
            conn.execute(
                """
                INSERT INTO questionnaires (
                    slug, name, title, created_at, updated_at
                ) VALUES (%s, %s, %s, NOW(), NOW())
                RETURNING id, slug, name, title
                """,
                (
                    f"r09-{suffix}",
                    f"R09 {suffix}",
                    f"R09 {suffix}",
                ),
            ).fetchone()
            or {}
        )
        question = dict(
            conn.execute(
                """
                INSERT INTO questionnaire_questions (
                    questionnaire_id, type, title, required, sort_order, created_at, updated_at
                ) VALUES (%s, 'single_choice', 'R09 choice', TRUE, 1, NOW(), NOW())
                RETURNING id, questionnaire_id, type, title
                """,
                (int(questionnaire["id"]),),
            ).fetchone()
            or {}
        )
        option = dict(
            conn.execute(
                """
                INSERT INTO questionnaire_options (
                    question_id, option_text, score, tag_codes, sort_order, created_at, updated_at
                ) VALUES (%s, 'yes', 10, '["tag_r09"]'::jsonb, 1, NOW(), NOW())
                RETURNING id
                """,
                (int(question["id"]),),
            ).fetchone()
            or {}
        )
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                primary_openid, openids_json, mobile, mobile_normalized,
                primary_owner_userid, identity_status, created_at, updated_at
            ) VALUES (%s, %s, %s::jsonb, '', '[]'::jsonb, '', '', %s, 'active', NOW(), NOW())
            """,
            (
                f"union-r09-{suffix}",
                f"wm-r09-{suffix}",
                f'["wm-r09-{suffix}"]',
                f"owner-r09-{suffix}",
            ),
        )
        conn.commit()
    return questionnaire, {**question, "option_id": int(option["id"])}


def _submission_payload(questionnaire: dict[str, Any], question: dict[str, Any], *, suffix: str) -> dict[str, Any]:
    return {
        "questionnaire_id": int(questionnaire["id"]),
        "slug": questionnaire["slug"],
        "answers": {str(question["id"]): int(question["option_id"])},
        "result_json": {"score": 10, "final_tags": ["tag_r09"]},
        "source_json": {"source_channel": "r09_pg"},
        "respondent_identity": {
            "unionid": f"union-r09-{suffix}",
            "external_userid": f"wm-r09-{suffix}",
        },
        "unionid": f"union-r09-{suffix}",
        "external_userid": f"wm-r09-{suffix}",
        "follow_user_userid": f"owner-r09-{suffix}",
        "final_tags": ["tag_r09"],
        "result_token": f"result-r09-{suffix}",
        "status": "submitted",
    }


def _event_factory(
    questionnaire: dict[str, Any],
    *,
    suffix: str,
) -> Callable[[dict[str, Any]], Any]:
    questionnaire_payload = {
        **questionnaire,
        "external_push_config": {
            "enabled": True,
            "webhook_url": "https://hooks.example.invalid/questionnaire",
        },
    }

    def factory(submission: dict[str, Any]):
        return build_questionnaire_submitted_event_request(
            questionnaire=questionnaire_payload,
            submission=submission,
            answer_snapshots=list(submission.get("answer_snapshots") or []),
            context=CommandContext(
                actor_id="r09-test",
                actor_type="test",
                request_id=f"req-{suffix}",
                trace_id=f"trace-{suffix}",
                source_route="/tests/r09-questionnaire",
            ),
            source_command_id=f"cmd-{suffix}",
        )

    return factory


def test_submission_answers_and_internal_event_outbox_commit_together(database_url: str) -> None:
    questionnaire, question = _seed_questionnaire(database_url, suffix="commit")
    repository = PostgresQuestionnaireReadRepository(database_url=database_url)

    submission = repository.create_submission(
        _submission_payload(questionnaire, question, suffix="commit"),
        internal_event_factory=_event_factory(questionnaire, suffix="commit"),
    )

    with _connect(database_url) as conn:
        submission_count = int(
            conn.execute(
                "SELECT COUNT(*) AS total FROM questionnaire_submissions WHERE id = %s",
                (int(submission["id"]),),
            ).fetchone()["total"]
        )
        answer_count = int(
            conn.execute(
                "SELECT COUNT(*) AS total FROM questionnaire_submission_answers WHERE submission_id = %s",
                (int(submission["id"]),),
            ).fetchone()["total"]
        )
        outboxes = conn.execute("SELECT event_type, aggregate_id, idempotency_key, status FROM internal_event_outbox").fetchall()

    assert submission_count == 1
    assert answer_count == 1
    assert [dict(row) for row in outboxes] == [
        {
            "event_type": "questionnaire.submitted",
            "aggregate_id": str(submission["id"]),
            "idempotency_key": f"questionnaire.submitted:{submission['id']}",
            "status": "pending",
        }
    ]
    assert submission["internal_event_outbox"]["status"] == "pending"


def test_outbox_failure_rolls_back_submission_answers_and_resolution_queue(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questionnaire, question = _seed_questionnaire(database_url, suffix="rollback")
    repository = PostgresQuestionnaireReadRepository(database_url=database_url)

    def fail_outbox(_conn, _request):
        raise RuntimeError("injected questionnaire outbox failure")

    monkeypatch.setattr(
        questionnaire_repo,
        "enqueue_transactional_internal_event_outbox",
        fail_outbox,
        raising=False,
    )
    payload = _submission_payload(questionnaire, question, suffix="rollback")
    payload.update(
        {
            "unionid": "",
            "external_userid": "wm-r09-unresolved-rollback",
            "respondent_identity": {"external_userid": "wm-r09-unresolved-rollback"},
        }
    )

    with pytest.raises(RuntimeError, match="injected questionnaire outbox failure"):
        repository.create_submission(
            payload,
            internal_event_factory=_event_factory(questionnaire, suffix="rollback"),
        )

    with _connect(database_url) as conn:
        counts = {
            "submissions": int(conn.execute("SELECT COUNT(*) AS total FROM questionnaire_submissions").fetchone()["total"]),
            "answers": int(conn.execute("SELECT COUNT(*) AS total FROM questionnaire_submission_answers").fetchone()["total"]),
            "outbox": int(conn.execute("SELECT COUNT(*) AS total FROM internal_event_outbox").fetchone()["total"]),
            "resolution_queue": int(conn.execute("SELECT COUNT(*) AS total FROM crm_user_identity_resolution_queue").fetchone()["total"]),
        }

    assert counts == {"submissions": 0, "answers": 0, "outbox": 0, "resolution_queue": 0}


@pytest.mark.parametrize(
    ("table", "suffix"),
    [
        ("questionnaire_submissions", "submission"),
        ("questionnaire_submission_answers", "answer"),
    ],
)
def test_submission_or_answer_insert_failure_rolls_back_the_entire_continuation(
    database_url: str,
    table: str,
    suffix: str,
) -> None:
    questionnaire, question = _seed_questionnaire(database_url, suffix=f"fault-{suffix}")
    repository = PostgresQuestionnaireReadRepository(database_url=database_url)

    with _insert_failure(database_url, table=table, suffix=suffix):
        with pytest.raises(Exception, match=f"injected {table} insert failure"):
            repository.create_submission(
                _submission_payload(questionnaire, question, suffix=f"fault-{suffix}"),
                internal_event_factory=_event_factory(questionnaire, suffix=f"fault-{suffix}"),
            )

    with _connect(database_url) as conn:
        counts = {
            "submissions": int(conn.execute("SELECT COUNT(*) AS total FROM questionnaire_submissions").fetchone()["total"]),
            "answers": int(conn.execute("SELECT COUNT(*) AS total FROM questionnaire_submission_answers").fetchone()["total"]),
            "outbox": int(conn.execute("SELECT COUNT(*) AS total FROM internal_event_outbox").fetchone()["total"]),
            "resolution_queue": int(conn.execute("SELECT COUNT(*) AS total FROM crm_user_identity_resolution_queue").fetchone()["total"]),
        }

    assert counts == {"submissions": 0, "answers": 0, "outbox": 0, "resolution_queue": 0}


def test_concurrent_duplicate_identity_produces_one_submission_and_one_event_lineage(database_url: str) -> None:
    questionnaire, question = _seed_questionnaire(database_url, suffix="concurrent")
    barrier = Barrier(2)

    def submit(index: int) -> tuple[str, str]:
        repository = PostgresQuestionnaireReadRepository(database_url=database_url)
        payload = _submission_payload(questionnaire, question, suffix="concurrent")
        payload["result_token"] = f"result-r09-concurrent-{index}"
        barrier.wait(timeout=10)
        try:
            submission = repository.create_submission(
                payload,
                internal_event_factory=_event_factory(questionnaire, suffix=f"concurrent-{index}"),
            )
        except Exception as exc:
            return "error", str(exc)
        return "created", str(submission["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, [1, 2]))

    with _connect(database_url) as conn:
        submission_count = int(
            conn.execute(
                "SELECT COUNT(*) AS total FROM questionnaire_submissions WHERE questionnaire_id = %s",
                (int(questionnaire["id"]),),
            ).fetchone()["total"]
        )
        outbox_count = int(conn.execute("SELECT COUNT(*) AS total FROM internal_event_outbox WHERE event_type = 'questionnaire.submitted'").fetchone()["total"])

    assert sorted(status for status, _detail in outcomes) == ["created", "error"]
    assert "already_submitted" in next(detail for status, detail in outcomes if status == "error")
    assert submission_count == 1
    assert outbox_count == 1


def test_repair_adds_only_the_missing_outbox_continuation(database_url: str) -> None:
    questionnaire, _question = _seed_questionnaire(database_url, suffix="repair")
    with _connect(database_url) as conn:
        submission_id = int(
            conn.execute(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, total_score,
                    final_tags, result_token, submitted_at
                ) VALUES (%s, %s, %s, 10, '["tag_r09"]'::jsonb, %s, NOW())
                RETURNING id
                """,
                (
                    int(questionnaire["id"]),
                    "union-r09-repair",
                    "owner-r09-repair",
                    "result-r09-repair",
                ),
            ).fetchone()["id"]
        )
        conn.commit()

    result = QuestionnaireRadarReconciliationService(database_url=database_url).repair(
        actor="r09-test-operator",
        reason="approved fixture continuation repair",
        limit=10,
    )

    with _connect(database_url) as conn:
        outbox = dict(conn.execute("SELECT aggregate_id, idempotency_key, payload_summary_json, status FROM internal_event_outbox").fetchone() or {})
        counts = {
            "events": int(conn.execute("SELECT COUNT(*) AS total FROM internal_event").fetchone()["total"]),
            "effects": int(conn.execute("SELECT COUNT(*) AS total FROM external_effect_job").fetchone()["total"]),
            "attempts": int(conn.execute("SELECT COUNT(*) AS total FROM external_effect_attempt").fetchone()["total"]),
            "projections": int(conn.execute("SELECT COUNT(*) AS total FROM contact_tags").fetchone()["total"]),
        }

    assert result["ok"] is True
    assert result["mode"] == "repair_continuation_only"
    assert result["repaired"]["questionnaire_submitted_outbox_count"] == 1
    assert result["consumer_executed"] is False
    assert result["provider_executed"] is False
    assert result["real_external_call_executed"] is False
    assert outbox["aggregate_id"] == str(submission_id)
    assert outbox["idempotency_key"] == f"questionnaire.submitted:{submission_id}"
    assert outbox["status"] == "pending"
    assert outbox["payload_summary_json"]["reconciliation_repair"] is True
    assert "r09-test-operator" not in str(outbox)
    assert "approved fixture continuation repair" not in str(outbox)
    assert counts == {"events": 0, "effects": 0, "attempts": 0, "projections": 0}


def test_reconciliation_ignores_pre_cutover_and_retained_canonical_lineage(database_url: str) -> None:
    questionnaire, _question = _seed_questionnaire(database_url, suffix="reconciliation-scope")
    with _connect(database_url) as conn:
        historical_id = int(
            conn.execute(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, total_score,
                    final_tags, result_token, submitted_at
                ) VALUES (%s, %s, %s, 0, '[]'::jsonb, %s, TIMESTAMPTZ '2026-07-13 05:42:29+00')
                RETURNING id
                """,
                (
                    int(questionnaire["id"]),
                    "union-r09-reconciliation-scope",
                    "owner-r09-reconciliation-scope",
                    "result-r09-reconciliation-historical",
                ),
            ).fetchone()["id"]
        )
        current_id = int(
            conn.execute(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, total_score,
                    final_tags, result_token, submitted_at
                ) VALUES (%s, %s, %s, 0, '[]'::jsonb, %s, TIMESTAMPTZ '2026-07-13 05:42:31+00')
                RETURNING id
                """,
                (
                    int(questionnaire["id"]),
                    "union-r09-reconciliation-scope",
                    "owner-r09-reconciliation-scope",
                    "result-r09-reconciliation-current",
                ),
            ).fetchone()["id"]
        )
        conn.execute(
            """
            INSERT INTO internal_event (
                event_id, event_type, aggregate_type, aggregate_id, idempotency_key
            ) VALUES (%s, 'questionnaire.submitted', 'questionnaire_submission', %s, %s)
            """,
            (
                "iev_r09_reconciliation_scope",
                str(current_id),
                f"questionnaire.submitted:{current_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                source_module, source_event_id, idempotency_key, status
            ) VALUES (
                'wecom.contact.tag.mark', 'test', 'mark', 'unionid', 'legacy-target',
                'channel_entry.application', 'channel_entry.application',
                'r09-legacy-noncanonical-effect', 'failed_terminal'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO questionnaire_external_push_logs (
                questionnaire_id, submission_record_id, retry_from_log_id, status
            ) VALUES (%s, %s, 1, 'failed')
            """,
            (int(questionnaire["id"]), historical_id),
        )
        conn.commit()

    result = QuestionnaireRadarReconciliationService(database_url=database_url).diagnose()

    assert result["counts"]["submission_without_outbox"] == 0
    assert result["counts"]["effect_without_succeeded_planner"] == 0
    assert result["counts"]["stale_legacy_retry_residue"] == 0
