from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets
from threading import RLock
from typing import Any, Mapping, Protocol

from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository, connect_commerce_db
from aicrm_next.crm.identity_contact.dto import IdentityResolveResult, ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.resolver import resolve_identity_with_dbapi, resolved_unionid
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .domain import (
    CouponClaimStatus,
    CouponRedemptionStatus,
    CouponLifecycleStatus,
    calculate_claim_validity,
    derive_coupon_state,
    validate_coupon_transition,
    validate_coupon_update,
    validate_discount_against_product_amounts,
    validate_payable_amount,
)
from .product_options import product_option
from .target_refs import product_id_from_target_ref, request_key_hash, target_ref_for_product_id
from ..wechat_pay_order_write_port import build_wechat_pay_order_write_port


TENANT_ID = "aicrm"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime | None) -> datetime:
    source = value or _utcnow()
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(timezone.utc)


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return value
    return Jsonb(value)


def _json_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return _aware_utc(value).isoformat()
    return _text(value)


class DbApiCouponOrderRepository:
    """Same-connection persistence boundary for coupon order state changes.

    The caller owns the transaction.  This repository intentionally never
    commits so the payment order, coupon reservation/consumption, and existing
    commerce event writes can stay atomic.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def reserve_coupon_for_order(
        self,
        *,
        order: Mapping[str, Any],
        choice_mode: str,
        claim_no: str,
        unionid: str,
        trade_product_id: int,
        now: datetime,
    ) -> dict[str, Any]:
        order_payload = dict(order)
        order_id = int(order_payload.get("id") or 0)
        out_trade_no = _text(order_payload.get("out_trade_no"))
        subtotal = int(
            order_payload.get("subtotal_amount_total")
            or order_payload.get("amount_total")
            or 0
        )
        currency = _text(order_payload.get("currency")) or "CNY"

        locked_order = self._conn.execute(
            "SELECT * FROM wechat_pay_orders WHERE id = %s AND out_trade_no = %s FOR UPDATE",
            (order_id, out_trade_no),
        ).fetchone()
        if not locked_order:
            raise ContractError("order disappeared before coupon reservation")
        locked_payload = dict(locked_order)
        order_unionid = _text(locked_payload.get("unionid"))
        if order_unionid and order_unionid != unionid:
            raise ContractError("coupon identity does not match the order")
        if locked_payload.get("coupon_claim_id"):
            existing = self._conn.execute(
                "SELECT * FROM commerce_coupon_redemptions WHERE order_id = %s FOR UPDATE",
                (order_id,),
            ).fetchone()
            if existing and _text(existing.get("status")) in {
                CouponRedemptionStatus.RESERVED.value,
                CouponRedemptionStatus.CONSUMED.value,
            }:
                return locked_payload
            raise ContractError("order coupon reservation is inconsistent")

        current_product = self._conn.execute(
            """
            SELECT amount_total, currency, status, enabled
            FROM wechat_pay_products
            WHERE id = %s
            FOR SHARE
            """,
            (trade_product_id,),
        ).fetchone()
        if not current_product:
            raise ContractError("coupon product is no longer available")
        if (
            int(current_product.get("amount_total") or 0) != subtotal
            or (_text(current_product.get("currency")) or "CNY") != currency
        ):
            raise ContractError("coupon product price changed before checkout")
        if _text(current_product.get("status")) != "active" or not bool(
            current_product.get("enabled")
        ):
            raise ContractError("coupon product is no longer available")

        claim_filter = ""
        params: list[Any] = [
            TENANT_ID,
            unionid,
            now,
            now,
            trade_product_id,
            subtotal,
            currency,
        ]
        if choice_mode == "claim":
            claim_filter = "AND claim.claim_no = %s"
            params.append(claim_no)
        claim_row = self._conn.execute(
            f"""
            SELECT claim.*, coupon.name AS coupon_name, coupon.public_slug
            FROM commerce_coupon_claims claim
            JOIN commerce_coupons coupon
              ON coupon.id = claim.coupon_id AND coupon.tenant_id = claim.tenant_id
            JOIN commerce_coupon_product_bindings binding
              ON binding.coupon_id = coupon.id AND binding.tenant_id = coupon.tenant_id
            WHERE claim.tenant_id = %s
              AND claim.unionid = %s
              AND claim.status = 'available'
              AND claim.valid_from <= %s
              AND claim.valid_until > %s
              AND binding.trade_product_id = %s
              AND claim.discount_amount_total < %s
              AND claim.currency = %s
              {claim_filter}
            ORDER BY claim.discount_amount_total DESC,
                     claim.valid_until ASC,
                     claim.claimed_at ASC,
                     claim.id ASC
            FOR UPDATE OF claim SKIP LOCKED
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if not claim_row:
            raise ContractError("coupon_unavailable")

        claim = dict(claim_row)
        discount = int(claim.get("discount_amount_total") or 0)
        payable = validate_payable_amount(
            subtotal_amount_total=subtotal,
            discount_amount_total=discount,
        )
        reserved_until = locked_payload.get("expires_at") or now + timedelta(minutes=15)
        snapshot = {
            "claim_no": _text(claim.get("claim_no")),
            "coupon_name": _text(claim.get("coupon_name")),
            "discount_amount_total": discount,
            "currency": currency,
            "valid_from": _json_timestamp(claim.get("valid_from")),
            "valid_until": _json_timestamp(claim.get("valid_until")),
        }

        updated_claim = self._conn.execute(
            """
            UPDATE commerce_coupon_claims
            SET status = 'reserved', reserved_at = %s, updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = %s AND id = %s AND status = 'available'
            RETURNING id
            """,
            (now, TENANT_ID, int(claim["id"])),
        ).fetchone()
        if not updated_claim:
            raise ContractError("coupon_unavailable")
        self._conn.execute(
            """
            INSERT INTO commerce_coupon_redemptions (
                tenant_id, claim_id, order_id, out_trade_no, status,
                original_amount_total, discount_amount_total, payable_amount_total,
                currency, reserved_until, idempotency_key_hash, reserved_at,
                created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, 'reserved', %s, %s, %s,
                %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (
                TENANT_ID,
                int(claim["id"]),
                order_id,
                out_trade_no,
                subtotal,
                discount,
                payable,
                currency,
                reserved_until,
                request_key_hash(out_trade_no),
                now,
            ),
        )
        updated_order = build_wechat_pay_order_write_port().apply_coupon_pricing_dbapi(
            self._conn,
            order_id=order_id,
            out_trade_no=out_trade_no,
            subtotal=subtotal,
            discount=discount,
            payable=payable,
            coupon_claim_id=int(claim["id"]),
            coupon_snapshot_json=_jsonb(snapshot),
        )
        if not updated_order:
            raise ContractError("order disappeared during coupon reservation")
        return dict(updated_order)

    def consume_coupon_for_paid_order(
        self,
        *,
        out_trade_no: str,
        provider_total: int,
        provider_currency: str,
    ) -> dict[str, Any]:
        order_row = self._conn.execute(
            "SELECT * FROM wechat_pay_orders WHERE out_trade_no = %s FOR UPDATE",
            (out_trade_no,),
        ).fetchone()
        if not order_row:
            raise NotFoundError("payment order not found")
        order = dict(order_row)
        expected_total = int(order.get("amount_total") or 0)
        expected_currency = _text(order.get("currency")) or "CNY"
        if int(provider_total) != expected_total:
            raise ContractError("wechat payment amount does not match the coupon order")
        if provider_currency != expected_currency:
            raise ContractError("wechat payment currency does not match the coupon order")
        if not order.get("coupon_claim_id"):
            return order

        redemption_row = self._conn.execute(
            "SELECT * FROM commerce_coupon_redemptions WHERE order_id = %s FOR UPDATE",
            (int(order["id"]),),
        ).fetchone()
        if not redemption_row:
            raise ContractError("coupon redemption is missing for the paid order")
        redemption = dict(redemption_row)
        claim_row = self._conn.execute(
            "SELECT * FROM commerce_coupon_claims WHERE id = %s FOR UPDATE",
            (int(redemption["claim_id"]),),
        ).fetchone()
        if not claim_row:
            raise ContractError("coupon claim is missing for the paid order")
        claim = dict(claim_row)

        if _text(redemption.get("status")) == CouponRedemptionStatus.CONSUMED.value:
            if _text(claim.get("status")) != CouponClaimStatus.CONSUMED.value:
                raise ContractError("consumed coupon redemption has an inconsistent claim")
            return order
        if _text(redemption.get("status")) != CouponRedemptionStatus.RESERVED.value:
            raise ContractError("paid order coupon is not reserved")
        if _text(claim.get("status")) != CouponClaimStatus.RESERVED.value:
            raise ContractError("paid order coupon claim is not reserved")

        current = _utcnow()
        self._conn.execute(
            """
            UPDATE commerce_coupon_redemptions
            SET status = 'consumed', consumed_at = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'reserved'
            """,
            (current, int(redemption["id"])),
        )
        self._conn.execute(
            """
            UPDATE commerce_coupon_claims
            SET status = 'consumed', consumed_at = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'reserved'
            """,
            (current, int(claim["id"])),
        )
        return order

    def release_coupon_for_order(
        self,
        *,
        out_trade_no: str,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        order_row = self._conn.execute(
            "SELECT * FROM wechat_pay_orders WHERE out_trade_no = %s FOR UPDATE",
            (out_trade_no,),
        ).fetchone()
        if not order_row:
            raise NotFoundError("payment order not found")
        order = dict(order_row)
        if not order.get("coupon_claim_id"):
            return order
        redemption_row = self._conn.execute(
            "SELECT * FROM commerce_coupon_redemptions WHERE order_id = %s FOR UPDATE",
            (int(order["id"]),),
        ).fetchone()
        if not redemption_row:
            raise ContractError("coupon redemption is missing for the order")
        redemption = dict(redemption_row)
        redemption_status = _text(redemption.get("status"))
        if redemption_status in {
            CouponRedemptionStatus.CONSUMED.value,
            CouponRedemptionStatus.RELEASED.value,
        }:
            return order
        if redemption_status != CouponRedemptionStatus.RESERVED.value:
            raise ContractError("unsupported coupon redemption state")

        claim_row = self._conn.execute(
            "SELECT * FROM commerce_coupon_claims WHERE id = %s FOR UPDATE",
            (int(redemption["claim_id"]),),
        ).fetchone()
        if not claim_row:
            raise ContractError("coupon claim is missing for the order")
        claim = dict(claim_row)
        if _text(claim.get("status")) != CouponClaimStatus.RESERVED.value:
            raise ContractError("coupon claim is not reserved")
        expired = _aware_utc(claim.get("valid_until")) <= now
        next_status = (
            CouponClaimStatus.EXPIRED.value
            if expired
            else CouponClaimStatus.AVAILABLE.value
        )
        self._conn.execute(
            """
            UPDATE commerce_coupon_redemptions
            SET status = 'released', release_reason = %s, released_at = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'reserved'
            """,
            (reason[:200], now, int(redemption["id"])),
        )
        self._conn.execute(
            """
            UPDATE commerce_coupon_claims
            SET status = %s,
                expired_at = CASE WHEN %s = 'expired' THEN %s ELSE NULL END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'reserved'
            """,
            (next_status, next_status, now, int(claim["id"])),
        )
        return order

    def consistency_counts(self) -> dict[str, Any]:
        counts = {
            "paid_order_without_consumed_coupon": self._count(
                """
                SELECT count(*) AS total
                FROM wechat_pay_orders orders
                WHERE orders.coupon_claim_id IS NOT NULL
                  AND (orders.status = 'paid' OR orders.trade_state = 'SUCCESS')
                  AND NOT EXISTS (
                      SELECT 1 FROM commerce_coupon_redemptions redemption
                      WHERE redemption.order_id = orders.id AND redemption.status = 'consumed'
                  )
                """
            ),
            "consumed_coupon_without_paid_order": self._count(
                """
                SELECT count(*) AS total
                FROM commerce_coupon_redemptions redemption
                JOIN wechat_pay_orders orders ON orders.id = redemption.order_id
                WHERE redemption.status = 'consumed'
                  AND NOT (orders.status = 'paid' OR orders.trade_state = 'SUCCESS')
                """
            ),
            "closed_order_with_reserved_coupon": self._count(
                """
                SELECT count(*) AS total
                FROM commerce_coupon_redemptions redemption
                JOIN wechat_pay_orders orders ON orders.id = redemption.order_id
                WHERE redemption.status = 'reserved'
                  AND (orders.status IN ('closed', 'failed') OR orders.trade_state IN ('CLOSED', 'REVOKED'))
                """
            ),
            "order_amount_equation_mismatch": self._count(
                """
                SELECT count(*) AS total
                FROM wechat_pay_orders
                WHERE subtotal_amount_total - discount_amount_total <> amount_total
                   OR subtotal_amount_total < 0
                   OR discount_amount_total < 0
                """
            ),
            "duplicate_active_coupon_redemption": self._count(
                """
                SELECT count(*) AS total
                FROM (
                    SELECT claim_id
                    FROM commerce_coupon_redemptions
                    WHERE status IN ('reserved', 'consumed')
                    GROUP BY claim_id
                    HAVING count(*) > 1
                ) duplicates
                """
            ),
        }
        return {"ok": True, "counts": counts, "total": sum(counts.values())}

    def _count(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        row = self._conn.execute(sql, params).fetchone() or {}
        return int(row.get("total") or 0)


def build_coupon_order_repository(conn: Any) -> DbApiCouponOrderRepository:
    return DbApiCouponOrderRepository(conn)


def _public_slug() -> str:
    return "cpn_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")


def _claim_no() -> str:
    return "CC" + secrets.token_hex(12).upper()


def _mask_unionid(value: Any) -> str:
    normalized = _text(value)
    if len(normalized) <= 8:
        return "****" if normalized else ""
    return f"{normalized[:4]}****{normalized[-4:]}"


def _coupon_display_payload(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    payload = dict(row)
    current = _aware_utc(now)
    payload["id"] = int(payload.get("id") or 0)
    payload["issued_count"] = int(payload.get("issued_count") or 0)
    payload["used_count"] = int(payload.get("used_count") or 0)
    payload["reserved_count"] = int(payload.get("reserved_count") or 0)
    payload["product_count"] = int(payload.get("product_count") or len(payload.get("products") or []))
    payload["target_refs"] = list(payload.get("target_refs") or [])
    payload["display_state"] = derive_coupon_state(
        status=_text(payload.get("status")),
        claim_starts_at=payload["claim_starts_at"],
        claim_ends_at=payload["claim_ends_at"],
        issued_count=payload["issued_count"],
        total_issue_limit=int(payload.get("total_issue_limit") or 0),
        now=current,
    ).value
    return payload


class CouponRepository(Protocol):
    def list_coupons(self, *, limit: int, offset: int, q: str, status: str) -> dict[str, Any]: ...
    def get_coupon(self, coupon_id: int) -> dict[str, Any] | None: ...
    def get_coupon_by_slug(self, public_slug: str) -> dict[str, Any] | None: ...
    def create_coupon(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]: ...
    def update_coupon(self, coupon_id: int, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]: ...
    def delete_coupon(self, coupon_id: int) -> dict[str, Any]: ...
    def transition_coupon(self, coupon_id: int, target_status: str, *, actor_id: str) -> dict[str, Any]: ...
    def copy_coupon(self, coupon_id: int, *, actor_id: str) -> dict[str, Any]: ...
    def list_product_options(self, *, q: str, product_type: str, limit: int, offset: int) -> dict[str, Any]: ...
    def product_options_for_target_refs(self, target_refs: list[str]) -> list[dict[str, Any]]: ...
    def list_claims(self, coupon_id: int, *, limit: int, offset: int) -> dict[str, Any]: ...
    def resolve_canonical_unionid(self, identity: dict[str, Any]) -> str: ...
    def count_user_claims(self, coupon_id: int, *, unionid: str) -> int: ...
    def claim_coupon(self, public_slug: str, *, unionid: str, idempotency_hash: str, now: datetime) -> dict[str, Any]: ...
    def list_available_claims(self, target_ref: str, *, unionid: str, now: datetime) -> dict[str, Any]: ...
    def assert_product_price(self, product_id: str, new_price: int) -> None: ...


class InMemoryCouponRepository:
    def __init__(self, *, product_options: list[dict[str, Any]] | None = None) -> None:
        self._lock = RLock()
        self._coupons: list[dict[str, Any]] = []
        self._claims: list[dict[str, Any]] = []
        self._next_coupon_id = 1
        self._next_claim_id = 1
        self._product_options = [
            product_option(item)
            for item in (product_options if product_options is not None else self._default_product_options())
        ]

    @staticmethod
    def _default_product_options() -> list[dict[str, Any]]:
        try:
            payload = build_commerce_repository().list_products(limit=200, offset=0)
            return list(payload.get("items") or [])
        except Exception:
            return []

    def list_coupons(self, *, limit: int, offset: int, q: str, status: str) -> dict[str, Any]:
        with self._lock:
            rows = [self._coupon_payload(row) for row in self._coupons]
        needle = _text(q).lower()
        normalized_status = _text(status)
        if needle:
            rows = [row for row in rows if needle in _text(row.get("name")).lower()]
        if normalized_status:
            rows = [
                row
                for row in rows
                if _text(row.get("status")) == normalized_status
                or _text(row.get("display_state")) == normalized_status
            ]
        rows.sort(key=lambda row: (row.get("updated_at") or row.get("created_at"), row.get("id")), reverse=True)
        return {"ok": True, "items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def get_coupon(self, coupon_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = next((item for item in self._coupons if int(item["id"]) == int(coupon_id)), None)
            return self._coupon_payload(row) if row else None

    def get_coupon_by_slug(self, public_slug: str) -> dict[str, Any] | None:
        with self._lock:
            row = next((item for item in self._coupons if _text(item.get("public_slug")) == _text(public_slug)), None)
            return self._coupon_payload(row) if row else None

    def create_coupon(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        now = _utcnow()
        options = self.product_options_for_target_refs(list(payload.get("target_refs") or []))
        with self._lock:
            row = {
                **deepcopy(payload),
                "id": self._next_coupon_id,
                "tenant_id": TENANT_ID,
                "public_slug": _public_slug(),
                "currency": "CNY",
                "status": CouponLifecycleStatus.DRAFT.value,
                "issued_count": 0,
                "first_claim_at": None,
                "created_by": _text(actor_id),
                "updated_by": _text(actor_id),
                "created_at": now,
                "updated_at": now,
                "product_ids": [_text(option["trade_product_id"]) for option in options],
            }
            self._next_coupon_id += 1
            self._coupons.append(row)
            return self._coupon_payload(row)

    def update_coupon(self, coupon_id: int, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        options = self.product_options_for_target_refs(list(payload.get("target_refs") or []))
        mutable_payload = deepcopy(payload)
        mutable_payload.pop("created_by", None)
        mutable_payload.pop("updated_by", None)
        with self._lock:
            row = self._coupon_row(coupon_id)
            existing_payload = self._coupon_payload(row)
            validate_coupon_update(
                existing_payload,
                mutable_payload,
                existing_target_refs=existing_payload.get("target_refs") or [],
                proposed_target_refs=mutable_payload.get("target_refs") or [],
            )
            row.update(mutable_payload)
            row["product_ids"] = [_text(option["trade_product_id"]) for option in options]
            row["updated_by"] = _text(actor_id)
            row["updated_at"] = _utcnow()
            return self._coupon_payload(row)

    def delete_coupon(self, coupon_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._coupon_row(coupon_id)
            if _text(row.get("status")) != CouponLifecycleStatus.DRAFT.value or int(row.get("issued_count") or 0):
                raise ContractError("only an unclaimed draft coupon can be deleted")
            self._coupons.remove(row)
            return {"ok": True, "deleted": True, "coupon_id": int(coupon_id)}

    def transition_coupon(self, coupon_id: int, target_status: str, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._coupon_row(coupon_id)
            ended = _aware_utc(row.get("claim_ends_at")) <= _utcnow()
            target = validate_coupon_transition(row["status"], target_status, claim_window_ended=ended)
            if target is CouponLifecycleStatus.PUBLISHED:
                options = self._options_for_product_ids(row.get("product_ids") or [])
                validate_discount_against_product_amounts(
                    int(row["discount_amount_total"]),
                    [int(option["amount_total"]) for option in options],
                )
            row["status"] = target.value
            row["updated_by"] = _text(actor_id)
            row["updated_at"] = _utcnow()
            return self._coupon_payload(row)

    def copy_coupon(self, coupon_id: int, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            source = self._coupon_row(coupon_id)
            target_refs = [target_ref_for_product_id(value) for value in source.get("product_ids") or []]
            payload = {
                key: deepcopy(source.get(key))
                for key in (
                    "discount_amount_total",
                    "total_issue_limit",
                    "per_user_issue_limit",
                    "claim_starts_at",
                    "claim_ends_at",
                    "validity_mode",
                    "use_starts_at",
                    "use_ends_at",
                    "relative_validity_days",
                    "instructions",
                )
            }
            payload.update({"name": f"{_text(source.get('name'))} 副本"[:45], "target_refs": target_refs})
        return self.create_coupon(payload, actor_id=actor_id)

    def list_product_options(self, *, q: str, product_type: str, limit: int, offset: int) -> dict[str, Any]:
        rows = deepcopy(self._product_options)
        needle = _text(q).lower()
        if needle:
            rows = [row for row in rows if needle in _text(row.get("title")).lower()]
        normalized_type = _text(product_type)
        if normalized_type and normalized_type != "all":
            rows = [row for row in rows if row.get("product_type") == normalized_type]
        return {"ok": True, "items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def product_options_for_target_refs(self, target_refs: list[str]) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(_text(value) for value in target_refs if _text(value)))
        by_ref = {option["target_ref"]: option for option in self._product_options}
        if any(value not in by_ref for value in normalized):
            raise ContractError("one or more selected products are invalid")
        return [deepcopy(by_ref[value]) for value in normalized]

    def list_claims(self, coupon_id: int, *, limit: int, offset: int) -> dict[str, Any]:
        with self._lock:
            self._coupon_row(coupon_id)
            rows = [self._claim_payload(row, masked=True) for row in self._claims if int(row["coupon_id"]) == int(coupon_id)]
        rows.sort(key=lambda row: (row.get("claimed_at"), row.get("id")), reverse=True)
        counts = {status.value: 0 for status in CouponClaimStatus}
        for row in rows:
            counts[_text(row.get("status"))] = counts.get(_text(row.get("status")), 0) + 1
        return {
            "ok": True,
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "stats": {"issued": len(rows), **counts},
        }

    def resolve_canonical_unionid(self, identity: dict[str, Any]) -> str:
        return _text(identity.get("unionid"))

    def count_user_claims(self, coupon_id: int, *, unionid: str) -> int:
        with self._lock:
            return sum(
                1
                for claim in self._claims
                if int(claim["coupon_id"]) == int(coupon_id) and claim["unionid"] == unionid
            )

    def claim_coupon(self, public_slug: str, *, unionid: str, idempotency_hash: str, now: datetime) -> dict[str, Any]:
        current = _aware_utc(now)
        with self._lock:
            coupon = next((item for item in self._coupons if _text(item.get("public_slug")) == _text(public_slug)), None)
            if not coupon:
                raise NotFoundError("coupon not found")
            existing = next(
                (
                    claim
                    for claim in self._claims
                    if int(claim["coupon_id"]) == int(coupon["id"])
                    and claim["unionid"] == unionid
                    and claim["idempotency_key_hash"] == idempotency_hash
                ),
                None,
            )
            if existing:
                return {"ok": True, "idempotent": True, "coupon": self._coupon_payload(coupon), "claim": self._claim_payload(existing)}
            if _text(coupon.get("status")) != CouponLifecycleStatus.PUBLISHED.value:
                raise ContractError("coupon is not claimable")
            if current < _aware_utc(coupon.get("claim_starts_at")):
                raise ContractError("coupon claim has not started")
            if current >= _aware_utc(coupon.get("claim_ends_at")):
                raise ContractError("coupon claim has ended")
            if int(coupon.get("issued_count") or 0) >= int(coupon.get("total_issue_limit") or 0):
                raise ContractError("coupon is sold out")
            user_claims = [
                claim
                for claim in self._claims
                if int(claim["coupon_id"]) == int(coupon["id"]) and claim["unionid"] == unionid
            ]
            if len(user_claims) >= int(coupon.get("per_user_issue_limit") or 0):
                raise ContractError("per-user coupon claim limit reached")
            options = self._options_for_product_ids(coupon.get("product_ids") or [])
            validate_discount_against_product_amounts(
                int(coupon["discount_amount_total"]),
                [int(option["amount_total"]) for option in options],
            )
            valid_from, valid_until = calculate_claim_validity(
                claimed_at=current,
                validity_mode=coupon["validity_mode"],
                use_starts_at=coupon.get("use_starts_at"),
                use_ends_at=coupon.get("use_ends_at"),
                relative_validity_days=coupon.get("relative_validity_days"),
            )
            claim = {
                "id": self._next_claim_id,
                "tenant_id": TENANT_ID,
                "coupon_id": int(coupon["id"]),
                "claim_no": _claim_no(),
                "unionid": unionid,
                "discount_amount_total": int(coupon["discount_amount_total"]),
                "currency": "CNY",
                "valid_from": valid_from,
                "valid_until": valid_until,
                "status": CouponClaimStatus.AVAILABLE.value,
                "idempotency_key_hash": idempotency_hash,
                "claimed_at": current,
                "created_at": current,
                "updated_at": current,
            }
            self._next_claim_id += 1
            self._claims.append(claim)
            coupon["issued_count"] = int(coupon.get("issued_count") or 0) + 1
            coupon["first_claim_at"] = coupon.get("first_claim_at") or current
            coupon["updated_at"] = current
            return {"ok": True, "idempotent": False, "coupon": self._coupon_payload(coupon), "claim": self._claim_payload(claim)}

    def list_available_claims(self, target_ref: str, *, unionid: str, now: datetime) -> dict[str, Any]:
        option = self.product_options_for_target_refs([target_ref])[0]
        current = _aware_utc(now)
        product_id = _text(option["trade_product_id"])
        with self._lock:
            items: list[dict[str, Any]] = []
            for claim in self._claims:
                if claim["unionid"] != unionid or claim["status"] != CouponClaimStatus.AVAILABLE.value:
                    continue
                if not (_aware_utc(claim["valid_from"]) <= current < _aware_utc(claim["valid_until"])):
                    continue
                coupon = self._coupon_row(int(claim["coupon_id"]))
                if product_id not in coupon.get("product_ids", []):
                    continue
                items.append({**self._claim_payload(claim), "coupon_name": coupon["name"]})
        items.sort(key=lambda item: (-int(item["discount_amount_total"]), item["valid_until"], item["claimed_at"], item["id"]))
        return {"ok": True, "items": items, "total": len(items), "target_ref": target_ref}

    def assert_product_price(self, product_id: str, new_price: int) -> None:
        with self._lock:
            now = _utcnow()
            for claim in self._claims:
                if claim["status"] not in {CouponClaimStatus.AVAILABLE.value, CouponClaimStatus.RESERVED.value}:
                    continue
                if _aware_utc(claim["valid_until"]) <= now:
                    continue
                coupon = self._coupon_row(int(claim["coupon_id"]))
                if _text(product_id) in coupon.get("product_ids", []) and int(claim["discount_amount_total"]) >= int(new_price):
                    raise ContractError("product price cannot be lower than or equal to an active coupon amount")

    def _coupon_row(self, coupon_id: int) -> dict[str, Any]:
        row = next((item for item in self._coupons if int(item["id"]) == int(coupon_id)), None)
        if not row:
            raise NotFoundError("coupon not found")
        return row

    def _options_for_product_ids(self, product_ids: list[str]) -> list[dict[str, Any]]:
        wanted = {_text(value) for value in product_ids}
        return [deepcopy(option) for option in self._product_options if _text(option["trade_product_id"]) in wanted]

    def _coupon_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        claims = [claim for claim in self._claims if int(claim["coupon_id"]) == int(row["id"])]
        options = self._options_for_product_ids(row.get("product_ids") or [])
        payload = {
            **deepcopy(row),
            "products": options,
            "target_refs": [option["target_ref"] for option in options],
            "product_count": len(options),
            "used_count": sum(1 for claim in claims if claim["status"] == CouponClaimStatus.CONSUMED.value),
            "reserved_count": sum(1 for claim in claims if claim["status"] == CouponClaimStatus.RESERVED.value),
        }
        return _coupon_display_payload(payload)

    @staticmethod
    def _claim_payload(row: dict[str, Any], *, masked: bool = False) -> dict[str, Any]:
        payload = deepcopy(row)
        payload["id"] = int(payload.get("id") or 0)
        if (
            _text(payload.get("status")) == CouponClaimStatus.AVAILABLE.value
            and payload.get("valid_until")
            and _aware_utc(payload.get("valid_until")) <= _utcnow()
        ):
            payload["status"] = CouponClaimStatus.EXPIRED.value
        if masked:
            payload["unionid"] = _mask_unionid(payload.get("unionid"))
        return payload


class PostgresCouponRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return connect_commerce_db(self._database_url)

    def list_coupons(self, *, limit: int, offset: int, q: str, status: str) -> dict[str, Any]:
        where = ["c.tenant_id = %s"]
        params: list[Any] = [TENANT_ID]
        if _text(q):
            where.append("c.name ILIKE %s")
            params.append(f"%{_text(q)}%")
        normalized_status = _text(status)
        if normalized_status in {item.value for item in CouponLifecycleStatus}:
            where.append("c.status = %s")
            params.append(normalized_status)
        elif normalized_status == "scheduled":
            where.append("c.status = 'published' AND CURRENT_TIMESTAMP < c.claim_starts_at")
        elif normalized_status == "active":
            where.append(
                "c.status = 'published' "
                "AND CURRENT_TIMESTAMP >= c.claim_starts_at "
                "AND CURRENT_TIMESTAMP < c.claim_ends_at "
                "AND c.issued_count < c.total_issue_limit"
            )
        elif normalized_status == "sold_out":
            where.append(
                "c.status = 'published' "
                "AND CURRENT_TIMESTAMP >= c.claim_starts_at "
                "AND CURRENT_TIMESTAMP < c.claim_ends_at "
                "AND c.issued_count >= c.total_issue_limit"
            )
        elif normalized_status == "ended":
            where.append("c.status = 'published' AND CURRENT_TIMESTAMP >= c.claim_ends_at")
        elif normalized_status:
            where.append("FALSE")
        clause = " AND ".join(where)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT c.* FROM commerce_coupons c WHERE {clause} ORDER BY c.updated_at DESC, c.id DESC LIMIT %s OFFSET %s",
                tuple([*params, int(limit), int(offset)]),
            ).fetchall()
            total_row = conn.execute(f"SELECT count(*) AS total FROM commerce_coupons c WHERE {clause}", tuple(params)).fetchone() or {}
            items = [self._coupon_payload(conn, dict(row)) for row in rows]
        return {"ok": True, "items": items, "total": int(total_row.get("total") or 0), "limit": limit, "offset": offset}

    def get_coupon(self, coupon_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commerce_coupons WHERE tenant_id = %s AND id = %s LIMIT 1",
                (TENANT_ID, int(coupon_id)),
            ).fetchone()
            return self._coupon_payload(conn, dict(row)) if row else None

    def get_coupon_by_slug(self, public_slug: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commerce_coupons WHERE tenant_id = %s AND public_slug = %s LIMIT 1",
                (TENANT_ID, _text(public_slug)),
            ).fetchone()
            return self._coupon_payload(conn, dict(row), include_claim_counts=False) if row else None

    def create_coupon(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            options = self._resolve_target_refs(conn, list(payload.get("target_refs") or []))
            row = conn.execute(
                """
                INSERT INTO commerce_coupons (
                    tenant_id, public_slug, name, discount_amount_total, currency, status,
                    total_issue_limit, per_user_issue_limit, issued_count,
                    claim_starts_at, claim_ends_at, validity_mode, use_starts_at, use_ends_at,
                    relative_validity_days, instructions, created_by, updated_by,
                    created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, 'CNY', 'draft', %s, %s, 0,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                RETURNING *
                """,
                (
                    TENANT_ID,
                    _public_slug(),
                    payload["name"],
                    int(payload["discount_amount_total"]),
                    int(payload["total_issue_limit"]),
                    int(payload["per_user_issue_limit"]),
                    payload["claim_starts_at"],
                    payload["claim_ends_at"],
                    _text(payload["validity_mode"]),
                    payload.get("use_starts_at"),
                    payload.get("use_ends_at"),
                    payload.get("relative_validity_days"),
                    payload.get("instructions") or "",
                    _text(actor_id),
                    _text(actor_id),
                ),
            ).fetchone()
            self._replace_bindings(conn, int(row["id"]), options)
            result = self._coupon_payload(conn, dict(row))
            conn.commit()
            return result

    def update_coupon(self, coupon_id: int, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            locked = conn.execute(
                "SELECT * FROM commerce_coupons WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (TENANT_ID, int(coupon_id)),
            ).fetchone()
            if not locked:
                raise NotFoundError("coupon not found")
            existing = self._coupon_payload(conn, dict(locked))
            validate_coupon_update(
                existing,
                payload,
                existing_target_refs=existing.get("target_refs") or [],
                proposed_target_refs=payload.get("target_refs") or [],
            )
            options = self._resolve_target_refs(conn, list(payload.get("target_refs") or []))
            row = conn.execute(
                """
                UPDATE commerce_coupons
                SET name = %s,
                    discount_amount_total = %s,
                    total_issue_limit = %s,
                    per_user_issue_limit = %s,
                    claim_starts_at = %s,
                    claim_ends_at = %s,
                    validity_mode = %s,
                    use_starts_at = %s,
                    use_ends_at = %s,
                    relative_validity_days = %s,
                    instructions = %s,
                    updated_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (
                    payload["name"],
                    int(payload["discount_amount_total"]),
                    int(payload["total_issue_limit"]),
                    int(payload["per_user_issue_limit"]),
                    payload["claim_starts_at"],
                    payload["claim_ends_at"],
                    _text(payload["validity_mode"]),
                    payload.get("use_starts_at"),
                    payload.get("use_ends_at"),
                    payload.get("relative_validity_days"),
                    payload.get("instructions") or "",
                    _text(actor_id),
                    TENANT_ID,
                    int(coupon_id),
                ),
            ).fetchone()
            self._replace_bindings(conn, int(coupon_id), options)
            result = self._coupon_payload(conn, dict(row))
            conn.commit()
            return result

    def delete_coupon(self, coupon_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                DELETE FROM commerce_coupons
                WHERE tenant_id = %s AND id = %s AND status = 'draft' AND issued_count = 0
                RETURNING id
                """,
                (TENANT_ID, int(coupon_id)),
            ).fetchone()
            if not row:
                exists = conn.execute(
                    "SELECT 1 FROM commerce_coupons WHERE tenant_id = %s AND id = %s",
                    (TENANT_ID, int(coupon_id)),
                ).fetchone()
                if not exists:
                    raise NotFoundError("coupon not found")
                raise ContractError("only an unclaimed draft coupon can be deleted")
            conn.commit()
        return {"ok": True, "deleted": True, "coupon_id": int(coupon_id)}

    def transition_coupon(self, coupon_id: int, target_status: str, *, actor_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            locked = conn.execute(
                "SELECT * FROM commerce_coupons WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (TENANT_ID, int(coupon_id)),
            ).fetchone()
            if not locked:
                raise NotFoundError("coupon not found")
            current = dict(locked)
            target = validate_coupon_transition(
                current["status"],
                target_status,
                claim_window_ended=_aware_utc(current.get("claim_ends_at")) <= _utcnow(),
            )
            if target is CouponLifecycleStatus.PUBLISHED:
                options = self._binding_options(conn, int(coupon_id))
                validate_discount_against_product_amounts(
                    int(current["discount_amount_total"]),
                    [int(option["amount_total"]) for option in options],
                )
            row = conn.execute(
                """
                UPDATE commerce_coupons
                SET status = %s, updated_by = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (target.value, _text(actor_id), TENANT_ID, int(coupon_id)),
            ).fetchone()
            result = self._coupon_payload(conn, dict(row))
            conn.commit()
            return result

    def copy_coupon(self, coupon_id: int, *, actor_id: str) -> dict[str, Any]:
        source = self.get_coupon(coupon_id)
        if not source:
            raise NotFoundError("coupon not found")
        payload = {
            key: deepcopy(source.get(key))
            for key in (
                "discount_amount_total",
                "total_issue_limit",
                "per_user_issue_limit",
                "claim_starts_at",
                "claim_ends_at",
                "validity_mode",
                "use_starts_at",
                "use_ends_at",
                "relative_validity_days",
                "instructions",
                "target_refs",
            )
        }
        payload["name"] = f"{_text(source.get('name'))} 副本"[:45]
        return self.create_coupon(payload, actor_id=actor_id)

    def list_product_options(self, *, q: str, product_type: str, limit: int, offset: int) -> dict[str, Any]:
        with self._connect() as conn:
            options = self._all_product_options(conn)
        needle = _text(q).lower()
        if needle:
            options = [option for option in options if needle in _text(option.get("title")).lower()]
        normalized_type = _text(product_type)
        if normalized_type and normalized_type != "all":
            options = [option for option in options if option["product_type"] == normalized_type]
        return {"ok": True, "items": options[offset : offset + limit], "total": len(options), "limit": limit, "offset": offset}

    def product_options_for_target_refs(self, target_refs: list[str]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self._resolve_target_refs(conn, target_refs)

    def list_claims(self, coupon_id: int, *, limit: int, offset: int) -> dict[str, Any]:
        with self._connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM commerce_coupons WHERE tenant_id = %s AND id = %s",
                (TENANT_ID, int(coupon_id)),
            ).fetchone():
                raise NotFoundError("coupon not found")
            rows = conn.execute(
                """
                SELECT claim.*,
                       redemption.out_trade_no,
                       redemption.consumed_at,
                       redemption.released_at,
                       orders.product_name AS product_title
                FROM commerce_coupon_claims claim
                LEFT JOIN LATERAL (
                    SELECT r.order_id, r.out_trade_no, r.consumed_at, r.released_at
                    FROM commerce_coupon_redemptions r
                    WHERE r.claim_id = claim.id
                    ORDER BY r.id DESC
                    LIMIT 1
                ) redemption ON TRUE
                LEFT JOIN wechat_pay_orders orders ON orders.id = redemption.order_id
                WHERE claim.tenant_id = %s AND claim.coupon_id = %s
                ORDER BY claim.claimed_at DESC, claim.id DESC
                LIMIT %s OFFSET %s
                """,
                (TENANT_ID, int(coupon_id), int(limit), int(offset)),
            ).fetchall()
            total_row = conn.execute(
                "SELECT count(*) AS total FROM commerce_coupon_claims WHERE tenant_id = %s AND coupon_id = %s",
                (TENANT_ID, int(coupon_id)),
            ).fetchone() or {}
            stats_rows = conn.execute(
                """
                SELECT CASE
                           WHEN status = 'available' AND valid_until <= CURRENT_TIMESTAMP THEN 'expired'
                           ELSE status
                       END AS status,
                       count(*) AS total
                FROM commerce_coupon_claims
                WHERE tenant_id = %s AND coupon_id = %s
                GROUP BY 1
                """,
                (TENANT_ID, int(coupon_id)),
            ).fetchall()
        stats = {status.value: 0 for status in CouponClaimStatus}
        for row in stats_rows:
            stats[_text(row.get("status"))] = int(row.get("total") or 0)
        return {
            "ok": True,
            "items": [self._claim_payload(dict(row), masked=True) for row in rows],
            "total": int(total_row.get("total") or 0),
            "limit": limit,
            "offset": offset,
            "stats": {"issued": int(total_row.get("total") or 0), **stats},
        }

    def resolve_canonical_unionid(self, identity: dict[str, Any]) -> str:
        with self._connect() as conn:
            return _resolve_identity(conn, identity)

    def count_user_claims(self, coupon_id: int, *, unionid: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count(*) AS total FROM commerce_coupon_claims "
                "WHERE tenant_id = %s AND coupon_id = %s AND unionid = %s",
                (TENANT_ID, int(coupon_id), unionid),
            ).fetchone() or {}
        return int(row.get("total") or 0)

    def claim_coupon(self, public_slug: str, *, unionid: str, idempotency_hash: str, now: datetime) -> dict[str, Any]:
        current = _aware_utc(now)
        with self._connect() as conn:
            coupon_row = conn.execute(
                "SELECT * FROM commerce_coupons WHERE tenant_id = %s AND public_slug = %s FOR UPDATE",
                (TENANT_ID, _text(public_slug)),
            ).fetchone()
            if not coupon_row:
                raise NotFoundError("coupon not found")
            coupon = dict(coupon_row)
            existing = conn.execute(
                """
                SELECT * FROM commerce_coupon_claims
                WHERE tenant_id = %s AND coupon_id = %s AND unionid = %s AND idempotency_key_hash = %s
                LIMIT 1
                """,
                (TENANT_ID, int(coupon["id"]), unionid, idempotency_hash),
            ).fetchone()
            if existing:
                result = {
                    "ok": True,
                    "idempotent": True,
                    "coupon": self._coupon_payload(conn, coupon),
                    "claim": self._claim_payload(dict(existing)),
                }
                conn.commit()
                return result
            if _text(coupon.get("status")) != CouponLifecycleStatus.PUBLISHED.value:
                raise ContractError("coupon is not claimable")
            if current < _aware_utc(coupon.get("claim_starts_at")):
                raise ContractError("coupon claim has not started")
            if current >= _aware_utc(coupon.get("claim_ends_at")):
                raise ContractError("coupon claim has ended")
            if int(coupon.get("issued_count") or 0) >= int(coupon.get("total_issue_limit") or 0):
                raise ContractError("coupon is sold out")
            user_count = conn.execute(
                "SELECT count(*) AS total FROM commerce_coupon_claims WHERE tenant_id = %s AND coupon_id = %s AND unionid = %s",
                (TENANT_ID, int(coupon["id"]), unionid),
            ).fetchone() or {}
            if int(user_count.get("total") or 0) >= int(coupon.get("per_user_issue_limit") or 0):
                raise ContractError("per-user coupon claim limit reached")
            # Serialize claim-time price validation with product price changes.
            # Without this shared lock, a concurrent decrease could commit
            # between validation and claim insertion and create a zero-payable
            # coupon instance that the price guard never observed.
            options = self._binding_options(conn, int(coupon["id"]), lock_products=True)
            validate_discount_against_product_amounts(
                int(coupon["discount_amount_total"]),
                [int(option["amount_total"]) for option in options],
            )
            valid_from, valid_until = calculate_claim_validity(
                claimed_at=current,
                validity_mode=coupon["validity_mode"],
                use_starts_at=coupon.get("use_starts_at"),
                use_ends_at=coupon.get("use_ends_at"),
                relative_validity_days=coupon.get("relative_validity_days"),
            )
            claim = conn.execute(
                """
                INSERT INTO commerce_coupon_claims (
                    tenant_id, coupon_id, claim_no, unionid, discount_amount_total, currency,
                    valid_from, valid_until, status, idempotency_key_hash, claimed_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'CNY', %s, %s, 'available', %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING *
                """,
                (
                    TENANT_ID,
                    int(coupon["id"]),
                    _claim_no(),
                    unionid,
                    int(coupon["discount_amount_total"]),
                    valid_from,
                    valid_until,
                    idempotency_hash,
                    current,
                ),
            ).fetchone()
            coupon = dict(
                conn.execute(
                    """
                    UPDATE commerce_coupons
                    SET issued_count = issued_count + 1,
                        first_claim_at = COALESCE(first_claim_at, %s),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING *
                    """,
                    (current, int(coupon["id"])),
                ).fetchone()
            )
            result = {
                "ok": True,
                "idempotent": False,
                "coupon": self._coupon_payload(conn, coupon),
                "claim": self._claim_payload(dict(claim)),
            }
            conn.commit()
            return result

    def list_available_claims(self, target_ref: str, *, unionid: str, now: datetime) -> dict[str, Any]:
        current = _aware_utc(now)
        with self._connect() as conn:
            option = self._resolve_target_refs(conn, [target_ref])[0]
            rows = conn.execute(
                """
                SELECT claim.*, coupon.name AS coupon_name
                FROM commerce_coupon_claims claim
                JOIN commerce_coupons coupon ON coupon.id = claim.coupon_id AND coupon.tenant_id = claim.tenant_id
                JOIN commerce_coupon_product_bindings binding ON binding.coupon_id = coupon.id
                WHERE claim.tenant_id = %s
                  AND claim.unionid = %s
                  AND claim.status = 'available'
                  AND claim.valid_from <= %s
                  AND claim.valid_until > %s
                  AND binding.trade_product_id = %s
                  AND claim.discount_amount_total < %s
                ORDER BY claim.discount_amount_total DESC, claim.valid_until ASC, claim.claimed_at ASC, claim.id ASC
                """,
                (
                    TENANT_ID,
                    unionid,
                    current,
                    current,
                    int(option["trade_product_id"]),
                    int(option["amount_total"]),
                ),
            ).fetchall()
        return {
            "ok": True,
            "items": [self._claim_payload(dict(row)) for row in rows],
            "total": len(rows),
            "target_ref": target_ref,
        }

    def assert_product_price(self, product_id: str, new_price: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT claim.claim_no
                FROM commerce_coupon_claims claim
                JOIN commerce_coupon_product_bindings binding ON binding.coupon_id = claim.coupon_id
                WHERE claim.tenant_id = %s
                  AND binding.trade_product_id = %s
                  AND claim.status IN ('available', 'reserved')
                  AND claim.valid_until > CURRENT_TIMESTAMP
                  AND claim.discount_amount_total >= %s
                LIMIT 1
                """,
                (TENANT_ID, int(product_id), int(new_price)),
            ).fetchone()
        if row:
            raise ContractError("product price cannot be lower than or equal to an active coupon amount")

    def _all_product_options(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT p.id AS trade_product_id,
                   p.product_code,
                   p.name,
                   p.amount_total,
                   p.currency,
                   p.status,
                   p.enabled,
                   p.metadata_json,
                   sp.id AS service_period_id,
                   sp.link_slug,
                   sp.duration_days,
                   CASE WHEN sp.id IS NOT NULL OR p.metadata_json->>'aicrm_product_owner' = 'service_period' THEN 'service_period' ELSE 'standard_product' END AS product_type
            FROM wechat_pay_products p
            LEFT JOIN service_period_products sp
              ON sp.trade_product_id = p.id AND sp.tenant_id = %s AND sp.deleted = FALSE
            ORDER BY p.updated_at DESC, p.id DESC
            """,
            (TENANT_ID,),
        ).fetchall()
        return [product_option(dict(row)) for row in rows]

    def _resolve_target_refs(self, conn: Any, target_refs: list[str]) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(_text(value) for value in target_refs if _text(value)))
        options = self._all_product_options(conn)
        by_ref = {option["target_ref"]: option for option in options}
        if not normalized or any(value not in by_ref for value in normalized):
            raise ContractError("one or more selected products are invalid")
        return [by_ref[value] for value in normalized]

    @staticmethod
    def _replace_bindings(conn: Any, coupon_id: int, options: list[dict[str, Any]]) -> None:
        conn.execute("DELETE FROM commerce_coupon_product_bindings WHERE coupon_id = %s", (int(coupon_id),))
        for option in options:
            conn.execute(
                """
                INSERT INTO commerce_coupon_product_bindings (tenant_id, coupon_id, trade_product_id, created_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (TENANT_ID, int(coupon_id), int(option["trade_product_id"])),
            )

    def _binding_options(
        self,
        conn: Any,
        coupon_id: int,
        *,
        lock_products: bool = False,
    ) -> list[dict[str, Any]]:
        if lock_products:
            conn.execute(
                """
                SELECT product.id
                FROM wechat_pay_products product
                JOIN commerce_coupon_product_bindings binding
                  ON binding.trade_product_id = product.id
                WHERE binding.tenant_id = %s AND binding.coupon_id = %s
                ORDER BY product.id
                FOR SHARE OF product
                """,
                (TENANT_ID, int(coupon_id)),
            ).fetchall()
        all_options = {option["trade_product_id"]: option for option in self._all_product_options(conn)}
        rows = conn.execute(
            "SELECT trade_product_id FROM commerce_coupon_product_bindings WHERE tenant_id = %s AND coupon_id = %s ORDER BY id ASC",
            (TENANT_ID, int(coupon_id)),
        ).fetchall()
        return [all_options[_text(row["trade_product_id"])] for row in rows if _text(row["trade_product_id"]) in all_options]

    def _coupon_payload(self, conn: Any, row: dict[str, Any], *, include_claim_counts: bool = True) -> dict[str, Any]:
        options = self._binding_options(conn, int(row["id"]))
        counts = {"used_count": 0, "reserved_count": 0}
        if include_claim_counts:
            count_row = conn.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'consumed') AS used_count,
                       count(*) FILTER (WHERE status = 'reserved') AS reserved_count
                FROM commerce_coupon_claims
                WHERE tenant_id = %s AND coupon_id = %s
                """,
                (TENANT_ID, int(row["id"])),
            ).fetchone() or {}
            counts = {"used_count": int(count_row.get("used_count") or 0), "reserved_count": int(count_row.get("reserved_count") or 0)}
        return _coupon_display_payload(
            {
                **dict(row),
                **counts,
                "products": options,
                "target_refs": [option["target_ref"] for option in options],
                "product_count": len(options),
            }
        )

    @staticmethod
    def _claim_payload(row: dict[str, Any], *, masked: bool = False) -> dict[str, Any]:
        payload = dict(row)
        payload["id"] = int(payload.get("id") or 0)
        if (
            _text(payload.get("status")) == CouponClaimStatus.AVAILABLE.value
            and payload.get("valid_until")
            and _aware_utc(payload.get("valid_until")) <= _utcnow()
        ):
            payload["status"] = CouponClaimStatus.EXPIRED.value
        if masked:
            payload["unionid"] = _mask_unionid(payload.get("unionid"))
        return payload


def _resolve_identity(conn: Any, identity: dict[str, Any]) -> str:
    unionid = _text(identity.get("unionid"))
    openid = _text(identity.get("openid"))
    if unionid:
        union_result = resolve_identity_with_dbapi(
            conn,
            ResolvePersonIdentityRequest(unionid=unionid),
            for_update=False,
        )
        canonical = resolved_unionid(union_result)
        if not canonical:
            return ""
        if openid:
            openid_result = resolve_identity_with_dbapi(
                conn,
                ResolvePersonIdentityRequest(openid=openid),
                for_update=False,
            )
            if openid_result.status == "conflict":
                return ""
            openid_unionid = resolved_unionid(openid_result)
            if openid_unionid and openid_unionid != canonical:
                return ""
        return canonical
    result: IdentityResolveResult = resolve_identity_with_dbapi(
        conn,
        ResolvePersonIdentityRequest(openid=openid or None),
        for_update=False,
    )
    return resolved_unionid(result)


_GLOBAL_REPOSITORY: InMemoryCouponRepository | None = None


def build_coupon_repository() -> CouponRepository:
    global _GLOBAL_REPOSITORY
    if production_data_ready():
        return assert_repository_allowed(
            PostgresCouponRepository(raw_database_url()),
            capability_owner="commerce",
        )
    if _GLOBAL_REPOSITORY is None:
        _GLOBAL_REPOSITORY = InMemoryCouponRepository()
    return assert_repository_allowed(_GLOBAL_REPOSITORY, capability_owner="commerce")


def reset_coupon_fixture_state() -> None:
    global _GLOBAL_REPOSITORY
    # The shared commerce fixture projection may contain presentation-only rows
    # without a persisted trade-product id.  Coupon fixtures start empty and
    # tests that exercise selection inject explicit product options.
    _GLOBAL_REPOSITORY = InMemoryCouponRepository(product_options=[])


__all__ = [
    "CouponRepository",
    "DbApiCouponOrderRepository",
    "InMemoryCouponRepository",
    "PostgresCouponRepository",
    "build_coupon_order_repository",
    "build_coupon_repository",
    "product_id_from_target_ref",
    "request_key_hash",
    "reset_coupon_fixture_state",
    "target_ref_for_product_id",
]
