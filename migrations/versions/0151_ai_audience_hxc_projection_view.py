"""Cut over the HuangXiaoCan audience view to the active projection generation.

Revision ID: 0151_ai_audience_hxc_projection_view
Revises: 0150_crm_identity_updated_cursor_index
"""

from __future__ import annotations

import importlib

from alembic import op


revision = "0151_ai_audience_hxc_projection_view"
down_revision = "0150_crm_identity_updated_cursor_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS audience_read")
    op.execute(
        """
        CREATE OR REPLACE VIEW audience_read.huangxiaocan_member_usage_status_v1 AS
        WITH projection_control AS MATERIALIZED (
            SELECT control.active_generation
            FROM public.ai_audience_hxc_member_usage_projection_control control
            WHERE control.singleton = TRUE
              AND control.active_generation > 0
        )
        SELECT
            mapped_identity.external_userid::text AS external_userid,
            NULL::bigint AS person_id,
            COALESCE(projection.mobile_hash, '')::text AS mobile_hash,
            projection.unionid::text AS unionid,
            projection.owner_userid::text AS owner_userid,
            projection.is_member::boolean AS is_member,
            projection.is_registered::boolean AS is_registered,
            projection.registered_at::timestamptz AS registered_at,
            projection.has_real_usage::boolean AS has_real_usage,
            projection.first_used_at::timestamptz AS first_used_at,
            projection.last_used_at::timestamptz AS last_used_at,
            projection.member_since::timestamptz AS member_since,
            projection.membership_expires_at::timestamptz AS membership_expires_at,
            projection.membership_tier::text AS membership_tier,
            projection.membership_status::text AS membership_status,
            projection.membership_source::text AS membership_source,
            projection.registration_source::text AS registration_source,
            projection.usage_source::text AS usage_source,
            projection.updated_at::timestamptz AS updated_at,
            projection.payload_json::jsonb AS payload_json
        FROM public.ai_audience_hxc_member_usage_projection projection
        JOIN LATERAL (
            SELECT identity_map.external_userid
            FROM public.wecom_external_contact_identity_map identity_map
            WHERE identity_map.unionid = projection.unionid
              AND COALESCE(identity_map.external_userid, '') <> ''
            ORDER BY
                (
                    COALESCE(identity_map.follow_user_userid, '') =
                    projection.owner_userid
                ) DESC,
                (COALESCE(identity_map.status, 'active') = 'active') DESC,
                identity_map.updated_at DESC NULLS LAST,
                identity_map.id DESC
            LIMIT 1
        ) mapped_identity ON TRUE
        WHERE projection.generation = (
            SELECT control.active_generation
            FROM projection_control control
        )
        """
    )
    op.execute(
        """
        COMMENT ON VIEW audience_read.huangxiaocan_member_usage_status_v1 IS
        'Stable v1 audience contract backed only by the atomically selected active HuangXiaoCan projection generation; never rebuilds source facts in an online request.'
        """
    )


def downgrade() -> None:
    # Restoring the historical view is an explicit database rollback. It does
    # not delete the projection or its last ready generation.
    legacy = importlib.import_module(
        "migrations.versions.0060_ai_audience_huangxiaocan_member_usage_view"
    )
    legacy._create_huangxiaocan_member_usage_view()
