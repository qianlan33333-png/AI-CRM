from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "public-product-pages-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_public_product_page_redirects_empty_material_to_checkout(monkeypatch) -> None:
    response = _client(monkeypatch).get("/p/test-product", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert response.headers["location"] == "/pay/test-product"


def test_public_product_page_redirect_preserves_noncredential_query_only(monkeypatch) -> None:
    response = _client(monkeypatch).get("/p/test-product?ctx=ctx-token&utm=qr", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/pay/test-product?utm=qr"


def test_public_product_page_renders_when_material_exists(monkeypatch) -> None:
    reset_commerce_fixture_state()
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "material-product",
            "title": "带素材商品",
            "price_cents": 100,
            "enabled": True,
            "status": "active",
            "buy_button_text": "立即报名",
            "slices": [{"image_library_id": 1, "image_url": "data:image/png;base64,YQ==", "sort_order": 1}],
        },
    )
    assert created.status_code == 200

    response = client.get("/p/material-product")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert "带素材商品" in response.text
    assert "/pay/material-product" in response.text
    assert 'class="slice-img"' in response.text
    assert 'class="sticky-buy"' in response.text
    assert response.text.index('class="sticky-buy"') < response.text.index('class="slice-img"')
    assert "data:image" not in response.text
    assert "/api/h5/product-images/material-product/1/variants/original" in response.text
    assert 'loading="eager" decoding="async" fetchpriority="high"' in response.text
    assert "不创建订单" not in response.text
    assert "商品编码" not in response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_public_product_page_unknown_path_is_controlled_404(monkeypatch) -> None:
    response = _client(monkeypatch).get("/p/unknown-path-for-smoke")

    assert response.status_code == 404
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert "商品不存在" in response.text


def test_public_product_page_options_is_next_owned(monkeypatch) -> None:
    response = _client(monkeypatch).options("/p/test-product")

    assert response.status_code == 200
    assert response.json()["allowed_methods"] == ["GET", "HEAD", "OPTIONS"]
    assert response.json()["fallback_used"] is False
