from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.extensions.commerce.commerce.coupons.repo import (
    PostgresCouponRepository,
    build_coupon_order_repository,
    request_key_hash,
    target_ref_for_product_id,
)
from aicrm_next.extensions.commerce.commerce.repo import PostgresCommerceRepository
from aicrm_next.shared.errors import ContractError


TENANT_ID = "aicrm"
DISCOUNT_AMOUNT_TOTAL = 1_000
PRODUCT_AMOUNT_TOTAL = 10_000


def _database_url() -> str:
    return str(
        os.getenv("AICRM_TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()


def _raw_connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


class _IndependentConnectionCouponRepository(PostgresCouponRepository):
    """Exercise production SQL while giving every contender its own PG session."""

    def _connect(self):
        return _raw_connect()


class _IndependentConnectionCommerceRepository(PostgresCommerceRepository):
    def _connect(self):
        return _raw_connect()


class _HookedConnection:
    def __init__(
        self,
        connection: Any,
        *,
        sql_marker: str,
        before_execute: Event | None = None,
        after_execute: Event | None = None,
        resume_after_execute: Event | None = None,
    ) -> None:
        self._connection = connection
        self._sql_marker = sql_marker
        self._before_execute = before_execute
        self._after_execute = after_execute
        self._resume_after_execute = resume_after_execute
        self._triggered = False

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._connection.__exit__(exc_type, exc, traceback)

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def execute(self, query: str, params: Any = None):
        normalized = " ".join(str(query).split())
        should_trigger = not self._triggered and self._sql_marker in normalized
        if should_trigger:
            self._triggered = True
            if self._before_execute is not None:
                self._before_execute.set()
        result = self._connection.execute(query, params)
        if should_trigger:
            if self._after_execute is not None:
                self._after_execute.set()
            if self._resume_after_execute is not None and not self._resume_after_execute.wait(20):
                raise TimeoutError(f"timed out waiting after SQL marker: {self._sql_marker}")
        return result


class _HookedCouponRepository(_IndependentConnectionCouponRepository):
    def __init__(self, database_url: str, **hook: Any) -> None:
        super().__init__(database_url)
        self._hook = hook

    def _connect(self):
        return _HookedConnection(_raw_connect(), **self._hook)


class _HookedCommerceRepository(_IndependentConnectionCommerceRepository):
    def __init__(self, database_url: str, **hook: Any) -> None:
        super().__init__(database_url)
        self._hook = hook

    def _connect(self):
        return _HookedConnection(_raw_connect(), **self._hook)


def _seed_product(conn: Any, *, code: str) -> int:
    row = conn.execute(
        """
        INSERT INTO wechat_pay_products (
            product_code, name, amount_total, currency, status, enabled
        )
        VALUES (%s, %s, %s, 'CNY', 'active', TRUE)
        RETURNING id
        """,
        (code, f"Coupon concurrency {code}", PRODUCT_AMOUNT_TOTAL),
    ).fetchone()
    return int(row["id"])


def _seed_coupon(
    conn: Any,
    *,
    product_id: int,
    public_slug: str,
    total_issue_limit: int,
    per_user_issue_limit: int,
    now: datetime,
    discount_amount_total: int = DISCOUNT_AMOUNT_TOTAL,
) -> int:
    row = conn.execute(
        """
        INSERT INTO commerce_coupons (
            tenant_id, public_slug, name, discount_amount_total, currency,
            status, total_issue_limit, per_user_issue_limit, issued_count,
            claim_starts_at, claim_ends_at, validity_mode,
            relative_validity_days, instructions
        )
        VALUES (
            %s, %s, %s, %s, 'CNY',
            'published', %s, %s, 0,
            %s, %s, 'relative_days', 2, ''
        )
        RETURNING id
        """,
        (
            TENANT_ID,
            public_slug,
            f"Concurrency {public_slug}",
            discount_amount_total,
            total_issue_limit,
            per_user_issue_limit,
            now - timedelta(days=1),
            now + timedelta(days=1),
        ),
    ).fetchone()
    coupon_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO commerce_coupon_product_bindings (
            tenant_id, coupon_id, trade_product_id
        )
        VALUES (%s, %s, %s)
        """,
        (TENANT_ID, coupon_id, product_id),
    )
    return coupon_id


def _seed_coupon_scenario(
    *,
    public_slug: str,
    total_issue_limit: int,
    per_user_issue_limit: int,
) -> tuple[int, int, datetime]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with _raw_connect() as conn:
        product_id = _seed_product(conn, code=f"product-{public_slug}")
        coupon_id = _seed_coupon(
            conn,
            product_id=product_id,
            public_slug=public_slug,
            total_issue_limit=total_issue_limit,
            per_user_issue_limit=per_user_issue_limit,
            now=now,
        )
        conn.commit()
    return coupon_id, product_id, now


def _run_claim_contenders(
    *,
    public_slug: str,
    contenders: list[tuple[str, str]],
    now: datetime,
) -> list[tuple[str, dict[str, Any] | str]]:
    barrier = Barrier(len(contenders))

    def claim(contender: tuple[str, str]) -> tuple[str, dict[str, Any] | str]:
        unionid, idempotency_key = contender
        repository = _IndependentConnectionCouponRepository(_database_url())
        barrier.wait(timeout=20)
        try:
            result = repository.claim_coupon(
                public_slug,
                unionid=unionid,
                idempotency_hash=request_key_hash(idempotency_key),
                now=now,
            )
        except ContractError as exc:
            return "contract_error", str(exc)
        return "ok", result

    with ThreadPoolExecutor(max_workers=len(contenders)) as executor:
        futures = [executor.submit(claim, contender) for contender in contenders]
        return [future.result(timeout=30) for future in futures]


def _coupon_counts(coupon_id: int) -> dict[str, int]:
    with _raw_connect() as conn:
        row = conn.execute(
            """
            SELECT
                coupon.issued_count,
                count(claim.id) AS claim_count,
                count(DISTINCT claim.unionid) AS distinct_user_count
            FROM commerce_coupons coupon
            LEFT JOIN commerce_coupon_claims claim
              ON claim.coupon_id = coupon.id
             AND claim.tenant_id = coupon.tenant_id
            WHERE coupon.tenant_id = %s AND coupon.id = %s
            GROUP BY coupon.id, coupon.issued_count
            """,
            (TENANT_ID, coupon_id),
        ).fetchone()
    return {
        "issued_count": int(row["issued_count"]),
        "claim_count": int(row["claim_count"]),
        "distinct_user_count": int(row["distinct_user_count"]),
    }


def _product_update_payload(*, product_code: str, amount_total: int) -> dict[str, Any]:
    return {
        "product_code": product_code,
        "title": f"Updated {product_code}",
        "price_cents": amount_total,
        "currency": "CNY",
        "status": "active",
        "enabled": True,
    }


def _product_amount(product_id: int) -> int:
    with _raw_connect() as conn:
        row = conn.execute(
            "SELECT amount_total FROM wechat_pay_products WHERE id = %s",
            (product_id,),
        ).fetchone()
    return int(row["amount_total"])


def test_last_coupon_concurrent_claims_never_oversell(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, _product_id, now = _seed_coupon_scenario(
        public_slug="last-stock",
        total_issue_limit=1,
        per_user_issue_limit=1,
    )
    contender_count = 12

    results = _run_claim_contenders(
        public_slug="last-stock",
        contenders=[
            (f"union-last-stock-{index}", f"last-stock-key-{index}")
            for index in range(contender_count)
        ],
        now=now,
    )

    successes = [payload for status, payload in results if status == "ok"]
    failures = [payload for status, payload in results if status == "contract_error"]
    assert len(successes) == 1
    assert failures == ["coupon is sold out"] * (contender_count - 1)
    assert _coupon_counts(coupon_id) == {
        "issued_count": 1,
        "claim_count": 1,
        "distinct_user_count": 1,
    }


def test_same_user_concurrent_claims_respect_per_user_limit(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, _product_id, now = _seed_coupon_scenario(
        public_slug="per-user-limit",
        total_issue_limit=12,
        per_user_issue_limit=1,
    )
    contender_count = 10

    results = _run_claim_contenders(
        public_slug="per-user-limit",
        contenders=[
            ("union-same-user", f"same-user-key-{index}")
            for index in range(contender_count)
        ],
        now=now,
    )

    successes = [payload for status, payload in results if status == "ok"]
    failures = [payload for status, payload in results if status == "contract_error"]
    assert len(successes) == 1
    assert failures == ["per-user coupon claim limit reached"] * (contender_count - 1)
    assert _coupon_counts(coupon_id) == {
        "issued_count": 1,
        "claim_count": 1,
        "distinct_user_count": 1,
    }


def test_same_idempotency_key_concurrent_retries_create_one_claim(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, _product_id, now = _seed_coupon_scenario(
        public_slug="idempotent-claim",
        total_issue_limit=12,
        per_user_issue_limit=12,
    )
    contender_count = 10

    results = _run_claim_contenders(
        public_slug="idempotent-claim",
        contenders=[("union-idempotent", "shared-idempotency-key")] * contender_count,
        now=now,
    )

    assert [status for status, _payload in results] == ["ok"] * contender_count
    payloads = [payload for _status, payload in results]
    assert sum(not bool(payload["idempotent"]) for payload in payloads) == 1
    assert sum(bool(payload["idempotent"]) for payload in payloads) == contender_count - 1
    assert len({str(payload["claim"]["claim_no"]) for payload in payloads}) == 1
    assert _coupon_counts(coupon_id) == {
        "issued_count": 1,
        "claim_count": 1,
        "distinct_user_count": 1,
    }


def test_price_decrease_committing_first_makes_waiting_claim_fail(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, product_id, now = _seed_coupon_scenario(
        public_slug="price-first-race",
        total_issue_limit=1,
        per_user_issue_limit=1,
    )
    product_locked = Event()
    allow_price_update = Event()
    claim_attempted_product_lock = Event()
    updater = _HookedCommerceRepository(
        _database_url(),
        sql_marker="SELECT product_code, amount_total FROM wechat_pay_products",
        after_execute=product_locked,
        resume_after_execute=allow_price_update,
    )
    claimant = _HookedCouponRepository(
        _database_url(),
        sql_marker="FOR SHARE OF product",
        before_execute=claim_attempted_product_lock,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        update_future = executor.submit(
            updater.save_product,
            _product_update_payload(
                product_code="product-price-first-race",
                amount_total=DISCOUNT_AMOUNT_TOTAL,
            ),
            str(product_id),
        )
        assert product_locked.wait(10)
        claim_future = executor.submit(
            claimant.claim_coupon,
            "price-first-race",
            unionid="union-price-first",
            idempotency_hash=request_key_hash("price-first-key"),
            now=now,
        )
        assert claim_attempted_product_lock.wait(10)
        allow_price_update.set()
        updated_product = update_future.result(timeout=20)
        with pytest.raises(
            ContractError,
            match="discount_amount_total must be less than every selected product price",
        ):
            claim_future.result(timeout=20)

    assert int(updated_product["price_cents"]) == DISCOUNT_AMOUNT_TOTAL
    assert _product_amount(product_id) == DISCOUNT_AMOUNT_TOTAL
    assert _coupon_counts(coupon_id) == {
        "issued_count": 0,
        "claim_count": 0,
        "distinct_user_count": 0,
    }


def test_claim_committing_first_makes_waiting_price_decrease_fail(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, product_id, now = _seed_coupon_scenario(
        public_slug="claim-first-race",
        total_issue_limit=1,
        per_user_issue_limit=1,
    )
    claim_locked_product = Event()
    allow_claim_to_commit = Event()
    price_update_attempted_lock = Event()
    claimant = _HookedCouponRepository(
        _database_url(),
        sql_marker="FOR SHARE OF product",
        after_execute=claim_locked_product,
        resume_after_execute=allow_claim_to_commit,
    )
    updater = _HookedCommerceRepository(
        _database_url(),
        sql_marker="SELECT product_code, amount_total FROM wechat_pay_products",
        before_execute=price_update_attempted_lock,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_future = executor.submit(
            claimant.claim_coupon,
            "claim-first-race",
            unionid="union-claim-first",
            idempotency_hash=request_key_hash("claim-first-key"),
            now=now,
        )
        assert claim_locked_product.wait(10)
        update_future = executor.submit(
            updater.save_product,
            _product_update_payload(
                product_code="product-claim-first-race",
                amount_total=DISCOUNT_AMOUNT_TOTAL,
            ),
            str(product_id),
        )
        assert price_update_attempted_lock.wait(10)
        allow_claim_to_commit.set()
        claim = claim_future.result(timeout=20)
        with pytest.raises(ContractError, match="商品价格不能低于或等于未过期优惠券的减免金额"):
            update_future.result(timeout=20)

    assert claim["claim"]["status"] == "available"
    assert _product_amount(product_id) == PRODUCT_AMOUNT_TOTAL
    assert _coupon_counts(coupon_id) == {
        "issued_count": 1,
        "claim_count": 1,
        "distinct_user_count": 1,
    }


def test_list_available_claims_uses_discount_then_expiry_order_in_postgres(next_pg_schema) -> None:
    del next_pg_schema
    now = datetime.now(timezone.utc).replace(microsecond=0)
    claim_specs = (
        ("high-late", 2_000, now + timedelta(days=3)),
        ("high-early", 2_000, now + timedelta(days=1)),
        ("low-earliest", 1_000, now + timedelta(hours=12)),
    )
    with _raw_connect() as conn:
        product_id = _seed_product(conn, code="product-available-order")
        for public_slug, discount, _valid_until in claim_specs:
            _seed_coupon(
                conn,
                product_id=product_id,
                public_slug=public_slug,
                total_issue_limit=1,
                per_user_issue_limit=1,
                now=now,
                discount_amount_total=discount,
            )
        conn.commit()

    repository = _IndependentConnectionCouponRepository(_database_url())
    claims: dict[str, dict[str, Any]] = {}
    for public_slug, _discount, _valid_until in claim_specs:
        claims[public_slug] = repository.claim_coupon(
            public_slug,
            unionid="union-available-order",
            idempotency_hash=request_key_hash(f"available-order-{public_slug}"),
            now=now,
        )["claim"]

    with _raw_connect() as conn:
        for public_slug, _discount, valid_until in claim_specs:
            conn.execute(
                "UPDATE commerce_coupon_claims SET valid_until = %s WHERE id = %s",
                (valid_until, int(claims[public_slug]["id"])),
            )
        conn.commit()

    result = repository.list_available_claims(
        target_ref_for_product_id(product_id),
        unionid="union-available-order",
        now=now,
    )

    assert result["ok"] is True
    assert result["total"] == 3
    assert [item["claim_no"] for item in result["items"]] == [
        claims["high-early"]["claim_no"],
        claims["high-late"]["claim_no"],
        claims["low-earliest"]["claim_no"],
    ]


def _seed_orders(
    conn: Any,
    *,
    product_code: str,
    unionid: str,
    count: int,
    now: datetime,
) -> list[int]:
    order_ids: list[int] = []
    for index in range(count):
        row = conn.execute(
            """
            INSERT INTO wechat_pay_orders (
                out_trade_no, order_source, client_order_ref,
                product_code, product_name, description,
                amount_total, subtotal_amount_total, discount_amount_total,
                currency, unionid, status, expires_at
            )
            VALUES (
                %s, 'coupon_concurrency_test', %s,
                %s, %s, %s,
                %s, %s, 0,
                'CNY', %s, 'created', %s
            )
            RETURNING id
            """,
            (
                f"WXP_COUPON_RESERVE_{index}",
                f"coupon-reserve-{index}",
                product_code,
                "Coupon reservation product",
                "Coupon reservation concurrency test",
                PRODUCT_AMOUNT_TOTAL,
                PRODUCT_AMOUNT_TOTAL,
                unionid,
                now + timedelta(minutes=15),
            ),
        ).fetchone()
        order_ids.append(int(row["id"]))
    return order_ids


def test_same_claim_concurrent_reservations_have_one_active_redemption(next_pg_schema) -> None:
    del next_pg_schema
    coupon_id, product_id, now = _seed_coupon_scenario(
        public_slug="reserve-one-claim",
        total_issue_limit=1,
        per_user_issue_limit=1,
    )
    claim_payload = _IndependentConnectionCouponRepository(_database_url()).claim_coupon(
        "reserve-one-claim",
        unionid="union-reserve-one",
        idempotency_hash=request_key_hash("reserve-one-claim-key"),
        now=now,
    )
    claim_no = str(claim_payload["claim"]["claim_no"])
    contender_count = 10
    with _raw_connect() as conn:
        product_code = str(
            conn.execute(
                "SELECT product_code FROM wechat_pay_products WHERE id = %s",
                (product_id,),
            ).fetchone()["product_code"]
        )
        order_ids = _seed_orders(
            conn,
            product_code=product_code,
            unionid="union-reserve-one",
            count=contender_count,
            now=now,
        )
        conn.commit()

    barrier = Barrier(contender_count)

    def reserve(order_id: int) -> tuple[str, int | str]:
        with _raw_connect() as conn:
            order = conn.execute(
                "SELECT * FROM wechat_pay_orders WHERE id = %s",
                (order_id,),
            ).fetchone()
            barrier.wait(timeout=20)
            try:
                updated_order = build_coupon_order_repository(conn).reserve_coupon_for_order(
                    order=order,
                    choice_mode="claim",
                    claim_no=claim_no,
                    unionid="union-reserve-one",
                    trade_product_id=product_id,
                    now=now,
                )
            except ContractError as exc:
                conn.rollback()
                return "contract_error", str(exc)
            conn.commit()
            return "ok", int(updated_order["id"])

    with ThreadPoolExecutor(max_workers=contender_count) as executor:
        futures = [executor.submit(reserve, order_id) for order_id in order_ids]
        results = [future.result(timeout=30) for future in futures]

    successes = [payload for status, payload in results if status == "ok"]
    failures = [payload for status, payload in results if status == "contract_error"]
    assert len(successes) == 1
    assert failures == ["coupon_unavailable"] * (contender_count - 1)

    with _raw_connect() as conn:
        state = conn.execute(
            """
            SELECT
                coupon.issued_count,
                claim.status AS claim_status,
                count(redemption.id) FILTER (
                    WHERE redemption.status IN ('reserved', 'consumed')
                ) AS active_redemption_count,
                count(orders.id) FILTER (
                    WHERE orders.coupon_claim_id = claim.id
                ) AS discounted_order_count
            FROM commerce_coupons coupon
            JOIN commerce_coupon_claims claim
              ON claim.coupon_id = coupon.id
             AND claim.tenant_id = coupon.tenant_id
            LEFT JOIN commerce_coupon_redemptions redemption
              ON redemption.claim_id = claim.id
            LEFT JOIN wechat_pay_orders orders
              ON orders.coupon_claim_id = claim.id
            WHERE coupon.tenant_id = %s AND coupon.id = %s
            GROUP BY coupon.id, coupon.issued_count, claim.id, claim.status
            """,
            (TENANT_ID, coupon_id),
        ).fetchone()
    assert dict(state) == {
        "issued_count": 1,
        "claim_status": "reserved",
        "active_redemption_count": 1,
        "discounted_order_count": 1,
    }
