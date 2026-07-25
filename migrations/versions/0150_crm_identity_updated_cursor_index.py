"""Add the stable CRM identity incremental-cursor index.

Revision ID: 0150_crm_identity_updated_cursor_index
Revises: 0149_ai_audience_hxc_projection
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0150_crm_identity_updated_cursor_index"
down_revision = "0149_ai_audience_hxc_projection"
branch_labels = None
depends_on = None


TABLE_NAME = "crm_user_identity"
INDEX_NAME = "idx_crm_user_identity_updated_unionid"


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(TABLE_NAME):
        return
    statement = (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE_NAME} (updated_at, unionid)"
    )
    if bind.dialect.name == "postgresql":
        statement = statement.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1)
        with op.get_context().autocommit_block():
            op.execute(statement)
        return
    op.execute(statement)


def downgrade() -> None:
    # This index protects the online identity cursor after the query change is
    # released separately. Removing it requires its own observed migration so
    # a code rollback cannot also remove database performance protection.
    pass
