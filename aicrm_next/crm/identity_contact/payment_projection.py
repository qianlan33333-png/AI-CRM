from __future__ import annotations

import json
from typing import Any

from .dto import ResolvePersonIdentityRequest
from .resolver import resolve_identity_with_dbapi, resolved_unionid


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _order_identity(order: dict[str, Any]) -> dict[str, Any]:
    metadata = _json_object(order.get("metadata_json"))
    for key in ("payer_identity", "buyer_identity"):
        identity = metadata.get(key)
        if isinstance(identity, dict):
            return identity
    return {}


def project_payment_order_mobile(
    conn: Any,
    order: dict[str, Any],
    *,
    source_route: str,
) -> dict[str, Any]:
    """Persist a paid order mobile into the canonical unionid identity row.

    The operation is idempotent and creates the canonical row when channel
    identity synchronization arrives after payment. Existing different mobile
    aliases are never overwritten.
    """

    identity = _order_identity(order)
    return _project_order_identity_mobile(
        conn,
        unionid=_text(order.get("unionid") or identity.get("unionid")),
        mobile=_text(identity.get("mobile")),
        external_userid=_text(identity.get("external_userid")),
        openid=_text(identity.get("openid")),
        owner_userid=_text(identity.get("owner_userid")),
        customer_name=_text(order.get("payer_name_snapshot") or identity.get("payer_name")),
        mobile_source="wechat_pay_order",
        audit_key="wechat_pay_mobile_projection",
        reference_key="out_trade_no",
        reference_value=_text(order.get("out_trade_no")),
        source_route=source_route,
    )


def project_wechat_shop_order_mobile(
    conn: Any,
    order: dict[str, Any],
    *,
    source_route: str,
) -> dict[str, Any]:
    """Persist a paid WeChat Shop buyer mobile into canonical identity."""

    if not order.get("paid_at"):
        return {"ok": True, "projected": False, "reason": "order_not_paid"}
    return _project_order_identity_mobile(
        conn,
        unionid=_text(order.get("unionid")),
        mobile=_text(order.get("buyer_mobile")),
        openid=_text(order.get("openid")),
        mobile_source="wechat_shop_order",
        audit_key="wechat_shop_mobile_projection",
        reference_key="order_id",
        reference_value=_text(order.get("order_id")),
        source_route=source_route,
    )


def _project_order_identity_mobile(
    conn: Any,
    *,
    unionid: str,
    mobile: str,
    mobile_source: str,
    audit_key: str,
    reference_key: str,
    reference_value: str,
    source_route: str,
    external_userid: str = "",
    openid: str = "",
    owner_userid: str = "",
    customer_name: str = "",
) -> dict[str, Any]:
    mobile = "".join(char for char in _text(mobile) if char.isdigit())
    if not mobile:
        return {"ok": True, "projected": False, "reason": "missing_mobile"}
    if not (len(mobile) == 11 and mobile.startswith("1")):
        return {"ok": True, "projected": False, "reason": "invalid_mobile"}

    explicit_unionid = _text(unionid)
    external_userid = _text(external_userid)
    openid = _text(openid)
    base_resolution = resolve_identity_with_dbapi(
        conn,
        ResolvePersonIdentityRequest(
            unionid=explicit_unionid or None,
            external_userid=external_userid or None,
            openid=openid or None,
        ),
    )
    if base_resolution.status in {"pending", "conflict"}:
        return {"ok": False, "projected": False, "reason": "identity_conflict"}
    resolved_base_unionid = resolved_unionid(base_resolution)
    if explicit_unionid and resolved_base_unionid and explicit_unionid != resolved_base_unionid:
        return {"ok": False, "projected": False, "reason": "identity_conflict"}
    unionid = resolved_base_unionid or explicit_unionid
    if not unionid:
        return {"ok": True, "projected": False, "reason": "missing_unionid"}

    mobile_resolution = resolve_identity_with_dbapi(
        conn,
        ResolvePersonIdentityRequest(mobile=mobile),
    )
    mobile_unionid = resolved_unionid(mobile_resolution)
    if mobile_resolution.status in {"pending", "conflict"} or (mobile_unionid and mobile_unionid != unionid):
        return {"ok": False, "projected": False, "reason": "mobile_alias_conflict"}

    owner_userid = _text(owner_userid)
    customer_name = _text(customer_name)
    audit_payload = json.dumps(
        {
            audit_key: {
                reference_key: _text(reference_value),
                "source_route": _text(source_route),
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    row = conn.execute(
        """
        INSERT INTO crm_user_identity (
            unionid,
            primary_external_userid,
            external_userids_json,
            primary_openid,
            openids_json,
            mobile,
            mobile_normalized,
            mobile_verified,
            mobile_source,
            customer_name,
            profile_json,
            primary_owner_userid,
            identity_status,
            unionid_resolved_at,
            first_seen_at,
            last_seen_at,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            CASE WHEN %s = '' THEN '[]'::jsonb ELSE jsonb_build_array(CAST(%s AS text)) END,
            %s,
            CASE WHEN %s = '' THEN '[]'::jsonb ELSE jsonb_build_array(CAST(%s AS text)) END,
            %s,
            %s,
            TRUE,
            %s,
            %s,
            %s::jsonb,
            %s,
            'active',
            NOW(),
            NOW(),
            NOW(),
            NOW(),
            NOW()
        )
        ON CONFLICT (unionid) DO UPDATE SET
            mobile = EXCLUDED.mobile,
            mobile_normalized = EXCLUDED.mobile_normalized,
            mobile_verified = crm_user_identity.mobile_verified OR EXCLUDED.mobile_verified,
            mobile_source = CASE
                WHEN COALESCE(NULLIF(crm_user_identity.mobile_source, ''), '') = '' THEN EXCLUDED.mobile_source
                ELSE crm_user_identity.mobile_source
            END,
            primary_external_userid = COALESCE(
                NULLIF(crm_user_identity.primary_external_userid, ''),
                NULLIF(EXCLUDED.primary_external_userid, ''),
                crm_user_identity.primary_external_userid
            ),
            primary_openid = COALESCE(
                NULLIF(crm_user_identity.primary_openid, ''),
                NULLIF(EXCLUDED.primary_openid, ''),
                crm_user_identity.primary_openid
            ),
            primary_owner_userid = COALESCE(
                NULLIF(crm_user_identity.primary_owner_userid, ''),
                NULLIF(EXCLUDED.primary_owner_userid, ''),
                crm_user_identity.primary_owner_userid
            ),
            customer_name = COALESCE(
                NULLIF(crm_user_identity.customer_name, ''),
                NULLIF(EXCLUDED.customer_name, ''),
                crm_user_identity.customer_name
            ),
            profile_json = COALESCE(crm_user_identity.profile_json, '{}'::jsonb) || EXCLUDED.profile_json,
            last_seen_at = NOW(),
            updated_at = NOW()
        WHERE COALESCE(crm_user_identity.mobile, '') = ''
           OR crm_user_identity.mobile = EXCLUDED.mobile
           OR crm_user_identity.mobile_normalized = EXCLUDED.mobile_normalized
        RETURNING unionid, mobile, primary_external_userid, primary_owner_userid
        """,
        (
            unionid,
            external_userid,
            external_userid,
            external_userid,
            openid,
            openid,
            openid,
            mobile,
            mobile,
            _text(mobile_source),
            customer_name,
            audit_payload,
            owner_userid,
        ),
    ).fetchone()
    if not row:
        return {"ok": False, "projected": False, "reason": "mobile_alias_conflict", "unionid": unionid}
    return {"ok": True, "projected": True, "unionid": unionid, "mobile": mobile}


__all__ = ["project_payment_order_mobile", "project_wechat_shop_order_mobile"]
