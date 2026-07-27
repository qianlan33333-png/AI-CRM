from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aicrm_next.extensions.commerce.commerce.coupons import (
    CouponChoice,
    CouponClaimStatus,
    CouponDisplayState,
    CouponLifecycleStatus,
    CouponRedemptionStatus,
    CouponUpsertRequest,
    CouponValidityMode,
    calculate_claim_validity,
    calculate_relative_validity,
    derive_coupon_state,
    validate_coupon_transition,
    validate_coupon_update,
    validate_discount_against_product_amounts,
    validate_discount_amount_total,
    validate_payable_amount,
)
from aicrm_next.shared.errors import ContractError


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _fixed_payload() -> dict:
    return {
        "name": "暑期立减券",
        "discount_amount_total": 1_000,
        "total_issue_limit": 100,
        "per_user_issue_limit": 1,
        "claim_starts_at": "2026-07-14T09:00:00+08:00",
        "claim_ends_at": "2026-07-20T23:00:00+08:00",
        "validity_mode": "fixed_range",
        "use_starts_at": "2026-07-14T09:00:00+08:00",
        "use_ends_at": "2026-07-31T23:59:59+08:00",
        "instructions": "  每单限用一张  ",
        "target_refs": ["product:standard:abc", "product:period:quarter"],
    }


def test_coupon_contract_uses_expected_closed_status_sets() -> None:
    assert {item.value for item in CouponLifecycleStatus} == {"draft", "published", "stopped", "archived"}
    assert {item.value for item in CouponClaimStatus} == {"available", "reserved", "consumed", "expired"}
    assert {item.value for item in CouponRedemptionStatus} == {"reserved", "consumed", "released"}


def test_coupon_upsert_normalizes_and_deduplicates_target_refs() -> None:
    payload = _fixed_payload()
    payload["target_refs"] = [" product:standard:abc ", "product:period:quarter", "product:standard:abc"]

    request = CouponUpsertRequest.model_validate(payload)

    assert request.name == "暑期立减券"
    assert request.instructions == "每单限用一张"
    assert request.target_refs == ["product:standard:abc", "product:period:quarter"]
    assert request.validity_mode is CouponValidityMode.FIXED_RANGE


@pytest.mark.parametrize("value", [0, -1, True, 10.5, "100"])
def test_coupon_discount_amount_is_strict_positive_integer_cents(value) -> None:
    with pytest.raises(ContractError, match="positive integer"):
        validate_discount_amount_total(value)

    payload = _fixed_payload()
    payload["discount_amount_total"] = value
    with pytest.raises(ValidationError):
        CouponUpsertRequest.model_validate(payload)


def test_coupon_upsert_requires_nonempty_products_and_valid_issue_limits() -> None:
    payload = _fixed_payload()
    payload["target_refs"] = []
    with pytest.raises(ValidationError, match="target_refs"):
        CouponUpsertRequest.model_validate(payload)

    payload = _fixed_payload()
    payload["per_user_issue_limit"] = 101
    with pytest.raises((ContractError, ValidationError), match="per_user_issue_limit"):
        CouponUpsertRequest.model_validate(payload)


def test_fixed_and_relative_validity_fields_are_mutually_exclusive() -> None:
    payload = _fixed_payload()
    payload["relative_validity_days"] = 2
    with pytest.raises((ContractError, ValidationError), match="relative_validity_days"):
        CouponUpsertRequest.model_validate(payload)

    payload = _fixed_payload()
    payload.update(
        {
            "validity_mode": "relative_days",
            "use_starts_at": None,
            "use_ends_at": None,
            "relative_validity_days": 2,
        }
    )
    request = CouponUpsertRequest.model_validate(payload)
    assert request.relative_validity_days == 2

    payload["use_starts_at"] = "2026-07-14T09:00:00+08:00"
    with pytest.raises((ContractError, ValidationError), match="relative_days"):
        CouponUpsertRequest.model_validate(payload)


def test_coupon_windows_require_timezone_and_fixed_use_end_covers_claim_end() -> None:
    payload = _fixed_payload()
    payload["claim_starts_at"] = "2026-07-14T09:00:00"
    with pytest.raises((ContractError, ValidationError), match="timezone"):
        CouponUpsertRequest.model_validate(payload)

    payload = _fixed_payload()
    payload["use_ends_at"] = "2026-07-20T22:59:59+08:00"
    with pytest.raises((ContractError, ValidationError), match="claim_ends_at"):
        CouponUpsertRequest.model_validate(payload)


def test_relative_validity_uses_shanghai_natural_days_and_returns_utc() -> None:
    claimed_at = datetime(2026, 7, 14, 23, 30, tzinfo=SHANGHAI)

    valid_from, valid_until = calculate_relative_validity(claimed_at, 2)

    assert valid_from == datetime(2026, 7, 14, 15, 30, tzinfo=timezone.utc)
    assert valid_until == datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    assert valid_until.astimezone(SHANGHAI) == datetime(2026, 7, 16, 0, 0, tzinfo=SHANGHAI)


def test_fixed_validity_starts_at_later_of_claim_and_configured_start() -> None:
    valid_from, valid_until = calculate_claim_validity(
        claimed_at=datetime(2026, 7, 14, 12, 0, tzinfo=SHANGHAI),
        validity_mode="fixed_range",
        use_starts_at=datetime(2026, 7, 15, 0, 0, tzinfo=SHANGHAI),
        use_ends_at=datetime(2026, 7, 31, 0, 0, tzinfo=SHANGHAI),
    )

    assert valid_from == datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    assert valid_until == datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)


def test_discount_must_leave_every_selected_product_with_positive_payable_amount() -> None:
    assert validate_discount_against_product_amounts(1_000, [1_001, 2_000]) == 1_000
    assert validate_payable_amount(subtotal_amount_total=1_001, discount_amount_total=1_000) == 1

    with pytest.raises(ContractError, match="less than every selected product"):
        validate_discount_against_product_amounts(1_000, [1_000, 2_000])
    with pytest.raises(ContractError, match="zero-value"):
        validate_payable_amount(subtotal_amount_total=1_000, discount_amount_total=1_000)


@pytest.mark.parametrize(
    ("now", "issued_count", "expected"),
    [
        (datetime(2026, 7, 13, tzinfo=timezone.utc), 0, CouponDisplayState.SCHEDULED),
        (datetime(2026, 7, 15, tzinfo=timezone.utc), 9, CouponDisplayState.ACTIVE),
        (datetime(2026, 7, 15, tzinfo=timezone.utc), 10, CouponDisplayState.SOLD_OUT),
        (datetime(2026, 7, 21, tzinfo=timezone.utc), 10, CouponDisplayState.ENDED),
    ],
)
def test_published_coupon_display_state_is_derived_from_window_and_stock(now, issued_count, expected) -> None:
    state = derive_coupon_state(
        status="published",
        claim_starts_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        claim_ends_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        issued_count=issued_count,
        total_issue_limit=10,
        now=now,
    )
    assert state is expected


def test_coupon_lifecycle_is_forward_only_and_ended_campaign_can_archive() -> None:
    assert validate_coupon_transition("draft", "published") is CouponLifecycleStatus.PUBLISHED
    assert validate_coupon_transition("published", "stopped") is CouponLifecycleStatus.STOPPED
    assert validate_coupon_transition("stopped", "archived") is CouponLifecycleStatus.ARCHIVED
    assert validate_coupon_transition("published", "archived", claim_window_ended=True) is CouponLifecycleStatus.ARCHIVED

    with pytest.raises(ContractError, match="cannot transition"):
        validate_coupon_transition("stopped", "published")
    with pytest.raises(ContractError, match="cannot transition"):
        validate_coupon_transition("published", "archived")


def test_first_claim_freezes_rules_but_allows_copy_and_issue_limit_increase() -> None:
    existing = {
        **_fixed_payload(),
        "first_claim_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
        "issued_count": 2,
    }

    allowed = validate_coupon_update(
        existing,
        {
            "name": "新展示名称",
            "instructions": "新说明",
            "total_issue_limit": 120,
            "discount_amount_total": 1_000,
            "target_refs": list(reversed(existing["target_refs"])),
        },
    )
    assert allowed["total_issue_limit"] == 120

    with pytest.raises(ContractError, match="discount_amount_total is frozen"):
        validate_coupon_update(existing, {"discount_amount_total": 900})
    with pytest.raises(ContractError, match="target_refs is frozen"):
        validate_coupon_update(existing, {"target_refs": ["product:standard:other"]})
    with pytest.raises(ContractError, match="only increase"):
        validate_coupon_update(existing, {"total_issue_limit": 99})


def test_coupon_choice_requires_opaque_claim_number_only_for_explicit_claim() -> None:
    assert CouponChoice().mode.value == "none"
    assert CouponChoice.model_validate({"mode": "auto"}).claim_no is None
    assert CouponChoice.model_validate({"mode": "claim", "claim_no": " CLM_opaque "}).claim_no == "CLM_opaque"

    with pytest.raises(ValidationError, match="claim_no"):
        CouponChoice.model_validate({"mode": "claim"})
    with pytest.raises(ValidationError, match="only allowed"):
        CouponChoice.model_validate({"mode": "none", "claim_no": "CLM_opaque"})
