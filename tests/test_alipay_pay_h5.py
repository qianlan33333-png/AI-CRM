from __future__ import annotations

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state


def _assert_next_payment_response(response) -> dict:
    payload = response.json()
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "x-aicrm-compatibility-facade" not in response.headers
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    return payload


def test_alipay_checkout_uses_next_fake_adapter_without_real_payment(next_client):
    reset_commerce_fixture_state()

    response = next_client.post(
        "/api/checkout/alipay",
        json={
            "product_code": "test-product",
            "quantity": 1,
            "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
            "return_url": "/pay/test-product",
        },
    )

    assert response.status_code == 200
    payload = _assert_next_payment_response(response)
    assert response.headers["X-AICRM-Payment-Request-Executed"] == "false"
    assert payload["payment_provider"] == "alipay"
    assert payload["adapter_mode"] == "fake"
    assert payload["fake_payment"] is True
    assert payload["payment_request_executed"] is False
    assert payload["side_effect_safety"]["real_alipay_executed"] is False


def test_alipay_notify_and_return_are_next_owned_local_contracts(next_client):
    reset_commerce_fixture_state()

    notify = next_client.post(
        "/api/alipay/notify",
        json={
            "order_no": "order_fake_0003",
            "payment_status": "paid",
            "transaction_id": "alipay_tx_fixture",
            "provider_payload": {"notify_id": "notify_alipay_tx_fixture"},
        },
    )
    assert notify.status_code == 200
    notify_payload = _assert_next_payment_response(notify)
    assert notify.headers["X-AICRM-Provider-Signature-Verified"] == "false"
    assert notify_payload["payment_provider"] == "alipay"
    assert notify_payload["payment_status"] == "paid"
    assert notify_payload["provider_signature_verified"] is False
    assert notify_payload["payment_notify_executed"] == "local_only"
    assert notify_payload["real_payment_notify_executed"] is False

    returned = next_client.get("/api/alipay/return?order_no=order_fake_0003&status=paid")
    assert returned.status_code == 200
    return_payload = _assert_next_payment_response(returned)
    assert returned.headers["X-AICRM-Payment-Return-Executed"] == "fake"
    assert return_payload["payment_provider"] == "alipay"
    assert return_payload["payment_return_executed"] == "fake"
    assert return_payload["provider_signature_verified"] is False


def test_legacy_h5_alipay_paths_are_retired_under_next(next_client):
    reset_commerce_fixture_state()

    response = next_client.post("/api/h5/alipay/wap/orders", json={"product_code": "test-product"})

    assert response.status_code == 410
    payload = _assert_next_payment_response(response)
    assert payload["error_code"] == "h5_alipay_path_removed"
    assert payload["replacement"] == "/api/checkout/alipay"


def test_admin_alipay_transactions_and_retired_order_paths_use_next_contract(next_client):
    reset_commerce_fixture_state()

    transactions = next_client.get("/api/admin/alipay/transactions")
    assert transactions.status_code == 200
    payload = _assert_next_payment_response(transactions)
    assert payload["ok"] is True
    assert payload["real_alipay_executed"] is False
    assert payload["provider_signature_verified"] is False

    retired = next_client.get("/api/admin/alipay/orders")
    assert retired.status_code == 410
    retired_payload = _assert_next_payment_response(retired)
    assert retired_payload["error_code"] == "admin_alipay_path_removed"
    assert retired_payload["replacement"] == "/api/admin/alipay/transactions"
