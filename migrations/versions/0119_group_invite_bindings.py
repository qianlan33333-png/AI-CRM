"""host customer-group invite bindings by chat id.

Lifecycle manifest entry:
- table: group_invite_library
- lifecycle: canonical
- write_owner: aicrm_next.engagement.media_library.postgres_repo

Schema ownership:
- capability_owner: aicrm_next.engagement.media_library
- business_key: chat_id, the stable WeCom customer-group identifier
- pii_level: low
- read_path: media library, send-content validation, group-ops material resolution, channel welcome resolution
- write_path: group-invite binding ensure/update API and group sync reconciliation

Rollback note:
- Roll back the application first; pending rows without join_url are removed before restoring the old constraint.

Fresh DB test:
- python3 -m pytest tests/test_database_bootstrap.py

Revision ID: 0119_group_invite_bindings
Revises: 0118_external_radar_read_api
"""

from __future__ import annotations

from alembic import op


revision = "0119_group_invite_bindings"
down_revision = "0118_external_radar_read_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE group_invite_library ADD COLUMN IF NOT EXISTS chat_id TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE group_invite_library ADD COLUMN IF NOT EXISTS binding_status TEXT NOT NULL DEFAULT 'ready'"
    )
    op.execute(
        """
        UPDATE group_invite_library
        SET chat_id = COALESCE(NULLIF(chat_id_list->>0, ''), '')
        WHERE chat_id = ''
        """
    )
    op.execute("ALTER TABLE group_invite_library ALTER COLUMN join_url SET DEFAULT ''")
    op.execute("ALTER TABLE group_invite_library DROP CONSTRAINT IF EXISTS ck_group_invite_join_url")
    op.execute(
        """
        ALTER TABLE group_invite_library
        ADD CONSTRAINT ck_group_invite_join_url
        CHECK (join_url = '' OR join_url ~ '^https://work\\.weixin\\.qq\\.com/gm/')
        """
    )
    op.execute(
        """
        ALTER TABLE group_invite_library
        DROP CONSTRAINT IF EXISTS ck_group_invite_binding_status
        """
    )
    op.execute(
        """
        ALTER TABLE group_invite_library
        ADD CONSTRAINT ck_group_invite_binding_status
        CHECK (binding_status IN ('pending', 'ready', 'invalid'))
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_group_invite_library_chat_id
        ON group_invite_library (chat_id)
        WHERE chat_id <> ''
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_group_invite_library_chat_id")
    op.execute("ALTER TABLE group_invite_library DROP CONSTRAINT IF EXISTS ck_group_invite_binding_status")
    op.execute("ALTER TABLE group_invite_library DROP CONSTRAINT IF EXISTS ck_group_invite_join_url")
    op.execute(
        """
        DELETE FROM group_invite_library
        WHERE join_url = ''
        """
    )
    op.execute(
        """
        ALTER TABLE group_invite_library
        ADD CONSTRAINT ck_group_invite_join_url
        CHECK (join_url ~ '^https://work\\.weixin\\.qq\\.com/gm/')
        """
    )
    op.execute("ALTER TABLE group_invite_library ALTER COLUMN join_url DROP DEFAULT")
    op.execute("ALTER TABLE group_invite_library DROP COLUMN IF EXISTS binding_status")
    op.execute("ALTER TABLE group_invite_library DROP COLUMN IF EXISTS chat_id")
