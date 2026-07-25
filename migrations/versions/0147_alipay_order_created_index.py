"""Add the stable Alipay order pagination index.

Revision ID: 0147_alipay_order_created_index
Revises: 0146_wechat_pay_event_lookup_index
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0147_alipay_order_created_index"
down_revision = "0146_wechat_pay_event_lookup_index"
branch_labels = None
depends_on = None


TABLE_NAME = "alipay_pay_orders"
INDEX_NAME = "ix_alipay_pay_orders_created_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(TABLE_NAME):
        return
    statement = (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE_NAME} (created_at DESC, id DESC)"
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
