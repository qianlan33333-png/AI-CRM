from __future__ import annotations

import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.extensions.commerce.commerce import admin_transactions, external_push_admin
from aicrm_next.extensions.commerce.commerce.fulfillment_reconciliation import CommerceFulfillmentReconciliationService
from aicrm_next.identity_contact.payment_projection import project_payment_order_mobile, project_wechat_shop_order_mobile
from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.commerce.service_period import payment_consumer, refund_consumer


def _database_url() -> str:
    return str(os.getenv("AICRM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _seed_product(conn, *, code: str, amount_total: int = 1000) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO wechat_pay_products (product_code, name, amount_total, status, enabled)
            VALUES (%s, %s, %s, 'active', TRUE)
            RETURNING id
            """,
            (code, f"R08 {code}", amount_total),
        ).fetchone()["id"]
    )


def _seed_order(
    conn,
    *,
    code: str,
    out_trade_no: str,
    amount_total: int = 1000,
    status: str = "paid",
    trade_state: str = "SUCCESS",
    unionid: str = "",
) -> dict:
    row = conn.execute(
        """
        INSERT INTO wechat_pay_orders (
            out_trade_no, transaction_id, product_code, product_name,
            amount_total, currency, status, trade_state, unionid, paid_at
        ) VALUES (%s, %s, %s, %s, %s, 'CNY', %s, %s, %s,
                  CASE WHEN %s = 'paid' THEN CURRENT_TIMESTAMP ELSE NULL END)
        RETURNING *
        """,
        (
            out_trade_no,
            f"wx_tx_{out_trade_no}",
            code,
            f"R08 {code}",
            amount_total,
            status,
            trade_state,
            unionid,
            status,
        ),
    ).fetchone()
    return dict(row)


def _public_refundable_order(order: dict) -> dict:
    return {
        "id": int(order["id"]),
        "out_trade_no": order["out_trade_no"],
        "transaction_id": order["transaction_id"],
        "amount_total": int(order["amount_total"]),
        "currency": "CNY",
        "status": "paid",
        "trade_state": "SUCCESS",
        "can_refund": True,
        "refundable_amount_total": int(order["amount_total"]),
    }


def _transaction(out_trade_no: str, *, amount_total: int = 1000) -> dict:
    return {
        "out_trade_no": out_trade_no,
        "trade_state": "SUCCESS",
        "transaction_id": f"wx_tx_{out_trade_no}",
        "bank_type": "OTHERS",
        "success_time": "2026-07-11T10:00:00Z",
        "amount": {"payer_total": amount_total},
        "payer": {"openid": "openid_not_persisted_in_reconciliation"},
    }


def test_paid_order_mobile_creates_queryable_canonical_identity_and_is_idempotent(next_pg_schema) -> None:
    del next_pg_schema
    order = {
        "out_trade_no": "WXP_R08_MOBILE_IDENTITY",
        "unionid": "union_r08_mobile_identity",
        "payer_name_snapshot": "R08 付款人",
        "metadata_json": {
            "payer_identity": {
                "openid": "openid_r08_mobile_identity",
                "external_userid": "wm_r08_mobile_identity",
                "owner_userid": "owner_r08_mobile_identity",
                "mobile": "15812345678",
            }
        },
    }

    for source_route in (
        "/api/h5/wechat-pay/notify",
        "/internal-events/payment.succeeded/order_projection_consumer",
    ):
        with _connect() as conn:
            result = project_payment_order_mobile(conn, order, source_route=source_route)
            conn.commit()
        assert result == {
            "ok": True,
            "projected": True,
            "unionid": "union_r08_mobile_identity",
            "mobile": "15812345678",
        }

    with _connect() as conn:
        identity = conn.execute(
            """
            SELECT unionid, mobile, mobile_normalized, mobile_verified, mobile_source,
                   primary_external_userid, primary_openid, primary_owner_userid
            FROM crm_user_identity
            WHERE mobile_normalized = %s
            """,
            ("15812345678",),
        ).fetchone()

    assert dict(identity) == {
        "unionid": "union_r08_mobile_identity",
        "mobile": "15812345678",
        "mobile_normalized": "15812345678",
        "mobile_verified": True,
        "mobile_source": "wechat_pay_order",
        "primary_external_userid": "wm_r08_mobile_identity",
        "primary_openid": "openid_r08_mobile_identity",
        "primary_owner_userid": "owner_r08_mobile_identity",
    }


def test_mobile_projection_migration_backfills_existing_paid_order_into_canonical_identity(next_pg_schema) -> None:
    del next_pg_schema
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO wechat_pay_orders (
                out_trade_no, transaction_id, product_code, product_name,
                amount_total, currency, status, trade_state, unionid,
                payer_name_snapshot, metadata_json, paid_at
            ) VALUES (
                'WXP_R08_MOBILE_BACKFILL', 'wx_tx_mobile_backfill', 'r08_mobile_backfill',
                'R08 Mobile Backfill', 9900, 'CNY', 'paid', 'SUCCESS',
                'union_r08_mobile_backfill', 'R08 历史付款人',
                %s::jsonb, CURRENT_TIMESTAMP
            )
            """,
            (
                json.dumps(
                    {
                        "payer_identity": {
                            "mobile": "15912345678",
                            "openid": "openid_r08_mobile_backfill",
                            "external_userid": "wm_r08_mobile_backfill",
                        }
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO crm_user_identity (unionid, mobile, mobile_normalized)
            VALUES ('union_r08_mobile_backfill', '', '')
            """
        )
        migration = import_module("migrations.versions.0110_wechat_pay_mobile_projection")
        conn.execute(migration._BACKFILL_SQL)
        conn.commit()

    with _connect() as conn:
        identity = conn.execute(
            """
            SELECT unionid, mobile, mobile_normalized, mobile_verified, mobile_source
            FROM crm_user_identity
            WHERE mobile_normalized = '15912345678'
            """
        ).fetchone()

    assert dict(identity) == {
        "unionid": "union_r08_mobile_backfill",
        "mobile": "15912345678",
        "mobile_normalized": "15912345678",
        "mobile_verified": True,
        "mobile_source": "wechat_pay_order",
    }


def test_wechat_shop_mobile_projection_and_backfill_are_queryable_from_canonical_identity(next_pg_schema) -> None:
    del next_pg_schema
    order = {
        "order_id": "3737749464399107840",
        "paid_at": "2026-07-14T01:34:29Z",
        "unionid": "union_r08_wechat_shop",
        "openid": "openid_r08_wechat_shop",
        "buyer_mobile": "15912345678",
    }
    with _connect() as conn:
        result = project_wechat_shop_order_mobile(conn, order, source_route="wechat_shop_order_sync")
        conn.commit()

    assert result == {
        "ok": True,
        "projected": True,
        "unionid": "union_r08_wechat_shop",
        "mobile": "15912345678",
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO wechat_shop_orders (order_id, unionid, paid_at, raw_order_json)
            VALUES ('shop-r08-backfill', 'union_r08_shop_backfill', CURRENT_TIMESTAMP, %s::jsonb)
            """,
            (
                json.dumps(
                    {
                        "order": {
                            "openid": "openid_r08_shop_backfill",
                            "order_detail": {
                                "delivery_info": {
                                    "address_info": {"virtual_order_tel_number": "13712345678"}
                                }
                            },
                        }
                    }
                ),
            ),
        )
        for suffix in ("a", "b"):
            conn.execute(
                """
                INSERT INTO wechat_shop_orders (order_id, unionid, paid_at, raw_order_json)
                VALUES (%s, %s, CURRENT_TIMESTAMP, %s::jsonb)
                """,
                (
                    f"shop-r08-shared-{suffix}",
                    f"union_r08_shop_shared_{suffix}",
                    json.dumps(
                        {
                            "order": {
                                "order_detail": {
                                    "delivery_info": {
                                        "address_info": {"virtual_order_tel_number": "13612345678"}
                                    }
                                }
                            }
                        }
                    ),
                ),
            )
        migration = import_module("migrations.versions.0111_wechat_shop_mobile_projection")
        conn.execute(migration._BACKFILL_SQL)
        conn.commit()

    with _connect() as conn:
        identities = conn.execute(
            """
            SELECT unionid, mobile, mobile_normalized, mobile_verified, mobile_source, primary_openid
            FROM crm_user_identity
            WHERE mobile_normalized IN ('15912345678', '13712345678')
            ORDER BY mobile_normalized
            """
        ).fetchall()
        shared_mobile_count = conn.execute(
            "SELECT COUNT(*) AS total FROM crm_user_identity WHERE mobile_normalized = '13612345678'"
        ).fetchone()["total"]

    assert [dict(row) for row in identities] == [
        {
            "unionid": "union_r08_shop_backfill",
            "mobile": "13712345678",
            "mobile_normalized": "13712345678",
            "mobile_verified": True,
            "mobile_source": "wechat_shop_order",
            "primary_openid": "openid_r08_shop_backfill",
        },
        {
            "unionid": "union_r08_wechat_shop",
            "mobile": "15912345678",
            "mobile_normalized": "15912345678",
            "mobile_verified": True,
            "mobile_source": "wechat_shop_order",
            "primary_openid": "openid_r08_wechat_shop",
        },
    ]
    assert shared_mobile_count == 0


def test_payment_state_and_unique_outbox_share_one_postgres_transaction(next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    with _connect() as conn:
        _seed_product(conn, code="r08_payment_atomic")
        order = _seed_order(
            conn,
            code="r08_payment_atomic",
            out_trade_no="WXP_R08_PAYMENT_ATOMIC",
            status="paying",
            trade_state="NOTPAY",
        )
        conn.commit()

    real_enqueue = h5_wechat_pay.enqueue_transactional_internal_event_outbox

    def fail_outbox(conn, request):
        raise RuntimeError("injected payment outbox failure")

    monkeypatch.setattr(h5_wechat_pay, "enqueue_transactional_internal_event_outbox", fail_outbox)
    with _connect() as conn:
        with pytest.raises(RuntimeError, match="injected payment outbox failure"):
            h5_wechat_pay._apply_transaction(conn, _transaction(order["out_trade_no"]))
        conn.rollback()

    with _connect() as conn:
        rolled_back = conn.execute(
            "SELECT status, trade_state FROM wechat_pay_orders WHERE id = %s", (order["id"],)
        ).fetchone()
        outbox_count = conn.execute("SELECT COUNT(*) AS total FROM internal_event_outbox").fetchone()["total"]
    assert dict(rolled_back) == {"status": "paying", "trade_state": "NOTPAY"}
    assert outbox_count == 0

    monkeypatch.setattr(h5_wechat_pay, "enqueue_transactional_internal_event_outbox", real_enqueue)
    for _ in range(2):
        with _connect() as conn:
            h5_wechat_pay._apply_transaction(conn, _transaction(order["out_trade_no"]))
            conn.commit()

    with _connect() as conn:
        paid = conn.execute(
            "SELECT status, trade_state FROM wechat_pay_orders WHERE id = %s", (order["id"],)
        ).fetchone()
        canonical_outbox = conn.execute(
            """
            SELECT COUNT(*) AS total FROM internal_event_outbox
            WHERE idempotency_key = 'payment.succeeded:' || %s
            """,
            (order["out_trade_no"],),
        ).fetchone()["total"]
        legacy_outbox = conn.execute(
            "SELECT COUNT(*) AS total FROM domain_event_outbox WHERE aggregate_id = %s",
            (str(order["id"]),),
        ).fetchone()["total"]
    assert dict(paid) == {"status": "paid", "trade_state": "SUCCESS"}
    assert canonical_outbox == 1
    assert legacy_outbox == 0


def test_refund_request_rolls_back_on_effect_fault_and_concurrent_requests_cannot_overspend(
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    with _connect() as conn:
        _seed_product(conn, code="r08_refund_atomic")
        order = _seed_order(
            conn,
            code="r08_refund_atomic",
            out_trade_no="WXP_R08_REFUND_ATOMIC",
        )
        conn.commit()
    public_order = _public_refundable_order(order)
    monkeypatch.setattr(admin_transactions, "get_wechat_admin_order", lambda order_id: public_order)

    real_plan_effect = admin_transactions.ExternalEffectService.plan_effect

    def fail_plan(self, **kwargs):
        raise RuntimeError("injected refund effect failure")

    monkeypatch.setattr(admin_transactions.ExternalEffectService, "plan_effect", fail_plan)
    with pytest.raises(RuntimeError, match="injected refund effect failure"):
        admin_transactions.create_wechat_refund_request(
            str(order["id"]),
            {
                "refund_amount_total": 1000,
                "reason": "R08 fault rollback",
                "transaction_id_confirmation": order["transaction_id"],
                "checked": True,
                "operator": "r08-test",
            },
        )
    with _connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS total FROM wechat_pay_refunds").fetchone()["total"] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) AS total FROM wechat_pay_order_events WHERE event_type = 'refund_request_queued'"
            ).fetchone()["total"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS total FROM external_effect_job WHERE effect_type = 'payment.wechat.refund.request'"
            ).fetchone()["total"]
            == 0
        )

    first_effect_planned = Event()
    allow_first_commit = Event()

    def blocking_first_plan(self, **kwargs):
        result = real_plan_effect(self, **kwargs)
        if not first_effect_planned.is_set():
            first_effect_planned.set()
            assert allow_first_commit.wait(timeout=5)
        return result

    monkeypatch.setattr(admin_transactions.ExternalEffectService, "plan_effect", blocking_first_plan)

    def request_refund():
        try:
            return admin_transactions.create_wechat_refund_request(
                str(order["id"]),
                {
                    "refund_amount_total": 700,
                    "reason": "R08 concurrent refund",
                    "transaction_id_confirmation": order["transaction_id"],
                    "checked": True,
                    "operator": "r08-test",
                },
            )
        except Exception as exc:  # noqa: BLE001 - result classification is under test
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request_refund)
        assert first_effect_planned.wait(timeout=5)
        second = pool.submit(request_refund)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with _connect() as conn:
                waiter_count = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM pg_stat_activity
                    WHERE pid <> pg_backend_pid()
                      AND datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND query ILIKE '%wechat_pay_orders%FOR UPDATE%'
                    """
                ).fetchone()["total"]
            if waiter_count:
                break
            time.sleep(0.02)
        else:
            pytest.fail("second refund request did not wait on the locked order")
        allow_first_commit.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert sum(isinstance(result, dict) and result.get("ok") is True for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    with _connect() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(refund_amount_total), 0) AS amount FROM wechat_pay_refunds"
        ).fetchone()
        event_total = conn.execute(
            "SELECT COUNT(*) AS total FROM wechat_pay_order_events WHERE event_type = 'refund_request_queued'"
        ).fetchone()["total"]
        effect_total = conn.execute(
            "SELECT COUNT(*) AS total FROM external_effect_job WHERE effect_type = 'payment.wechat.refund.request'"
        ).fetchone()["total"]
    assert totals == {"total": 1, "amount": 700}
    assert event_total == 1
    assert effect_total == 1


def test_refund_success_and_outbox_are_atomic_and_duplicate_notify_does_not_double_count(
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    with _connect() as conn:
        _seed_product(conn, code="r08_refund_notify")
        order = _seed_order(
            conn,
            code="r08_refund_notify",
            out_trade_no="WXP_R08_REFUND_NOTIFY",
        )
        conn.execute(
            """
            INSERT INTO wechat_pay_refunds (
                order_id, out_trade_no, transaction_id, out_refund_no,
                refund_amount_total, order_amount_total, currency, status
            ) VALUES (%s, %s, %s, 'WXR_R08_NOTIFY', 1000, 1000, 'CNY', 'requested')
            """,
            (order["id"], order["out_trade_no"], order["transaction_id"]),
        )
        conn.commit()

    real_enqueue = admin_transactions.enqueue_transactional_internal_event_outbox
    monkeypatch.setattr(
        admin_transactions,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: (_ for _ in ()).throw(RuntimeError("injected refund outbox failure")),
    )
    payload = {
        "out_trade_no": order["out_trade_no"],
        "transaction_id": order["transaction_id"],
        "out_refund_no": "WXR_R08_NOTIFY",
        "refund_id": "wx_refund_r08_notify",
        "refund_status": "SUCCESS",
        "amount": {"refund": 1000, "total": 1000, "currency": "CNY"},
    }
    with pytest.raises(RuntimeError, match="injected refund outbox failure"):
        admin_transactions.apply_wechat_refund_result(payload)
    with _connect() as conn:
        refund = conn.execute(
            "SELECT status, refund_id FROM wechat_pay_refunds WHERE out_refund_no = 'WXR_R08_NOTIFY'"
        ).fetchone()
        persisted_order = conn.execute(
            "SELECT refunded_amount_total, refund_status FROM wechat_pay_orders WHERE id = %s", (order["id"],)
        ).fetchone()
    assert refund == {"status": "requested", "refund_id": ""}
    assert persisted_order == {"refunded_amount_total": 0, "refund_status": ""}

    monkeypatch.setattr(admin_transactions, "enqueue_transactional_internal_event_outbox", real_enqueue)
    first = admin_transactions.apply_wechat_refund_result(payload)
    second = admin_transactions.apply_wechat_refund_result(payload)

    with _connect() as conn:
        persisted_order = conn.execute(
            "SELECT refunded_amount_total, refund_status FROM wechat_pay_orders WHERE id = %s", (order["id"],)
        ).fetchone()
        outbox_total = conn.execute(
            "SELECT COUNT(*) AS total FROM internal_event_outbox WHERE idempotency_key = 'refund.succeeded:WXR_R08_NOTIFY'"
        ).fetchone()["total"]
    assert first["updated_order_amount"] is True
    assert second["updated_order_amount"] is False
    assert persisted_order == {"refunded_amount_total": 1000, "refund_status": "full_refunded"}
    assert outbox_total == 1


def test_external_push_delivery_and_effect_job_are_atomic_and_idempotent(next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    with _connect() as conn:
        product_id = _seed_product(conn, code="r08_external_push")
        order = _seed_order(
            conn,
            code="r08_external_push",
            out_trade_no="WXP_R08_EXTERNAL_PUSH",
        )
        conn.execute(
            """
            INSERT INTO external_push_config (
                tenant_id, target_type, target_id, event_type, enabled,
                webhook_url, push_type, day, frequency, remark, secret
            ) VALUES ('aicrm', 'product', %s, 'transaction.paid', TRUE,
                      'https://example.com/order-paid', 'paid_notify', 7, 1, 'R08', 'secret')
            """,
            (str(product_id),),
        )
        conn.commit()

    monkeypatch.setattr(external_push_admin, "resolve_and_validate_public_https_url", lambda url: url)
    real_plan_effect = external_push_admin.ExternalEffectService.plan_effect
    monkeypatch.setattr(
        external_push_admin.ExternalEffectService,
        "plan_effect",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("injected delivery effect failure")),
    )
    with _connect() as conn:
        with pytest.raises(RuntimeError, match="injected delivery effect failure"):
            external_push_admin.plan_order_paid_external_push_effect(
                conn,
                order=order,
                transaction=_transaction(order["out_trade_no"]),
            )
        conn.rollback()
    with _connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS total FROM external_push_delivery").fetchone()["total"] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) AS total FROM external_effect_job WHERE effect_type = 'webhook.order_paid.push'"
            ).fetchone()["total"]
            == 0
        )

    monkeypatch.setattr(external_push_admin.ExternalEffectService, "plan_effect", real_plan_effect)
    results = []
    for _ in range(2):
        with _connect() as conn:
            results.append(
                external_push_admin.plan_order_paid_external_push_effect(
                    conn,
                    order=order,
                    transaction=_transaction(order["out_trade_no"]),
                )
            )
            conn.commit()
    with _connect() as conn:
        delivery_total = conn.execute("SELECT COUNT(*) AS total FROM external_push_delivery").fetchone()["total"]
        effect_total = conn.execute(
            "SELECT COUNT(*) AS total FROM external_effect_job WHERE effect_type = 'webhook.order_paid.push'"
        ).fetchone()["total"]
    assert results[0]["external_effect_job_id"] == results[1]["external_effect_job_id"]
    assert results[1]["deduped"] is True
    assert delivery_total == 1
    assert effect_total == 1


def test_entitlement_recovers_after_unionid_backfill_and_refund_retry_is_idempotent(
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    with _connect() as conn:
        product_id = _seed_product(conn, code="r08_service_period")
        service_product_id = int(
            conn.execute(
                """
                INSERT INTO service_period_products (trade_product_id, link_slug, duration_days)
                VALUES (%s, 'r08-service-period', 30)
                RETURNING id
                """,
                (product_id,),
            ).fetchone()["id"]
        )
        order = _seed_order(
            conn,
            code="r08_service_period",
            out_trade_no="WXP_R08_SERVICE_PERIOD",
            unionid="",
        )
        conn.commit()

    event = InternalEvent(
        event_id="iev_r08_service_period",
        event_type="payment.succeeded",
        aggregate_type="wechat_pay_order",
        aggregate_id=str(order["id"]),
        correlation_id=order["out_trade_no"],
        payload_json={"order": order, "transaction": _transaction(order["out_trade_no"])},
    )
    run = InternalEventConsumerRun(
        event_id=event.event_id,
        consumer_name="service_period_entitlement_consumer",
    )
    missing = payment_consumer.service_period_entitlement_consumer(event, run)
    assert missing.status == "failed_retryable"
    assert missing.error_code == "missing_unionid"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                mobile, mobile_normalized, identity_status
            ) VALUES (
                'union_r08_backfilled', 'external_r08_backfilled',
                '["external_r08_backfilled"]'::jsonb,
                '13800138000', '13800138000', 'active'
            )
            """
        )
        conn.execute(
            """
            UPDATE wechat_pay_orders
            SET unionid = 'union_r08_backfilled',
                metadata_json = jsonb_build_object(
                    'payer_identity',
                    jsonb_build_object(
                        'external_userid', 'external_r08_backfilled',
                        'mobile', '13800138000',
                        'openid', 'openid_r08_not_projected_yet'
                    )
                )
            WHERE id = %s
            """,
            (order["id"],),
        )
        conn.commit()

    recovered = payment_consumer.service_period_entitlement_consumer(event, run)
    duplicate = payment_consumer.service_period_entitlement_consumer(event, run)
    assert recovered.status == "succeeded"
    assert recovered.result_summary["event_type"] == "activated"
    assert duplicate.status == "succeeded"
    assert duplicate.response_summary["reason"] == "event_already_applied"

    with _connect() as conn:
        entitlement = conn.execute(
            "SELECT * FROM service_period_entitlements WHERE service_product_id = %s",
            (service_product_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO wechat_pay_refunds (
                order_id, out_trade_no, transaction_id, out_refund_no,
                refund_amount_total, order_amount_total, currency, status
            ) VALUES (%s, %s, %s, 'WXR_R08_SERVICE_PERIOD', 1000, 1000, 'CNY', 'requested')
            """,
            (order["id"], order["out_trade_no"], order["transaction_id"]),
        )
        conn.commit()
    assert entitlement["status"] == "active"

    refund_payload = {
        "out_trade_no": order["out_trade_no"],
        "transaction_id": order["transaction_id"],
        "out_refund_no": "WXR_R08_SERVICE_PERIOD",
        "refund_id": "wx_refund_r08_service_period",
        "refund_status": "SUCCESS",
        "amount": {"refund": 1000, "total": 1000, "currency": "CNY"},
    }
    admin_transactions.apply_wechat_refund_result(refund_payload)
    admin_transactions.apply_wechat_refund_result(refund_payload)

    with _connect() as conn:
        outbox = conn.execute(
            "SELECT * FROM internal_event_outbox WHERE idempotency_key = 'refund.succeeded:WXR_R08_SERVICE_PERIOD'"
        ).fetchone()
    refund_event = InternalEvent(
        event_id="iev_r08_refund",
        event_type="refund.succeeded",
        aggregate_type=outbox["aggregate_type"],
        aggregate_id=outbox["aggregate_id"],
        correlation_id=outbox["correlation_id"],
        payload_json=dict(outbox["payload_json"]),
    )
    refund_run = InternalEventConsumerRun(
        event_id=refund_event.event_id,
        consumer_name="service_period_refund_consumer",
    )
    real_command = refund_consumer.ApplyServicePeriodRefundCommand
    monkeypatch.setattr(
        refund_consumer,
        "ApplyServicePeriodRefundCommand",
        lambda: (_ for _ in ()).throw(RuntimeError("injected consumer construction fault")),
    )
    failed = refund_consumer.service_period_refund_consumer(refund_event, refund_run)
    assert failed.status == "failed_retryable"
    monkeypatch.setattr(refund_consumer, "ApplyServicePeriodRefundCommand", real_command)

    applied = refund_consumer.service_period_refund_consumer(refund_event, refund_run)
    applied_again = refund_consumer.service_period_refund_consumer(refund_event, refund_run)
    assert applied.status == "succeeded"
    assert applied_again.status == "succeeded"
    assert applied_again.response_summary["idempotent"] is True
    with _connect() as conn:
        entitlement = conn.execute(
            "SELECT status FROM service_period_entitlements WHERE service_product_id = %s",
            (service_product_id,),
        ).fetchone()
        refund_event_total = conn.execute(
            """
            SELECT COUNT(*) AS total FROM service_period_events
            WHERE out_trade_no = %s AND event_type = 'refunded'
            """,
            (order["out_trade_no"],),
        ).fetchone()["total"]
        outbox_total = conn.execute(
            "SELECT COUNT(*) AS total FROM internal_event_outbox WHERE idempotency_key = 'refund.succeeded:WXR_R08_SERVICE_PERIOD'"
        ).fetchone()["total"]
    assert entitlement["status"] == "refunded"
    assert refund_event_total == 1
    assert outbox_total == 1


def test_reconciliation_count_only_and_repair_only_add_durable_continuation(next_pg_schema) -> None:
    del next_pg_schema
    with _connect() as conn:
        _seed_product(conn, code="r08_reconciliation")
        order = _seed_order(
            conn,
            code="r08_reconciliation",
            out_trade_no="WXP_R08_RECONCILIATION",
        )
        historical_order = _seed_order(
            conn,
            code="r08_reconciliation",
            out_trade_no="WXP_R08_RECONCILIATION_HISTORICAL",
        )
        conn.execute(
            """
            UPDATE wechat_pay_orders
            SET paid_at = TIMESTAMPTZ '2026-07-13 09:46:08+00',
                created_at = TIMESTAMPTZ '2026-07-13 09:46:08+00',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (historical_order["id"],),
        )
        conn.execute(
            """
            INSERT INTO domain_event_outbox (
                tenant_id, event_type, aggregate_type, aggregate_id, payload, status
            ) VALUES ('aicrm', 'transaction.paid', 'wechat_pay_order', %s, '{}'::jsonb, 'pending')
            """,
            (str(order["id"]),),
        )
        conn.commit()

    service = CommerceFulfillmentReconciliationService(database_url=_database_url())
    before = service.diagnose()
    with _connect() as conn:
        before_outbox = conn.execute("SELECT COUNT(*) AS total FROM internal_event_outbox").fetchone()["total"]
    assert before["counts"]["paid_without_payment_outbox"] == 1
    assert before["counts"]["legacy_domain_outbox_pending"] == 1
    assert before_outbox == 0
    assert before["database_mutation_performed"] is False

    repaired = service.repair(actor="r08-operator@example.test", reason="restore durable payment continuation")
    serialized = json.dumps(repaired, ensure_ascii=False)
    with _connect() as conn:
        outbox = conn.execute(
            "SELECT * FROM internal_event_outbox WHERE idempotency_key = 'payment.succeeded:WXP_R08_RECONCILIATION'"
        ).fetchone()
        effect_total = conn.execute("SELECT COUNT(*) AS total FROM external_effect_job").fetchone()["total"]
    assert repaired["repaired"]["payment_succeeded_outbox_count"] == 1
    assert repaired["real_external_call_executed"] is False
    assert repaired["consumer_executed"] is False
    assert repaired["pii_in_output"] is False
    assert "r08-operator@example.test" not in serialized
    assert outbox["actor_id"].startswith("repair:")
    assert outbox["payload_summary_json"]["repair_reason"] == "restore durable payment continuation"
    assert effect_total == 0
    assert repaired["after"]["counts"]["paid_without_payment_outbox"] == 0


def test_r08_migration_adds_refund_uniqueness_and_active_lookup_indexes(next_pg_schema) -> None:
    del next_pg_schema
    with _connect() as conn:
        indexes = {
            row["indexname"]: row["indexdef"]
            for row in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'wechat_pay_refunds'"
            ).fetchall()
        }

    assert "uq_wechat_pay_refunds_out_refund_no" in indexes
    assert "UNIQUE INDEX" in indexes["uq_wechat_pay_refunds_out_refund_no"]
    assert "WHERE (out_refund_no <> ''::text)" in indexes["uq_wechat_pay_refunds_out_refund_no"]
    assert "uq_wechat_pay_refunds_refund_id" in indexes
    assert "idx_wechat_pay_refunds_order_active" in indexes


def test_r08_migration_is_safe_without_optional_pre_alembic_refund_table() -> None:
    source = Path("migrations/versions/0101_commerce_fulfillment_invariants.py").read_text(encoding="utf-8")

    assert 'inspect(op.get_bind()).has_table("wechat_pay_refunds")' in source
    assert "return" in source.split('has_table("wechat_pay_refunds")', 1)[1].split("op.execute", 1)[0]
