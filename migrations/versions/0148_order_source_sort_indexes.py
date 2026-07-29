"""Register the existing order-source sorting indexes in the bootstrap chain.

Revision ID: 0148_order_source_sort_indexes
Revises: 0147_alipay_order_created_index
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0148_order_source_sort_indexes"
down_revision = "0147_alipay_order_created_index"
branch_labels = None
depends_on = None


INDEXES = (
    (
        "wechat_pay_orders",
        "idx_wechat_pay_orders_created",
        "created_at DESC, id DESC",
    ),
    (
        "wechat_shop_orders",
        "idx_wechat_shop_orders_provider_created",
        "provider, created_at DESC, id DESC",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name, index_name, columns in INDEXES:
        if not inspector.has_table(table_name):
            continue
        statement = (
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table_name} ({columns})"
        )
        if bind.dialect.name == "postgresql":
            statement = statement.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1)
            with op.get_context().autocommit_block():
                op.execute(statement)
            continue
        op.execute(statement)


def downgrade() -> None:
    # These index names and definitions predate this migration in production.
    # A release rollback must not delete pre-existing performance protection;
    # any future index removal requires a separate, observable migration.
    pass
