"""Add the generation-based HuangXiaoCan audience projection foundation.

Revision ID: 0149_ai_audience_hxc_projection
Revises: 0148_order_source_sort_indexes
"""

from __future__ import annotations

from alembic import op


revision = "0149_ai_audience_hxc_projection"
down_revision = "0148_order_source_sort_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_audience_hxc_member_usage_projection_control (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton IS TRUE),
            active_generation BIGINT NOT NULL DEFAULT 0 CHECK (active_generation >= 0),
            status TEXT NOT NULL DEFAULT 'empty'
                CHECK (status IN ('empty', 'building', 'ready', 'failed')),
            projected_row_count BIGINT NOT NULL DEFAULT 0 CHECK (projected_row_count >= 0),
            last_incremental_watermark_at TIMESTAMPTZ,
            last_full_refreshed_at TIMESTAMPTZ,
            last_refresh_started_at TIMESTAMPTZ,
            last_refresh_finished_at TIMESTAMPTZ,
            source_watermarks_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(source_watermarks_json) = 'object'),
            last_error_code TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        INSERT INTO ai_audience_hxc_member_usage_projection_control (singleton)
        VALUES (TRUE)
        ON CONFLICT (singleton) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_audience_hxc_member_usage_projection (
            generation BIGINT NOT NULL CHECK (generation > 0),
            unionid TEXT NOT NULL CHECK (BTRIM(unionid) <> ''),
            owner_userid TEXT NOT NULL DEFAULT '',
            mobile_hash TEXT NOT NULL DEFAULT '',
            is_member BOOLEAN NOT NULL DEFAULT FALSE,
            is_registered BOOLEAN NOT NULL DEFAULT FALSE,
            registered_at TIMESTAMPTZ,
            has_real_usage BOOLEAN NOT NULL DEFAULT FALSE,
            first_used_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            member_since TIMESTAMPTZ,
            membership_expires_at TIMESTAMPTZ,
            membership_tier TEXT NOT NULL DEFAULT '',
            membership_status TEXT NOT NULL DEFAULT '',
            membership_source TEXT NOT NULL DEFAULT '',
            registration_source TEXT NOT NULL DEFAULT '',
            usage_source TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(payload_json) = 'object'),
            projected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (generation, unionid, owner_userid)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_hxc_projection_member_unused
        ON ai_audience_hxc_member_usage_projection (
            generation, owner_userid, unionid
        )
        WHERE is_member AND is_registered AND NOT has_real_usage
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_hxc_projection_active_member
        ON ai_audience_hxc_member_usage_projection (generation, unionid, owner_userid)
        WHERE is_member
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_hxc_projection_mobile_hash
        ON ai_audience_hxc_member_usage_projection (generation, mobile_hash)
        WHERE mobile_hash <> ''
        """
    )
    op.execute(
        """
        COMMENT ON TABLE ai_audience_hxc_member_usage_projection IS
        'Generation-based unionid read model for HuangXiaoCan membership and real-usage audience queries; stores one-way mobile hashes, never raw mobile values or legacy contact identifiers.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE ai_audience_hxc_member_usage_projection_control IS
        'Singleton atomic generation and freshness watermark for the HuangXiaoCan audience projection.'
        """
    )


def downgrade() -> None:
    # Canonical/read-model tables are append-only schema once published. The
    # legacy audience_read view is unchanged, so a code rollback simply leaves
    # this empty foundation unused. Any future retirement requires its own
    # observed and explicitly governed migration.
    pass
