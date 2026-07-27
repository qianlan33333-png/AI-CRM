from __future__ import annotations

import json

import aicrm_next.extensions.commerce.commerce.api as commerce_api
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state


def _product_payload(**overrides) -> dict:
    payload = {
        "product_code": "contract_product_001",
        "product": {"code": "contract_product_001"},
        "title": "契约测试商品",
        "description": "商品管理二级页契约测试",
        "price_cents": 19900,
        "enabled": True,
        "status": "active",
        "page_slug": "contract-product-001",
        "buy_button_text": "立即购买",
        "require_mobile": False,
        "lead_channel_id": None,
        "completion_redirect_enabled": False,
        "completion_redirect_url": "",
        "completion_target": {
            "enabled": False,
            "target_type": "h5",
            "open_strategy": "h5_redirect",
            "h5_url": "",
            "fallback_url": "",
            "url_link": {"enabled": False, "source_url": "", "response_url_key": "url_link"},
        },
        "slices": [],
    }
    payload.update(overrides)
    if "product_code" in overrides:
        payload["product"] = {"code": overrides["product_code"]}
    return payload


def _assert_next_headers(response) -> None:
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"


def _assert_admin_response(response, expected_status: int = 200) -> dict:
    assert response.status_code == expected_status
    _assert_next_headers(response)
    return response.json()


def test_wechat_product_admin_pages_keep_existing_routes_and_sections(next_client) -> None:
    reset_commerce_fixture_state()

    list_page = next_client.get("/admin/wechat-pay/products")
    new_page = next_client.get("/admin/wechat-pay/products/new")
    edit_page = next_client.get("/admin/wechat-pay/products/prod_000/edit")

    for response in (list_page, new_page, edit_page):
        assert response.status_code == 200
        _assert_next_headers(response)

    assert "已售卖数量" in list_page.text
    assert "<th>贴图</th>" not in list_page.text
    assert 'data-mode="edit"' in edit_page.text
    assert "product-editor-topbar" not in edit_page.text
    assert "product-editor-crumb" not in edit_page.text
    for text in ("商品管理", "售卖信息", "页面素材", "购买后动作", "外部推送"):
        assert text in edit_page.text
    assert 'id="panel-sale" data-product-panel-content="sale"' in edit_page.text
    assert 'class="panel active" id="panel-sale"' in edit_page.text
    assert 'data-product-panel="sale"' in edit_page.text
    assert 'data-product-panel="media"' in edit_page.text
    assert 'data-product-panel="after"' in edit_page.text
    assert 'data-product-panel="push"' in edit_page.text
    assert 'id="saveCurrentPanelBtn"' in edit_page.text
    assert 'let activeProductPanel = "sale";' in edit_page.text
    assert "image_upload_client.js" in edit_page.text
    assert "prepareImageForUpload(file)" in edit_page.text
    assert "prepared.file" in edit_page.text
    assert 'payload.ok === false' in edit_page.text
    assert "saveExternalPushOnly" in edit_page.text
    assert "请先保存商品" in edit_page.text
    assert "product-module" not in edit_page.text
    assert "saleInfoModule" not in edit_page.text
    assert "mediaModule" not in edit_page.text
    assert "afterActionModule" not in edit_page.text
    assert "externalPushSection" not in edit_page.text


def test_wechat_product_admin_page_removes_old_explanatory_copy(next_client) -> None:
    reset_commerce_fixture_state()

    response = next_client.get("/admin/wechat-pay/products/prod_000/edit")
    assert response.status_code == 200
    text = response.text

    forbidden_copy = [
        "只保留影响用户下单的核心字段",
        "商品页长图切片。上传、排序、删除集中在这里。",
        "支持 PNG / JPG，最多 10 张；拖拽排序后保存生效。",
        "开关开启后，支付后的引流或跳转配置才会生效。",
        "用户支付成功后看到已绑定的引流渠道码。",
        "用户完成报名/支付后直接进入指定链接。",
        "支付成功后直接进入该 H5 地址。",
        "支付成功后先访问该接口，从 JSON 里取微信官方 URL Link 后再跳转。",
        "开关开启后，订单 webhook 外推才会生效。",
        "测试前会先保存当前外推配置。",
        "高级参数",
        "对应接口",
        "这里保留",
        "这里不混入",
    ]
    for copy in forbidden_copy:
        assert copy not in text


def test_wechat_product_admin_api_contract_routes_remain_available(next_client, monkeypatch) -> None:
    reset_commerce_fixture_state()
    monkeypatch.setattr(
        commerce_api,
        "send_product_external_push_test",
        lambda product_id: {"delivery": {"status": "retrying", "delivery_id": f"test_{product_id}"}},
    )

    listed = _assert_admin_response(next_client.get("/api/admin/wechat-pay/products?limit=100"))
    assert listed["ok"] is True
    listed_product = listed["items"][0]
    assert {"paid_order_count", "refund_order_count", "sold_count", "slice_count"}.issubset(listed_product)

    created = _assert_admin_response(next_client.post("/api/admin/wechat-pay/products", json=_product_payload()))
    product = created["product"]
    product_id = product["id"]

    detail = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{product_id}"))
    assert detail["product"]["product_code"] == "contract_product_001"

    updated = _assert_admin_response(
        next_client.put(
            f"/api/admin/wechat-pay/products/{product_id}",
            json=_product_payload(title="契约测试商品更新", price_cents=29900, status="draft", enabled=False),
        )
    )
    assert updated["product"]["title"] == "契约测试商品更新"

    enabled = _assert_admin_response(next_client.post(f"/api/admin/wechat-pay/products/{product_id}/enable"))
    assert enabled["product"]["enabled"] is True

    disabled = _assert_admin_response(next_client.post(f"/api/admin/wechat-pay/products/{product_id}/disable"))
    assert disabled["product"]["enabled"] is False

    copied = _assert_admin_response(next_client.post(f"/api/admin/wechat-pay/products/{product_id}/copy"), expected_status=201)
    copied_product_id = copied["product"]["id"]

    share = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{product_id}/share"))
    assert share["share"]["url"].endswith("/pay/contract_product_001")

    material_product = _assert_admin_response(
        next_client.post(
            "/api/admin/wechat-pay/products",
            json=_product_payload(
                product_code="contract_product_material",
                slices=[{"image_library_id": 11, "sort_order": 1}],
            ),
        )
    )["product"]
    material_share = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{material_product['id']}/share"))
    assert material_share["share"]["url"].endswith("/p/contract_product_material")

    lead_channels = _assert_admin_response(next_client.get("/api/admin/wechat-pay/products/lead-channels"))
    assert lead_channels["items"]

    external_push = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{product_id}/external-push"))
    assert external_push["config"]["enabled"] is False

    saved_push = _assert_admin_response(
        next_client.put(
            f"/api/admin/wechat-pay/products/{product_id}/external-push",
            json={"enabled": True, "webhook_url": "https://example.com/hook", "push_type": "paid_notify", "custom_params": {"source": "contract"}},
        )
    )
    assert saved_push["config"]["enabled"] is True

    push_test = _assert_admin_response(next_client.post("/api/admin/wechat-pay/products/1/external-push/test"))
    assert push_test["result"]["delivery"]["delivery_id"] == "test_1"

    deleted = _assert_admin_response(next_client.delete(f"/api/admin/wechat-pay/products/{copied_product_id}"))
    assert deleted["deleted"] is True


def test_wechat_product_update_preserves_read_product_body_contract(next_client) -> None:
    reset_commerce_fixture_state()
    created = _assert_admin_response(next_client.post("/api/admin/wechat-pay/products", json=_product_payload(product_code="contract_product_update")))
    product_id = created["product"]["id"]

    completion_target = {
        "enabled": True,
        "target_type": "h5",
        "open_strategy": "h5_redirect",
        "h5_url": "/paid/updated",
        "fallback_url": "",
        "url_link": {"enabled": False, "source_url": "", "response_url_key": "url_link"},
    }
    response = _assert_admin_response(
        next_client.put(
            f"/api/admin/wechat-pay/products/{product_id}",
            json=_product_payload(
                product_code="contract_product_update",
                title="更新后的商品标题",
                price_cents=29900,
                status="disabled",
                enabled=False,
                buy_button_text="立即报名",
                require_mobile=True,
                description="更新后的商品描述",
                slices=[{"image_library_id": 11, "sort_order": 1}, {"image_library_id": 12, "sort_order": 2}],
                lead_channel_id=1,
                completion_redirect_enabled=True,
                completion_redirect_url="/paid/updated",
                completion_target=completion_target,
            ),
        )
    )
    product = response["product"]
    assert product["title"] == "更新后的商品标题"
    assert product["price_cents"] == 29900
    assert product["status"] == "disabled"
    assert product["buy_button_text"] == "立即报名"
    assert product["require_mobile"] is True
    assert product["description"] == "更新后的商品描述"
    assert [item["image_library_id"] for item in product["slices"]] == [11, 12]
    assert product["lead_channel_id"] == 1
    assert product["completion_target"]["enabled"] is True
    assert product["completion_target"]["h5_url"] == "/paid/updated"


def test_wechat_product_admin_omits_inline_slice_image_data(next_client) -> None:
    reset_commerce_fixture_state()
    created = _assert_admin_response(
        next_client.post(
            "/api/admin/wechat-pay/products",
            json=_product_payload(
                product_code="contract_product_light_slices",
                slices=[
                    {"image_library_id": 11, "image_url": "data:image/png;base64,YQ==", "sort_order": 1},
                    {"image_library_id": 12, "data_url": "data:image/png;base64,Yg==", "sort_order": 2},
                ],
            ),
        )
    )
    product_id = created["product"]["id"]

    detail = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{product_id}"))
    detail_text = json.dumps(detail, ensure_ascii=False)
    assert "data:image" not in detail_text
    assert "data_base64" not in detail_text
    slices = detail["product"]["slices"]
    assert [item["image_library_id"] for item in slices] == [11, 12]
    assert [item["sort_order"] for item in slices] == [1, 2]
    assert all(not str(item.get("image_url") or "").startswith("data:") for item in slices)

    edit_page = next_client.get(f"/admin/wechat-pay/products/{product_id}/edit")
    assert edit_page.status_code == 200
    assert "data:image" not in edit_page.text
    assert "data_base64" not in edit_page.text

    share = _assert_admin_response(next_client.get(f"/api/admin/wechat-pay/products/{product_id}/share"))
    assert share["share"]["url"].endswith("/p/contract_product_light_slices")


def test_wechat_product_external_push_update_contract(next_client) -> None:
    reset_commerce_fixture_state()
    created = _assert_admin_response(next_client.post("/api/admin/wechat-pay/products", json=_product_payload(product_code="contract_product_push")))
    product_id = created["product"]["id"]

    disabled = _assert_admin_response(
        next_client.put(
            f"/api/admin/wechat-pay/products/{product_id}/external-push",
            json={"enabled": False, "webhook_url": "", "push_type": "", "custom_params": {}},
        )
    )
    assert disabled["config"]["enabled"] is False
    assert disabled["config"]["custom_params"] == {}

    enabled = _assert_admin_response(
        next_client.put(
            f"/api/admin/wechat-pay/products/{product_id}/external-push",
            json={
                "enabled": True,
                "webhook_url": "https://example.com/product-push",
                "push_type": "paid_notify",
                "custom_params": {"source": "wechat-product-admin", "tier": "gold"},
            },
        )
    )
    config = enabled["config"]
    assert config["enabled"] is True
    assert config["webhook_url"] == "https://example.com/product-push"
    assert config["push_type"] == "paid_notify"
    assert config["custom_params"] == {"source": "wechat-product-admin", "tier": "gold"}
