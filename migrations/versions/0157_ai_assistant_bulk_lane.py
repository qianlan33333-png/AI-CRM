"""Add a fail-closed AI-assistant WeCom bulk execution lane.

Lifecycle manifest entry:
- queue_lane_policy, queue_worker_heartbeat, external_effect_job (existing tables)
Capability owner:
- aicrm_next.platform.platform_foundation.execution_runtime
Business key:
- lane; service_name plus worker_id; external-effect idempotency key
PII level:
- none for policy/heartbeat; existing internal_contact classification for effect jobs
Read path:
- execution runtime claim gate and admin read model
Write path:
- migration seed, worker heartbeat, canonical external-effect producer
Repository ownership:
- docs/architecture/repository_ownership.yml
Rollback note:
- stop new routing first; downgrade refuses to discard any AI-lane history
Fresh DB test:
- tests/test_database_bootstrap.py and tests/test_alembic_revision_chain.py

Revision ID: 0157_ai_assistant_bulk_lane
Revises: 0156_campaign_preparation_context
"""

from __future__ import annotations

from alembic import op


revision = "0157_ai_assistant_bulk_lane"
down_revision = "0156_campaign_preparation_context"
branch_labels = None
depends_on = None


LANE = "wecom_ai_assistant_bulk"
INITIAL_CAPACITY = 4
MAX_RESERVED_CAPACITY = 24


def _replace_external_lane_constraint(*, include_ai_lane: bool) -> None:
    lanes = [
        "wecom_welcome",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_media",
        "outbound_webhook",
    ]
    if include_ai_lane:
        lanes.insert(3, LANE)
    quoted = ", ".join(f"'{lane}'" for lane in lanes)
    op.execute(
        f"""
        ALTER TABLE external_effect_job
        DROP CONSTRAINT IF EXISTS ck_external_effect_job_runtime_lane,
        ADD CONSTRAINT ck_external_effect_job_runtime_lane
            CHECK (lane IN ({quoted})) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE external_effect_job VALIDATE CONSTRAINT ck_external_effect_job_runtime_lane"
    )


def upgrade() -> None:
    _replace_external_lane_constraint(include_ai_lane=True)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_effect_job_recent_created "
        "ON external_effect_job (created_at, lane)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_effect_job_recent_completed "
        "ON external_effect_job (completed_at, lane) WHERE completed_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_effect_attempt_recent "
        "ON external_effect_attempt (started_at, job_id)"
    )
    op.execute(
        "ALTER TABLE queue_worker_heartbeat ADD COLUMN IF NOT EXISTS metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        f"""
        INSERT INTO queue_lane_policy (
            lane, max_in_flight, enabled, rollout_mode, blocked_until,
            policy_version, updated_by, updated_reason, updated_at
        )
        SELECT '{LANE}', {INITIAL_CAPACITY}, TRUE, 'blocked', NULL,
               control.policy_version, 'migration',
               'dark deploy AI assistant bulk lane at initial concurrency four',
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
            updated_reason = 'reserve AI assistant bulk lane ceiling without reducing existing lane capacity',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM external_effect_job WHERE lane = '{LANE}') THEN
                RAISE EXCEPTION 'AI assistant bulk lane history prevents destructive downgrade';
            END IF;
        END;
        $$
        """
    )
    op.execute(f"DELETE FROM queue_fairness_cursor WHERE lane = '{LANE}'")
    op.execute(f"DELETE FROM queue_lane_policy WHERE lane = '{LANE}'")
    op.execute("DROP INDEX IF EXISTS idx_external_effect_attempt_recent")
    op.execute("DROP INDEX IF EXISTS idx_external_effect_job_recent_completed")
    op.execute("DROP INDEX IF EXISTS idx_external_effect_job_recent_created")
    _replace_external_lane_constraint(include_ai_lane=False)
    op.execute("ALTER TABLE queue_worker_heartbeat DROP COLUMN IF EXISTS metrics_json")
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET global_max_in_flight = GREATEST(1, global_max_in_flight - {MAX_RESERVED_CAPACITY}),
            updated_by = 'migration-downgrade',
            updated_reason = 'remove AI assistant bulk lane reserved capacity',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )
