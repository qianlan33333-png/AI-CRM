"""Add the stable WeChat payment-event lookup index.

Revision ID: 0146_wechat_pay_event_lookup_index
Revises: 0145_archived_message_search_trgm
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0146_wechat_pay_event_lookup_index"
down_revision = "0145_archived_message_search_trgm"
branch_labels = None
depends_on = None


TABLE_NAME = "wechat_pay_order_events"
INDEX_NAME = "ix_wechat_pay_order_events_trade_created_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(TABLE_NAME):
        return
    statement = (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE_NAME} (out_trade_no, created_at DESC, id DESC)"
    )
    if bind.dialect.name == "postgresql":
        statement = statement.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1)
        with op.get_context().autocommit_block():
            op.execute(statement)
        return
    op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        return
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
