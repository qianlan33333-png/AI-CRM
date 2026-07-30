"""Add append-only audit evidence for AI automation lane rollouts.

Revision ID: 0159_ai_automation_lane_rollout_audit
Revises: 0158_ai_assistant_item_pipeline
"""

from __future__ import annotations

from alembic import op


revision = "0159_ai_automation_lane_rollout_audit"
down_revision = "0158_ai_assistant_item_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_lane_rollout_audit (
            transition_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL CHECK (lane IN ('ai_generation', 'wecom_ai_assistant_bulk')),
            active_generation BIGINT NOT NULL CHECK (active_generation > 0),
            policy_version TEXT NOT NULL,
            from_mode TEXT NOT NULL CHECK (from_mode IN ('blocked', 'canary', 'execute')),
            to_mode TEXT NOT NULL CHECK (to_mode IN ('blocked', 'canary', 'execute')),
            from_capacity INTEGER NOT NULL CHECK (from_capacity > 0),
            to_capacity INTEGER NOT NULL CHECK (to_capacity > 0),
            backlog_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_queue_lane_rollout_direction CHECK (
                (from_mode = 'blocked' AND to_mode = 'canary')
                OR (from_mode = 'canary' AND to_mode = 'execute')
                OR (from_mode IN ('canary', 'execute') AND to_mode = 'blocked')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_lane_rollout_lane_created
        ON queue_lane_rollout_audit (lane, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_lane_canary_job_authorization (
            transition_id TEXT NOT NULL REFERENCES queue_lane_rollout_audit(transition_id),
            lane TEXT NOT NULL CHECK (lane IN ('ai_generation', 'wecom_ai_assistant_bulk')),
            external_effect_job_id BIGINT NOT NULL REFERENCES external_effect_job(id),
            authorized_row_version BIGINT NOT NULL CHECK (authorized_row_version >= 1),
            policy_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (transition_id, external_effect_job_id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_lane_canary_job_version
        ON queue_lane_canary_job_authorization (
            lane, external_effect_job_id, authorized_row_version, policy_version
        )
        """
    )
    for suffix, timing in (("append_only", "UPDATE OR DELETE"), ("reject_truncate", "TRUNCATE")):
        trigger_name = f"trg_queue_lane_rollout_audit_{suffix}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON queue_lane_rollout_audit")
        scope = "FOR EACH STATEMENT" if timing == "TRUNCATE" else "FOR EACH ROW"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {timing} ON queue_lane_rollout_audit
            {scope}
            EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
            """
        )


def downgrade() -> None:
    # Production rollback keeps immutable lane-rollout evidence readable.
    pass
