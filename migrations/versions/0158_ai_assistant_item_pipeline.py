"""Add the durable per-recipient AI generation pipeline.

Lifecycle manifest entry:
- automation_agent_webhook_batch, automation_agent_webhook_item, external_effect_job
Capability owner:
- aicrm_next.extensions.ai.automation_agents
Business key:
- batch idempotency key; batch plus unionid; generation effect idempotency key
PII level:
- internal_contact
Read path:
- Automation Agent runtime/admin read models and queue runtime metrics
Write path:
- Automation Agent webhook transaction, item prepare consumer, effect continuations
Repository ownership:
- docs/architecture/repository_ownership.yml
Rollback note:
- stop new routing first; downgrade refuses to discard item-pipeline history
Fresh DB test:
- tests/test_database_bootstrap.py and tests/test_alembic_revision_chain.py

Revision ID: 0158_ai_assistant_item_pipeline
Revises: 0157_ai_assistant_bulk_lane
"""

from __future__ import annotations

from alembic import op


revision = "0158_ai_assistant_item_pipeline"
down_revision = "0157_ai_assistant_bulk_lane"
branch_labels = None
depends_on = None


LANE = "ai_generation"
INITIAL_CAPACITY = 4
MAX_RESERVED_CAPACITY = 64


def _replace_external_lane_constraint(*, include_generation_lane: bool) -> None:
    lanes = [
        "wecom_welcome",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_ai_assistant_bulk",
        "wecom_media",
        "outbound_webhook",
    ]
    if include_generation_lane:
        lanes.insert(0, LANE)
    quoted = ", ".join(f"'{lane}'" for lane in lanes)
    op.execute(
        f"""
        ALTER TABLE external_effect_job
        DROP CONSTRAINT IF EXISTS ck_external_effect_job_runtime_lane,
        ADD CONSTRAINT ck_external_effect_job_runtime_lane
            CHECK (lane IN ({quoted})) NOT VALID
        """
    )
    op.execute("ALTER TABLE external_effect_job VALIDATE CONSTRAINT ck_external_effect_job_runtime_lane")


def _replace_item_status_constraint(*, include_pipeline_statuses: bool) -> None:
    statuses = [
        "queued",
        "running",
        "generated",
        "callback_succeeded",
        "callback_failed",
        "failed",
        "failed_retryable",
    ]
    if include_pipeline_statuses:
        statuses.extend(
            [
                "prepare_queued",
                "preparing",
                "generation_queued",
                "generation_succeeded",
                "send_plan_created",
            ]
        )
    quoted = ", ".join(f"'{status}'" for status in statuses)
    op.execute(
        f"""
        ALTER TABLE automation_agent_webhook_item
        DROP CONSTRAINT IF EXISTS automation_agent_webhook_item_status_check,
        ADD CONSTRAINT automation_agent_webhook_item_status_check
            CHECK (status IN ({quoted})) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE automation_agent_webhook_item "
        "VALIDATE CONSTRAINT automation_agent_webhook_item_status_check"
    )


def upgrade() -> None:
    _replace_external_lane_constraint(include_generation_lane=True)
    _replace_item_status_constraint(include_pipeline_statuses=True)
    op.execute(
        """
        ALTER TABLE automation_agent_webhook_batch
            ADD COLUMN IF NOT EXISTS agent_published_version INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS agent_config_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS prepare_enqueued_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS prepared_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS generation_queued_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS generation_succeeded_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS send_plan_created_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS failed_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS prepare_enqueued_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE automation_agent_webhook_item
            ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS prepare_outbox_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS generation_effect_job_id BIGINT
                REFERENCES external_effect_job(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS prepare_enqueued_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS generation_completed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS send_plan_created_at TIMESTAMPTZ
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_agent_item_generation_effect "
        "ON automation_agent_webhook_item (generation_effect_job_id) "
        "WHERE generation_effect_job_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_agent_batch_pipeline_status "
        "ON automation_agent_webhook_batch (status, created_at ASC, id ASC)"
    )
    op.execute(
        f"""
        INSERT INTO queue_lane_policy (
            lane, max_in_flight, enabled, rollout_mode, blocked_until,
            policy_version, updated_by, updated_reason, updated_at
        )
        SELECT '{LANE}', {INITIAL_CAPACITY}, TRUE, 'blocked', NULL,
               control.policy_version, 'migration',
               'dark deploy AI generation lane at initial concurrency four',
               CURRENT_TIMESTAMP
        FROM queue_runtime_control control
        WHERE control.singleton = TRUE
        ON CONFLICT (lane) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET global_max_in_flight = global_max_in_flight + {MAX_RESERVED_CAPACITY},
            updated_by = 'migration',
            updated_reason = 'reserve AI generation lane ceiling without reducing existing lane capacity',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM external_effect_job WHERE lane = '{LANE}')
               OR EXISTS (
                    SELECT 1
                    FROM automation_agent_webhook_item
                    WHERE prepare_outbox_id <> ''
                       OR generation_effect_job_id IS NOT NULL
                       OR status IN (
                            'prepare_queued', 'preparing', 'generation_queued',
                            'generation_succeeded', 'send_plan_created'
                       )
               ) THEN
                RAISE EXCEPTION 'AI assistant item pipeline history prevents destructive downgrade';
            END IF;
        END;
        $$
        """
    )
    op.execute(f"DELETE FROM queue_fairness_cursor WHERE lane = '{LANE}'")
    op.execute(f"DELETE FROM queue_lane_policy WHERE lane = '{LANE}'")
    op.execute("DROP INDEX IF EXISTS idx_automation_agent_batch_pipeline_status")
    op.execute("DROP INDEX IF EXISTS uq_automation_agent_item_generation_effect")
    op.execute(
        """
        ALTER TABLE automation_agent_webhook_item
            DROP COLUMN IF EXISTS send_plan_created_at,
            DROP COLUMN IF EXISTS generation_completed_at,
            DROP COLUMN IF EXISTS prepared_at,
            DROP COLUMN IF EXISTS prepare_enqueued_at,
            DROP COLUMN IF EXISTS generation_effect_job_id,
            DROP COLUMN IF EXISTS prepare_outbox_id,
            DROP COLUMN IF EXISTS state_version
        """
    )
    op.execute(
        """
        ALTER TABLE automation_agent_webhook_batch
            DROP COLUMN IF EXISTS prepare_enqueued_at,
            DROP COLUMN IF EXISTS failed_count,
            DROP COLUMN IF EXISTS send_plan_created_count,
            DROP COLUMN IF EXISTS generation_succeeded_count,
            DROP COLUMN IF EXISTS generation_queued_count,
            DROP COLUMN IF EXISTS prepared_count,
            DROP COLUMN IF EXISTS prepare_enqueued_count,
            DROP COLUMN IF EXISTS agent_config_snapshot_json,
            DROP COLUMN IF EXISTS agent_published_version
        """
    )
    _replace_item_status_constraint(include_pipeline_statuses=False)
    _replace_external_lane_constraint(include_generation_lane=False)
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET global_max_in_flight = GREATEST(1, global_max_in_flight - {MAX_RESERVED_CAPACITY}),
            updated_by = 'migration-downgrade',
            updated_reason = 'remove AI generation lane reserved capacity',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )
