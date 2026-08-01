"""Add immutable AI Audience template version metadata and read projections.

Revision ID: 0165_ai_audience_template_registry
Revises: 0164_ai_audience_send_record_read_index
"""

from __future__ import annotations

from alembic import op


revision = "0165_ai_audience_template_registry"
down_revision = "0164_ai_audience_send_record_read_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS audience_read")
    op.execute(
        "ALTER TABLE ai_audience_package_version ADD COLUMN IF NOT EXISTS template_key TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE ai_audience_package_version ADD COLUMN IF NOT EXISTS template_version INTEGER"
    )
    op.execute(
        "ALTER TABLE ai_audience_package_version ADD COLUMN IF NOT EXISTS template_params_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE ai_audience_package_version ADD COLUMN IF NOT EXISTS template_fingerprint TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_audience_template_fingerprint
        ON ai_audience_package_version (package_id, template_fingerprint)
        WHERE template_fingerprint <> ''
        """
    )
    _create_questionnaire_answers_view()
    _create_radar_clicks_view()
    _create_concurrent_indexes()


def _create_questionnaire_answers_view() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW audience_read.questionnaire_answers_v1 AS
        SELECT
            answers.id AS answer_id,
            submissions.submission_id,
            submissions.questionnaire_id,
            COALESCE(NULLIF(questionnaires.title, ''), questionnaires.name, '')::text AS questionnaire_title,
            answers.question_id,
            answers.question_type,
            COALESCE(NULLIF(answers.question_title_snapshot, ''), questions.title, '')::text AS question_title,
            answers.selected_option_ids,
            answers.selected_option_texts_snapshot AS selected_option_texts,
            COALESCE(submissions.external_userid, '')::text AS external_userid,
            COALESCE(submissions.owner_userid, '')::text AS owner_userid,
            submissions.submitted_at,
            answers.created_at AS answered_at
        FROM questionnaire_submission_answers answers
        JOIN audience_read.questionnaire_submissions_v1 submissions ON submissions.submission_id = answers.submission_id
        JOIN questionnaires ON questionnaires.id = submissions.questionnaire_id
        LEFT JOIN questionnaire_questions questions ON questions.id = answers.question_id
        WHERE submissions.submitted_at IS NOT NULL
        """
    )


def _create_radar_clicks_view() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW audience_read.radar_clicks_v1 AS
        SELECT
            events.id AS click_id,
            links.id AS radar_id,
            links.code::text AS radar_code,
            links.title::text AS radar_title,
            COALESCE(
                NULLIF(events.external_userid, ''),
                NULLIF(identity.primary_external_userid, ''),
                ''
            )::text AS external_userid,
            COALESCE(
                NULLIF(identity.primary_owner_userid, ''),
                NULLIF(events.staff_id_snapshot, ''),
                NULLIF(events.staff_id, ''),
                ''
            )::text AS owner_userid,
            events.stage::text AS stage,
            events.created_at AS clicked_at
        FROM radar_click_events events
        JOIN radar_links links ON links.id = events.link_id
        LEFT JOIN crm_user_identity identity
          ON identity.unionid = events.unionid
         AND COALESCE(identity.identity_status, 'active') = 'active'
        WHERE links.deleted_at IS NULL
          AND (
              events.stage IN ('authorized', 'authorized_click')
              OR (
                  events.stage = 'landing'
                  AND COALESCE(
                      NULLIF(events.external_userid, ''),
                      NULLIF(events.unionid, ''),
                      NULLIF(events.openid, ''),
                      ''
                  ) <> ''
              )
          )
          AND COALESCE(
              NULLIF(events.external_userid, ''),
              NULLIF(identity.primary_external_userid, ''),
              ''
          ) <> ''
        """
    )


def _create_concurrent_indexes() -> None:
    statements = (
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_questionnaire_answers_question_submission
        ON questionnaire_submission_answers (question_id, submission_id, id)
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_questionnaire_answers_selected_option_ids_gin
        ON questionnaire_submission_answers USING GIN (selected_option_ids)
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_questionnaire_questions_exact_title
        ON questionnaire_questions (questionnaire_id, title, id)
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_questionnaire_options_exact_text
        ON questionnaire_options (question_id, option_text, id)
        """,
    )
    for statement in statements:
        with op.get_context().autocommit_block():
            op.execute(statement)


def downgrade() -> None:
    # Release rollback uses the previous application SHA. The additive columns,
    # indexes and read views stay in place so already compiled versions keep
    # running and rollback never requires a destructive database contraction.
    pass
