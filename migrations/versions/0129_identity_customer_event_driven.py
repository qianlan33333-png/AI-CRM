"""Identity resolution and customer read-model event-driven intents.

Revision ID: 0129_identity_customer_event_driven
Revises: 0128_ai_audience_refresh_intents
"""

from __future__ import annotations

from alembic import op


revision = "0129_identity_customer_event_driven"
down_revision = "0128_ai_audience_refresh_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        ADD COLUMN IF NOT EXISTS provider_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS provider_result_hash TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS provider_result_consumed_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE crm_user_identity_resolution_queue
        DROP CONSTRAINT IF EXISTS crm_user_identity_resolution_queue_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE crm_user_identity_resolution_queue
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS external_effect_job_id BIGINT REFERENCES external_effect_job(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'wecom_interactive',
        ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS hold_reason TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS held_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
        ADD CONSTRAINT crm_user_identity_resolution_queue_status_check
            CHECK (status IN ('pending', 'polling', 'resolved', 'conflict', 'failed', 'ignored', 'held')),
        ADD CONSTRAINT ck_identity_resolution_queue_lane
            CHECK (lane = 'wecom_interactive')
        """
    )
    op.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'held',
            hold_reason = 'pre_event_driven_cutover_requires_manual_classification',
            held_at = CURRENT_TIMESTAMP,
            next_attempt_at = NULL,
            row_version = row_version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('pending', 'polling')
        """
    )
    op.execute(
        """
        ALTER TABLE automation_channel_entry_runtime
        ADD COLUMN IF NOT EXISTS identity_external_effect_job_id BIGINT REFERENCES external_effect_job(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS identity_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS identity_parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS identity_hold_reason TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS identity_held_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        UPDATE automation_channel_entry_runtime
        SET identity_status = 'held',
            identity_hold_reason = 'pre_event_driven_cutover_requires_manual_classification',
            identity_held_at = CURRENT_TIMESTAMP,
            identity_next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE identity_status IN ('pending', 'pending_identity', 'failed')
        """
    )
    op.execute(
        """
        CREATE TABLE identity_resolution_completion_receipt (
            id BIGSERIAL PRIMARY KEY,
            external_effect_job_id BIGINT NOT NULL REFERENCES external_effect_job(id) ON DELETE CASCADE,
            attempt_id TEXT NOT NULL,
            queue_id BIGINT REFERENCES crm_user_identity_resolution_queue(id) ON DELETE SET NULL,
            result_status TEXT NOT NULL,
            result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            execution_id TEXT NOT NULL DEFAULT '',
            parent_execution_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_identity_resolution_completion_job UNIQUE (external_effect_job_id),
            CONSTRAINT uq_identity_resolution_completion_attempt UNIQUE (external_effect_job_id, attempt_id),
            CONSTRAINT ck_identity_resolution_completion_status
                CHECK (result_status IN ('resolved', 'conflict', 'ignored'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE customer_read_model_refresh_intent (
            singleton_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
            dirty_generation BIGINT NOT NULL DEFAULT 0,
            completed_generation BIGINT NOT NULL DEFAULT 0,
            signal_generation BIGINT NOT NULL DEFAULT 0,
            running_generation BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            execution_id TEXT NOT NULL DEFAULT '',
            parent_execution_id TEXT NOT NULL DEFAULT '',
            running_execution_id TEXT NOT NULL DEFAULT '',
            running_parent_execution_id TEXT NOT NULL DEFAULT '',
            owner_consumer_run_id BIGINT,
            owner_lease_token TEXT NOT NULL DEFAULT '',
            lane TEXT NOT NULL DEFAULT 'internal_general',
            available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            last_source_event_key TEXT NOT NULL DEFAULT '',
            last_error_code TEXT NOT NULL DEFAULT '',
            last_error_message TEXT NOT NULL DEFAULT '',
            row_version BIGINT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMPTZ,
            CONSTRAINT ck_customer_refresh_intent_generation CHECK (
                dirty_generation >= 0
                AND completed_generation >= 0
                AND signal_generation >= 0
                AND running_generation >= 0
                AND completed_generation <= dirty_generation
                AND running_generation <= dirty_generation
            ),
            CONSTRAINT ck_customer_refresh_intent_status
                CHECK (status IN ('idle', 'waiting', 'running', 'retry_wait', 'blocked')),
            CONSTRAINT ck_customer_refresh_intent_lane CHECK (lane = 'internal_general'),
            CONSTRAINT ck_customer_refresh_intent_attempts
                CHECK (attempt_count >= 0 AND max_attempts > 0)
        )
        """
    )
    op.execute(
        """
        INSERT INTO customer_read_model_refresh_intent (singleton_id)
        VALUES (1)
        ON CONFLICT (singleton_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE customer_read_model_refresh_source_receipt (
            id BIGSERIAL PRIMARY KEY,
            source_event_key TEXT NOT NULL,
            source_event_type TEXT NOT NULL DEFAULT '',
            generation BIGINT NOT NULL,
            execution_id TEXT NOT NULL DEFAULT '',
            parent_execution_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_customer_refresh_source_event UNIQUE (source_event_key),
            CONSTRAINT ck_customer_refresh_source_generation CHECK (generation > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_identity_resolution_queue_effect
        ON crm_user_identity_resolution_queue(external_effect_job_id)
        WHERE external_effect_job_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_customer_refresh_intent_due
        ON customer_read_model_refresh_intent(available_at)
        WHERE status IN ('waiting', 'retry_wait')
        """
    )
    op.execute(
        """
        COMMENT ON TABLE identity_resolution_completion_receipt IS
        'Idempotent local continuation receipt after a canonical provider-success attempt.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE customer_read_model_refresh_intent IS
        'Singleton coalescing generation intent; internal_event owns execution.'
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_read_model_refresh_source_receipt")
    op.execute("DROP TABLE IF EXISTS customer_read_model_refresh_intent")
    op.execute("DROP TABLE IF EXISTS identity_resolution_completion_receipt")
    op.execute("DROP INDEX IF EXISTS idx_identity_resolution_queue_effect")
    op.execute(
        """
        UPDATE automation_channel_entry_runtime
        SET identity_status = 'pending'
        WHERE identity_status = 'held'
        """
    )
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        DROP COLUMN IF EXISTS provider_result_consumed_at,
        DROP COLUMN IF EXISTS provider_result_hash,
        DROP COLUMN IF EXISTS provider_result_json
        """
    )
    op.execute(
        """
        ALTER TABLE automation_channel_entry_runtime
        DROP COLUMN IF EXISTS identity_held_at,
        DROP COLUMN IF EXISTS identity_hold_reason,
        DROP COLUMN IF EXISTS identity_parent_execution_id,
        DROP COLUMN IF EXISTS identity_execution_id,
        DROP COLUMN IF EXISTS identity_external_effect_job_id
        """
    )
    op.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'pending'
        WHERE status = 'held'
        """
    )
    op.execute(
        """
        ALTER TABLE crm_user_identity_resolution_queue
        DROP CONSTRAINT IF EXISTS ck_identity_resolution_queue_lane,
        DROP CONSTRAINT IF EXISTS crm_user_identity_resolution_queue_status_check,
        DROP COLUMN IF EXISTS completed_at,
        DROP COLUMN IF EXISTS held_at,
        DROP COLUMN IF EXISTS hold_reason,
        DROP COLUMN IF EXISTS row_version,
        DROP COLUMN IF EXISTS lane,
        DROP COLUMN IF EXISTS external_effect_job_id,
        DROP COLUMN IF EXISTS parent_execution_id,
        DROP COLUMN IF EXISTS execution_id,
        ADD CONSTRAINT crm_user_identity_resolution_queue_status_check
            CHECK (status IN ('pending', 'polling', 'resolved', 'conflict', 'failed', 'ignored'))
        """
    )
