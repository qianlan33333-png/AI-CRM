from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aicrm_next.app.admin_console.navigation import nav_items
from aicrm_next.extensions.commerce.commerce.coupons import application as coupon_application
from aicrm_next.extensions.commerce.commerce.coupons.application import CouponPublicApplication


_PRODUCTION_ENV_KEYS = ("AICRM_NEXT_ENV", "ENVIRONMENT", "APP_ENV", "FLASK_ENV")
_RETIRED_ROLLOUT_ENV = "AICRM_COMMERCE_COUPONS_ENABLED"


def _production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PRODUCTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    # The retired variable must no longer hide or disable coupons.
    monkeypatch.setenv(_RETIRED_ROLLOUT_ENV, "false")


def _transaction_items() -> list[dict]:
    transaction = next(group for group in nav_items("") if group["title"] == "交易")
    return list(transaction["items"])


def test_coupon_is_a_fixed_primary_transaction_entry_in_production(monkeypatch) -> None:
    _production_environment(monkeypatch)

    items = _transaction_items()
    assert [str(item["label"]) for item in items] == [
        "交易管理",
        "商品管理",
        "周期商品管理",
        "优惠券",
    ]
    coupon = next(item for item in items if item["key"] == "coupons")
    assert coupon["endpoint"] == "api.admin_coupons_page"
    assert coupon["href"] == "/admin/coupons"


class _PublicCouponRepository:
    def __init__(self) -> None:
        self.claim_calls = 0
        self.available_calls = 0

    @staticmethod
    def resolve_canonical_unionid(_identity) -> str:
        return "union_coupon_direct"

    @staticmethod
    def get_coupon_by_slug(_public_slug: str) -> dict:
        return {
            "id": 17,
            "public_slug": "direct-coupon",
            "name": "正式优惠券",
            "display_state": "active",
            "per_user_issue_limit": 2,
            "validity_mode": "fixed_range",
            "use_starts_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
            "use_ends_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "products": [],
        }

    @staticmethod
    def count_user_claims(_coupon_id: int, *, unionid: str) -> int:
        assert unionid == "union_coupon_direct"
        return 0

    def list_available_claims(self, _target_ref: str, *, unionid: str, now) -> dict:
        assert unionid == "union_coupon_direct"
        assert now.tzinfo is not None
        self.available_calls += 1
        return {"ok": True, "items": [], "total": 0}

    def claim_coupon(self, *_args, **_kwargs) -> dict:
        self.claim_calls += 1
        return {
            "ok": True,
            "claim": {"claim_no": "clm_direct_coupon", "status": "available"},
        }


def test_production_allows_coupon_discovery_and_claims_without_rollout_gate(monkeypatch) -> None:
    _production_environment(monkeypatch)
    repo = _PublicCouponRepository()
    application = CouponPublicApplication(repository=repo)

    state = application.get_coupon(
        "direct-coupon",
        identity={"openid": "openid_coupon_direct"},
    )
    available = application.list_available_claims(
        "opaque-target-ref",
        identity={"openid": "openid_coupon_direct"},
    )
    claimed = application.claim_coupon(
        "direct-coupon",
        identity={"openid": "openid_coupon_direct"},
        idempotency_key="claim-direct-enabled",
    )

    assert state["rollout_enabled"] is True
    assert state["claimable"] is True
    assert state["claimed"] is False
    assert available["ok"] is True
    assert available["items"] == []
    assert available["total"] == 0
    assert available["rollout_enabled"] is True
    assert claimed["claim"]["claim_no"] == "clm_direct_coupon"
    assert repo.available_calls == 1
    assert repo.claim_calls == 1


class _OrderCouponRepository:
    def __init__(self) -> None:
        self.events: list[str] = []

    def reserve_coupon_for_order(self, **kwargs) -> dict:
        self.events.append("reserve")
        assert kwargs["choice_mode"] == "auto"
        payload = dict(kwargs["order"])
        payload["subtotal_amount_total"] = 10_000
        payload["discount_amount_total"] = 2_000
        payload["amount_total"] = 8_000
        payload["coupon_claim_id"] = 55
        payload["coupon_snapshot_json"] = {"coupon_id": 17}
        return payload

    def consume_coupon_for_paid_order(self, **_kwargs):
        self.events.append("consume")
        return {"ok": True, "status": "consumed"}

    def release_coupon_for_order(self, **_kwargs):
        self.events.append("release")
        return {"ok": True, "status": "released"}


def test_production_allows_new_coupon_reservations_and_existing_order_completion(
    monkeypatch,
) -> None:
    _production_environment(monkeypatch)
    repository = _OrderCouponRepository()
    monkeypatch.setattr(
        coupon_application,
        "build_coupon_order_repository",
        lambda _conn: repository,
    )
    order = {
        "id": 91,
        "out_trade_no": "WXP_DIRECT_COUPON",
        "amount_total": 10_000,
        "currency": "CNY",
    }

    no_coupon_order = coupon_application.reserve_coupon_for_order(
        object(),
        order=order,
        coupon_choice={"mode": "none"},
        unionid="union_coupon_direct",
        trade_product_id=7,
    )
    assert no_coupon_order["amount_total"] == 10_000
    assert no_coupon_order["coupon_claim_id"] is None

    discounted_order = coupon_application.reserve_coupon_for_order(
        object(),
        order=order,
        coupon_choice={"mode": "auto"},
        unionid="union_coupon_direct",
        trade_product_id=7,
    )
    assert discounted_order["discount_amount_total"] == 2_000
    assert discounted_order["amount_total"] == 8_000
    assert discounted_order["coupon_claim_id"] == 55

    consumed = coupon_application.consume_coupon_for_paid_order(
        object(),
        out_trade_no="WXP_DIRECT_COUPON",
        provider_total=8_000,
        provider_currency="CNY",
    )
    released = coupon_application.release_coupon_for_order(
        object(),
        out_trade_no="WXP_DIRECT_RELEASE",
        reason="order_closed",
    )

    assert consumed["status"] == "consumed"
    assert released["status"] == "released"
    assert repository.events == ["reserve", "consume", "release"]


def test_public_page_has_no_rollout_unavailable_branch() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "aicrm_next/extensions/commerce/commerce/coupons/templates/coupon_public.html"
    ).read_text(encoding="utf-8")

    assert "state.rollout_enabled is sameas false" not in template
    assert "优惠券暂未开放" not in template
    assert "立即领取" in template
