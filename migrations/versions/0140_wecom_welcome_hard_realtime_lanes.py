"""Reserve hard-real-time ingress and effect lanes for WeCom welcome codes.

Revision ID: 0140_wecom_welcome_hard_realtime_lanes
Revises: 0139_queue_terminal_acknowledgement_scope
"""

from __future__ import annotations

from alembic import op


revision = "0140_wecom_welcome_hard_realtime_lanes"
down_revision = "0139_queue_terminal_acknowledgement_scope"
branch_labels = None
depends_on = None


RESERVED_CAPACITY = 4


def _replace_lane_constraints(*, include_realtime: bool) -> None:
    external_lanes = (
        "'wecom_welcome', 'wecom_interactive', 'wecom_bulk', 'wecom_media', 'outbound_webhook'"
        if include_realtime
        else "'wecom_interactive', 'wecom_bulk', 'wecom_media', 'outbound_webhook'"
    )
    webhook_lanes = (
        "'wecom_welcome_ingress', 'webhook_inbox'"
        if include_realtime
        else "'webhook_inbox'"
    )
    op.execute(
        f"""
        ALTER TABLE external_effect_job
        DROP CONSTRAINT IF EXISTS ck_external_effect_job_runtime_lane,
        ADD CONSTRAINT ck_external_effect_job_runtime_lane
            CHECK (lane IN ({external_lanes})) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE external_effect_job VALIDATE CONSTRAINT ck_external_effect_job_runtime_lane"
    )
    op.execute(
        f"""
        ALTER TABLE webhook_inbox
        DROP CONSTRAINT IF EXISTS ck_webhook_inbox_runtime_lane,
        ADD CONSTRAINT ck_webhook_inbox_runtime_lane
            CHECK (lane IN ({webhook_lanes})) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE webhook_inbox VALIDATE CONSTRAINT ck_webhook_inbox_runtime_lane"
    )


def upgrade() -> None:
    _replace_lane_constraints(include_realtime=True)
    op.execute(
        """
        INSERT INTO queue_lane_policy (
            lane, max_in_flight, enabled, rollout_mode, blocked_until,
            policy_version, updated_by, updated_reason, updated_at
        )
        SELECT lane, 2, TRUE, control.rollout_mode, NULL,
               control.policy_version, 'migration',
               'reserved hard-real-time WeCom welcome capacity', CURRENT_TIMESTAMP
        FROM queue_runtime_control control
        CROSS JOIN (VALUES ('wecom_welcome_ingress'), ('wecom_welcome')) lanes(lane)
        WHERE control.singleton = TRUE
        ON CONFLICT (lane) DO UPDATE
        SET max_in_flight = EXCLUDED.max_in_flight,
            enabled = TRUE,
            rollout_mode = EXCLUDED.rollout_mode,
            blocked_until = NULL,
            policy_version = EXCLUDED.policy_version,
            updated_by = EXCLUDED.updated_by,
            updated_reason = EXCLUDED.updated_reason,
            updated_at = CURRENT_TIMESTAMP
        """
    )
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET global_max_in_flight = global_max_in_flight + {RESERVED_CAPACITY},
            updated_by = 'migration',
            updated_reason = 'reserve four in-flight slots for WeCom welcome hard-real-time lanes',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM external_effect_job
                WHERE lane = 'wecom_welcome'
            ) OR EXISTS (
                SELECT 1 FROM webhook_inbox
                WHERE lane = 'wecom_welcome_ingress'
            ) THEN
                RAISE EXCEPTION 'welcome realtime lane history prevents destructive downgrade';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DELETE FROM queue_fairness_cursor WHERE lane IN ('wecom_welcome_ingress', 'wecom_welcome')"
    )
    op.execute(
        "DELETE FROM queue_lane_policy WHERE lane IN ('wecom_welcome_ingress', 'wecom_welcome')"
    )
    _replace_lane_constraints(include_realtime=False)
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET global_max_in_flight = GREATEST(1, global_max_in_flight - {RESERVED_CAPACITY}),
            updated_by = 'migration-downgrade',
            updated_reason = 'remove WeCom welcome hard-real-time capacity reservation',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )
