"""Add bounded AI Audience admin-member read indexes.

Revision ID: 0163_ai_audience_admin_member_read_indexes
Revises: 0162_ai_audience_groups_binding
"""

from __future__ import annotations

from alembic import op


revision = "0163_ai_audience_admin_member_read_indexes"
down_revision = "0162_ai_audience_groups_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ai_audience_member_current_admin_page
            ON ai_audience_member_current (
                package_id,
                status,
                first_entered_at DESC,
                id DESC
            )
            INCLUDE (unionid)
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wecom_identity_map_external_updated
            ON wecom_external_contact_identity_map (
                external_userid,
                updated_at DESC,
                id DESC
            )
            INCLUDE (name)
            WHERE COALESCE(external_userid, '') <> ''
            """
        )


def downgrade() -> None:
    # Additive read-path indexes are safe to keep across an application rollback.
    pass
