"""Append-only acknowledgement for one pre-cutover welcome terminal history.

Revision ID: 0138_queue_terminal_acknowledgement
Revises: 0137_queue_production_scope_cutover
"""

from __future__ import annotations

from alembic import op


revision = "0138_queue_terminal_acknowledgement"
down_revision = "0137_queue_production_scope_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_terminal_acknowledgement (
            acknowledgement_id TEXT PRIMARY KEY,
            acknowledgement_type TEXT NOT NULL,
            job_id BIGINT NOT NULL,
            job_execution_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            graph_id BIGINT NOT NULL,
            release_sha TEXT NOT NULL,
            authorization_base_sha TEXT NOT NULL,
            authorization_confirmation_sha256 TEXT NOT NULL,
            job_fingerprint_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            job_status TEXT NOT NULL,
            error_code TEXT NOT NULL,
            replay_prohibited BOOLEAN NOT NULL DEFAULT TRUE,
            provider_success_claimed BOOLEAN NOT NULL DEFAULT FALSE,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_queue_terminal_ack_type CHECK (
                acknowledgement_type = 'pre_cutover_welcome_41050_no_replay'
            ),
            CONSTRAINT ck_queue_terminal_ack_release_sha CHECK (
                release_sha ~ '^[0-9a-f]{40}$'
            ),
            CONSTRAINT ck_queue_terminal_ack_base_sha CHECK (
                authorization_base_sha ~ '^[0-9a-f]{40}$'
            ),
            CONSTRAINT ck_queue_terminal_ack_confirmation_hash CHECK (
                authorization_confirmation_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_queue_terminal_ack_fingerprint CHECK (
                job_fingerprint_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_queue_terminal_ack_status CHECK (
                status = 'acknowledged_history'
            ),
            CONSTRAINT ck_queue_terminal_ack_job_status CHECK (
                job_status = 'failed_terminal'
            ),
            CONSTRAINT ck_queue_terminal_ack_error CHECK (
                error_code = 'wecom_error_41050'
            ),
            CONSTRAINT ck_queue_terminal_ack_no_replay CHECK (
                replay_prohibited IS TRUE
            ),
            CONSTRAINT ck_queue_terminal_ack_no_success_claim CHECK (
                provider_success_claimed IS FALSE
            ),
            CONSTRAINT ck_queue_terminal_ack_actor_reason CHECK (
                LENGTH(BTRIM(actor)) > 0 AND LENGTH(BTRIM(reason)) > 0
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_terminal_ack_type_job
        ON queue_terminal_acknowledgement (acknowledgement_type, job_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_terminal_ack_job_fingerprint
        ON queue_terminal_acknowledgement (job_id, job_fingerprint_sha256)
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_terminal_ack_append_only
        ON queue_terminal_acknowledgement
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_terminal_ack_append_only
        BEFORE UPDATE OR DELETE ON queue_terminal_acknowledgement
        FOR EACH ROW
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_terminal_ack_reject_truncate
        ON queue_terminal_acknowledgement
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_terminal_ack_reject_truncate
        BEFORE TRUNCATE ON queue_terminal_acknowledgement
        FOR EACH STATEMENT
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )


def downgrade() -> None:
    # Rollback deliberately retains this append-only production authorization evidence.
    pass
