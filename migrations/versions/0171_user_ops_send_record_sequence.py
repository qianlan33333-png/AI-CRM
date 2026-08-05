"""Align the User Ops send-record sequence with durable production rows.

Revision ID: 0171_user_ops_send_seq
Revises: 0170_release_governance_foundation
"""

from __future__ import annotations

from alembic import op


revision = "0171_user_ops_send_seq"
down_revision = "0170_release_governance_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE user_ops_send_records_next IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('user_ops_send_records_next', 'id')::regclass,
            GREATEST(
                (SELECT MAX(id) FROM user_ops_send_records_next),
                (SELECT last_value FROM user_ops_send_records_next_id_seq)
            ),
            TRUE
        )
        WHERE EXISTS (SELECT 1 FROM user_ops_send_records_next)
        """
    )
    op.execute(
        """
        INSERT INTO schema_release_compatibility (
            revision, parent_revision, change_kind, compatibility_epoch,
            previous_runtime_compatible, downgrade_policy, metadata_json
        ) VALUES (
            '0171_user_ops_send_seq',
            '0170_release_governance_foundation',
            'expand',
            1,
            TRUE,
            'forward_only',
            '{"repair":"user_ops_send_records_next_sequence_alignment"}'::jsonb
        )
        ON CONFLICT (revision) DO NOTHING
        """
    )


def downgrade() -> None:
    # Sequence alignment and release evidence are safe to retain on rollback.
    pass
