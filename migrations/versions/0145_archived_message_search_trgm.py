"""Add the archived-message substring search index.

Revision ID: 0145_archived_message_search_trgm
Revises: 0144_config_release_control_plane
"""

from __future__ import annotations

from alembic import op


revision = "0145_archived_message_search_trgm"
down_revision = "0144_config_release_control_plane"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_archived_messages_content_trgm"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
            "ON archived_messages USING gin (content gin_trgm_ops) "
            "WHERE content <> ''"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
