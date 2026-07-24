"""Add the bounded recent-message read-path index.

Revision ID: 0142_sidebar_recent_message_index
Revises: 0141_production_welcome_timeout_ack_scope
"""

from __future__ import annotations

from alembic import op


revision = "0142_sidebar_recent_message_index"
down_revision = "0141_production_welcome_timeout_ack_scope"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_customer_recent_message_next_unionid_time_id"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON customer_recent_message_next (unionid, send_time DESC, id DESC)"
            )
        return
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON customer_recent_message_next (unionid, send_time DESC, id DESC)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        return
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
