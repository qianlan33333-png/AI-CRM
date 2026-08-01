"""Add traceable AI Audience manual-send ownership and read indexes.

Revision ID: 0164_ai_audience_send_record_read_index
Revises: 0163_ai_audience_admin_member_read_indexes
"""

from __future__ import annotations

from alembic import op


revision = "0164_ai_audience_send_record_read_index"
down_revision = "0163_ai_audience_admin_member_read_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE user_ops_send_records_next
            ADD COLUMN IF NOT EXISTS target_source VARCHAR(64) NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS target_source_id BIGINT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ops_send_records_ai_audience
        ON user_ops_send_records_next (
            target_source,
            target_source_id,
            created_at DESC,
            id DESC
        )
        WHERE target_source = 'ai_audience_package'
          AND target_source_id IS NOT NULL
        """
    )
    op.execute(
        """
        -- Do not add a COALESCE(package_key, '') predicate here: the production
        -- equality query cannot prove that extra predicate and would miss this index.
        CREATE INDEX IF NOT EXISTS idx_cloud_broadcast_plans_ai_audience_send_records
        ON cloud_broadcast_plans (
            (selection_json ->> 'package_key'),
            created_at DESC,
            id DESC
        )
        WHERE content_strategy = 'agent_generated_single'
          AND selection_json ->> 'source' = 'automation_agent'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cloud_broadcast_plans_ai_audience_send_records")
    op.execute("DROP INDEX IF EXISTS idx_user_ops_send_records_ai_audience")
    op.execute(
        """
        ALTER TABLE user_ops_send_records_next
            DROP COLUMN IF EXISTS target_source_id,
            DROP COLUMN IF EXISTS target_source
        """
    )
