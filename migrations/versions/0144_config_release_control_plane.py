"""Add versioned deployment profile and atomic config release state.

Revision ID: 0144_config_release_control_plane
Revises: 0143_data_health_snapshot
"""

from __future__ import annotations

from alembic import op


revision = "0144_config_release_control_plane"
down_revision = "0143_data_health_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS config_releases (
            id BIGSERIAL PRIMARY KEY,
            release_key TEXT NOT NULL UNIQUE,
            profile_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            changes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            validation_errors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            checksum TEXT NOT NULL,
            based_on_release_id BIGINT NULL,
            rollback_of_release_id BIGINT NULL,
            created_by TEXT NOT NULL,
            published_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            validated_at TIMESTAMPTZ NULL,
            published_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_config_release_status CHECK (
                status IN ('draft', 'validated', 'validation_failed', 'published', 'superseded')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_config_releases_profile_created
        ON config_releases(profile_id, created_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_config_releases_profile_published
        ON config_releases(profile_id)
        WHERE status = 'published'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deployment_profile_state (
            profile_id TEXT PRIMARY KEY,
            core_version TEXT NOT NULL,
            config_schema_version INTEGER NOT NULL,
            activation_mode TEXT NOT NULL DEFAULT 'observe',
            enabled_capabilities_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            runtime_roles_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            active_config_release_id BIGINT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_deployment_profile_activation_mode CHECK (
                activation_mode IN ('observe', 'enforce')
            ),
            CONSTRAINT ck_deployment_profile_config_schema_version CHECK (
                config_schema_version > 0
            )
        )
        """
    )


def downgrade() -> None:
    # Config release history is production audit evidence. A previous-release
    # rollback can ignore these additive tables and must not delete that history.
    pass
