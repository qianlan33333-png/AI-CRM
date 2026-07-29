"""add shared member-grid views for service-period products.

Lifecycle manifest entry:
- table: service_period_member_views
- lifecycle: canonical
- write_owner: aicrm_next.extensions.commerce.service_period

Rollback note:
- Roll back the application first. The additive view table can remain because older
  releases do not read it; downgrade is available when a full schema rollback is required.

Revision ID: 0120_service_period_member_views
Revises: 0119_group_invite_bindings
"""

from __future__ import annotations

from alembic import op


revision = "0120_service_period_member_views"
down_revision = "0119_group_invite_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS service_period_member_views (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            service_product_id BIGINT NOT NULL REFERENCES service_period_products(id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK (BTRIM(name) <> '' AND CHAR_LENGTH(name) <= 60),
            position INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            schema_version SMALLINT NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            config_json JSONB NOT NULL DEFAULT '{
                "schema_version": 1,
                "filter": {"logic": "and", "conditions": []},
                "sorts": [],
                "groups": []
            }'::jsonb,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_service_period_member_views_name
        ON service_period_member_views (tenant_id, service_product_id, LOWER(name))
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_service_period_member_views_default
        ON service_period_member_views (tenant_id, service_product_id)
        WHERE is_default = TRUE
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_service_period_member_views_product_position
        ON service_period_member_views (tenant_id, service_product_id, position, id)
        """
    )
    op.execute(
        """
        INSERT INTO service_period_member_views (
            tenant_id, service_product_id, name, position, is_default,
            schema_version, config_json, version, created_by, updated_by,
            created_at, updated_at
        )
        SELECT
            products.tenant_id,
            products.id,
            '表格',
            0,
            TRUE,
            1,
            '{"schema_version": 1, "filter": {"logic": "and", "conditions": []}, "sorts": [], "groups": []}'::jsonb,
            1,
            'migration',
            'migration',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM service_period_products products
        WHERE products.deleted = FALSE
          AND NOT EXISTS (
              SELECT 1
              FROM service_period_member_views existing
              WHERE existing.tenant_id = products.tenant_id
                AND existing.service_product_id = products.id
                AND existing.is_default = TRUE
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_service_period_member_views_product_position")
    op.execute("DROP INDEX IF EXISTS uq_service_period_member_views_default")
    op.execute("DROP INDEX IF EXISTS uq_service_period_member_views_name")
    op.execute("DROP TABLE IF EXISTS service_period_member_views")
