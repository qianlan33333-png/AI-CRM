from __future__ import annotations

from aicrm_next.extensions.commerce.commerce import admin_transactions as next_wechat_admin_transactions
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.shared.runtime import database_mode, raw_database_url


def _seed_wechat_pay_admin_order() -> None:
    reset_commerce_fixture_state()
    if database_mode() != "postgres":
        return

    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(raw_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM wechat_pay_order_events WHERE out_trade_no = %s", ("order_masked_001",))
            cur.execute("DELETE FROM wechat_pay_refunds WHERE out_trade_no = %s", ("order_masked_001",))
            cur.execute("DELETE FROM wechat_pay_orders WHERE out_trade_no = %s", ("order_masked_001",))
            cur.execute(
                """
                INSERT INTO wechat_pay_orders (
                    out_trade_no, transaction_id, product_code, product_name,
                    amount_total, currency, payer_openid, respondent_key, unionid,
                    external_userid, userid_snapshot, mobile_snapshot, payer_name_snapshot,
                    status, trade_state, paid_at, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, 'CNY', %s, %s, %s,
                    %s, %s, %s, %s,
                    'paid', 'SUCCESS', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                (
                    "order_masked_001",
                    "transaction_masked_001",
                    "course_masked_001",
                    "Next 私域成交课",
                    12900,
                    "op_masked_001",
                    "respondent_masked_001",
                    "union_masked_001",
                    "wm_masked_001",
                    "owner_masked_001",
                    "13800138000",
                    "张三",
                ),
            )
            cur.execute(
                """
                INSERT INTO wechat_pay_order_events (
                    out_trade_no, event_type, transaction_id, trade_state,
                    payload_json, headers_json, created_at
                )
                VALUES (%s, 'payment_success', %s, 'SUCCESS', %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    "order_masked_001",
                    "transaction_masked_001",
                    Jsonb({"trade_state": "SUCCESS"}),
                    Jsonb({}),
                ),
            )
        conn.commit()


def test_next_wechat_pay_admin_present_order_uses_operator_product_label():
    row = {
        "id": "order_display",
        "created_at": "2026-05-18 12:00:00",
        "transaction_id": "420000DISPLAY",
        "payer_name_snapshot": "张三",
        "mobile_snapshot": "13800000000",
        "userid_snapshot": "zhangsan",
        "external_userid": "wm_test",
        "product_code": "assessment_report_v1",
        "product_name": "AI 测评报告",
        "amount_total": 9900,
        "status": "paid",
        "trade_state": "SUCCESS",
    }

    presented = next_wechat_admin_transactions._present_order(row)

    assert presented["product_name"] == "AI 测评报告"
    assert presented["product_code"] == "assessment_report_v1"
    assert presented["status_label"] == "已支付"
    assert presented["can_refund"] is True


def test_next_wechat_pay_admin_repairs_utf8_and_emoji_mojibake_payer_name_snapshot():
    utf8_row = {
        "id": "order_utf8",
        "created_at": "2026-06-09 13:41:05",
        "transaction_id": "4200003181202606097097565241",
        "payer_name_snapshot": "æ›¾å¾·é’§",
        "mobile_snapshot": "18875125771",
        "userid_snapshot": "HuangYouCan",
        "external_userid": "",
        "product_code": "subscription_monthly",
        "product_name": "黄小璨订阅版-月付",
        "amount_total": 1990,
        "status": "paid",
        "trade_state": "SUCCESS",
    }
    emoji_row = {
        **utf8_row,
        "id": "order_emoji",
        "payer_name_snapshot": "AuroraðŸŒŸ",
        "mobile_snapshot": "15873389131",
        "userid_snapshot": "",
        "external_userid": "orSqJ5sDYFX2LpC769_gQSdiwjc8",
        "product_code": "premium_monthly_trial",
        "product_name": "黄小璨月度会员私教版",
        "amount_total": 6900,
    }

    assert next_wechat_admin_transactions._present_order(utf8_row)["payer_name"] == "曾德钧"
    assert next_wechat_admin_transactions._present_order(emoji_row)["payer_name"] == "Aurora🌟"


def test_next_wechat_pay_admin_status_mapping_and_refund_amounts():
    paid = next_wechat_admin_transactions._present_order(
        {
            "id": "paid",
            "amount_total": 9900,
            "status": "paid",
            "trade_state": "SUCCESS",
            "refunded_amount_total": 0,
            "active_refund_amount_total": 0,
        }
    )
    partial = next_wechat_admin_transactions._present_order(
        {
            "id": "partial",
            "amount_total": 9900,
            "status": "paid",
            "trade_state": "SUCCESS",
            "refunded_amount_total": 1000,
            "active_refund_amount_total": 0,
        }
    )
    processing = next_wechat_admin_transactions._present_order(
        {
            "id": "processing",
            "amount_total": 9900,
            "status": "paid",
            "trade_state": "SUCCESS",
            "refunded_amount_total": 0,
            "active_refund_amount_total": 1000,
        }
    )

    assert paid["status_label"] == "已支付"
    assert paid["refundable_amount_total"] == 9900
    assert partial["status_label"] == "部分退款"
    assert partial["refundable_amount_total"] == 8900
    assert processing["status_label"] == "退款处理中"
    assert processing["can_refund"] is False


def test_next_wechat_pay_admin_order_list_filter_and_detail_use_next_routes(next_client):
    _seed_wechat_pay_admin_order()

    list_response = next_client.get("/api/admin/wechat-pay/orders?product_code=course_masked_001&limit=20")
    payload = list_response.json()

    assert list_response.status_code == 200
    assert list_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert payload["ok"] is True
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert [item["product_code"] for item in payload["items"]] == ["course_masked_001"]
    assert payload["items"][0]["status_label"] == "已支付"
    assert payload["items"][0]["can_refund"] is True

    detail_response = next_client.get("/api/admin/wechat-pay/transactions/order_masked_001")
    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert detail["transaction"]["merchant_order_no"] == "order_masked_001"
    assert detail["transaction"]["timeline"]


def test_next_wechat_pay_admin_export_returns_required_fields_without_legacy_job_path(next_client):
    _seed_wechat_pay_admin_order()

    response = next_client.post(
        "/api/admin/wechat-pay/order-exports",
        json={"filters": {"product_code": "course_masked_001"}, "operator": "tester"},
    )

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert "订单创建时间,微信单号,手机号,unionid,商品名称,商品编码,金额,状态" in response.text
    assert "course_masked_001" in response.text
    assert "transaction_masked_001" in response.text

    retired = next_client.get("/api/admin/wechat-pay/order-exports/job_legacy/download")
    retired_payload = retired.json()
    assert retired.status_code == 410
    assert retired_payload["fallback_used"] is False
    assert retired_payload["error_code"] == "admin_wechat_pay_export_job_removed"


def test_next_wechat_pay_admin_refund_requires_confirmation_and_never_calls_real_provider(next_client, monkeypatch):
    _seed_wechat_pay_admin_order()

    class FakeRefundClient:
        def create_refund(self, payload: dict) -> dict:
            return {"status": "PROCESSING", "refund_id": "refund_fake_001", "request": payload}

    monkeypatch.setattr(
        next_wechat_admin_transactions,
        "_create_wechat_pay_refund_client",
        lambda: FakeRefundClient(),
    )

    missing_confirmation = next_client.post(
        "/api/admin/wechat-pay/orders/order_masked_001/refunds",
        json={
            "refund_amount_total": 100,
            "reason": "客户主动申请退款",
            "transaction_id_confirmation": "wrong_tx",
            "checked": True,
            "operator": "tester",
        },
    )
    assert missing_confirmation.status_code == 400
    assert missing_confirmation.json()["error"] == "微信单号二次确认不匹配"

    response = next_client.post(
        "/api/admin/wechat-pay/orders/order_masked_001/refunds",
        json={
            "refund_amount_total": 100,
            "reason": "客户主动申请退款",
            "transaction_id_confirmation": "transaction_masked_001",
            "checked": True,
            "operator": "tester",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert payload["ok"] is True
    if database_mode() == "postgres":
        assert response.headers["X-AICRM-Real-Refund-Executed"] == "true"
        assert payload["refund"]["status"] == "PROCESSING"
        assert payload["refund"]["provider_refund_executed"] is True
        assert payload["real_refund_executed"] is True
    else:
        assert response.headers["X-AICRM-Real-Refund-Executed"] == "false"
        assert payload["refund"]["status"] == "requested"
        assert payload["refund"]["provider_refund_executed"] is False
        assert payload["real_refund_executed"] is False
    assert payload["order"]["status_label"] == "退款处理中"


def test_next_wechat_pay_admin_refund_rejects_amount_over_order_total(next_client):
    _seed_wechat_pay_admin_order()

    response = next_client.post(
        "/api/admin/wechat-pay/orders/order_masked_001/refunds",
        json={
            "refund_amount_total": 1000000,
            "reason": "金额过大",
            "transaction_id_confirmation": "transaction_masked_001",
            "checked": True,
            "operator": "tester",
        },
    )

    assert response.status_code == 400
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.json()["error"] == "累计退款金额不能超过订单金额"
