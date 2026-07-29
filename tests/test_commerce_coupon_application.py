from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aicrm_next.extensions.commerce.commerce.coupons.application import (
    CouponAdminApplication,
    CouponPublicApplication,
    assert_product_price_allows_coupons,
    consume_coupon_for_paid_order,
    coupon_consistency_counts,
    normalize_coupon_choice,
    product_id_from_target_ref,
    release_coupon_for_order,
    reserve_coupon_for_order,
    target_ref_for_product_id,
)
from aicrm_next.extensions.commerce.commerce.coupons.domain import CouponChoiceMode, CouponClaimStatus
from aicrm_next.extensions.commerce.commerce.coupons.dto import CouponUpsertRequest
from aicrm_next.extensions.commerce.commerce.coupons.repo import InMemoryCouponRepository, PostgresCouponRepository
from aicrm_next.platform.shared.errors import ContractError


UTC = timezone.utc


def _products() -> list[dict[str, Any]]:
    return [
        {
            "id": "101",
            "product_code": "ordinary-course",
            "name": "普通商品",
            "amount_total": 10_000,
            "currency": "CNY",
            "status": "active",
            "enabled": True,
        },
        {
            "id": "202",
            "product_code": "period-membership",
            "name": "周期商品",
            "amount_total": 20_000,
            "currency": "CNY",
            "status": "active",
            "enabled": True,
            "service_period_id": 9,
            "link_slug": "period-membership",
            "duration_days": 30,
        },
    ]


def _request(
    repository: InMemoryCouponRepository,
    *,
    name: str = "新人减免券",
    discount: int = 1_000,
    total: int = 10,
    target_index: int = 0,
) -> CouponUpsertRequest:
    now = datetime.now(UTC)
    target_ref = repository.list_product_options(
        q="",
        product_type="all",
        limit=20,
        offset=0,
    )["items"][target_index]["target_ref"]
    return CouponUpsertRequest(
        name=name,
        discount_amount_total=discount,
        total_issue_limit=total,
        per_user_issue_limit=1,
        claim_starts_at=now - timedelta(minutes=5),
        claim_ends_at=now + timedelta(days=2),
        validity_mode="relative_days",
        relative_validity_days=3,
        target_refs=[target_ref],
        instructions="每单限用一张",
    )


def test_target_ref_is_signed_round_trippable_and_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "coupon-target-test-secret")
    target_ref = target_ref_for_product_id(101)

    assert target_ref.startswith("cpt_")
    assert "MTAx" not in target_ref  # base64("101") must not leak the database id
    assert product_id_from_target_ref(target_ref) == "101"
    with pytest.raises(ContractError, match="invalid coupon target_ref"):
        product_id_from_target_ref(target_ref[:-1] + ("A" if target_ref[-1] != "A" else "B"))


def test_target_ref_fails_closed_without_production_signing_secret(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.commerce.coupons import target_refs

    monkeypatch.setattr(target_refs, "runtime_setting", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(target_refs, "production_environment", lambda: True)

    with pytest.raises(ContractError, match="secret is not configured"):
        target_refs.target_ref_for_product_id(101)


def test_admin_application_unifies_ordinary_and_period_products_without_ids() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    application = CouponAdminApplication(repository)
    request = _request(repository)

    all_options = application.list_product_options(product_type="all")
    period_options = application.list_product_options(product_type="service_period")
    created = application.create_coupon(request)

    assert {item["product_type"] for item in all_options["items"]} == {
        "standard_product",
        "service_period",
    }
    assert len(period_options["items"]) == 1
    assert all("trade_product_id" not in item and "id" not in item for item in all_options["items"])
    assert created["coupon"]["status"] == "draft"
    assert created["coupon"]["products"][0]["title"] == "普通商品"
    assert "trade_product_id" not in created["coupon"]["products"][0]
    assert created["coupon"]["claim_starts_at_display"] == request.claim_starts_at.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y-%m-%d %H:%M")


def test_coupon_admin_share_uses_jpeg_qr_data_url() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    application = CouponAdminApplication(repository)
    coupon = application.create_coupon(_request(repository))["coupon"]

    share = application.get_share(coupon["id"], request_base_url="https://crm.example.test")["share"]

    assert share["url"].startswith("https://crm.example.test/c/")
    assert share["qr_data_url"].startswith("data:image/jpeg;base64,")


def test_admin_mutations_persist_authenticated_actor_and_reject_payload_spoofing() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    request = _request(repository)

    with pytest.raises(ValidationError, match="created_by"):
        CouponUpsertRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "created_by": "request-attacker",
                "updated_by": "request-attacker",
            }
        )

    created = CouponAdminApplication(repository, actor_id="admin-17").create_coupon(request)["coupon"]
    assert created["created_by"] == "admin-17"
    assert created["updated_by"] == "admin-17"

    update_request = CouponUpsertRequest.model_validate(
        {**request.model_dump(mode="python"), "name": "已审核优惠券"}
    )
    updated = CouponAdminApplication(repository, actor_id="principal-22").update_coupon(
        created["id"],
        update_request,
    )["coupon"]
    assert updated["created_by"] == "admin-17"
    assert updated["updated_by"] == "principal-22"

    repository_updated = repository.update_coupon(
        created["id"],
        {
            **update_request.model_dump(mode="python"),
            "created_by": "repository-attacker",
            "updated_by": "repository-attacker",
        },
        actor_id="trusted-repository-actor",
    )
    assert repository_updated["created_by"] == "admin-17"
    assert repository_updated["updated_by"] == "trusted-repository-actor"

    published = CouponAdminApplication(repository, actor_id="wecom-publisher").publish_coupon(
        created["id"]
    )["coupon"]
    assert published["updated_by"] == "wecom-publisher"

    stopped = CouponAdminApplication(repository, actor_id="wecom-stopper").stop_coupon(created["id"])[
        "coupon"
    ]
    assert stopped["updated_by"] == "wecom-stopper"

    archived = CouponAdminApplication(repository, actor_id="wecom-archiver").archive_coupon(
        created["id"]
    )["coupon"]
    assert archived["updated_by"] == "wecom-archiver"

    copied = CouponAdminApplication(repository, actor_id="wecom-copier").copy_coupon(created["id"])[
        "coupon"
    ]
    assert copied["created_by"] == "wecom-copier"
    assert copied["updated_by"] == "wecom-copier"


def test_deleted_period_product_stays_visible_but_is_not_purchaseable() -> None:
    repository = InMemoryCouponRepository(
        product_options=[
            {
                "id": "303",
                "product_code": "deleted-period",
                "name": "已下架周期商品",
                "amount_total": 20_000,
                "currency": "CNY",
                "status": "active",
                "enabled": True,
                "metadata_json": {"aicrm_product_owner": "service_period"},
            }
        ]
    )

    option = repository.list_product_options(
        q="",
        product_type="service_period",
        limit=20,
        offset=0,
    )["items"][0]

    assert option["product_type"] == "service_period"
    assert option["available"] is False


def test_public_claim_is_idempotent_freezes_rules_and_lists_best_coupon_first() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    admin = CouponAdminApplication(repository)
    public = CouponPublicApplication(repository)
    first = admin.create_coupon(_request(repository, name="十元券", discount=1_000))["coupon"]
    second = admin.create_coupon(_request(repository, name="十五元券", discount=1_500))["coupon"]
    admin.publish_coupon(first["id"])
    admin.publish_coupon(second["id"])
    identity = {"unionid": "union-coupon-user"}

    first_claim = public.claim_coupon(first["public_slug"], identity=identity, idempotency_key="first-key")
    retried_claim = public.claim_coupon(first["public_slug"], identity=identity, idempotency_key="first-key")
    public.claim_coupon(second["public_slug"], identity=identity, idempotency_key="second-key")
    available = public.list_available_claims(first["target_refs"][0], identity=identity)

    assert first_claim["claim"]["claim_no"] == retried_claim["claim"]["claim_no"]
    assert retried_claim["idempotent"] is True
    assert [item["discount_amount_total"] for item in available["items"]] == [1_500, 1_000]
    assert available["items"][0]["name"] == "十五元券"
    assert "unionid" not in available["items"][0]
    assert "id" not in available["items"][0]

    update_payload = {
        key: first[key]
        for key in (
            "name",
            "discount_amount_total",
            "total_issue_limit",
            "per_user_issue_limit",
            "claim_starts_at",
            "claim_ends_at",
            "validity_mode",
            "use_starts_at",
            "use_ends_at",
            "relative_validity_days",
            "instructions",
            "target_refs",
        )
    }
    frozen_change = CouponUpsertRequest.model_validate(
        {**update_payload, "name": "改名允许", "discount_amount_total": 900}
    )
    with pytest.raises(ContractError, match="frozen"):
        admin.update_coupon(first["id"], frozen_change)

    allowed = CouponUpsertRequest.model_validate(
        {**update_payload, "name": "改名允许", "total_issue_limit": 20}
    )
    updated = admin.update_coupon(first["id"], allowed)["coupon"]
    assert updated["name"] == "改名允许"
    assert updated["total_issue_limit"] == 20


def test_admin_claim_projection_never_exposes_raw_identifiers_but_public_keeps_claim_no() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    admin = CouponAdminApplication(repository)
    public = CouponPublicApplication(repository)
    coupon = admin.create_coupon(_request(repository))["coupon"]
    admin.publish_coupon(coupon["id"])
    claimed = public.claim_coupon(
        coupon["public_slug"],
        identity={"unionid": "union-sensitive-identity"},
        idempotency_key="projection-key",
    )["claim"]
    public_claim = public.list_available_claims(
        coupon["target_refs"][0],
        identity={"unionid": "union-sensitive-identity"},
    )["items"][0]
    repository._claims[0].update(
        {
            "order_id": 88,
            "redemption_id": 99,
            "out_trade_no": "WXP-SENSITIVE-ORDER-1234",
            "claimed_at": datetime(2026, 7, 15, 1, 2, tzinfo=UTC),
            "valid_from": datetime(2026, 7, 15, 2, 3, tzinfo=UTC),
            "valid_until": datetime(2026, 7, 18, 16, 0, tzinfo=UTC),
            "consumed_at": datetime(2026, 7, 16, 4, 5, tzinfo=UTC),
        }
    )

    admin_claim = admin.list_claims(coupon["id"], limit=20, offset=0)["items"][0]

    assert admin_claim["masked_identity"] != "union-sensitive-identity"
    assert admin_claim["claim_no_masked"] != claimed["claim_no"]
    assert admin_claim["order_no_masked"] != "WXP-SENSITIVE-ORDER-1234"
    assert admin_claim["claimed_at_display"] == "2026-07-15 09:02"
    assert admin_claim["valid_from_display"] == "2026-07-15 10:03"
    assert admin_claim["valid_until_display"] == "2026-07-19 00:00"
    assert admin_claim["consumed_at_display"] == "2026-07-16 12:05"
    assert not {
        "id",
        "coupon_id",
        "claim_id",
        "order_id",
        "redemption_id",
        "unionid",
        "claim_no",
        "out_trade_no",
    }.intersection(admin_claim)
    assert public_claim["claim_no"] == claimed["claim_no"]
    assert "out_trade_no" not in public_claim


class _CouponListResult:
    def __init__(self, *, row: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _CouponListConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> _CouponListResult:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if normalized.startswith("SELECT c.*"):
            return _CouponListResult(rows=[])
        if normalized.startswith("SELECT count(*)"):
            return _CouponListResult(row={"total": 0})
        raise AssertionError(f"unexpected SQL: {normalized}")


class _CouponAuditConnection:
    def __init__(self, *, locked: dict[str, Any] | None = None) -> None:
        self.locked = locked
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> _CouponListResult:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if normalized.startswith("SELECT * FROM commerce_coupons"):
            return _CouponListResult(row=self.locked)
        if normalized.startswith("INSERT INTO commerce_coupons"):
            return _CouponListResult(
                row={"id": 41, "created_by": params[-2], "updated_by": params[-1]}
            )
        if normalized.startswith("UPDATE commerce_coupons SET name"):
            return _CouponListResult(row={"id": 41, "updated_by": params[-3]})
        if normalized.startswith("UPDATE commerce_coupons SET status"):
            return _CouponListResult(row={"id": 41, "status": params[0], "updated_by": params[1]})
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        return None


def _postgres_audit_repository(connection: _CouponAuditConnection) -> PostgresCouponRepository:
    repository = PostgresCouponRepository("postgresql://unused")
    repository._connect = lambda: connection
    repository._resolve_target_refs = lambda _conn, _refs: []
    repository._replace_bindings = lambda _conn, _coupon_id, _options: None
    repository._binding_options = lambda _conn, _coupon_id: [{"amount_total": 10_000}]
    repository._coupon_payload = lambda _conn, row, **_kwargs: dict(row)
    return repository


def test_postgres_admin_mutations_bind_audit_actor_in_sql() -> None:
    memory_repository = InMemoryCouponRepository(product_options=_products())
    request = _request(memory_repository)
    payload = request.model_dump(mode="python")

    create_connection = _CouponAuditConnection()
    created = _postgres_audit_repository(create_connection).create_coupon(
        payload,
        actor_id="admin-create",
    )
    create_sql, create_params = create_connection.statements[0]
    assert "created_by, updated_by" in create_sql
    assert create_params[-2:] == ("admin-create", "admin-create")
    assert created["created_by"] == created["updated_by"] == "admin-create"

    locked = {
        **payload,
        "id": 41,
        "status": "draft",
        "first_claim_at": None,
        "issued_count": 0,
    }
    update_connection = _CouponAuditConnection(locked=locked)
    updated = _postgres_audit_repository(update_connection).update_coupon(
        41,
        payload,
        actor_id="admin-update",
    )
    update_sql, update_params = update_connection.statements[-1]
    assert "updated_by = %s" in update_sql
    assert update_params[-3] == "admin-update"
    assert updated["updated_by"] == "admin-update"

    transition_connection = _CouponAuditConnection(locked=locked)
    transitioned = _postgres_audit_repository(transition_connection).transition_coupon(
        41,
        "published",
        actor_id="admin-publish",
    )
    transition_sql, transition_params = transition_connection.statements[-1]
    assert "updated_by = %s" in transition_sql
    assert transition_params == ("published", "admin-publish", "aicrm", 41)
    assert transitioned["updated_by"] == "admin-publish"


@pytest.mark.parametrize(
    ("status", "expected_sql"),
    [
        ("scheduled", "CURRENT_TIMESTAMP < c.claim_starts_at"),
        ("active", "c.issued_count < c.total_issue_limit"),
        ("sold_out", "c.issued_count >= c.total_issue_limit"),
        ("ended", "CURRENT_TIMESTAMP >= c.claim_ends_at"),
    ],
)
def test_postgres_derived_status_filter_is_applied_before_pagination_and_total(
    status: str,
    expected_sql: str,
) -> None:
    connection = _CouponListConnection()
    repository = PostgresCouponRepository("postgresql://unused")
    repository._connect = lambda: connection

    result = repository.list_coupons(limit=20, offset=40, q="", status=status)

    list_sql, list_params = connection.statements[0]
    count_sql, count_params = connection.statements[1]
    assert expected_sql in list_sql
    assert expected_sql in count_sql
    assert "LIMIT %s OFFSET %s" in list_sql
    assert "LIMIT" not in count_sql
    assert list_params == ("aicrm", 20, 40)
    assert count_params == ("aicrm",)
    assert result["items"] == []
    assert result["total"] == 0


def test_price_guard_blocks_zero_value_orders_from_unexpired_claims() -> None:
    repository = InMemoryCouponRepository(product_options=_products())
    admin = CouponAdminApplication(repository)
    public = CouponPublicApplication(repository)
    coupon = admin.create_coupon(_request(repository, discount=1_000))["coupon"]
    admin.publish_coupon(coupon["id"])
    public.claim_coupon(coupon["public_slug"], identity={"unionid": "guard-user"}, idempotency_key="guard-key")

    with pytest.raises(ContractError, match="lower than or equal"):
        assert_product_price_allows_coupons(101, 1_000, repository=repository)
    assert_product_price_allows_coupons(101, 1_001, repository=repository)


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _NoSqlConnection:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("none coupon choice must not execute SQL")


def test_old_checkout_omission_defaults_none_and_reservation_executes_zero_sql() -> None:
    choice = normalize_coupon_choice({"product_code": "ordinary-course"})
    order = {"id": 1, "out_trade_no": "WXP-NONE", "amount_total": 10_000, "currency": "CNY"}

    result = reserve_coupon_for_order(
        _NoSqlConnection(),
        order=order,
        coupon_choice=choice,
        unionid="union-none",
        trade_product_id=101,
    )

    assert choice.mode is CouponChoiceMode.NONE
    assert result["subtotal_amount_total"] == 10_000
    assert result["discount_amount_total"] == 0
    assert result["amount_total"] == 10_000


class _ReserveConnection:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.order = {
            "id": 88,
            "out_trade_no": "WXP-RESERVE",
            "amount_total": 10_000,
            "subtotal_amount_total": 10_000,
            "currency": "CNY",
            "unionid": "union-reserve",
            "expires_at": now + timedelta(minutes=15),
            "coupon_claim_id": None,
        }
        self.claim = {
            "id": 7,
            "coupon_id": 3,
            "claim_no": "CC-BEST",
            "discount_amount_total": 1_500,
            "currency": "CNY",
            "valid_from": now - timedelta(days=1),
            "valid_until": now + timedelta(days=2),
            "claimed_at": now - timedelta(hours=1),
            "status": "available",
            "coupon_name": "最佳优惠券",
            "public_slug": "cpn_best",
        }
        self.product_amount = 10_000
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if normalized.startswith("SELECT * FROM wechat_pay_orders WHERE id"):
            return _Result(dict(self.order))
        if normalized.startswith("SELECT amount_total, currency, status, enabled FROM wechat_pay_products"):
            return _Result(
                {
                    "amount_total": self.product_amount,
                    "currency": "CNY",
                    "status": "active",
                    "enabled": True,
                }
            )
        if normalized.startswith("SELECT claim.*"):
            return _Result(dict(self.claim))
        if normalized.startswith("UPDATE commerce_coupon_claims"):
            self.claim["status"] = "reserved"
            return _Result({"id": 7})
        if normalized.startswith("INSERT INTO commerce_coupon_redemptions"):
            return _Result()
        if normalized.startswith("UPDATE wechat_pay_orders"):
            self.order.update(
                {
                    "subtotal_amount_total": int(params[0]),
                    "discount_amount_total": int(params[1]),
                    "amount_total": int(params[2]),
                    "coupon_claim_id": int(params[3]),
                    "coupon_snapshot_json": {
                        "claim_no": "CC-BEST",
                        "discount_amount_total": 1_500,
                    },
                }
            )
            return _Result(dict(self.order))
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_reserve_coupon_updates_claim_redemption_and_full_order_snapshot() -> None:
    conn = _ReserveConnection()

    result = reserve_coupon_for_order(
        conn,
        order=conn.order,
        coupon_choice={"mode": "auto"},
        unionid="union-reserve",
        trade_product_id=101,
    )

    assert result["subtotal_amount_total"] == 10_000
    assert result["discount_amount_total"] == 1_500
    assert result["amount_total"] == 8_500
    assert result["coupon_claim_id"] == 7
    assert any(statement.startswith("INSERT INTO commerce_coupon_redemptions") for statement in conn.statements)


def test_reserve_coupon_revalidates_locked_product_price_before_selecting_claim() -> None:
    conn = _ReserveConnection()
    conn.product_amount = 9_000

    with pytest.raises(ContractError, match="price changed"):
        reserve_coupon_for_order(
            conn,
            order=conn.order,
            coupon_choice={"mode": "auto"},
            unionid="union-reserve",
            trade_product_id=101,
        )

    assert not any(statement.startswith("SELECT claim.*") for statement in conn.statements)


class _LifecycleConnection:
    def __init__(self, *, redemption_status: str = "reserved", valid_until: datetime | None = None) -> None:
        self.order = {
            "id": 88,
            "out_trade_no": "WXP-LIFECYCLE",
            "amount_total": 8_500,
            "currency": "CNY",
            "coupon_claim_id": 7,
            "status": "paid",
        }
        self.redemption = {"id": 9, "claim_id": 7, "order_id": 88, "status": redemption_status}
        self.claim = {
            "id": 7,
            "status": "consumed" if redemption_status == "consumed" else "reserved",
            "valid_until": valid_until or datetime.now(UTC) + timedelta(days=1),
        }
        self.updates: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM wechat_pay_orders"):
            return _Result(dict(self.order))
        if normalized.startswith("SELECT * FROM commerce_coupon_redemptions"):
            return _Result(dict(self.redemption))
        if normalized.startswith("SELECT * FROM commerce_coupon_claims"):
            return _Result(dict(self.claim))
        if normalized.startswith("UPDATE commerce_coupon_redemptions"):
            self.updates.append((normalized, params))
            return _Result()
        if normalized.startswith("UPDATE commerce_coupon_claims"):
            self.updates.append((normalized, params))
            return _Result()
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_consume_is_strict_and_idempotent_while_release_never_restores_expired_claim() -> None:
    consume_conn = _LifecycleConnection()
    consume_coupon_for_paid_order(
        consume_conn,
        out_trade_no="WXP-LIFECYCLE",
        provider_total=8_500,
        provider_currency="CNY",
    )
    assert len(consume_conn.updates) == 2

    with pytest.raises(ContractError, match="amount"):
        consume_coupon_for_paid_order(
            _LifecycleConnection(),
            out_trade_no="WXP-LIFECYCLE",
            provider_total=8_501,
            provider_currency="CNY",
        )

    consumed_conn = _LifecycleConnection(redemption_status="consumed")
    consume_coupon_for_paid_order(
        consumed_conn,
        out_trade_no="WXP-LIFECYCLE",
        provider_total=8_500,
        provider_currency="CNY",
    )
    assert consumed_conn.updates == []

    release_conn = _LifecycleConnection(valid_until=datetime.now(UTC) - timedelta(seconds=1))
    release_conn.order["status"] = "closed"
    release_coupon_for_order(release_conn, out_trade_no="WXP-LIFECYCLE", reason="timeout")
    assert len(release_conn.updates) == 2
    claim_update_params = release_conn.updates[-1][1]
    assert claim_update_params[0] == CouponClaimStatus.EXPIRED.value


class _CountConnection:
    def __init__(self) -> None:
        self.values = iter([1, 2, 3, 4, 5])

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Result:
        return _Result({"total": next(self.values)})


def test_consistency_checker_exposes_all_alert_counters() -> None:
    payload = coupon_consistency_counts(_CountConnection())

    assert payload["total"] == 15
    assert payload["counts"]["paid_order_without_consumed_coupon"] == 1
    assert payload["counts"]["duplicate_active_coupon_redemption"] == 5
