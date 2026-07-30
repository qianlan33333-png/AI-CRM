"""huangyoucan unregistered ai audience package.

Revision ID: 0057_huangyoucan_unregistered_ai_audience
Revises: 0056_ai_audience_group_chat_members_view
"""

from __future__ import annotations

from alembic import op


revision = "0057_huangyoucan_unregistered_ai_audience"
down_revision = "0056_ai_audience_group_chat_members_view"
branch_labels = None
depends_on = None


HUANGYOUCAN_UNREGISTERED_PACKAGE_KEY = "huangyoucan_wecom_unregistered"
HUANGYOUCAN_UNREGISTERED_SNAPSHOT_SQL = """
SELECT 'external_userid' AS identity_type,
    wc.external_userid AS identity_value,
    'huangyoucan_unregistered:' || wc.external_userid AS event_source_key,
    jsonb_build_object(
        'audience_key', 'huangyoucan_wecom_unregistered',
        'external_userid', wc.external_userid,
        'unionid', wc.unionid,
        'owner_userid', wc.owner_userid,
        'customer_name', wc.customer_name,
        'has_mobile_hash', COALESCE(wc.mobile_hash, '') <> '',
        'has_unionid', COALESCE(wc.unionid, '') <> '',
        'registered_mobile_match', false,
        'registered_unionid_match', false
    ) AS payload_json,
    wc.external_userid,
    wc.unionid,
    wc.mobile_hash,
    wc.owner_userid,
    wc.updated_at AS event_at
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.huangyoucan_registered_identities_v1 registered_mobile
    ON registered_mobile.identity_type = 'mobile_hash'
   AND registered_mobile.identity_value = wc.mobile_hash
   AND COALESCE(wc.mobile_hash, '') <> ''
LEFT JOIN audience_read.huangyoucan_registered_identities_v1 registered_union
    ON registered_union.identity_type = 'unionid'
   AND registered_union.identity_value = wc.unionid
   AND COALESCE(wc.unionid, '') <> ''
WHERE wc.owner_userid = :owner_userid
  AND COALESCE(wc.external_userid, '') <> ''
  AND (COALESCE(wc.status, '') = '' OR wc.status = 'active')
  AND (COALESCE(wc.mobile_hash, '') <> '' OR COALESCE(wc.unionid, '') <> '')
  AND registered_mobile.identity_value IS NULL
  AND registered_union.identity_value IS NULL
"""


def upgrade() -> None:
    _refresh_wecom_contacts_view()
    _create_huangyoucan_registered_identities_view()
    _seed_huangyoucan_unregistered_package()


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM ai_audience_package_dependency
        WHERE package_id IN (
            SELECT id FROM ai_audience_package WHERE package_key = 'huangyoucan_wecom_unregistered'
        )
        """
    )
    op.execute(
        """
        DELETE FROM ai_audience_package_version
        WHERE package_id IN (
            SELECT id FROM ai_audience_package WHERE package_key = 'huangyoucan_wecom_unregistered'
        )
        """
    )
    op.execute("DELETE FROM ai_audience_package WHERE package_key = 'huangyoucan_wecom_unregistered'")
    op.execute("DROP VIEW IF EXISTS audience_read.huangyoucan_registered_identities_v1")


def _refresh_wecom_contacts_view(*, ensure_schema: bool = True) -> None:
    if ensure_schema:
        op.execute("CREATE SCHEMA IF NOT EXISTS audience_read")
    op.execute(
        """
        CREATE OR REPLACE VIEW audience_read.wecom_contacts_v1 AS
        SELECT ''::text AS external_userid, ''::text AS unionid, ''::text AS openid,
               ''::text AS owner_userid, ''::text AS customer_name, ''::text AS status,
               NULL::timestamptz AS updated_at, '{}'::jsonb AS payload_json, ''::text AS mobile_hash
        WHERE FALSE
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.wecom_external_contact_identity_map') IS NOT NULL
               AND to_regclass('public.external_contact_bindings') IS NOT NULL
               AND to_regclass('public.people') IS NOT NULL THEN
                CREATE OR REPLACE VIEW audience_read.wecom_contacts_v1 AS
                SELECT
                    COALESCE(im.external_userid, '')::text AS external_userid,
                    COALESCE(im.unionid, '')::text AS unionid,
                    COALESCE(im.openid, '')::text AS openid,
                    COALESCE(NULLIF(im.follow_user_userid, ''), '')::text AS owner_userid,
                    COALESCE(im.name, '')::text AS customer_name,
                    COALESCE(im.status, '')::text AS status,
                    COALESCE(im.updated_at::timestamptz, CURRENT_TIMESTAMP) AS updated_at,
                    jsonb_build_object(
                        'external_userid', COALESCE(im.external_userid, ''),
                        'owner_userid', COALESCE(im.follow_user_userid, ''),
                        'name', COALESCE(im.name, ''),
                        'status', COALESCE(im.status, ''),
                        'mobile_bound', COALESCE(p.mobile, '') <> ''
                    ) AS payload_json,
                    CASE WHEN COALESCE(p.mobile, '') <> '' THEN md5(p.mobile) ELSE '' END::text AS mobile_hash
                FROM wecom_external_contact_identity_map im
                LEFT JOIN external_contact_bindings b ON b.external_userid = im.external_userid
                LEFT JOIN people p ON p.id::text = b.person_id::text
                WHERE COALESCE(im.external_userid, '') <> '';
            ELSIF to_regclass('public.wecom_external_contact_identity_map') IS NOT NULL THEN
                CREATE OR REPLACE VIEW audience_read.wecom_contacts_v1 AS
                SELECT
                    COALESCE(im.external_userid, '')::text AS external_userid,
                    COALESCE(im.unionid, '')::text AS unionid,
                    COALESCE(im.openid, '')::text AS openid,
                    COALESCE(NULLIF(im.follow_user_userid, ''), '')::text AS owner_userid,
                    COALESCE(im.name, '')::text AS customer_name,
                    COALESCE(im.status, '')::text AS status,
                    COALESCE(im.updated_at::timestamptz, CURRENT_TIMESTAMP) AS updated_at,
                    jsonb_build_object(
                        'external_userid', COALESCE(im.external_userid, ''),
                        'owner_userid', COALESCE(im.follow_user_userid, ''),
                        'name', COALESCE(im.name, ''),
                        'status', COALESCE(im.status, ''),
                        'mobile_bound', false
                    ) AS payload_json,
                    ''::text AS mobile_hash
                FROM wecom_external_contact_identity_map im
                WHERE COALESCE(im.external_userid, '') <> '';
            END IF;
        END $$;
        """
    )


def _create_huangyoucan_registered_identities_view() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW audience_read.huangyoucan_registered_identities_v1 AS
        SELECT ''::text AS identity_type, ''::text AS identity_value,
               ''::text AS registered_source, NULL::timestamptz AS registered_at
        WHERE FALSE
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            parts text[] := ARRAY[]::text[];
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'new_version_users' AND column_name = 'phone'
            ) THEN
                parts := array_append(parts, $sql$
                    SELECT 'mobile_hash'::text AS identity_type,
                           md5(phone)::text AS identity_value,
                           'new_version_users.phone'::text AS registered_source,
                           CURRENT_TIMESTAMP AS registered_at
                    FROM public.new_version_users
                    WHERE COALESCE(phone, '') <> ''
                $sql$);
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'new_version_memberships' AND column_name = 'phone'
            ) THEN
                parts := array_append(parts, $sql$
                    SELECT 'mobile_hash'::text AS identity_type,
                           md5(phone)::text AS identity_value,
                           'new_version_memberships.phone'::text AS registered_source,
                           CURRENT_TIMESTAMP AS registered_at
                    FROM public.new_version_memberships
                    WHERE COALESCE(phone, '') <> ''
                $sql$);
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'new_version_wechat_auth' AND column_name = 'unionid'
            ) THEN
                parts := array_append(parts, $sql$
                    SELECT 'unionid'::text AS identity_type,
                           unionid::text AS identity_value,
                           'new_version_wechat_auth.unionid'::text AS registered_source,
                           CURRENT_TIMESTAMP AS registered_at
                    FROM public.new_version_wechat_auth
                    WHERE COALESCE(unionid, '') <> ''
                $sql$);
            END IF;

            IF array_length(parts, 1) > 0 THEN
                EXECUTE 'CREATE OR REPLACE VIEW audience_read.huangyoucan_registered_identities_v1 AS '
                    || array_to_string(parts, ' UNION ALL ');
            END IF;
        END $$;
        """
    )


def _seed_huangyoucan_unregistered_package() -> None:
    snapshot_sql = _snapshot_sql_literal()
    op.execute(
        f"""
        WITH package_upsert AS (
            INSERT INTO ai_audience_package (
                package_key, name, natural_language_definition, status, query_mode, identity_policy,
                incremental_enabled, daily_enabled, incremental_interval_seconds, daily_refresh_time,
                timezone, lookback_seconds, inbound_webhook_secret,
                next_incremental_refresh_at, next_daily_refresh_at, created_at, updated_at
            )
            VALUES (
                '{HUANGYOUCAN_UNREGISTERED_PACKAGE_KEY}',
                'HuangYouCan企微未注册人群',
                '企微账号 HuangYouCan 下，手机号哈希和 unionid 都没有命中 HuangYouCanAI 注册身份索引的人群。',
                'active',
                'snapshot_current',
                'external_userid',
                FALSE,
                TRUE,
                86400,
                '02:00',
                'Asia/Shanghai',
                0,
                '',
                NULL,
                CASE
                    WHEN (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::time < TIME '02:00'
                    THEN (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai') + interval '2 hours') AT TIME ZONE 'Asia/Shanghai'
                    ELSE (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai') + interval '1 day 2 hours') AT TIME ZONE 'Asia/Shanghai'
                END,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (package_key) DO UPDATE SET
                name = EXCLUDED.name,
                natural_language_definition = EXCLUDED.natural_language_definition,
                status = 'active',
                query_mode = EXCLUDED.query_mode,
                identity_policy = EXCLUDED.identity_policy,
                incremental_enabled = FALSE,
                daily_enabled = TRUE,
                daily_refresh_time = '02:00',
                timezone = 'Asia/Shanghai',
                lookback_seconds = 0,
                next_incremental_refresh_at = NULL,
                next_daily_refresh_at = COALESCE(ai_audience_package.next_daily_refresh_at, EXCLUDED.next_daily_refresh_at),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        ),
        version_upsert AS (
            INSERT INTO ai_audience_package_version (
                package_id, version_number, status, incremental_sql_text, snapshot_sql_text,
                parameters_json, ai_prompt, ai_rationale, natural_language_explanation, dependencies_json,
                explain_json, sample_rows_json, validation_errors_json, created_at, published_at
            )
            SELECT
                id,
                1,
                'published',
                '',
                '{snapshot_sql}',
                '{{"owner_userid":"HuangYouCan"}}'::jsonb,
                '',
                '每日 02:00 生成 HuangYouCan 企微账号下，在 HuangYouCanAI 注册库中手机号和 unionid 均未命中的客户。',
                '使用企微联系人只读视图反查 HuangYouCanAI 注册身份索引；仅写入 AI Audience 人群包，不执行真实外呼。',
                '["audience_read.wecom_contacts_v1","audience_read.huangyoucan_registered_identities_v1"]'::jsonb,
                '{{}}'::jsonb,
                '[]'::jsonb,
                '[]'::jsonb,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM package_upsert
            ON CONFLICT (package_id, version_number) DO UPDATE SET
                status = 'published',
                snapshot_sql_text = EXCLUDED.snapshot_sql_text,
                parameters_json = EXCLUDED.parameters_json,
                ai_rationale = EXCLUDED.ai_rationale,
                natural_language_explanation = EXCLUDED.natural_language_explanation,
                dependencies_json = EXCLUDED.dependencies_json,
                validation_errors_json = '[]'::jsonb,
                published_at = CURRENT_TIMESTAMP
            RETURNING id, package_id
        )
        UPDATE ai_audience_package p
        SET current_version_id = v.id,
            status = 'active',
            updated_at = CURRENT_TIMESTAMP
        FROM version_upsert v
        WHERE p.id = v.package_id
        """
    )
    op.execute(
        """
        INSERT INTO ai_audience_package_dependency (
            package_id, version_id, source_type, source_key, view_name, created_at
        )
        SELECT
            p.id,
            v.id,
            dep.source_type,
            '',
            dep.view_name,
            CURRENT_TIMESTAMP
        FROM ai_audience_package p
        JOIN ai_audience_package_version v ON v.id = p.current_version_id
        CROSS JOIN (
            VALUES
                ('wecom_contact', 'audience_read.wecom_contacts_v1'),
                ('huangyoucan_registered_identity', 'audience_read.huangyoucan_registered_identities_v1')
        ) AS dep(source_type, view_name)
        WHERE p.package_key = 'huangyoucan_wecom_unregistered'
        ON CONFLICT DO NOTHING
        """
    )


def _snapshot_sql_literal() -> str:
    return HUANGYOUCAN_UNREGISTERED_SNAPSHOT_SQL.strip().replace("'", "''").replace(":owner_userid", r"\:owner_userid")
