from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from aicrm_next.admin_config.api_docs_view_model import build_api_docs_view_model
from aicrm_next.extensions.commerce.commerce.admin_exports import reset_export_jobs_for_tests
from aicrm_next.extensions.commerce.commerce.admin_unified_orders import list_orders
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.crm.customer_read_model import admin_business_profile, api as customer_api
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    reset_export_jobs_for_tests()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "admin-p0-commerce-api")
    return TestClient(create_app(), raise_server_exceptions=False)


def _paths(view_model: dict) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for group in view_model["endpoint_groups"]:
        for endpoint in group.get("endpoints") or []:
            result[(endpoint["method"], endpoint["path"])] = group["title"]
    return result


def test_admin_p0_routes_are_in_api_docs() -> None:
    app = create_app()
    view_model = build_api_docs_view_model(routes=app.routes)
    paths = _paths(view_model)
    assert view_model["endpoint_count"] > 80
    assert view_model["markdown_data"]["full"]
    expected = {
        ("GET", "/api/admin/orders"): "交易 / 商品",
        ("GET", "/api/admin/orders/{order_no}"): "交易 / 商品",
        ("GET", "/api/admin/orders/{order_no}/items"): "交易 / 商品",
        ("GET", "/api/admin/payments"): "交易 / 商品",
        ("GET", "/api/admin/refunds"): "交易 / 商品",
        ("POST", "/api/admin/refunds"): "交易 / 商品",
        ("GET", "/api/admin/customers/{external_userid}/orders"): "客户 / 身份 / 侧边栏",
        ("GET", "/api/admin/customers/{external_userid}/commerce-summary"): "客户 / 身份 / 侧边栏",
        ("GET", "/api/admin/customers/{unionid}/business-profile"): "客户 / 身份 / 侧边栏",
        ("GET", "/api/admin/identity/resolve"): "客户 / 身份 / 侧边栏",
        ("GET", "/api/admin/identity/links/{identity_key}"): "客户 / 身份 / 侧边栏",
        ("GET", "/api/admin/webhooks/events"): "认证 / 回调",
        ("POST", "/api/admin/webhooks/replay"): "认证 / 回调",
        ("POST", "/api/admin/exports"): "系统 / MCP",
        ("GET", "/api/admin/exports/{job_id}"): "系统 / MCP",
    }
    for endpoint, group_title in expected.items():
        assert paths[endpoint] == group_title
        assert endpoint[1] in view_model["markdown_data"]["full"]


def test_unified_orders_list_detail_and_items(monkeypatch) -> None:
    client = _client(monkeypatch)
    wechat = client.get("/api/admin/orders?provider=wechat").json()
    alipay = client.get("/api/admin/orders?provider=alipay").json()
    merged = client.get("/api/admin/orders?provider=all").json()
    assert wechat["ok"] is True
    assert alipay["ok"] is True
    assert merged["ok"] is True
    assert merged["providers"] == ["wechat", "alipay", "wechat_shop"]
    item = merged["items"][0]
    assert {"id", "provider", "customer", "order_no", "amount_total", "can_refund", "payer_name"}.issubset(item)
    assert "name" in item["customer"]

    missing = client.get("/api/admin/orders/not_found_order")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"

    items = client.get("/api/admin/orders/order_masked_001/items?provider=wechat").json()
    assert items["ok"] is True
    assert items["total"] == 1
    assert items["items"][0]["quantity"] == 1
    assert items["items"][0]["order_no"] == "order_masked_001"


def test_unified_orders_paginate_by_created_at_not_paid_at(monkeypatch) -> None:
    rows_by_provider = {
        "wechat": [
            {
                "order_no": "wx-newer-001",
                "provider": "wechat",
                "created_at": "2026-06-26 21:41:21",
                "paid_at": "2026-06-26 21:41:21",
                "amount_total": 99900,
                "status": "paid",
            },
            {
                "order_no": "wx-newer-002",
                "provider": "wechat",
                "created_at": "2026-06-26 20:39:28",
                "paid_at": "2026-06-26 20:39:28",
                "amount_total": 99900,
                "status": "paid",
            },
        ],
        "alipay": [],
        "wechat_shop": [
            {
                "order_no": "shop-old-created-late-paid",
                "provider": "wechat_shop",
                "created_at": "2026-06-18 23:07:46",
                "paid_at": "2026-06-30 02:32:35",
                "amount_total": 990,
                "status": "paid",
            }
        ],
    }

    def fake_execute(self, filters, *, limit=50, offset=0):
        rows = rows_by_provider[self.provider]
        return {
            "ok": True,
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    monkeypatch.setattr(
        "aicrm_next.extensions.commerce.commerce.admin_unified_orders.CommerceAdminTransactionListReadModel.execute",
        fake_execute,
    )

    first_page = list_orders(provider="all", limit=2, offset=0)
    second_page = list_orders(provider="all", limit=2, offset=2)

    assert [item["order_no"] for item in first_page["items"]] == ["wx-newer-001", "wx-newer-002"]
    assert [item["order_no"] for item in second_page["items"]] == ["shop-old-created-late-paid"]


def test_payments_and_refunds(monkeypatch) -> None:
    client = _client(monkeypatch)
    payments = client.get("/api/admin/payments").json()
    assert payments["ok"] is True
    assert "payments" in payments
    assert {"provider", "order_no", "transaction_id", "payment_status", "customer"}.issubset(payments["payments"][0])

    refunds = client.get("/api/admin/refunds").json()
    assert refunds["ok"] is True
    assert refunds["refunds"] == []

    alipay_refund = client.post("/api/admin/refunds", json={"provider": "alipay", "order_no": "order_fake_0003"})
    assert alipay_refund.status_code == 400
    assert alipay_refund.json()["error_code"] == "provider_refund_not_supported"

    wechat_refund = client.post(
        "/api/admin/refunds",
        json={
            "provider": "wechat",
            "order_no": "order_masked_001",
            "refund_amount_total": 100,
            "reason": "客户主动申请退款",
            "transaction_id_confirmation": "transaction_masked_001",
            "checked": True,
            "operator": "tester",
        },
    )
    assert wechat_refund.status_code == 200
    payload = wechat_refund.json()
    assert payload["ok"] is True
    assert payload["refund"]["status"] == "requested"
    assert payload["source_status"] == "next_admin_refund_request"


def test_product_share_uses_real_qr_svg(monkeypatch) -> None:
    client = _client(monkeypatch)
    products = client.get("/api/admin/wechat-pay/products").json()
    product = products["items"][0]

    payload = client.get(f"/api/admin/wechat-pay/products/{product['id']}/share").json()

    share = payload["share"]
    assert share["url"].endswith(f"/pay/{product['product_code']}")
    assert share["qr_data_url"].startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(share["qr_data_url"].split(",", 1)[1]).decode("utf-8")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "<path" in svg
    assert "PRODUCT" not in svg
    assert product["product_code"] not in svg

    material = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "share_material_product",
            "title": "带素材商品",
            "price_cents": 100,
            "enabled": True,
            "status": "active",
            "buy_button_text": "立即购买",
            "slices": [{"image_library_id": 1, "image_url": "data:image/png;base64,YQ==", "sort_order": 1}],
        },
    ).json()["product"]
    material_share = client.get(f"/api/admin/wechat-pay/products/{material['id']}/share").json()["share"]
    assert material_share["url"].endswith("/p/share_material_product")


def test_customer_business_profile_orders_and_summary(monkeypatch) -> None:
    client = _client(monkeypatch)
    profile = client.get("/api/admin/customers/union_customer_001/business-profile").json()
    assert profile["ok"] is True
    assert profile["unionid"] == "union_customer_001"
    assert set(profile["business_profile"]) == {"tags", "recent_messages", "questionnaire_answers"}
    assert profile["counts"]["recent_messages"] <= 20
    assert isinstance(profile["business_profile"]["tags"], list)
    assert profile["business_profile"]["questionnaire_answers"][0]["question"]
    assert profile["business_profile"]["questionnaire_answers"][0]["answer"]
    for forbidden in ("orders", "commerce_summary", "tasks", "coupons", "entitlements"):
        assert forbidden not in profile["business_profile"]

    orders = client.get("/api/admin/customers/wx_ext_001/orders").json()
    assert orders["ok"] is True
    assert "orders" in orders

    summary = client.get("/api/admin/customers/wx_ext_001/commerce-summary").json()
    assert summary["ok"] is True
    assert "summary" in summary
    assert {"order_count", "paid_order_count", "total_paid_amount", "providers"}.issubset(summary["summary"])


def test_customer_questionnaire_answers_fallback_reads_submissions(monkeypatch) -> None:
    class FakeSidebarRepo:
        def list_questionnaire_answers(self, *, external_userid: str, mobile: str = ""):
            assert external_userid == "wx_ext_questionnaire"
            return [
                {
                    "submission_id": 462,
                    "questionnaire_id": 12,
                    "questionnaire_title": "沙龙商业落地需求调研",
                    "submitted_at": "2026-06-27 07:44:14+08:00",
                    "question_id": "q_industry",
                    "question": "你目前所处的行业是？",
                    "selected_option_texts_snapshot": ["健康/养生/大健康"],
                    "text_value": "",
                },
                {
                    "submission_id": 462,
                    "questionnaire_id": 12,
                    "questionnaire_title": "沙龙商业落地需求调研",
                    "submitted_at": "2026-06-27 07:44:14+08:00",
                    "question_id": "q_mobile",
                    "question": "手机号",
                    "selected_option_texts_snapshot": [],
                    "text_value": "15820198818",
                },
            ]

    monkeypatch.setattr(customer_api, "SidebarV2SqlRepository", FakeSidebarRepo)
    monkeypatch.setattr(admin_business_profile, "SidebarV2SqlRepository", FakeSidebarRepo)

    answers = customer_api._profile_questionnaire_answers_from_submissions(
        external_userid="wx_ext_questionnaire",
        mobile="",
    )
    business_answers = admin_business_profile._questionnaire_answers_from_submissions(
        external_userid="wx_ext_questionnaire",
        mobile="",
    )

    assert [item["answer"] for item in answers] == ["健康/养生/大健康", "15820198818"]
    assert business_answers[0]["questionnaire_title"] == "沙龙商业落地需求调研"
    assert business_answers[1]["answer"] == "15820198818"


def test_identity_admin_resolve_and_links(monkeypatch) -> None:
    client = _client(monkeypatch)
    resolved = client.get("/api/admin/identity/resolve?external_userid=wx_ext_001&transaction_id=tx_ignored").json()
    assert resolved["ok"] is True
    assert resolved["identity"]["person_id"] == "person_001"
    assert resolved["warnings"]

    links = client.get("/api/admin/identity/links/13800138000").json()
    assert links["ok"] is True
    assert links["links"]["mobile"] == "13800138000"

    missing = client.get("/api/admin/identity/links/not_found_identity")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"


def test_webhooks_and_exports(monkeypatch) -> None:
    client = _client(monkeypatch)
    events = client.get("/api/admin/webhooks/events").json()
    assert events["ok"] is True
    assert "events" in events

    replay = client.post("/api/admin/webhooks/replay", json={"source": "wechat-pay", "event_id": "evt_fixture", "operator": "tester"}).json()
    assert replay["ok"] is True
    assert replay["dry_run"] is True

    export = client.post("/api/admin/exports", json={"resource": "orders", "format": "csv", "filters": {}, "operator": "tester"}).json()
    assert export["ok"] is True
    assert export["job"]["status"] == "completed"
    result = client.get(export["job"]["download_url"]).json()
    assert result["ok"] is True
    assert result["content_text"]
    assert result["content_type"] == "text/csv; charset=utf-8"

    missing = client.get("/api/admin/exports/exp_missing")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"
