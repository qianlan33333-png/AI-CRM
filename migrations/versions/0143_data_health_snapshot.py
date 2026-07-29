"""Add the singleton data-health read snapshot.

Revision ID: 0143_data_health_snapshot
Revises: 0142_sidebar_recent_message_index
"""

from __future__ import annotations

from alembic import op


revision = "0143_data_health_snapshot"
down_revision = "0142_sidebar_recent_message_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_health_snapshot (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton IS TRUE),
            generation_id UUID NOT NULL,
            source_release_sha TEXT NOT NULL CHECK (
                source_release_sha = 'unknown'
                OR source_release_sha ~ '^[0-9a-f]{40}$'
            ),
            overall_status TEXT NOT NULL CHECK (
                overall_status IN ('ok', 'warn', 'fail', 'not_applicable')
            ),
            check_count INTEGER NOT NULL CHECK (check_count > 0),
            checks_json JSONB NOT NULL CHECK (
                jsonb_typeof(checks_json) = 'array'
                AND jsonb_array_length(checks_json) = check_count
            ),
            duration_ms BIGINT NOT NULL CHECK (duration_ms >= 0),
            captured_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_health_snapshot")
