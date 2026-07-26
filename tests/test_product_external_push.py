from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aicrm_next.commerce import external_push_admin
from aicrm_next.external_push import security, service
from aicrm_next.commerce.repo import reset_commerce_fixture_state
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, WEBHOOK_ORDER_PAID_PUSH, reset_external_effect_fixture_state


def _public_dns(monkeypatch, ip: str = "93.184.216.34") -> None:
    monkeypatch.setattr(
        external_push_admin.socket,
        "getaddrinfo",
        lambda host, port, type=None: [(external_push_admin.socket.AF_INET, external_push_admin.socket.SOCK_STREAM, 6, "", (ip, port))],
    )


def test_product_external_push_config_api_uses_next_repository(next_client):
    reset_commerce_fixture_state()

    empty = next_client.get("/api/admin/wechat-pay/products/prod_000/external-push")
    assert empty.status_code == 200
    assert empty.json()["config"]["enabled"] is False
    assert empty.json()["route_owner"] == "ai_crm_next"
    assert empty.json()["fallback_used"] is False

    saved = next_client.put(
        "/api/admin/wechat-pay/products/prod_000/external-push",
        json={
            "enabled": True,
            "webhook_url": "https://example.com/hook",
            "push_type": "paid_notify",
            "day": 7,
            "frequency": 1,
            "remark": "用户购买后通知外部系统",
            "custom_params": {"source": "next-commerce-test"},
            "secret": "webhook-secret",
        },
    )
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["config"]["enabled"] is True
    assert payload["config"]["webhook_url"] == "https://example.com/hook"
    assert payload["config"]["has_secret"] is True

    fetched = next_client.get("/api/admin/wechat-pay/products/prod_000/external-push")
    assert fetched.json()["config"]["custom_params"] == {"source": "next-commerce-test"}

    missing = next_client.put("/api/admin/wechat-pay/products/prod_missing/external-push", json={"enabled": False})
    assert missing.status_code == 404


def test_webhook_url_security_rejects_private_targets(monkeypatch):
    _public_dns(monkeypatch)
    assert external_push_admin.resolve_and_validate_public_https_url("https://example.com/foo") == "https://example.com/foo"

    for url in [
        "http://example.com/foo",
        "https://localhost/foo",
        "https://127.0.0.1/foo",
        "https://192.168.1.1/foo",
    ]:
        with pytest.raises(external_push_admin.WebhookUrlValidationError):
            external_push_admin.resolve_and_validate_public_https_url(url)

    _public_dns(monkeypatch, ip="10.0.0.8")
    with pytest.raises(external_push_admin.WebhookUrlValidationError):
        external_push_admin.resolve_and_validate_public_https_url("https://example.com/foo")


def test_external_push_admin_reuses_next_native_security_and_payload_helpers():
    source = Path("aicrm_next/commerce/external_push_admin.py").read_text(encoding="utf-8")

    assert "def _validate_webhook_url" not in source
    assert "def _resolve_and_validate_public_https_url" not in source
    assert "def _sign_webhook_payload" not in source
    assert "def _redact_sensitive_fields" not in source
    assert "def _build_external_push_payload" not in source
    assert external_push_admin.resolve_and_validate_public_https_url is security.resolve_and_validate_public_https_url
    assert external_push_admin._sign_webhook_payload is service.sign_webhook_payload
    assert external_push_admin._redact_sensitive_fields is service.redact_sensitive_fields


def test_external_push_payload_masks_stored_body_but_sends_full_payload():
    payload = external_push_admin._build_external_push_payload(
        "transaction.paid",
        {
            "id": 7,
            "out_trade_no": "WXP_NEXT_PUSH",
            "amount_total": 9900,
            "paid_at": "2026-06-01T07:30:10+00:00",
            "payer_openid": "openid_product_push",
            "unionid": "unionid_product_push",
            "external_userid": "wm_product_push",
            "metadata_json": {"payer_identity": {"mobile": "13800000000"}},
        },
        {"id": 3, "product_code": "subscription_trial_month", "name": "外推商品", "amount_total": 9900},
        {"push_type": "premium", "day": 31, "frequency": 3, "remark": "69元1个月续费"},
        delivery_id="deliv_next_push",
    )

    assert payload["phone_number"] == "13800000000"
    assert payload["buyer"]["openid"] == "open***push"
    assert payload["buyer"]["unionid"] == "unionid_product_push"
    assert payload["submitted_at"] == "2026-06-01T15:30:10+08:00"
    redacted = external_push_admin._redact_sensitive_fields(payload)
    assert redacted["phone_number"] == "[pii]"
    assert redacted["buyer"]["phone"] == "[pii]"
    assert redacted["buyer"]["unionid"] == "[pii]"
    assert "config" not in redacted
    assert "tenant" not in redacted


def test_external_push_payload_resolves_openid_from_order_metadata_without_legacy_column():
    payload = external_push_admin._build_external_push_payload(
        "transaction.paid",
        {
            "id": 8,
            "out_trade_no": "WXP_METADATA_PUSH",
            "amount_total": 6900,
            "paid_at": "2026-06-01T07:30:10+00:00",
            "unionid": "unionid_metadata_push",
            "external_userid": "wm_metadata_push",
            "metadata_json": {"buyer_identity": {"openid": "openid_from_metadata", "mobile": "13900000000"}},
        },
        {"id": 4, "product_code": "subscription_trial_month", "name": "外推商品", "amount_total": 6900},
        {"push_type": "premium", "day": 31, "frequency": 3, "remark": "69元1个月续费"},
        delivery_id="deliv_metadata_push",
    )

    assert payload["buyer"]["openid"] == "open***data"
    assert payload["phone_number"] == "13900000000"


def test_external_push_payload_can_resolve_mobile_from_identity_table() -> None:
    class FakeConn:
        def execute(self, query, params):
            assert "crm_user_identity" in query
            assert params == ("unionid_product_push",)
            return SimpleNamespace(fetchone=lambda: {"mobile": "13900000000"})

    order = {"id": 7, "unionid": "unionid_product_push", "metadata_json": {}}
    phone = external_push_admin._resolve_order_mobile(FakeConn(), order)

    assert phone == "13900000000"


def test_external_push_attempt_queues_external_effect_and_updates_delivery(monkeypatch):
    reset_external_effect_fixture_state()
    _public_dns(monkeypatch)
    updates: list[dict] = []

    class FakeConn:
        def execute(self, query, params):
            updates.append(
                {
                    "status": params[0],
                    "attempt_count": params[1],
                    "request_url": params[2],
                    "request_headers": params[3],
                    "request_body": params[4],
                    "response_status": params[5],
                    "response_body": params[6],
                    "error_message": params[7],
                    "next_retry_at": params[8],
                    "delivery_id": params[9],
                }
            )
            return SimpleNamespace(fetchone=lambda: {"delivery_id": params[9], "status": params[0], "attempt_count": params[1]})

    connection = FakeConn()
    real_plan_effect = external_push_admin.ExternalEffectService.plan_effect

    def fixture_plan_effect(self, **kwargs):
        assert kwargs.pop("connection") is connection
        return real_plan_effect(self, **kwargs)

    monkeypatch.setattr(external_push_admin.ExternalEffectService, "plan_effect", fixture_plan_effect)

    result = external_push_admin._attempt_delivery(
        connection,
        {"delivery_id": "deliv_attempt", "event_type": "transaction.paid", "attempt_count": 0, "request_url": "https://example.com/hook"},
        config={"webhook_url": "https://example.com/hook", "secret": "secret"},
        payload={"event": "transaction.paid", "phone_number": "13800000000"},
    )

    assert result["ok"] is True
    assert result["delivery"]["status"] == "retrying"
    assert result["external_effect_job_id"]
    assert result["real_external_call_executed"] is False
    assert json.loads(updates[0]["request_headers"])["X-AICRM-Signature"].startswith("sha256=")
    assert json.loads(updates[0]["request_body"])["phone_number"] == "[pii]"
    assert updates[0]["response_status"] is None
    items, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "target_id": "deliv_attempt"})
    assert total == 1
    assert items[0].status == "queued"


def test_external_push_test_payload_does_not_include_order_or_real_payment_fields():
    payload = external_push_admin._build_external_push_payload(
        "external_push.test",
        {},
        {"id": "prod_001", "name": "测试商品"},
        {"tenant_id": "aicrm", "target_id": "prod_001", "custom_params": {"source": "preview"}},
        delivery_id="deliv_test",
    )

    assert payload == {
        "event": "external_push.test",
        "delivery_id": "deliv_test",
        "occurred_at": payload["occurred_at"],
        "tenant": {"id": "aicrm"},
        "product": {"id": "prod_001", "name": "测试商品"},
        "custom_params": {"source": "preview"},
    }
    json.dumps(payload, ensure_ascii=False)
