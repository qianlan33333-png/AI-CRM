from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import aicrm_next.extensions.commerce.commerce.coupons.admin_api as coupon_admin_api
import aicrm_next.extensions.commerce.commerce.coupons.admin_pages as coupon_admin_pages
from aicrm_next.admin_shell.navigation import ADMIN_NAV_GROUPS, ADMIN_ROUTE_REGISTRY


ROOT = Path(__file__).resolve().parents[1]


def _coupon() -> dict:
    return {
        "id": 17,
        "name": "暑期周期商品优惠券",
        "discount_amount_total": 2000,
        "total_issue_limit": 100,
        "per_user_issue_limit": 1,
        "issued_count": 3,
        "used_count": 1,
        "claim_starts_at": "2026-07-15T00:00:00+08:00",
        "claim_ends_at": "2026-07-31T23:59:59+08:00",
        "claim_starts_at_display": "2026-07-15 00:00",
        "claim_ends_at_display": "2026-07-31 23:59",
        "validity_mode": "relative_days",
        "relative_validity_days": 7,
        "use_starts_at": None,
        "use_ends_at": None,
        "instructions": "领取后七个自然日内使用。",
        "status": "published",
        "view_status": "active",
        "product_count": 2,
        "has_standard_product": True,
        "has_service_period": True,
        "products": [
            {
                "target_ref": "prd_standard_opaque",
                "product_type": "standard_product",
                "title": "普通商品",
                "price_cents": 9900,
                "currency": "CNY",
                "status": "active",
            },
            {
                "target_ref": "prd_period_opaque",
                "product_type": "service_period",
                "title": "90 天服务",
                "price_cents": 29900,
                "currency": "CNY",
                "duration_days": 90,
                "status": "active",
            },
        ],
    }


def _claims() -> dict:
    return {
        "ok": True,
        "total": 1,
        "stats": {"issued": 3, "available": 1, "reserved": 1, "consumed": 1, "expired": 0},
        "items": [
            {
                "masked_identity": "微信用户 ab***89",
                "claim_no_masked": "CPN***001",
                "status": "consumed",
                "claimed_at": "2026-07-15T02:00:00+00:00",
                "claimed_at_display": "2026-07-15 10:00",
                "valid_from": "2026-07-14T16:00:00+00:00",
                "valid_from_display": "2026-07-15 00:00",
                "valid_until": "2026-07-21T16:00:00+00:00",
                "valid_until_display": "2026-07-22 00:00",
                "product_title": "90 天服务",
                "order_no_masked": "ORD***002",
                "consumed_at": "2026-07-16T01:00:00+00:00",
                "consumed_at_display": "2026-07-16 09:00",
            }
        ],
    }


class _FakeCouponAdminApplication:
    calls: list[tuple] = []
    actors: list[str] = []

    def __init__(self, *, actor_id: str | None = None) -> None:
        if actor_id is not None:
            self.actors.append(actor_id)

    def list_coupons(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {"ok": True, "items": [_coupon()], "total": 1}

    def get_coupon(self, coupon_id):
        self.calls.append(("get", coupon_id))
        return {"ok": True, "coupon": _coupon()}

    def create_coupon(self, payload):
        self.calls.append(("create", payload))
        return {"ok": True, "coupon": _coupon()}

    def update_coupon(self, coupon_id, payload):
        self.calls.append(("update", coupon_id, payload))
        return {"ok": True, "coupon": _coupon()}

    def delete_coupon(self, coupon_id):
        self.calls.append(("delete", coupon_id))
        return {"ok": True, "deleted": True}

    def publish_coupon(self, coupon_id):
        self.calls.append(("publish", coupon_id))
        return {"ok": True, "coupon": _coupon()}

    def stop_coupon(self, coupon_id):
        self.calls.append(("stop", coupon_id))
        return {"ok": True, "coupon": {**_coupon(), "status": "stopped"}}

    def archive_coupon(self, coupon_id):
        self.calls.append(("archive", coupon_id))
        return {"ok": True, "coupon": {**_coupon(), "status": "archived"}}

    def copy_coupon(self, coupon_id):
        self.calls.append(("copy", coupon_id))
        return {"ok": True, "coupon": {**_coupon(), "id": 18, "status": "draft"}}

    def get_share(self, coupon_id, *, request_base_url):
        self.calls.append(("share", coupon_id, request_base_url))
        return {
            "ok": True,
            "share": {
                "url": f"{request_base_url}/c/summer-17",
                "qr_data_url": "data:image/svg+xml;base64,PHN2Zy8+",
            },
        }

    def list_claims(self, coupon_id, **kwargs):
        self.calls.append(("claims", coupon_id, kwargs))
        return _claims()

    def list_product_options(self, **kwargs):
        self.calls.append(("products", kwargs))
        return {"ok": True, "items": _coupon()["products"], "total": 2}


def _upsert_payload() -> dict:
    return {
        "name": "暑期周期商品优惠券",
        "discount_amount_total": 2000,
        "total_issue_limit": 100,
        "per_user_issue_limit": 1,
        "claim_starts_at": "2026-07-15T00:00:00+08:00",
        "claim_ends_at": "2026-07-31T23:59:59+08:00",
        "validity_mode": "relative_days",
        "use_starts_at": None,
        "use_ends_at": None,
        "relative_validity_days": 7,
        "instructions": "领取后七个自然日内使用。",
        "target_refs": ["prd_standard_opaque", "prd_period_opaque"],
    }


def test_coupon_navigation_is_fourth_transaction_entry() -> None:
    transaction_group = next(group for group in ADMIN_NAV_GROUPS if group["title"] == "交易")
    assert [item["label"] for item in transaction_group["items"]] == ["交易管理", "商品管理", "周期商品管理", "优惠券"]
    assert ADMIN_ROUTE_REGISTRY["api.admin_coupons_page"].path == "/admin/coupons"


def test_coupon_admin_pages_are_split_and_follow_frontend_contract(next_client, monkeypatch) -> None:
    monkeypatch.setattr(coupon_admin_pages, "CouponAdminApplication", _FakeCouponAdminApplication)

    list_page = next_client.get("/admin/coupons")
    new_page = next_client.get("/admin/coupons/new")
    edit_page = next_client.get("/admin/coupons/17/edit")
    data_page = next_client.get("/admin/coupons/17/data")

    for response in (list_page, new_page, edit_page, data_page):
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.headers["X-AICRM-Fallback-Used"] == "false"

    assert '<h1 class="admin-page-title">优惠券管理</h1>' in list_page.text
    assert list_page.text.count('<h1 class="admin-page-title">') == 1
    for header in (
        "优惠券名称",
        "减免金额",
        "适用商品",
        "领取时间（北京时间）",
        "发行 / 使用",
        "状态",
        "操作",
    ):
        assert f"<th>{header}</th>" in list_page.text
    assert "/admin/coupons/17/edit" in list_page.text
    assert "/admin/coupons/17/data" in list_page.text
    assert 'data-coupon-action="share"' in list_page.text

    for label in ("基本信息", "适用商品", "领取与有效期", "使用说明"):
        assert label in new_page.text
    assert "无门槛固定减免券" in new_page.text
    assert "使用门槛" not in new_page.text
    assert "商品 ID" not in new_page.text
    assert 'name="product_id"' not in new_page.text
    assert 'data-product-type="standard_product"' in new_page.text
    assert 'data-product-type="service_period"' in new_page.text
    assert "target_refs: Array.from(selectedProducts.keys())" in new_page.text
    assert "window.AdminApi" in new_page.text
    assert "requestJson" in new_page.text
    assert "discount_amount_total" in new_page.text
    assert "relative_validity_days" in new_page.text

    assert "暑期周期商品优惠券" in edit_page.text
    assert "普通商品" in edit_page.text
    assert "90 天服务" in edit_page.text
    assert "领取与使用明细" in data_page.text
    assert "微信用户 ab***89" in data_page.text
    assert "ORD***002" in data_page.text
    assert "2026-07-15 00:00" in list_page.text
    assert "2026-07-31 23:59" in list_page.text
    for header in ("领取时间（北京时间）", "有效期（北京时间）", "使用时间（北京时间）"):
        assert f"<th>{header}</th>" in data_page.text
    assert "2026-07-15 10:00" in data_page.text
    assert "2026-07-22 00:00" in data_page.text
    assert "2026-07-16 09:00" in data_page.text
    assert "2026-07-15T02:00:00+00:00" not in data_page.text


def test_coupon_admin_api_is_thin_application_adapter(next_client, monkeypatch) -> None:
    _FakeCouponAdminApplication.calls = []
    _FakeCouponAdminApplication.actors = []
    monkeypatch.setattr(coupon_admin_api, "CouponAdminApplication", _FakeCouponAdminApplication)

    listed = next_client.get("/api/admin/coupons?limit=20&offset=0&q=暑期&status=active")
    created = next_client.post("/api/admin/coupons", json=_upsert_payload())
    updated = next_client.put("/api/admin/coupons/17", json=_upsert_payload())
    products = next_client.get("/api/admin/coupons/product-options?product_type=service_period&limit=20&offset=0")
    claims = next_client.get("/api/admin/coupons/17/claims?limit=20&offset=0")
    share = next_client.get("/api/admin/coupons/17/share")
    copied = next_client.post("/api/admin/coupons/17/copy")

    assert listed.status_code == 200
    assert created.status_code == 201
    assert updated.status_code == 200
    assert products.json()["total"] == 2
    assert claims.json()["stats"]["reserved"] == 1
    assert share.json()["share"]["url"].endswith("/c/summer-17")
    assert share.json()["share"]["qr_data_url"].startswith("data:image/svg+xml")
    assert copied.status_code == 201
    assert all(response.headers["X-AICRM-Real-External-Call-Executed"] == "false" for response in (listed, created, updated, products, claims, share, copied))

    call_names = [call[0] for call in _FakeCouponAdminApplication.calls]
    assert call_names == ["list", "create", "update", "products", "claims", "share", "copy"]
    assert _FakeCouponAdminApplication.actors == ["admin_console", "admin_console", "admin_console"]


def test_coupon_admin_actor_uses_authenticated_context_without_auth_implementation_dependency() -> None:
    request = SimpleNamespace(
        state=SimpleNamespace(
            auth_context={
                "admin_user_id": "admin-17",
                "principal_id": "principal-fallback",
                "userid": "wecom-fallback",
            }
        )
    )
    assert coupon_admin_api._admin_actor_id(request) == "admin-17"

    request.state.auth_context = SimpleNamespace(
        admin_user_id="",
        principal_id="service:coupon-admin",
        userid="wecom-fallback",
    )
    assert coupon_admin_api._admin_actor_id(request) == "service:coupon-admin"

    request.state.auth_context = {"userid": "wecom-admin"}
    assert coupon_admin_api._admin_actor_id(request) == "wecom-admin"

    request.state.auth_context = None
    assert coupon_admin_api._admin_actor_id(request) == "admin_console"


def test_coupon_share_ignores_untrusted_forwarded_host(next_client, monkeypatch) -> None:
    _FakeCouponAdminApplication.calls = []
    monkeypatch.setattr(coupon_admin_api, "CouponAdminApplication", _FakeCouponAdminApplication)
    for setting in ("AICRM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(setting, raising=False)

    response = next_client.get(
        "/api/admin/coupons/17/share",
        headers={
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 200
    assert response.json()["share"]["url"] == "http://testserver/c/summer-17"
    assert "attacker.example" not in response.text


def test_coupon_share_requires_configured_base_url_in_canonical_production(next_client, monkeypatch) -> None:
    _FakeCouponAdminApplication.calls = []
    monkeypatch.setattr(coupon_admin_api, "CouponAdminApplication", _FakeCouponAdminApplication)
    monkeypatch.setenv("APP_ENV", "production")
    for setting in ("AICRM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(setting, raising=False)

    response = next_client.get(
        "/api/admin/coupons/17/share",
        headers={"Host": "attacker.example"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "public_base_url_required"
    assert _FakeCouponAdminApplication.calls == []


def test_coupon_share_rejects_insecure_production_base_url(next_client, monkeypatch) -> None:
    _FakeCouponAdminApplication.calls = []
    monkeypatch.setattr(coupon_admin_api, "CouponAdminApplication", _FakeCouponAdminApplication)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "http://coupon.example.test")

    response = next_client.get("/api/admin/coupons/17/share")

    assert response.status_code == 400
    assert response.json()["detail"] == "public_base_url_https_required"
    assert _FakeCouponAdminApplication.calls == []


def test_coupon_admin_http_layers_do_not_import_repositories_or_embed_sql() -> None:
    for relative_path in (
        "aicrm_next/extensions/commerce/commerce/coupons/admin_api.py",
        "aicrm_next/extensions/commerce/commerce/coupons/admin_pages.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        lowered = source.lower()
        assert "from .repo import" not in lowered
        assert "from aicrm_next.extensions.commerce.service_period" not in lowered
        assert "select " not in lowered
        assert "insert " not in lowered
        assert "update " not in lowered
        assert "delete from" not in lowered
        assert "couponadminapplication" in lowered
