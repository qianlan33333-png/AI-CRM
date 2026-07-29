"""AI Audience event-driven refresh intents.

Revision ID: 0128_ai_audience_refresh_intents
Revises: 0127_group_ops_durable_effect_graph
"""

from __future__ import annotations

from alembic import op


revision = "0128_ai_audience_refresh_intents"
down_revision = "0127_group_ops_durable_effect_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_audience_inbound_webhook_event
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'internal_general',
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_audience_refresh_intent (
            package_id BIGINT PRIMARY KEY REFERENCES ai_audience_package(id) ON DELETE CASCADE,
            dirty_generation BIGINT NOT NULL DEFAULT 0,
            completed_generation BIGINT NOT NULL DEFAULT 0,
            signal_generation BIGINT NOT NULL DEFAULT 0,
            running_generation BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            target_refresh_kind TEXT NOT NULL DEFAULT 'incremental',
            running_refresh_kind TEXT NOT NULL DEFAULT '',
            target_params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            running_params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            target_row_limit INTEGER NOT NULL DEFAULT 10000,
            running_row_limit INTEGER NOT NULL DEFAULT 10000,
            execution_id TEXT NOT NULL DEFAULT '',
            parent_execution_id TEXT NOT NULL DEFAULT '',
            running_execution_id TEXT NOT NULL DEFAULT '',
            running_parent_execution_id TEXT NOT NULL DEFAULT '',
            owner_consumer_run_id BIGINT,
            owner_lease_token TEXT NOT NULL DEFAULT '',
            lane TEXT NOT NULL DEFAULT 'internal_general',
            available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 10,
            last_source_event_key TEXT NOT NULL DEFAULT '',
            last_run_id BIGINT REFERENCES ai_audience_package_run(id) ON DELETE SET NULL,
            last_error_code TEXT NOT NULL DEFAULT '',
            last_error_message TEXT NOT NULL DEFAULT '',
            row_version BIGINT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMPTZ,
            CONSTRAINT ck_ai_audience_refresh_intent_generation
                CHECK (
                    dirty_generation >= 0
                    AND completed_generation >= 0
                    AND signal_generation >= 0
                    AND running_generation >= 0
                    AND completed_generation <= dirty_generation
                    AND running_generation <= dirty_generation
                ),
            CONSTRAINT ck_ai_audience_refresh_intent_status
                CHECK (status IN ('idle', 'waiting', 'running', 'retry_wait', 'blocked')),
            CONSTRAINT ck_ai_audience_refresh_intent_target_kind
                CHECK (target_refresh_kind IN ('incremental', 'daily', 'manual')),
            CONSTRAINT ck_ai_audience_refresh_intent_running_kind
                CHECK (running_refresh_kind IN ('', 'incremental', 'daily', 'manual')),
            CONSTRAINT ck_ai_audience_refresh_intent_lane
                CHECK (lane = 'internal_general'),
            CONSTRAINT ck_ai_audience_refresh_intent_attempts
                CHECK (attempt_count >= 0 AND max_attempts > 0),
            CONSTRAINT ck_ai_audience_refresh_intent_row_limit
                CHECK (
                    target_row_limit BETWEEN 1 AND 100000
                    AND running_row_limit BETWEEN 1 AND 100000
                )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_audience_refresh_source_receipt (
            id BIGSERIAL PRIMARY KEY,
            package_id BIGINT NOT NULL REFERENCES ai_audience_package(id) ON DELETE CASCADE,
            source_event_key TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_key TEXT NOT NULL DEFAULT '',
            refresh_kind TEXT NOT NULL DEFAULT 'incremental',
            generation BIGINT NOT NULL,
            execution_id TEXT NOT NULL DEFAULT '',
            parent_execution_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_ai_audience_refresh_source_receipt
                UNIQUE (package_id, source_event_key),
            CONSTRAINT ck_ai_audience_refresh_source_receipt_kind
                CHECK (refresh_kind IN ('incremental', 'daily', 'manual')),
            CONSTRAINT ck_ai_audience_refresh_source_receipt_generation
                CHECK (generation > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_refresh_intent_due
        ON ai_audience_refresh_intent(available_at, package_id)
        WHERE status IN ('waiting', 'retry_wait')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_refresh_source_receipt_event
        ON ai_audience_refresh_source_receipt(source_event_key, package_id)
        """
    )
    op.execute(
        """
        COMMENT ON TABLE ai_audience_refresh_intent IS
        'One coalescing durable AI Audience refresh intent per package; internal_event owns execution.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE ai_audience_refresh_source_receipt IS
        'Idempotent source-event to package-generation mapping; contains identifiers only, never source payload.'
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_audience_refresh_source_receipt")
    op.execute("DROP TABLE IF EXISTS ai_audience_refresh_intent")
    op.execute(
        """
        ALTER TABLE IF EXISTS ai_audience_inbound_webhook_event
        DROP COLUMN IF EXISTS processed_at,
        DROP COLUMN IF EXISTS row_version,
        DROP COLUMN IF EXISTS available_at,
        DROP COLUMN IF EXISTS lane,
        DROP COLUMN IF EXISTS parent_execution_id,
        DROP COLUMN IF EXISTS execution_id
        """
    )
