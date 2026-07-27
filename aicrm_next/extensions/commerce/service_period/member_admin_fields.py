from __future__ import annotations

from copy import deepcopy
from typing import Any

from aicrm_next.shared.errors import ContractError, NotFoundError

from .domain import entitlement_status, isoformat, remaining_days, text, utcnow


_ADMIN_TEXT_PATHS = {
    "admin_remark": "{admin_remark}",
    "admin_alliance": "{admin_alliance}",
}


def _metadata_path(metadata_key: str) -> str:
    path = _ADMIN_TEXT_PATHS.get(metadata_key)
    if not path:
        raise ContractError("unsupported service period member field")
    return path


class InMemoryMemberAdminFieldsMixin:
    def update_member_remark(self, service_product_id: str, unionid: str, remark: str) -> dict[str, Any]:
        return self._update_member_admin_text(service_product_id, unionid, metadata_key="admin_remark", value=remark)

    def update_member_alliance(self, service_product_id: str, unionid: str, alliance: str) -> dict[str, Any]:
        return self._update_member_admin_text(service_product_id, unionid, metadata_key="admin_alliance", value=alliance)

    def _update_member_admin_text(
        self,
        service_product_id: str,
        unionid: str,
        *,
        metadata_key: str,
        value: str,
    ) -> dict[str, Any]:
        _metadata_path(metadata_key)
        row = self._find_entitlement(text(service_product_id), text(unionid))
        if not row:
            raise NotFoundError("service period member not found")
        metadata = deepcopy(row.get("metadata_json") or {})
        metadata[metadata_key] = text(value)
        row["metadata_json"] = metadata
        row["updated_at"] = utcnow().isoformat()
        return {"ok": True, "member": self._member_payload(row, now=utcnow())}


class PostgresMemberAdminFieldsMixin:
    def update_member_remark(self, service_product_id: str, unionid: str, remark: str) -> dict[str, Any]:
        return self._update_member_admin_text(service_product_id, unionid, metadata_key="admin_remark", value=remark)

    def update_member_alliance(self, service_product_id: str, unionid: str, alliance: str) -> dict[str, Any]:
        return self._update_member_admin_text(service_product_id, unionid, metadata_key="admin_alliance", value=alliance)

    def _update_member_admin_text(
        self,
        service_product_id: str,
        unionid: str,
        *,
        metadata_key: str,
        value: str,
    ) -> dict[str, Any]:
        metadata_path = _metadata_path(metadata_key)
        with self._connect() as conn:
            updated = conn.execute(
                f"""
                UPDATE service_period_entitlements
                SET metadata_json = jsonb_set(COALESCE(metadata_json, '{{}}'::jsonb), '{metadata_path}', to_jsonb(%s::text), true),
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                  AND unionid = %s
                RETURNING id
                """,
                (text(value), text(service_product_id), text(unionid)),
            ).fetchone()
            if not updated:
                raise NotFoundError("service period member not found")
            row = conn.execute(
                """
                SELECT
                    e.*,
                    p.duration_days AS last_order_duration_days,
                    o.amount_total AS last_order_amount,
                    COALESCE(
                        NULLIF(c.remark, ''),
                        NULLIF(wfu.remark, ''),
                        NULLIF(NULLIF(c.customer_name, ''), '问卷提交用户'),
                        NULLIF(NULLIF(c.profile_json->>'name', ''), '问卷提交用户'),
                        NULLIF(wim.name, ''),
                        NULLIF(c.customer_name, ''),
                        NULLIF(e.metadata_json->>'payer_name', ''),
                        NULLIF(o.payer_name_snapshot, '')
                    ) AS display_name,
                    COALESCE(
                        NULLIF(e.external_userid_snapshot, ''),
                        NULLIF(c.primary_external_userid, ''),
                        NULLIF(wim.external_userid, '')
                    ) AS external_userid,
                    COALESCE(NULLIF(c.mobile, ''), NULLIF(c.mobile_normalized, '')) AS mobile,
                    COALESCE(NULLIF(e.metadata_json->>'admin_remark', ''), NULLIF(e.metadata_json->>'remark', '')) AS remark,
                    COALESCE(NULLIF(e.metadata_json->>'admin_alliance', ''), '') AS alliance
                FROM service_period_entitlements e
                JOIN service_period_products p ON p.id = e.service_product_id
                LEFT JOIN wechat_pay_orders o ON o.id = e.last_order_id
                LEFT JOIN crm_user_identity c ON c.unionid = e.unionid
                LEFT JOIN LATERAL (
                    SELECT im.external_userid, im.name
                    FROM wecom_external_contact_identity_map im
                    WHERE im.unionid = e.unionid
                    ORDER BY im.updated_at DESC NULLS LAST, im.id DESC
                    LIMIT 1
                ) wim ON TRUE
                LEFT JOIN LATERAL (
                    SELECT fu.remark
                    FROM wecom_external_contact_follow_users fu
                    WHERE fu.external_userid = COALESCE(NULLIF(e.external_userid_snapshot, ''), NULLIF(c.primary_external_userid, ''), NULLIF(wim.external_userid, ''))
                      AND COALESCE(fu.relation_status, 'active') = 'active'
                    ORDER BY fu.is_primary DESC NULLS LAST, fu.updated_at DESC NULLS LAST, fu.id DESC
                    LIMIT 1
                ) wfu ON TRUE
                WHERE e.tenant_id = 'aicrm'
                  AND e.service_product_id::text = %s
                  AND e.unionid = %s
                LIMIT 1
                """,
                (text(service_product_id), text(unionid)),
            ).fetchone()
            conn.commit()
        if not row:
            raise NotFoundError("service period member not found")
        now = utcnow()
        return {
            "ok": True,
            "member": {
                "unionid": text(row.get("unionid")),
                "display_name": text(row.get("display_name")),
                "external_userid": text(row.get("external_userid")),
                "mobile": text(row.get("mobile")),
                "status": entitlement_status(row.get("end_at"), row.get("status"), now=now),
                "remaining_days": remaining_days(row.get("end_at"), now=now),
                "end_at": isoformat(row.get("end_at")),
                "last_order_amount": int(row.get("last_order_amount") or 0),
                "last_order_duration_days": int(row.get("last_order_duration_days") or 0),
                "renewal_count": max(0, int(row.get("renewal_count") or 0)),
                "remark": text(row.get("remark")),
                "alliance": text(row.get("alliance")),
            },
        }
