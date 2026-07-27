from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.share_qr import svg_qr_data_url

from .domain import (
    CouponChoiceMode,
    CouponLifecycleStatus,
    CouponValidityMode,
    SHANGHAI_TZ,
    validate_discount_against_product_amounts,
)
from .dto import CouponChoice, CouponUpsertRequest
from .repo import (
    CouponRepository,
    build_coupon_repository,
    build_coupon_order_repository,
    product_id_from_target_ref,
    request_key_hash,
    target_ref_for_product_id,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime | None) -> datetime:
    source = value or _utcnow()
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(timezone.utc)


def _public_product(product: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(product)
    payload.pop("trade_product_id", None)
    payload.pop("service_period_id", None)
    payload.pop("tenant_id", None)
    payload.pop("id", None)
    return payload


def _masked_reference(value: Any) -> str:
    normalized = _text(value)
    if not normalized or "*" in normalized:
        return normalized
    if len(normalized) <= 8:
        return f"{normalized[:2]}****{normalized[-2:]}"
    return f"{normalized[:4]}****{normalized[-4:]}"


def _shanghai_datetime_display(value: Any) -> str:
    if value is None or value == "":
        return ""
    source: datetime
    if isinstance(value, datetime):
        source = value
    else:
        normalized = _text(value)
        try:
            source = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return normalized
    return _aware_utc(source).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")


def _with_datetime_displays(
    payload: dict[str, Any],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    for field in fields:
        display = _shanghai_datetime_display(payload.get(field))
        if display:
            payload[f"{field}_display"] = display
    return payload


def _coupon_payload(coupon: Mapping[str, Any], *, public: bool = False) -> dict[str, Any]:
    payload = deepcopy(dict(coupon))
    payload.pop("tenant_id", None)
    payload.pop("product_ids", None)
    payload["products"] = [_public_product(item) for item in payload.get("products") or []]
    _with_datetime_displays(
        payload,
        (
            "claim_starts_at",
            "claim_ends_at",
            "use_starts_at",
            "use_ends_at",
            "first_claim_at",
            "created_at",
            "updated_at",
        ),
    )
    if public:
        payload.pop("id", None)
        payload.pop("created_by", None)
        payload.pop("updated_by", None)
    return payload


def _claim_payload(claim: Mapping[str, Any], *, public: bool = False) -> dict[str, Any]:
    payload = deepcopy(dict(claim))
    payload.pop("tenant_id", None)
    payload.pop("idempotency_key_hash", None)
    if payload.get("coupon_name") and not payload.get("name"):
        payload["name"] = payload["coupon_name"]
    _with_datetime_displays(
        payload,
        (
            "claimed_at",
            "valid_from",
            "valid_until",
            "reserved_at",
            "consumed_at",
            "expired_at",
            "released_at",
            "created_at",
            "updated_at",
        ),
    )
    if public:
        payload.pop("id", None)
        payload.pop("coupon_id", None)
        payload.pop("unionid", None)
        payload.pop("masked_identity", None)
        payload.pop("claim_id", None)
        payload.pop("order_id", None)
        payload.pop("redemption_id", None)
        payload.pop("out_trade_no", None)
    else:
        masked_identity = payload.pop("masked_identity", "")
        raw_identity = payload.pop("unionid", "")
        identity = masked_identity or raw_identity
        claim_no = payload.pop("claim_no", "")
        order_no = payload.pop("out_trade_no", "")
        payload["masked_identity"] = _masked_reference(identity)
        payload["claim_no_masked"] = _masked_reference(claim_no)
        payload["order_no_masked"] = _masked_reference(order_no)
        for key in ("id", "coupon_id", "claim_id", "order_id", "redemption_id"):
            payload.pop(key, None)
    return payload


def _sanitize_coupon_result(result: Mapping[str, Any], *, public: bool = False) -> dict[str, Any]:
    payload = deepcopy(dict(result))
    if isinstance(payload.get("coupon"), Mapping):
        payload["coupon"] = _coupon_payload(payload["coupon"], public=public)
    if isinstance(payload.get("claim"), Mapping):
        payload["claim"] = _claim_payload(payload["claim"], public=public)
    return payload


def normalize_coupon_choice(payload: Mapping[str, Any] | None) -> CouponChoice:
    """Normalize the additive checkout field, preserving old-client behavior.

    Omission is intentionally ``none``.  New payment pages explicitly submit
    ``auto`` so rolling out this feature cannot change old checkout requests.
    """

    source = payload if isinstance(payload, Mapping) else {}
    if "coupon_choice" not in source or source.get("coupon_choice") is None:
        return CouponChoice(mode=CouponChoiceMode.NONE)
    raw_choice = source.get("coupon_choice")
    if isinstance(raw_choice, CouponChoice):
        return raw_choice
    try:
        return CouponChoice.model_validate(raw_choice)
    except ValidationError as exc:
        raise ContractError("invalid coupon_choice") from exc


def _normalized_choice(value: CouponChoice | Mapping[str, Any]) -> CouponChoice:
    if isinstance(value, CouponChoice):
        return value
    try:
        return CouponChoice.model_validate(value)
    except ValidationError as exc:
        raise ContractError("invalid coupon_choice") from exc


def _no_coupon_order(order: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(order)
    subtotal = int(payload.get("subtotal_amount_total") or payload.get("amount_total") or 0)
    payload["subtotal_amount_total"] = subtotal
    payload["discount_amount_total"] = 0
    payload["amount_total"] = subtotal
    payload["coupon_claim_id"] = None
    payload["coupon_snapshot_json"] = {}
    return payload


def reserve_coupon_for_order(
    conn: Any,
    *,
    order: Mapping[str, Any],
    coupon_choice: CouponChoice | Mapping[str, Any],
    unionid: str,
    trade_product_id: str | int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reserve one eligible claim and snapshot it onto an existing order.

    The caller owns the transaction.  The order insert and this reservation
    must be committed together before the WeChat Pay create-order request.
    """

    choice = _normalized_choice(coupon_choice)
    if choice.mode is CouponChoiceMode.NONE:
        # Compatibility invariant: omission/none performs no coupon SQL.
        return _no_coupon_order(order)
    current = _aware_utc(now)
    canonical_unionid = _text(unionid)
    if not canonical_unionid:
        raise ContractError("canonical unionid is required for coupon use")
    product_id = _text(trade_product_id)
    if not product_id.isdigit() or int(product_id) <= 0:
        raise ContractError("trade_product_id is required for coupon use")

    order_payload = dict(order)
    order_id = int(order_payload.get("id") or 0)
    out_trade_no = _text(order_payload.get("out_trade_no"))
    subtotal = int(order_payload.get("subtotal_amount_total") or order_payload.get("amount_total") or 0)
    currency = _text(order_payload.get("currency")) or "CNY"
    if order_id <= 0 or not out_trade_no:
        raise ContractError("persisted order is required before coupon reservation")
    if subtotal <= 0 or currency != "CNY":
        raise ContractError("coupon orders require a positive CNY subtotal")
    return build_coupon_order_repository(conn).reserve_coupon_for_order(
        order=order_payload,
        choice_mode=choice.mode.value,
        claim_no=_text(choice.claim_no),
        unionid=canonical_unionid,
        trade_product_id=int(product_id),
        now=current,
    )


def consume_coupon_for_paid_order(
    conn: Any,
    *,
    out_trade_no: str,
    provider_total: int,
    provider_currency: str,
) -> dict[str, Any]:
    """Atomically consume a reserved claim after strict provider validation."""

    trade_no = _text(out_trade_no)
    currency = _text(provider_currency)
    return build_coupon_order_repository(conn).consume_coupon_for_paid_order(
        out_trade_no=trade_no,
        provider_total=int(provider_total),
        provider_currency=currency,
    )


def release_coupon_for_order(
    conn: Any,
    *,
    out_trade_no: str,
    reason: str = "order_closed",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release only an unconsumed reservation; refunds never call this path."""

    trade_no = _text(out_trade_no)
    current = _aware_utc(now)
    return build_coupon_order_repository(conn).release_coupon_for_order(
        out_trade_no=trade_no,
        reason=_text(reason),
        now=current,
    )


def assert_product_price_allows_coupons(
    product_id: str | int,
    new_price: int,
    *,
    repository: CouponRepository | None = None,
) -> None:
    if isinstance(new_price, bool) or not isinstance(new_price, int) or new_price <= 0:
        raise ContractError("product price must be a positive integer amount")
    (repository or build_coupon_repository()).assert_product_price(_text(product_id), new_price)


def coupon_consistency_counts(conn: Any) -> dict[str, Any]:
    """Return reconciliation counters without mutating production state."""
    return build_coupon_order_repository(conn).consistency_counts()


class CouponAdminApplication:
    def __init__(
        self,
        repository: CouponRepository | None = None,
        *,
        actor_id: str = "admin_console",
    ) -> None:
        self._repo = repository or build_coupon_repository()
        self._actor_id = _text(actor_id) or "admin_console"

    def list_coupons(self, *, limit: int, offset: int, q: str = "", status: str = "") -> dict[str, Any]:
        payload = self._repo.list_coupons(limit=limit, offset=offset, q=q, status=status)
        payload["items"] = [_coupon_payload(item) for item in payload.get("items") or []]
        return payload

    def get_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._required_coupon(coupon_id)
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def create_coupon(self, request: CouponUpsertRequest) -> dict[str, Any]:
        payload = request.model_dump(mode="python")
        self._validate_selected_products(payload)
        coupon = self._repo.create_coupon(payload, actor_id=self._actor_id)
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def update_coupon(self, coupon_id: int, request: CouponUpsertRequest) -> dict[str, Any]:
        payload = request.model_dump(mode="python")
        self._validate_selected_products(payload)
        coupon = self._repo.update_coupon(coupon_id, payload, actor_id=self._actor_id)
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def delete_coupon(self, coupon_id: int) -> dict[str, Any]:
        return self._repo.delete_coupon(coupon_id)

    def publish_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._repo.transition_coupon(
            coupon_id,
            CouponLifecycleStatus.PUBLISHED.value,
            actor_id=self._actor_id,
        )
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def stop_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._repo.transition_coupon(
            coupon_id,
            CouponLifecycleStatus.STOPPED.value,
            actor_id=self._actor_id,
        )
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def archive_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._repo.transition_coupon(
            coupon_id,
            CouponLifecycleStatus.ARCHIVED.value,
            actor_id=self._actor_id,
        )
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def copy_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._repo.copy_coupon(coupon_id, actor_id=self._actor_id)
        return {"ok": True, "coupon": _coupon_payload(coupon)}

    def get_share(self, coupon_id: int, *, request_base_url: str = "") -> dict[str, Any]:
        coupon = self._required_coupon(coupon_id)
        path = f"/c/{coupon['public_slug']}"
        base = _text(request_base_url).rstrip("/")
        url = f"{base}{path}" if base else path
        return {
            "ok": True,
            "share": {
                "public_url": url,
                "url": url,
                "qr_value": url,
                "qr_data_url": svg_qr_data_url(url),
            },
        }

    def list_product_options(
        self,
        *,
        q: str = "",
        product_type: str = "all",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        payload = self._repo.list_product_options(
            q=q,
            product_type=product_type,
            limit=limit,
            offset=offset,
        )
        payload["items"] = [_public_product(item) for item in payload.get("items") or []]
        return payload

    def list_claims(self, coupon_id: int, *, limit: int, offset: int) -> dict[str, Any]:
        payload = self._repo.list_claims(coupon_id, limit=limit, offset=offset)
        payload["items"] = [_claim_payload(item) for item in payload.get("items") or []]
        return payload

    def _required_coupon(self, coupon_id: int) -> dict[str, Any]:
        coupon = self._repo.get_coupon(coupon_id)
        if not coupon:
            raise NotFoundError("coupon not found")
        return coupon

    def _validate_selected_products(self, payload: Mapping[str, Any]) -> None:
        options = self._repo.product_options_for_target_refs(list(payload.get("target_refs") or []))
        if any(_text(option.get("currency")) != "CNY" for option in options):
            raise ContractError("coupon products must use CNY")
        validate_discount_against_product_amounts(
            int(payload.get("discount_amount_total") or 0),
            [int(option.get("amount_total") or 0) for option in options],
        )


class CouponSidebarApplication:
    """Read-only, PII-free coupon projection for the signed customer sidebar."""

    def __init__(self, repository: CouponRepository | None = None) -> None:
        self._repo = repository or build_coupon_repository()

    def list_claimable(
        self,
        *,
        public_base_url: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        requested_limit = max(1, min(int(limit), 10_000)) if limit is not None else None
        page_limit = min(requested_limit or 200, 200)
        coupons: list[dict[str, Any]] = []
        total = 0
        offset = 0
        while True:
            payload = self._repo.list_coupons(limit=page_limit, offset=offset, q="", status="active")
            batch = [dict(item) for item in payload.get("items") or []]
            coupons.extend(batch)
            total = int(payload.get("total") or len(coupons))
            offset += len(batch)
            if not batch or offset >= total or (requested_limit is not None and offset >= requested_limit):
                break
        if requested_limit is not None:
            coupons = coupons[:requested_limit]
        base = _text(public_base_url).rstrip("/")
        items: list[dict[str, Any]] = []
        for coupon in coupons:
            amount = int(coupon.get("discount_amount_total") or 0)
            amount_text = f"{amount / 100:.2f}".rstrip("0").rstrip(".")
            items.append(
                {
                    "id": int(coupon.get("id") or 0),
                    "name": _text(coupon.get("name")),
                    "discount_amount_total": amount,
                    "discount_label": f"立减 ¥{amount_text}",
                    "products": [
                        {
                            "title": _text(product.get("title")),
                            "product_type": _text(product.get("product_type")),
                        }
                        for product in coupon.get("products") or []
                    ],
                    "claim_ends_at": _shanghai_datetime_display(coupon.get("claim_ends_at")),
                    "url": f"{base}/c/{_text(coupon.get('public_slug'))}",
                }
            )
        return {"ok": True, "items": items, "total": total}

    __call__ = list_claimable


class CouponPublicApplication:
    def __init__(self, repository: CouponRepository | None = None) -> None:
        self._repo = repository or build_coupon_repository()

    def get_coupon(self, public_slug: str, *, identity: dict[str, Any]) -> dict[str, Any]:
        coupon = self._repo.get_coupon_by_slug(_text(public_slug))
        if not coupon:
            raise NotFoundError("coupon not found")
        canonical_unionid = ""
        if _text(identity.get("unionid")) or _text(identity.get("openid")):
            canonical_unionid = self._repo.resolve_canonical_unionid(identity)
        user_claim_count = (
            self._repo.count_user_claims(int(coupon["id"]), unionid=canonical_unionid)
            if canonical_unionid
            else 0
        )
        display_state = _text(coupon.get("display_state"))
        user_limit_reached = user_claim_count >= int(coupon.get("per_user_issue_limit") or 1)
        claimable = (
            display_state == "active"
            and bool(canonical_unionid)
            and not user_limit_reached
        )
        public_coupon = _coupon_payload(coupon, public=True)
        return {
            "ok": True,
            "coupon": public_coupon,
            "products": public_coupon.get("products") or [],
            "display_state": display_state,
            "identity_ready": bool(canonical_unionid),
            "rollout_enabled": True,
            "claimable": claimable,
            "claimed": user_claim_count > 0,
            "user_claim_count": user_claim_count,
            "user_limit_reached": user_limit_reached,
            "validity_text": self._validity_text(coupon),
        }

    def claim_coupon(
        self,
        public_slug: str,
        *,
        identity: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        unionid = self._repo.resolve_canonical_unionid(identity)
        if not unionid:
            raise ContractError("canonical unionid is required")
        result = self._repo.claim_coupon(
            _text(public_slug),
            unionid=unionid,
            idempotency_hash=request_key_hash(idempotency_key),
            now=_utcnow(),
        )
        return _sanitize_coupon_result(result, public=True)

    def list_available_claims(self, target_ref: str, *, identity: dict[str, Any]) -> dict[str, Any]:
        unionid = self._repo.resolve_canonical_unionid(identity)
        if not unionid:
            raise ContractError("canonical unionid is required")
        payload = self._repo.list_available_claims(
            _text(target_ref),
            unionid=unionid,
            now=_utcnow(),
        )
        payload["items"] = [_claim_payload(item, public=True) for item in payload.get("items") or []]
        payload["identity_ready"] = True
        payload["rollout_enabled"] = True
        return payload

    @staticmethod
    def _validity_text(coupon: Mapping[str, Any]) -> str:
        if _text(coupon.get("validity_mode")) == CouponValidityMode.RELATIVE_DAYS.value:
            return f"领取后 {int(coupon.get('relative_validity_days') or 0)} 个自然日内有效"
        starts_at = coupon.get("use_starts_at")
        ends_at = coupon.get("use_ends_at")
        if not isinstance(starts_at, datetime) or not isinstance(ends_at, datetime):
            return "请在有效期内使用"
        start_text = _aware_utc(starts_at).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
        end_text = _aware_utc(ends_at).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
        return f"{start_text} 至 {end_text}"


__all__ = [
    "CouponAdminApplication",
    "CouponPublicApplication",
    "assert_product_price_allows_coupons",
    "consume_coupon_for_paid_order",
    "coupon_consistency_counts",
    "normalize_coupon_choice",
    "product_id_from_target_ref",
    "release_coupon_for_order",
    "reserve_coupon_for_order",
    "target_ref_for_product_id",
]
