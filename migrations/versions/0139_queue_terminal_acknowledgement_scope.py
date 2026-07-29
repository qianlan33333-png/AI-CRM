"""Extend append-only terminal acknowledgements to two authorized histories.

Revision ID: 0139_queue_terminal_acknowledgement_scope
Revises: 0138_queue_terminal_acknowledgement
"""

from __future__ import annotations

from alembic import op


revision = "0139_queue_terminal_acknowledgement_scope"
down_revision = "0138_queue_terminal_acknowledgement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE queue_terminal_acknowledgement
        DROP CONSTRAINT IF EXISTS ck_queue_terminal_ack_type,
        DROP CONSTRAINT IF EXISTS ck_queue_terminal_ack_error,
        ALTER COLUMN graph_id DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE queue_terminal_acknowledgement
        ADD CONSTRAINT ck_queue_terminal_ack_type CHECK (
            acknowledgement_type IN (
                'pre_cutover_welcome_41050_no_replay',
                'production_private_message_84061_no_replay',
                'production_wechat_refund_not_enough_no_replay'
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
        )
        """
    )


def downgrade() -> None:
    # These rows are immutable operator authorizations. A release rollback must
    # retain the schema capable of reading them and must never delete evidence.
    pass
