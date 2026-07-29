"""Add inactive Customer 360 projection slots for atomic full calibration.

Revision ID: 0153_customer_read_model_generation_slots
Revises: 0152_customer_read_model_incremental
"""

from __future__ import annotations

from alembic import op


revision = "0153_customer_read_model_generation_slots"
down_revision = "0152_customer_read_model_incremental"
branch_labels = None
depends_on = None


LIST_SHADOW = "customer_list_index_next_shadow"
DETAIL_SHADOW = "customer_detail_snapshot_next_shadow"
RECENT_SHADOW = "customer_recent_message_next_shadow"


def _create_index(*, postgres: str, portable: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(postgres)
        return
    op.execute(portable)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_list_index_next_shadow (
            id BIGSERIAL PRIMARY KEY,
            unionid TEXT NOT NULL,
            customer_name TEXT NOT NULL DEFAULT '',
            owner_userid TEXT NOT NULL DEFAULT '',
            owner_display_name TEXT NOT NULL DEFAULT '',
            remark TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            mobile TEXT,
            is_bound BOOLEAN NOT NULL DEFAULT false,
            binding_status TEXT NOT NULL DEFAULT 'unbound',
            tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            class_user_status_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_message_at TIMESTAMPTZ,
            last_touch_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_detail_snapshot_next_shadow (
            id BIGSERIAL PRIMARY KEY,
            unionid TEXT NOT NULL,
            customer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            binding_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            follow_users_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            marketing_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            marketing_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            contact_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            sidebar_context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_recent_message_next_shadow (
            id BIGSERIAL PRIMARY KEY,
            msgid TEXT NOT NULL,
            unionid TEXT NOT NULL,
            msgtype TEXT NOT NULL DEFAULT 'text',
            content TEXT NOT NULL DEFAULT '',
            send_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            owner_userid TEXT,
            chat_type TEXT NOT NULL DEFAULT 'single',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    op.execute(
        """
        ALTER TABLE customer_read_model_refresh_state
        ADD COLUMN IF NOT EXISTS active_slot TEXT NOT NULL DEFAULT 'primary'
        """
    )
    op.execute(
        """
        ALTER TABLE customer_read_model_refresh_state
        ADD COLUMN IF NOT EXISTS active_generation BIGINT NOT NULL DEFAULT 1
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_customer_refresh_state_active_slot'
                  AND conrelid = 'customer_read_model_refresh_state'::regclass
            ) THEN
                ALTER TABLE customer_read_model_refresh_state
                ADD CONSTRAINT ck_customer_refresh_state_active_slot
                CHECK (active_slot IN ('primary', 'shadow'));
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_customer_refresh_state_active_generation'
                  AND conrelid = 'customer_read_model_refresh_state'::regclass
            ) THEN
                ALTER TABLE customer_read_model_refresh_state
                ADD CONSTRAINT ck_customer_refresh_state_active_generation
                CHECK (active_generation >= 1);
            END IF;
        END $$
        """
    )

    indexes = (
        (
            "uq_customer_list_index_next_shadow_unionid",
            f"{LIST_SHADOW} (unionid)",
            True,
        ),
        (
            "ix_customer_list_index_next_shadow_owner_userid",
            f"{LIST_SHADOW} (owner_userid)",
            False,
        ),
        (
            "ix_customer_list_index_next_shadow_mobile",
            f"{LIST_SHADOW} (mobile)",
            False,
        ),
        (
            "ix_customer_list_index_next_shadow_updated_at",
            f"{LIST_SHADOW} (updated_at)",
            False,
        ),
        (
            "uq_customer_detail_snapshot_next_shadow_unionid",
            f"{DETAIL_SHADOW} (unionid)",
            True,
        ),
        (
            "ix_customer_recent_message_next_shadow_unionid",
            f"{RECENT_SHADOW} (unionid)",
            False,
        ),
        (
            "ix_customer_recent_message_next_shadow_send_time",
            f"{RECENT_SHADOW} (send_time)",
            False,
        ),
        (
            "ix_customer_recent_message_next_shadow_unionid_time_id",
            f"{RECENT_SHADOW} (unionid, send_time DESC, id DESC)",
            False,
        ),
    )
    for index_name, target, unique in indexes:
        unique_sql = "UNIQUE " if unique else ""
        _create_index(
            postgres=f"CREATE {unique_sql}INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {target}",
            portable=f"CREATE {unique_sql}INDEX IF NOT EXISTS {index_name} ON {target}",
        )


def downgrade() -> None:
    # The inactive tables and additive state columns do not affect the previous
    # runtime. Keeping them makes a code rollback immediate and prevents a
    # downgrade from discarding a fully built recovery slot.
    pass
