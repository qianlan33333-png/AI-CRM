from __future__ import annotations


HXC_OPTIONAL_SOURCE_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "new_version_user_subscriptions": frozenset({"phone", "expires_at"}),
    "automation_laohuang_chat_job": frozenset(
        {
            "external_contact_id",
            "phone",
            "status",
            "created_at",
            "updated_at",
            "finished_at",
        }
    ),
    "user_ops_huangxiaocan_activation_source": frozenset(
        {"mobile", "activation_state", "is_active", "created_at", "updated_at"}
    ),
    "user_ops_activation_status_source": frozenset(
        {"mobile", "activation_status", "is_active", "created_at", "updated_at"}
    ),
}


_OPTIONAL_MEMBERSHIP_SOURCE_SQL: dict[str, str] = {
    "new_version_user_subscriptions": """
    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           (s.expires_at > CURRENT_TIMESTAMP),
           NULL::timestamptz,
           s.expires_at::timestamptz,
           ''::text,
           CASE
               WHEN s.expires_at > CURRENT_TIMESTAMP THEN 'active'
               ELSE 'expired'
           END::text,
           'new_version_user_subscriptions.phone'::text
    FROM public.new_version_user_subscriptions s
    JOIN contact_mobile cm ON cm.mobile_hash = md5(s.phone::text)
    WHERE COALESCE(s.phone::text, '') <> ''
""",
}


_OPTIONAL_REGISTRATION_SOURCE_SQL: dict[str, str] = {
    "new_version_user_subscriptions": """
    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           TRUE,
           NULL::timestamptz,
           'new_version_user_subscriptions.phone'::text
    FROM public.new_version_user_subscriptions s
    JOIN contact_mobile cm ON cm.mobile_hash = md5(s.phone::text)
    WHERE COALESCE(s.phone::text, '') <> ''
""",
}


_OPTIONAL_USAGE_SOURCE_SQL: dict[str, str] = {
    "automation_laohuang_chat_job": """
    UNION ALL

    SELECT wc.unionid,
           wc.owner_userid,
           COALESCE(
               NULLIF(j.finished_at, '')::timestamptz,
               j.updated_at,
               j.created_at,
               CURRENT_TIMESTAMP
           ),
           'automation_laohuang_chat_job'::text
    FROM public.automation_laohuang_chat_job j
    JOIN contact_rows wc ON wc.external_userid = j.external_contact_id
    WHERE COALESCE(wc.unionid, '') <> ''
      AND COALESCE(j.status, '') IN (
          'accepted', 'send_success', 'callback_success'
      )
      AND COALESCE(j.external_contact_id, '') <> ''

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           COALESCE(
               NULLIF(j.finished_at, '')::timestamptz,
               j.updated_at,
               j.created_at,
               CURRENT_TIMESTAMP
           ),
           'automation_laohuang_chat_job'::text
    FROM public.automation_laohuang_chat_job j
    JOIN contact_mobile cm ON cm.mobile_hash = md5(j.phone)
    WHERE COALESCE(j.status, '') IN (
          'accepted', 'send_success', 'callback_success'
      )
      AND COALESCE(j.phone, '') <> ''
""",
    "user_ops_huangxiaocan_activation_source": """
    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           COALESCE(a.updated_at, a.created_at, CURRENT_TIMESTAMP),
           'user_ops_huangxiaocan_activation_source'::text
    FROM public.user_ops_huangxiaocan_activation_source a
    JOIN contact_mobile cm ON cm.mobile_hash = md5(a.mobile)
    WHERE COALESCE(a.is_active, TRUE)
      AND COALESCE(a.activation_state, '') = 'activated'
      AND COALESCE(a.mobile, '') <> ''
""",
    "user_ops_activation_status_source": """
    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           COALESCE(a.updated_at, a.created_at, CURRENT_TIMESTAMP),
           'user_ops_activation_status_source'::text
    FROM public.user_ops_activation_status_source a
    JOIN contact_mobile cm ON cm.mobile_hash = md5(a.mobile)
    WHERE COALESCE(a.is_active, TRUE)
      AND COALESCE(a.activation_status, '') = 'activated'
      AND COALESCE(a.mobile, '') <> ''
""",
}


# Every source is first resolved to the canonical (unionid, owner_userid) key.
# Keeping the identity joins as separate UNION ALL branches avoids the three
# OR predicates that made the legacy online view grow combinatorially.
_HXC_FULL_PROJECTION_SELECT_SQL_TEMPLATE = """
WITH contact_rows AS MATERIALIZED (
    SELECT wc.external_userid,
           wc.unionid,
           wc.owner_userid,
           wc.mobile_hash,
           wc.updated_at
    FROM audience_read.wecom_contacts_v1 wc
    WHERE COALESCE(wc.unionid, '') <> ''
),
contacts AS MATERIALIZED (
    SELECT wc.unionid,
           wc.owner_userid,
           MIN(NULLIF(wc.mobile_hash, '')) AS mobile_hash,
           MAX(wc.updated_at) AS contact_updated_at
    FROM contact_rows wc
    GROUP BY wc.unionid, wc.owner_userid
),
contact_mobile AS MATERIALIZED (
    SELECT DISTINCT wc.unionid, wc.owner_userid, wc.mobile_hash
    FROM contact_rows wc
    WHERE COALESCE(wc.mobile_hash, '') <> ''
),
membership_candidates AS MATERIALIZED (
    SELECT c.unionid,
           c.owner_userid,
           (
               COALESCE(s.hxc_member_hit, FALSE)
               OR COALESCE(s.membership_status, '') IN (
                   'active', 'valid', 'premium', 'standard', 'trial'
               )
               OR s.membership_end_at > CURRENT_TIMESTAMP
           ) AS is_member,
           s.hxc_registered_at::timestamptz AS member_since,
           s.membership_end_at::timestamptz AS membership_expires_at,
           COALESCE(s.membership_type, '')::text AS membership_tier,
           COALESCE(s.membership_status, '')::text AS membership_status,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contacts c ON c.unionid = s.unionid
    WHERE COALESCE(s.unionid, '') <> ''

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           (
               COALESCE(s.hxc_member_hit, FALSE)
               OR COALESCE(s.membership_status, '') IN (
                   'active', 'valid', 'premium', 'standard', 'trial'
               )
               OR s.membership_end_at > CURRENT_TIMESTAMP
           ) AS is_member,
           s.hxc_registered_at::timestamptz AS member_since,
           s.membership_end_at::timestamptz AS membership_expires_at,
           COALESCE(s.membership_type, '')::text AS membership_tier,
           COALESCE(s.membership_status, '')::text AS membership_status,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contact_mobile cm ON cm.mobile_hash = md5(s.mobile)
    WHERE COALESCE(s.mobile, '') <> ''
__OPTIONAL_MEMBERSHIP_SOURCES__
),
membership AS MATERIALIZED (
    SELECT unionid,
           owner_userid,
           BOOL_OR(is_member) AS is_member,
           MIN(member_since) AS member_since,
           MAX(membership_expires_at) AS membership_expires_at,
           COALESCE(
               STRING_AGG(DISTINCT NULLIF(membership_tier, ''), ','), ''
           ) AS membership_tier,
           COALESCE(
               STRING_AGG(DISTINCT NULLIF(membership_status, ''), ','), ''
           ) AS membership_status,
           COALESCE(STRING_AGG(DISTINCT source, ','), '') AS membership_source
    FROM membership_candidates
    GROUP BY unionid, owner_userid
),
registration_candidates AS MATERIALIZED (
    SELECT c.unionid,
           c.owner_userid,
           (
               COALESCE(s.hxc_user_hit, FALSE)
               OR s.hxc_registered_at IS NOT NULL
           ) AS is_registered,
           s.hxc_registered_at::timestamptz AS registered_at,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contacts c ON c.unionid = s.unionid
    WHERE COALESCE(s.unionid, '') <> ''
      AND (COALESCE(s.hxc_user_hit, FALSE) OR s.hxc_registered_at IS NOT NULL)

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           (
               COALESCE(s.hxc_user_hit, FALSE)
               OR s.hxc_registered_at IS NOT NULL
           ) AS is_registered,
           s.hxc_registered_at::timestamptz AS registered_at,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contact_mobile cm ON cm.mobile_hash = md5(s.mobile)
    WHERE COALESCE(s.mobile, '') <> ''
      AND (COALESCE(s.hxc_user_hit, FALSE) OR s.hxc_registered_at IS NOT NULL)

    UNION ALL

    SELECT wc.unionid,
           wc.owner_userid,
           COALESCE(rs.is_registered, FALSE),
           rs.registered_at,
           COALESCE(rs.source, 'registration_status_v1')
    FROM audience_read.registration_status_v1 rs
    JOIN contact_rows wc ON wc.external_userid = rs.external_userid
    WHERE COALESCE(wc.unionid, '') <> ''
      AND COALESCE(rs.external_userid, '') <> ''

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           COALESCE(rs.is_registered, FALSE),
           rs.registered_at,
           COALESCE(rs.source, 'registration_status_v1')
    FROM audience_read.registration_status_v1 rs
    JOIN contact_mobile cm ON cm.mobile_hash = rs.mobile_hash
    WHERE COALESCE(rs.mobile_hash, '') <> ''

    UNION ALL

    SELECT c.unionid,
           c.owner_userid,
           TRUE,
           h.registered_at,
           COALESCE(
               h.registered_source,
               'huangyoucan_registered_identities_v1'
           )
    FROM audience_read.huangyoucan_registered_identities_v1 h
    JOIN contacts c
      ON h.identity_type = 'unionid'
     AND h.identity_value = c.unionid
    WHERE COALESCE(h.identity_value, '') <> ''

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           TRUE,
           h.registered_at,
           COALESCE(
               h.registered_source,
               'huangyoucan_registered_identities_v1'
           )
    FROM audience_read.huangyoucan_registered_identities_v1 h
    JOIN contact_mobile cm
      ON h.identity_type = 'mobile_hash'
     AND h.identity_value = cm.mobile_hash
    WHERE COALESCE(h.identity_value, '') <> ''
__OPTIONAL_REGISTRATION_SOURCES__
),
registration AS MATERIALIZED (
    SELECT unionid,
           owner_userid,
           BOOL_OR(is_registered) AS is_registered,
           MIN(registered_at) AS registered_at,
           COALESCE(
               STRING_AGG(DISTINCT NULLIF(source, ''), ','), ''
           ) AS registration_source
    FROM registration_candidates
    GROUP BY unionid, owner_userid
),
usage_candidates AS MATERIALIZED (
    SELECT c.unionid,
           c.owner_userid,
           COALESCE(
               s.last_msg_at::timestamptz,
               s.refreshed_at::timestamptz
           ) AS used_at,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contacts c ON c.unionid = s.unionid
    WHERE COALESCE(s.unionid, '') <> ''
      AND (
          COALESCE(s.conv_chat, 0) > 0
          OR COALESCE(s.conv_consult, 0) > 0
          OR COALESCE(s.conv_lesson, 0) > 0
          OR COALESCE(s.msg_user, 0) > 0
          OR COALESCE(s.msg_ai, 0) > 0
          OR COALESCE(s.consult_completed, 0) > 0
          OR s.last_msg_at IS NOT NULL
      )

    UNION ALL

    SELECT cm.unionid,
           cm.owner_userid,
           COALESCE(
               s.last_msg_at::timestamptz,
               s.refreshed_at::timestamptz
           ) AS used_at,
           'user_ops_hxc_dashboard_snapshot'::text AS source
    FROM public.user_ops_hxc_dashboard_snapshot s
    JOIN contact_mobile cm ON cm.mobile_hash = md5(s.mobile)
    WHERE COALESCE(s.mobile, '') <> ''
      AND (
          COALESCE(s.conv_chat, 0) > 0
          OR COALESCE(s.conv_consult, 0) > 0
          OR COALESCE(s.conv_lesson, 0) > 0
          OR COALESCE(s.msg_user, 0) > 0
          OR COALESCE(s.msg_ai, 0) > 0
          OR COALESCE(s.consult_completed, 0) > 0
          OR s.last_msg_at IS NOT NULL
      )

    UNION ALL

    SELECT c.unionid,
           c.owner_userid,
           m.send_time,
           'customer_recent_message_next'::text
    FROM public.customer_recent_message_next m
    JOIN contacts c ON c.unionid = m.unionid
    WHERE COALESCE(m.unionid, '') <> ''
__OPTIONAL_USAGE_SOURCES__
),
usage AS MATERIALIZED (
    SELECT unionid,
           owner_userid,
           MIN(used_at) AS first_used_at,
           MAX(used_at) AS last_used_at,
           COALESCE(STRING_AGG(DISTINCT source, ','), '') AS usage_source
    FROM usage_candidates
    GROUP BY unionid, owner_userid
)
SELECT c.unionid,
       c.owner_userid,
       COALESCE(c.mobile_hash, '') AS mobile_hash,
       COALESCE(m.is_member, FALSE) AS is_member,
       COALESCE(r.is_registered, FALSE) AS is_registered,
       r.registered_at,
       (u.unionid IS NOT NULL) AS has_real_usage,
       u.first_used_at,
       u.last_used_at,
       m.member_since,
       m.membership_expires_at,
       COALESCE(m.membership_tier, '') AS membership_tier,
       COALESCE(m.membership_status, '') AS membership_status,
       COALESCE(m.membership_source, '') AS membership_source,
       COALESCE(r.registration_source, '') AS registration_source,
       COALESCE(u.usage_source, '') AS usage_source,
       NULLIF(
           GREATEST(
               COALESCE(c.contact_updated_at, '-infinity'::timestamptz),
               COALESCE(r.registered_at, '-infinity'::timestamptz),
               COALESCE(u.last_used_at, '-infinity'::timestamptz),
               COALESCE(m.membership_expires_at, '-infinity'::timestamptz)
           ),
           '-infinity'::timestamptz
       ) AS updated_at,
       jsonb_build_object(
           'membership_source', COALESCE(m.membership_source, ''),
           'registration_source', COALESCE(r.registration_source, ''),
           'usage_source', COALESCE(u.usage_source, ''),
           'has_mobile_hash', COALESCE(c.mobile_hash, '') <> '',
           'has_unionid', TRUE
       ) AS payload_json
FROM contacts c
LEFT JOIN membership m USING (unionid, owner_userid)
LEFT JOIN registration r USING (unionid, owner_userid)
LEFT JOIN usage u USING (unionid, owner_userid)
"""


_HXC_FULL_PROJECTION_INSERT_SQL_TEMPLATE = """
INSERT INTO ai_audience_hxc_member_usage_projection (
    generation,
    unionid,
    owner_userid,
    mobile_hash,
    is_member,
    is_registered,
    registered_at,
    has_real_usage,
    first_used_at,
    last_used_at,
    member_since,
    membership_expires_at,
    membership_tier,
    membership_status,
    membership_source,
    registration_source,
    usage_source,
    updated_at,
    payload_json,
    projected_at
)
SELECT :generation,
       projection.unionid,
       projection.owner_userid,
       projection.mobile_hash,
       projection.is_member,
       projection.is_registered,
       projection.registered_at,
       projection.has_real_usage,
       projection.first_used_at,
       projection.last_used_at,
       projection.member_since,
       projection.membership_expires_at,
       projection.membership_tier,
       projection.membership_status,
       projection.membership_source,
       projection.registration_source,
       projection.usage_source,
       projection.updated_at,
       projection.payload_json,
       CURRENT_TIMESTAMP
FROM (
__PROJECTION_SELECT_SQL__
) projection
ON CONFLICT (generation, unionid, owner_userid) DO UPDATE
SET mobile_hash = EXCLUDED.mobile_hash,
    is_member = EXCLUDED.is_member,
    is_registered = EXCLUDED.is_registered,
    registered_at = EXCLUDED.registered_at,
    has_real_usage = EXCLUDED.has_real_usage,
    first_used_at = EXCLUDED.first_used_at,
    last_used_at = EXCLUDED.last_used_at,
    member_since = EXCLUDED.member_since,
    membership_expires_at = EXCLUDED.membership_expires_at,
    membership_tier = EXCLUDED.membership_tier,
    membership_status = EXCLUDED.membership_status,
    membership_source = EXCLUDED.membership_source,
    registration_source = EXCLUDED.registration_source,
    usage_source = EXCLUDED.usage_source,
    updated_at = EXCLUDED.updated_at,
    payload_json = EXCLUDED.payload_json,
    projected_at = CURRENT_TIMESTAMP
"""


def build_hxc_full_projection_select_sql(optional_sources: set[str]) -> str:
    optional_membership_sql = "".join(
        sql
        for source, sql in _OPTIONAL_MEMBERSHIP_SOURCE_SQL.items()
        if source in optional_sources
    )
    optional_registration_sql = "".join(
        sql
        for source, sql in _OPTIONAL_REGISTRATION_SOURCE_SQL.items()
        if source in optional_sources
    )
    optional_sql = "".join(
        sql
        for source, sql in _OPTIONAL_USAGE_SOURCE_SQL.items()
        if source in optional_sources
    )
    return (
        _HXC_FULL_PROJECTION_SELECT_SQL_TEMPLATE.replace(
            "__OPTIONAL_MEMBERSHIP_SOURCES__",
            optional_membership_sql,
        )
        .replace(
            "__OPTIONAL_REGISTRATION_SOURCES__",
            optional_registration_sql,
        )
        .replace(
            "__OPTIONAL_USAGE_SOURCES__",
            optional_sql,
        )
    )


def build_hxc_full_projection_insert_sql(optional_sources: set[str]) -> str:
    return _HXC_FULL_PROJECTION_INSERT_SQL_TEMPLATE.replace(
        "__PROJECTION_SELECT_SQL__",
        build_hxc_full_projection_select_sql(optional_sources),
    )


HXC_FULL_PROJECTION_SELECT_SQL = build_hxc_full_projection_select_sql(set())
HXC_FULL_PROJECTION_INSERT_SQL = build_hxc_full_projection_insert_sql(set())
