"""Authorize one exact production welcome timeout acknowledgement.

Revision ID: 0141_production_welcome_timeout_ack_scope
Revises: 0140_wecom_welcome_hard_realtime_lanes
"""

from __future__ import annotations

from alembic import op


revision = "0141_production_welcome_timeout_ack_scope"
down_revision = "0140_wecom_welcome_hard_realtime_lanes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE queue_terminal_acknowledgement
        DROP CONSTRAINT IF EXISTS ck_queue_terminal_ack_type,
        DROP CONSTRAINT IF EXISTS ck_queue_terminal_ack_error
        """
    )
    op.execute(
        """
        ALTER TABLE queue_terminal_acknowledgement
        ADD CONSTRAINT ck_queue_terminal_ack_type CHECK (
            acknowledgement_type IN (
                'pre_cutover_welcome_41050_no_replay',
                'production_private_message_84061_no_replay',
                'production_wechat_refund_not_enough_no_replay',
                'production_welcome_41050_job_2157_no_replay'
            )
        ),
        ADD CONSTRAINT ck_queue_terminal_ack_error CHECK (
            (
                acknowledgement_type = 'pre_cutover_welcome_41050_no_replay'
                AND error_code = 'wecom_error_41050'
                AND graph_id IS NOT NULL
            )
            OR (
                acknowledgement_type = 'production_private_message_84061_no_replay'
                AND error_code = 'external_call_failed_known'
                AND graph_id IS NULL
            )
            OR (
                acknowledgement_type = 'production_wechat_refund_not_enough_no_replay'
                AND error_code = 'http_403'
                AND graph_id IS NULL
            )
            OR (
                acknowledgement_type = 'production_welcome_41050_job_2157_no_replay'
                AND error_code = 'wecom_error_41050'
                AND graph_id IS NOT NULL
            )
        )
        """
    )


def downgrade() -> None:
    # The acknowledgement is append-only production evidence. A code rollback
    # must retain a schema that can continue to read the immutable row.
    pass
