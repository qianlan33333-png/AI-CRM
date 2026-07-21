"""Harden the shared Customer 360 timeline for sidebar activity projection."""

from __future__ import annotations

from alembic import op


revision = "0133_sidebar_customer_timeline"
down_revision = "0132_external_claim_scope_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id
                       ORDER BY event_time DESC, id DESC
                   ) AS row_number
            FROM customer_timeline_event_next
        )
        DELETE FROM customer_timeline_event_next target
        USING ranked duplicate
        WHERE target.id = duplicate.id
          AND duplicate.row_number > 1
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('customer_timeline_event_next', 'id'),
            COALESCE((SELECT MAX(id) FROM customer_timeline_event_next), 1),
            EXISTS (SELECT 1 FROM customer_timeline_event_next)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_timeline_event_next_event_id
        ON customer_timeline_event_next (event_id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'customer_timeline_event_next'::regclass
                  AND conname = 'uq_customer_timeline_event_next_event_id'
            ) THEN
                ALTER TABLE customer_timeline_event_next
                ADD CONSTRAINT uq_customer_timeline_event_next_event_id
                UNIQUE USING INDEX uq_customer_timeline_event_next_event_id;
            END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_customer_timeline_event_next_unionid_time_id
        ON customer_timeline_event_next (unionid, event_time DESC, id DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customer_timeline_event_next_unionid_time_id")
    op.execute(
        """
        ALTER TABLE customer_timeline_event_next
        DROP CONSTRAINT IF EXISTS uq_customer_timeline_event_next_event_id
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_customer_timeline_event_next_event_id")
