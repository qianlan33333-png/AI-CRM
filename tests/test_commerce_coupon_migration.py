from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aicrm_next.platform.shared.db_session import get_session_factory


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0114_commerce_coupons.py"


def test_commerce_coupon_migration_contract_is_complete_and_chained() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0114_commerce_coupons"' in source
    assert 'down_revision = "0113_operation_cycles"' in source
    for table_name in (
        "commerce_coupons",
        "commerce_coupon_product_bindings",
        "commerce_coupon_claims",
        "commerce_coupon_redemptions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source

    for column_name in (
        "discount_amount_total",
        "total_issue_limit",
        "per_user_issue_limit",
        "issued_count",
        "coupon_id",
        "trade_product_id",
        "claim_no",
        "unionid",
        "valid_from",
        "valid_until",
        "idempotency_key_hash",
        "claim_id",
        "order_id",
        "out_trade_no",
        "original_amount_total",
        "payable_amount_total",
        "reserved_until",
    ):
        assert column_name in source

    assert "status IN ('draft', 'published', 'stopped', 'archived')" in source
    assert "status IN ('available', 'reserved', 'consumed', 'expired')" in source
    assert "status IN ('reserved', 'consumed', 'released')" in source
    assert "validity_mode IN ('fixed_range', 'relative_days')" in source
    assert "uq_commerce_coupon_claim_idempotency" in source
    assert "uq_commerce_coupons_tenant_id" in source
    assert "uq_commerce_coupon_claims_tenant_id" in source
    assert "fk_commerce_coupon_bindings_coupon_tenant" in source
    assert "fk_commerce_coupon_claims_coupon_tenant" in source
    assert "fk_commerce_coupon_redemptions_claim_tenant" in source
    assert "FOREIGN KEY (tenant_id, coupon_id)" in source
    assert "REFERENCES commerce_coupons (tenant_id, id)" in source
    assert "FOREIGN KEY (tenant_id, claim_id)" in source
    assert "REFERENCES commerce_coupon_claims (tenant_id, id)" in source
    assert "uq_commerce_coupon_redemptions_active_claim" in source
    assert "WHERE status IN ('reserved', 'consumed')" in source
    assert "payable_amount_total = original_amount_total - discount_amount_total" in source


def test_commerce_coupon_migration_extends_orders_additively_and_reversibly() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    for column_name in (
        "subtotal_amount_total",
        "discount_amount_total",
        "coupon_claim_id",
        "coupon_snapshot_json",
        "reconciliation_not_found_count",
        "reconciliation_last_checked_at",
        "provider_unknown_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column_name}" in source
        assert f"DROP COLUMN IF EXISTS {column_name}" in source

    assert "SET subtotal_amount_total = amount_total" in source
    assert "ck_wechat_pay_orders_coupon_amounts" in source
    assert "amount_total = subtotal_amount_total - discount_amount_total" in source
    assert "(coupon_claim_id IS NULL AND discount_amount_total = 0)" in source
    assert "(coupon_claim_id IS NOT NULL AND discount_amount_total > 0)" in source
    assert "commerce_sync_wechat_pay_order_coupon_amounts" in source
    assert "trg_wechat_pay_orders_coupon_amounts" in source
    assert "fk_wechat_pay_orders_coupon_claim" in source
    assert "fk_commerce_coupon_redemptions_order" in source


def _assert_constraint_violation(session, sql: str, params: dict, constraint_name: str) -> None:
    with pytest.raises(IntegrityError) as violation:
        session.execute(text(sql), params)
        session.commit()
    session.rollback()
    assert violation.value.orig.diag.constraint_name == constraint_name


def test_coupon_tenant_foreign_keys_reject_cross_tenant_rows(next_pg_schema) -> None:
    del next_pg_schema
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with get_session_factory()() as session:
        product_id = session.execute(
            text(
                """
                INSERT INTO wechat_pay_products (
                    product_code, name, amount_total, currency, status, enabled
                ) VALUES (
                    'coupon-tenant-fk-product', 'Coupon tenant FK product',
                    10000, 'CNY', 'active', TRUE
                )
                RETURNING id
                """
            )
        ).scalar_one()
        coupon_id = session.execute(
            text(
                """
                INSERT INTO commerce_coupons (
                    tenant_id, public_slug, name, discount_amount_total, currency,
                    status, total_issue_limit, per_user_issue_limit,
                    claim_starts_at, claim_ends_at, validity_mode,
                    relative_validity_days
                ) VALUES (
                    'tenant-a', 'coupon-tenant-fk', 'Coupon tenant FK', 1000, 'CNY',
                    'published', 10, 1, :claim_starts_at, :claim_ends_at,
                    'relative_days', 2
                )
                RETURNING id
                """
            ),
            {
                "claim_starts_at": now - timedelta(days=1),
                "claim_ends_at": now + timedelta(days=1),
            },
        ).scalar_one()
        session.commit()

        _assert_constraint_violation(
            session,
            """
            INSERT INTO commerce_coupon_product_bindings (
                tenant_id, coupon_id, trade_product_id
            ) VALUES ('tenant-b', :coupon_id, :product_id)
            """,
            {"coupon_id": coupon_id, "product_id": product_id},
            "fk_commerce_coupon_bindings_coupon_tenant",
        )
        session.execute(
            text(
                """
                INSERT INTO commerce_coupon_product_bindings (
                    tenant_id, coupon_id, trade_product_id
                ) VALUES ('tenant-a', :coupon_id, :product_id)
                """
            ),
            {"coupon_id": coupon_id, "product_id": product_id},
        )
        session.commit()

        claim_values = {
            "coupon_id": coupon_id,
            "valid_from": now,
            "valid_until": now + timedelta(days=2),
        }
        _assert_constraint_violation(
            session,
            """
            INSERT INTO commerce_coupon_claims (
                tenant_id, coupon_id, claim_no, unionid,
                discount_amount_total, currency, valid_from, valid_until,
                status, idempotency_key_hash, claimed_at
            ) VALUES (
                'tenant-b', :coupon_id, 'claim-cross-tenant', 'union-tenant-fk',
                1000, 'CNY', :valid_from, :valid_until,
                'available', 'claim-cross-tenant-key', :valid_from
            )
            """,
            claim_values,
            "fk_commerce_coupon_claims_coupon_tenant",
        )
        claim_id = session.execute(
            text(
                """
                INSERT INTO commerce_coupon_claims (
                    tenant_id, coupon_id, claim_no, unionid,
                    discount_amount_total, currency, valid_from, valid_until,
                    status, idempotency_key_hash, claimed_at
                ) VALUES (
                    'tenant-a', :coupon_id, 'claim-same-tenant', 'union-tenant-fk',
                    1000, 'CNY', :valid_from, :valid_until,
                    'available', 'claim-same-tenant-key', :valid_from
                )
                RETURNING id
                """
            ),
            claim_values,
        ).scalar_one()
        order_id = session.execute(
            text(
                """
                INSERT INTO wechat_pay_orders (
                    out_trade_no, product_code, product_name,
                    amount_total, currency, unionid, status, expires_at
                ) VALUES (
                    'WXP_COUPON_TENANT_FK', 'coupon-tenant-fk-product',
                    'Coupon tenant FK product', 10000, 'CNY',
                    'union-tenant-fk', 'created', :reserved_until
                )
                RETURNING id
                """
            ),
            {"reserved_until": now + timedelta(minutes=15)},
        ).scalar_one()
        session.commit()

        _assert_constraint_violation(
            session,
            """
            INSERT INTO commerce_coupon_redemptions (
                tenant_id, claim_id, order_id, out_trade_no, status,
                original_amount_total, discount_amount_total,
                payable_amount_total, currency, reserved_until
            ) VALUES (
                'tenant-b', :claim_id, :order_id, 'WXP_COUPON_TENANT_FK',
                'reserved', 10000, 1000, 9000, 'CNY', :reserved_until
            )
            """,
            {
                "claim_id": claim_id,
                "order_id": order_id,
                "reserved_until": now + timedelta(minutes=15),
            },
            "fk_commerce_coupon_redemptions_claim_tenant",
        )


def test_commerce_coupon_schema_is_available_after_upgrade(next_pg_schema) -> None:
    with get_session_factory()() as session:
        table_names = {
            row["table_name"]
            for row in session.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name LIKE 'commerce_coupon%'
                    """
                )
            ).mappings()
        }
        order_columns = {
            row["column_name"]
            for row in session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'wechat_pay_orders'
                    """
                )
            ).mappings()
        }
        constraint_definitions = {
            row["conname"]: row["definition"]
            for row in session.execute(
                text(
                    """
                    SELECT constraint_row.conname, pg_get_constraintdef(constraint_row.oid) AS definition
                    FROM pg_constraint constraint_row
                    WHERE constraint_row.conrelid IN (
                        'commerce_coupons'::regclass,
                        'commerce_coupon_product_bindings'::regclass,
                        'commerce_coupon_claims'::regclass,
                        'commerce_coupon_redemptions'::regclass,
                        'wechat_pay_orders'::regclass
                    )
                    """
                )
            ).mappings()
        }
        index_names = {
            row["indexname"]
            for row in session.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename IN ('commerce_coupons', 'commerce_coupon_claims', 'commerce_coupon_redemptions')
                    """
                )
            ).mappings()
        }

    assert {
        "commerce_coupons",
        "commerce_coupon_product_bindings",
        "commerce_coupon_claims",
        "commerce_coupon_redemptions",
    } <= table_names
    assert {
        "subtotal_amount_total",
        "discount_amount_total",
        "coupon_claim_id",
        "coupon_snapshot_json",
        "reconciliation_not_found_count",
        "reconciliation_last_checked_at",
        "provider_unknown_at",
    } <= order_columns
    assert "ck_commerce_coupons_validity_configuration" in constraint_definitions
    assert "ck_commerce_coupon_claims_validity" in constraint_definitions
    assert "ck_commerce_coupon_redemptions_amounts" in constraint_definitions
    assert "ck_wechat_pay_orders_coupon_amounts" in constraint_definitions
    assert "uq_commerce_coupons_tenant_id" in constraint_definitions
    assert "uq_commerce_coupon_claims_tenant_id" in constraint_definitions
    assert "fk_commerce_coupon_bindings_coupon_tenant" in constraint_definitions
    assert "fk_commerce_coupon_claims_coupon_tenant" in constraint_definitions
    assert "fk_commerce_coupon_redemptions_claim_tenant" in constraint_definitions
    assert {
        "uq_commerce_coupons_public_slug",
        "uq_commerce_coupon_claims_claim_no",
        "uq_commerce_coupon_redemptions_active_claim",
        "uq_commerce_coupon_redemptions_order_id",
        "uq_commerce_coupon_redemptions_out_trade_no",
    } <= index_names


def test_coupon_order_amount_guard_keeps_legacy_writes_compatible(next_pg_schema) -> None:
    """Old writers get a subtotal snapshot, while discount-without-claim is rejected."""

    with get_session_factory()() as session:
        created = session.execute(
            text(
                """
                INSERT INTO wechat_pay_orders (out_trade_no, amount_total, currency)
                VALUES ('WXP_COUPON_LEGACY_WRITER', 9900, 'CNY')
                RETURNING id, amount_total, subtotal_amount_total, discount_amount_total,
                          reconciliation_not_found_count,
                          reconciliation_last_checked_at,
                          provider_unknown_at
                """
            )
        ).mappings().one()
        session.commit()

        assert created["subtotal_amount_total"] == 9900
        assert created["discount_amount_total"] == 0
        assert created["reconciliation_not_found_count"] == 0
        assert created["reconciliation_last_checked_at"] is None
        assert created["provider_unknown_at"] is None

        updated = session.execute(
            text(
                """
                UPDATE wechat_pay_orders
                SET amount_total = 8800
                WHERE id = :order_id
                RETURNING amount_total, subtotal_amount_total, discount_amount_total
                """
            ),
            {"order_id": created["id"]},
        ).mappings().one()
        session.commit()

        assert updated["subtotal_amount_total"] == 8800
        assert updated["discount_amount_total"] == 0

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    UPDATE wechat_pay_orders
                    SET subtotal_amount_total = 8800,
                        discount_amount_total = 100,
                        amount_total = 8700
                    WHERE id = :order_id
                    """
                ),
                {"order_id": created["id"]},
            )
            session.commit()
        session.rollback()
