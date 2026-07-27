from __future__ import annotations

import pytest

import aicrm_next.extensions.commerce.commerce.application as commerce_application
import aicrm_next.extensions.commerce.public_product.service as public_product_service
from aicrm_next.extensions.commerce.commerce.application import (
    CheckoutCommand,
    DeleteProductCommand,
    GetProductQuery,
    GetPublicProductQuery,
    NotifyPaymentCommand,
    SetProductEnabledCommand,
    UpsertProductCommand,
)
from aicrm_next.extensions.commerce.commerce.dto import CheckoutRequest, PaymentNotifyRequest, ProductUpsertRequest
from aicrm_next.extensions.commerce.commerce.repo import InMemoryCommerceRepository, reset_commerce_fixture_state
from aicrm_next.platform.shared.errors import ContractError


def _product_payload(**overrides) -> dict:
    payload = {
        "product_code": "next_course_001",
        "title": "Next 私域成交课",
        "description": "Next commerce product fixture",
        "price_cents": 19900,
        "enabled": True,
        "status": "active",
        "page_slug": "next-course-001",
        "buy_button_text": "立即报名",
        "completion_redirect_enabled": False,
        "completion_redirect_url": "",
    }
    payload.update(overrides)
    return payload


def _assert_next_payment_contract(response) -> dict:
    payload = response.json()
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "x-aicrm-compatibility-facade" not in response.headers
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_request_executed"] is False
    return payload


def _listed_product(repo: InMemoryCommerceRepository, product_code: str) -> dict:
    payload = repo.list_products(limit=100, offset=0)
    return next(item for item in payload["items"] if item["product_code"] == product_code)


def test_admin_product_api_uses_next_fixture_routes(next_client):
    reset_commerce_fixture_state()

    created = next_client.post("/api/admin/wechat-pay/products", json=_product_payload())
    assert created.status_code == 200
    created_payload = _assert_next_payment_contract(created)
    product = created_payload["product"]
    assert product["product_code"] == "next_course_001"
    assert product["enabled"] is True

    listed = next_client.get("/api/admin/wechat-pay/products")
    assert listed.status_code == 200
    payload = _assert_next_payment_contract(listed)
    assert payload["ok"] is True
    assert "next_course_001" in [item["product_code"] for item in payload["items"]]

    detail = next_client.get(f"/api/admin/wechat-pay/products/{product['id']}")
    assert detail.status_code == 200
    assert _assert_next_payment_contract(detail)["product"]["title"] == "Next 私域成交课"

    updated = next_client.put(
        f"/api/admin/wechat-pay/products/{product['id']}",
        json=_product_payload(title="Next 私域成交课升级版", completion_redirect_enabled=True, completion_redirect_url="/paid"),
    )
    assert updated.status_code == 200
    updated_product = _assert_next_payment_contract(updated)["product"]
    assert updated_product["title"] == "Next 私域成交课升级版"
    assert updated_product["completion_redirect"]["enabled"] is True
    assert updated_product["completion_action"] == {"type": "redirect", "redirect_url": "/paid"}

    disabled = next_client.post(f"/api/admin/wechat-pay/products/{product['id']}/disable")
    assert disabled.status_code == 200
    assert _assert_next_payment_contract(disabled)["product"]["enabled"] is False

    enabled = next_client.post(f"/api/admin/wechat-pay/products/{product['id']}/enable")
    assert enabled.status_code == 200
    assert _assert_next_payment_contract(enabled)["product"]["enabled"] is True

    copied = next_client.post(f"/api/admin/wechat-pay/products/{product['id']}/copy")
    assert copied.status_code == 201
    copied_product = _assert_next_payment_contract(copied)["product"]
    assert copied_product["id"] != product["id"]
    assert copied_product["status"] == "draft"
    assert copied_product["completion_redirect_url"] == "/paid"

    deleted = next_client.delete(f"/api/admin/wechat-pay/products/{copied_product['id']}")
    assert deleted.status_code == 200
    assert _assert_next_payment_contract(deleted)["deleted"] is True


def test_next_product_commands_preserve_delete_and_redirect_contracts():
    repo = InMemoryCommerceRepository()
    created = UpsertProductCommand(repo)(ProductUpsertRequest(**_product_payload(completion_redirect_enabled=True, completion_redirect_url="https://example.com/paid")))["product"]

    assert GetProductQuery(repo)(created["id"])["product"]["completion_redirect"]["url"] == "https://example.com/paid"

    disabled = SetProductEnabledCommand(repo)(created["id"], enabled=False)["product"]
    assert disabled["status"] == "disabled"

    copied = repo.copy_product(created["id"])
    assert copied["status"] == "draft"
    assert copied["completion_redirect_url"] == "https://example.com/paid"

    with pytest.raises(ContractError, match="已有订单"):
        DeleteProductCommand(repo)("prod_000")

    assert DeleteProductCommand(repo)(created["id"])["deleted"] is True


def test_product_list_sold_count_follows_paid_orders_and_refund_requests():
    repo = InMemoryCommerceRepository()

    seeded = _listed_product(repo, "course_masked_001")
    assert seeded["paid_order_count"] == 1
    assert seeded["refund_order_count"] == 0
    assert seeded["sold_count"] == 1

    product = UpsertProductCommand(repo)(
        ProductUpsertRequest(
            **_product_payload(
                product_code="sales_count_product",
                title="销量统计商品",
                page_slug="sales-count-product",
            )
        )
    )["product"]
    assert product["sold_count"] == 0

    checkout = CheckoutCommand("wechat", repo)(
        CheckoutRequest(
            product_code="sales_count_product",
            buyer_identity={"mobile": "13800138000", "openid": "op_sales_count"},
            return_url="/pay/sales-count-product",
        )
    )
    assert _listed_product(repo, "sales_count_product")["sold_count"] == 0

    NotifyPaymentCommand("wechat", repo)(
        PaymentNotifyRequest(
            order_no=checkout["order_no"],
            payment_status="paid",
            transaction_id="transaction_sales_count",
            provider_payload={"notify_id": "notify_sales_count"},
        )
    )
    paid = _listed_product(repo, "sales_count_product")
    assert paid["paid_order_count"] == 1
    assert paid["refund_order_count"] == 0
    assert paid["sold_count"] == 1

    repo.request_refund(
        "wechat",
        checkout["order_no"],
        {
            "out_refund_no": "refund_sales_count",
            "amount": {"refund": 100},
        },
    )
    refunded = _listed_product(repo, "sales_count_product")
    assert refunded["paid_order_count"] == 1
    assert refunded["refund_order_count"] == 1
    assert refunded["sold_count"] == 0


def test_product_completion_target_admin_update_preview_and_order_payload():
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _order_payload

    repo = InMemoryCommerceRepository()
    target = {
        "enabled": True,
        "target_type": "mini_program",
        "open_strategy": "wechat_open_tag",
        "h5_url": "/paid-fallback",
        "fallback_url": "/paid-fallback",
        "mini_program": {
            "username": "gh_paid_target",
            "path": "/pages/paid/index",
            "query": "from=pay",
            "env_version": "release",
        },
    }
    created = UpsertProductCommand(repo)(
        ProductUpsertRequest(**_product_payload(product_code="target_product_001", page_slug="target-product-001", completion_target=target))
    )["product"]
    assert created["completion_target"]["target_type"] == "mini_program"
    assert created["completion_action"]["type"] == "mini_program"

    updated = UpsertProductCommand(repo)(
        ProductUpsertRequest(
            **_product_payload(
                product_code="target_product_001",
                page_slug="target-product-001",
                title="完成目标商品更新",
                completion_target={**target, "fallback_url": "/updated-fallback"},
            )
        ),
        product_id=created["id"],
    )["product"]
    assert updated["completion_target"]["fallback_url"] == "/updated-fallback"

    preview = GetPublicProductQuery(repo)("target-product-001")["product"]
    assert preview["completion_target"]["mini_program"]["username"] == "gh_paid_target"

    order = _order_payload(
        {
            "out_trade_no": "WXP_TARGET",
            "product_code": "target_product_001",
            "product_name": "完成目标商品更新",
            "amount_total": 19900,
            "currency": "CNY",
            "status": "paid",
            "trade_state": "SUCCESS",
        },
        completion_redirect=updated,
        lead_qr={"qr_url": "https://example.com/lead.png"},
    )
    assert order["completion_target"]["target_type"] == "mini_program"
    assert order["completion_action"] == {"type": "mini_program", "navigation_target": order["completion_target"]}
    assert "lead_qr" not in order


def test_product_dynamic_url_link_completion_target_order_payload():
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _order_payload

    repo = InMemoryCommerceRepository()
    target = {
        "enabled": True,
        "target_type": "url_link",
        "open_strategy": "url_link",
        "h5_url": "/paid-fallback",
        "url_link": {
            "enabled": True,
            "source_url": "https://ip.lhbl.com.cn/api/wxlink?from=qianlan_pay",
            "response_url_key": "url_link",
        },
    }
    created = UpsertProductCommand(repo)(
        ProductUpsertRequest(**_product_payload(product_code="dynamic_url_link_001", page_slug="dynamic-url-link-001", completion_target=target))
    )["product"]
    assert created["completion_target"]["target_type"] == "url_link"
    assert created["completion_redirect_enabled"] is False
    assert created["completion_action"]["type"] == "url_link"

    order = _order_payload(
        {
            "out_trade_no": "WXP_DYNAMIC_URL_LINK",
            "product_code": "dynamic_url_link_001",
            "product_name": "动态 URL Link 商品",
            "amount_total": 19900,
            "currency": "CNY",
            "status": "paid",
            "trade_state": "SUCCESS",
        },
        completion_redirect=created,
        lead_qr={"qr_url": "https://example.com/lead.png"},
    )
    assert order["completion_action"]["type"] == "url_link"
    assert order["completion_action"]["navigation_target"]["url_link"]["source_url"].endswith("from=qianlan_pay")
    assert "lead_qr" not in order


def test_product_legacy_completion_redirect_auto_builds_h5_completion_target():
    repo = InMemoryCommerceRepository()
    created = UpsertProductCommand(repo)(
        ProductUpsertRequest(
            **_product_payload(
                product_code="legacy_target_001",
                page_slug="legacy-target-001",
                completion_redirect_enabled=True,
                completion_redirect_url="/paid",
            )
        )
    )["product"]

    assert created["completion_redirect"] == {"enabled": True, "url": "/paid"}
    assert created["completion_target"]["enabled"] is True
    assert created["completion_target"]["target_type"] == "h5"
    assert created["completion_target"]["h5_url"] == "/paid"


def test_completion_redirect_validation_blocks_unsafe_url():
    repo = InMemoryCommerceRepository()

    with pytest.raises(ContractError, match="completion_redirect_url"):
        UpsertProductCommand(repo)(
            ProductUpsertRequest(
                **_product_payload(
                    product_code="bad_redirect_001",
                    completion_redirect_enabled=True,
                    completion_redirect_url="javascript:alert(1)",
                )
            )
        )


def test_completion_target_validation_blocks_unsafe_values():
    repo = InMemoryCommerceRepository()

    with pytest.raises(ContractError, match="mini_program.path"):
        UpsertProductCommand(repo)(
            ProductUpsertRequest(
                **_product_payload(
                    product_code="bad_target_path",
                    completion_target={
                        "enabled": True,
                        "target_type": "mini_program",
                        "mini_program": {"username": "gh_bad", "path": "pages bad", "env_version": "release"},
                    },
                )
            )
        )

    with pytest.raises(ContractError, match="env_version"):
        UpsertProductCommand(repo)(
            ProductUpsertRequest(
                **_product_payload(
                    product_code="bad_target_env",
                    completion_target={
                        "enabled": True,
                        "target_type": "mini_program",
                        "mini_program": {"username": "gh_bad", "path": "/pages/index", "env_version": "gray"},
                    },
                )
            )
        )

    with pytest.raises(ContractError, match="fallback_url"):
        UpsertProductCommand(repo)(
            ProductUpsertRequest(
                **_product_payload(
                    product_code="bad_target_url",
                    completion_target={"enabled": True, "target_type": "h5", "h5_url": "/ok", "fallback_url": "javascript:alert(1)"},
                )
            )
        )


def test_public_product_and_checkout_routes_are_next_owned_and_no_real_payment(next_client, monkeypatch):
    reset_commerce_fixture_state()

    checkout_repo = InMemoryCommerceRepository()
    UpsertProductCommand(checkout_repo)(
        ProductUpsertRequest(
            **_product_payload(
                product_code="test-product-next-fixture",
                title="测试商品",
                description="测试商品",
                price_cents=12900,
                page_slug="test-product-next-fixture",
            )
        )
    )
    monkeypatch.setattr(commerce_application, "build_commerce_repository", lambda: checkout_repo)
    monkeypatch.setattr(public_product_service, "build_commerce_repository", lambda: checkout_repo)

    product = next_client.get("/api/products/test-product")
    assert product.status_code == 200
    assert product.json()["product"]["product_code"] == "test-product"

    page = next_client.get("/p/test-product")
    assert page.status_code == 200
    assert "测试商品" in page.text

    checkout = next_client.post(
        "/api/checkout/wechat",
        json={
            "product_code": "test-product-next-fixture",
            "quantity": 1,
            "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001", "openid": "op_fixture"},
            "return_url": "/pay/test-product-next-fixture",
        },
    )
    assert checkout.status_code == 200
    payload = checkout.json()
    assert checkout.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert checkout.headers["X-AICRM-Real-External-Call-Executed"] == "false"
    assert checkout.headers["X-AICRM-Payment-Request-Executed"] == "false"
    assert payload["payment_provider"] == "wechat"
    assert payload["amount_cents"] == 12900
    assert payload["fake_payment"] is True
    assert payload["fallback_used"] is False
    assert payload["payment_request_executed"] is False
    assert payload["side_effect_safety"]["real_wechat_pay_executed"] is False
