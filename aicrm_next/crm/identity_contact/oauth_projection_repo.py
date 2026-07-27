from __future__ import annotations

import json
from typing import Any

from .dto import ResolvePersonIdentityRequest
from .resolver import resolve_identity_with_dbapi, resolved_unionid


def _text(value: Any) -> str:
    return str(value or "").strip()


def _record_conflict(
    conn: Any,
    *,
    unionid: str,
    candidate_unionid: str,
    openid: str,
    reason: str,
    source_route: str,
) -> None:
    payload = json.dumps(
        {"reason": reason, "source_route": source_route},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    conn.execute(
        """
        INSERT INTO crm_user_identity_conflicts (
            conflict_type,
            unionid,
            candidate_unionid,
            openid,
            source_type,
            source_key,
            payload_json,
            source_payload_json,
            status,
            resolution_status,
            created_at,
            updated_at
        )
        SELECT
            'wechat_oauth_alias_conflict',
            %s,
            %s,
            %s,
            'wechat_payment_oauth',
            %s,
            %s::jsonb,
            '{}'::jsonb,
            'open',
            'open',
            NOW(),
            NOW()
        WHERE NOT EXISTS (
            SELECT 1
            FROM crm_user_identity_conflicts
            WHERE conflict_type = 'wechat_oauth_alias_conflict'
              AND unionid = %s
              AND candidate_unionid = %s
              AND openid = %s
              AND resolution_status = 'open'
        )
        """,
        (
            unionid,
            candidate_unionid,
            openid,
            openid,
            payload,
            unionid,
            candidate_unionid,
            openid,
        ),
    )


def project_wechat_oauth_identity(
    conn: Any,
    *,
    openid: str,
    unionid: str = "",
    payer_name: str = "",
    source_route: str = "/api/h5/wechat-pay/oauth/callback",
) -> dict[str, Any]:
    """Project a server-verified WeChat OAuth identity into the canonical table.

    The public-account ``openid`` is serialized with an advisory transaction
    lock before it is inspected or attached.  This prevents two concurrent
    callbacks from assigning the same alias to different canonical unionids.
    Conflicts are audited and never auto-merged.
    """

    normalized_openid = _text(openid)
    explicit_unionid = _text(unionid)
    normalized_name = _text(payer_name)[:200]
    if not normalized_openid:
        return {"ok": False, "projected": False, "reason": "openid_required", "unionid": ""}
    if len(normalized_openid) > 255 or len(explicit_unionid) > 255:
        return {"ok": False, "projected": False, "reason": "identity_value_invalid", "unionid": ""}

    lock_keys = {f"wechat-oauth-openid:{normalized_openid}"}
    if explicit_unionid:
        lock_keys.add(f"wechat-oauth-unionid:{explicit_unionid}")
    for lock_key in sorted(lock_keys):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))

    openid_result = resolve_identity_with_dbapi(
        conn,
        ResolvePersonIdentityRequest(openid=normalized_openid),
        for_update=True,
    )
    openid_unionid = resolved_unionid(openid_result)
    if openid_result.status in {"pending", "conflict"}:
        _record_conflict(
            conn,
            unionid=explicit_unionid,
            candidate_unionid=openid_unionid,
            openid=normalized_openid,
            reason=openid_result.reason or "openid_identity_conflict",
            source_route=source_route,
        )
        return {
            "ok": False,
            "projected": False,
            "reason": "openid_identity_conflict",
            "unionid": "",
        }

    if not explicit_unionid:
        if not openid_unionid:
            return {"ok": False, "projected": False, "reason": "unionid_required", "unionid": ""}
        conn.execute(
            """
            UPDATE crm_user_identity
            SET customer_name = COALESCE(NULLIF(customer_name, ''), NULLIF(%s, ''), customer_name),
                last_seen_at = NOW(),
                updated_at = NOW()
            WHERE unionid = %s
              AND identity_status = 'active'
            """,
            (normalized_name, openid_unionid),
        )
        return {"ok": True, "projected": True, "reason": "resolved_by_openid", "unionid": openid_unionid}

    unionid_result = resolve_identity_with_dbapi(
        conn,
        ResolvePersonIdentityRequest(unionid=explicit_unionid),
        for_update=True,
    )
    if unionid_result.status in {"pending", "conflict"}:
        _record_conflict(
            conn,
            unionid=explicit_unionid,
            candidate_unionid=openid_unionid,
            openid=normalized_openid,
            reason=unionid_result.reason or "unionid_identity_conflict",
            source_route=source_route,
        )
        return {
            "ok": False,
            "projected": False,
            "reason": "unionid_identity_conflict",
            "unionid": "",
        }
    canonical_unionid = resolved_unionid(unionid_result) or explicit_unionid
    if openid_unionid and openid_unionid != canonical_unionid:
        _record_conflict(
            conn,
            unionid=canonical_unionid,
            candidate_unionid=openid_unionid,
            openid=normalized_openid,
            reason="openid_mapped_to_other_unionid",
            source_route=source_route,
        )
        return {
            "ok": False,
            "projected": False,
            "reason": "openid_identity_conflict",
            "unionid": "",
        }

    audit_payload = json.dumps(
        {
            "wechat_payment_oauth": {
                "source_route": _text(source_route),
                "openid_verified": True,
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    row = conn.execute(
        """
        INSERT INTO crm_user_identity (
            unionid,
            primary_openid,
            openids_json,
            customer_name,
            profile_json,
            legacy_sources_json,
            identity_status,
            unionid_resolved_at,
            first_seen_at,
            last_seen_at,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            jsonb_build_array(CAST(%s AS text)),
            %s,
            %s::jsonb,
            jsonb_build_object('wechat_payment_oauth', TRUE),
            'active',
            NOW(),
            NOW(),
            NOW(),
            NOW(),
            NOW()
        )
        ON CONFLICT (unionid) DO UPDATE SET
            primary_openid = COALESCE(
                NULLIF(crm_user_identity.primary_openid, ''),
                EXCLUDED.primary_openid
            ),
            openids_json = (
                SELECT COALESCE(jsonb_agg(DISTINCT alias), '[]'::jsonb)
                FROM jsonb_array_elements_text(
                    COALESCE(crm_user_identity.openids_json, '[]'::jsonb)
                    || EXCLUDED.openids_json
                ) AS merged(alias)
            ),
            customer_name = COALESCE(
                NULLIF(crm_user_identity.customer_name, ''),
                NULLIF(EXCLUDED.customer_name, ''),
                crm_user_identity.customer_name
            ),
            profile_json = COALESCE(crm_user_identity.profile_json, '{}'::jsonb) || EXCLUDED.profile_json,
            legacy_sources_json = COALESCE(crm_user_identity.legacy_sources_json, '{}'::jsonb)
                || EXCLUDED.legacy_sources_json,
            unionid_resolved_at = COALESCE(crm_user_identity.unionid_resolved_at, NOW()),
            last_seen_at = NOW(),
            updated_at = NOW()
        WHERE crm_user_identity.identity_status = 'active'
        RETURNING unionid
        """,
        (
            canonical_unionid,
            normalized_openid,
            normalized_openid,
            normalized_name,
            audit_payload,
        ),
    ).fetchone()
    if not row:
        _record_conflict(
            conn,
            unionid=canonical_unionid,
            candidate_unionid=openid_unionid,
            openid=normalized_openid,
            reason="canonical_identity_not_active",
            source_route=source_route,
        )
        return {
            "ok": False,
            "projected": False,
            "reason": "canonical_identity_not_active",
            "unionid": "",
        }
    return {"ok": True, "projected": True, "reason": "projected", "unionid": canonical_unionid}


__all__ = ["project_wechat_oauth_identity"]
