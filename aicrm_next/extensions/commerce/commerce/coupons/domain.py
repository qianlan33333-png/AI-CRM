from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from aicrm_next.shared.errors import ContractError


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SUPPORTED_CURRENCY = "CNY"


class _StringEnum(str, Enum):
    """Python 3.10-compatible equivalent of enum.StrEnum."""

    def __str__(self) -> str:
        return self.value


class CouponLifecycleStatus(_StringEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class CouponDisplayState(_StringEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    SOLD_OUT = "sold_out"
    ENDED = "ended"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class CouponValidityMode(_StringEnum):
    FIXED_RANGE = "fixed_range"
    RELATIVE_DAYS = "relative_days"


class CouponClaimStatus(_StringEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class CouponRedemptionStatus(_StringEnum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class CouponChoiceMode(_StringEnum):
    AUTO = "auto"
    NONE = "none"
    CLAIM = "claim"


FROZEN_RULE_FIELDS = frozenset(
    {
        "discount_amount_total",
        "per_user_issue_limit",
        "claim_starts_at",
        "claim_ends_at",
        "validity_mode",
        "use_starts_at",
        "use_ends_at",
        "relative_validity_days",
        "target_refs",
    }
)


def _strict_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{field} must be a positive integer")
    return value


def _strict_non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field} must be a non-negative integer")
    return value


def require_aware_datetime(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone")
    return value


def validate_currency(value: str) -> str:
    currency = str(value or "").strip().upper()
    if currency != SUPPORTED_CURRENCY:
        raise ContractError("currency must be CNY")
    return currency


def validate_discount_amount_total(value: int) -> int:
    return _strict_positive_int(value, field="discount_amount_total")


def validate_issue_limits(*, total_issue_limit: int, per_user_issue_limit: int) -> tuple[int, int]:
    total = _strict_positive_int(total_issue_limit, field="total_issue_limit")
    per_user = _strict_positive_int(per_user_issue_limit, field="per_user_issue_limit")
    if per_user > total:
        raise ContractError("per_user_issue_limit cannot exceed total_issue_limit")
    return total, per_user


def validate_discount_against_product_amounts(
    discount_amount_total: int,
    product_amount_totals: Iterable[int],
) -> int:
    discount = validate_discount_amount_total(discount_amount_total)
    amounts = list(product_amount_totals)
    if not amounts:
        raise ContractError("at least one product is required")
    for amount in amounts:
        product_amount = _strict_positive_int(amount, field="product_amount_total")
        if discount >= product_amount:
            raise ContractError("discount_amount_total must be less than every selected product price")
    return discount


def validate_payable_amount(*, subtotal_amount_total: int, discount_amount_total: int) -> int:
    subtotal = _strict_positive_int(subtotal_amount_total, field="subtotal_amount_total")
    discount = _strict_non_negative_int(discount_amount_total, field="discount_amount_total")
    payable = subtotal - discount
    if payable <= 0:
        raise ContractError("coupon cannot create a zero-value order")
    return payable


def validate_time_window(*, starts_at: datetime, ends_at: datetime, field_prefix: str) -> tuple[datetime, datetime]:
    start = require_aware_datetime(starts_at, field=f"{field_prefix}_starts_at")
    end = require_aware_datetime(ends_at, field=f"{field_prefix}_ends_at")
    if start >= end:
        raise ContractError(f"{field_prefix}_starts_at must be earlier than {field_prefix}_ends_at")
    return start, end


def validate_validity_configuration(
    *,
    validity_mode: CouponValidityMode | str,
    claim_ends_at: datetime,
    use_starts_at: datetime | None = None,
    use_ends_at: datetime | None = None,
    relative_validity_days: int | None = None,
) -> CouponValidityMode:
    try:
        mode = CouponValidityMode(validity_mode)
    except (TypeError, ValueError) as exc:
        raise ContractError("validity_mode must be fixed_range or relative_days") from exc

    claim_end = require_aware_datetime(claim_ends_at, field="claim_ends_at")
    if mode is CouponValidityMode.FIXED_RANGE:
        if use_starts_at is None or use_ends_at is None:
            raise ContractError("fixed_range requires use_starts_at and use_ends_at")
        use_start, use_end = validate_time_window(
            starts_at=use_starts_at,
            ends_at=use_ends_at,
            field_prefix="use",
        )
        if claim_end > use_end:
            raise ContractError("claim_ends_at cannot be later than use_ends_at")
        if relative_validity_days is not None:
            raise ContractError("fixed_range cannot set relative_validity_days")
        # Keep the local variable to make the timezone validation explicit.
        _ = use_start
        return mode

    if use_starts_at is not None or use_ends_at is not None:
        raise ContractError("relative_days cannot set use_starts_at or use_ends_at")
    _strict_positive_int(relative_validity_days, field="relative_validity_days")
    return mode


def calculate_relative_validity(
    claimed_at: datetime,
    relative_validity_days: int,
) -> tuple[datetime, datetime]:
    """Return an immediate start and an end-exclusive natural-day boundary.

    The claim day is day one in Asia/Shanghai.  A coupon claimed at 20:00 on
    July 14 with two valid days therefore expires at July 16 00:00 local time.
    Persisted values are returned in UTC.
    """

    claimed = require_aware_datetime(claimed_at, field="claimed_at")
    days = _strict_positive_int(relative_validity_days, field="relative_validity_days")
    local_claimed = claimed.astimezone(SHANGHAI_TZ)
    local_valid_until = datetime.combine(
        local_claimed.date() + timedelta(days=days),
        time.min,
        tzinfo=SHANGHAI_TZ,
    )
    return claimed.astimezone(UTC), local_valid_until.astimezone(UTC)


def calculate_claim_validity(
    *,
    claimed_at: datetime,
    validity_mode: CouponValidityMode | str,
    use_starts_at: datetime | None = None,
    use_ends_at: datetime | None = None,
    relative_validity_days: int | None = None,
) -> tuple[datetime, datetime]:
    claimed = require_aware_datetime(claimed_at, field="claimed_at")
    try:
        mode = CouponValidityMode(validity_mode)
    except (TypeError, ValueError) as exc:
        raise ContractError("validity_mode must be fixed_range or relative_days") from exc

    if mode is CouponValidityMode.RELATIVE_DAYS:
        return calculate_relative_validity(claimed, relative_validity_days)

    if use_starts_at is None or use_ends_at is None:
        raise ContractError("fixed_range requires use_starts_at and use_ends_at")
    use_start, use_end = validate_time_window(
        starts_at=use_starts_at,
        ends_at=use_ends_at,
        field_prefix="use",
    )
    valid_from = max(claimed.astimezone(UTC), use_start.astimezone(UTC))
    valid_until = use_end.astimezone(UTC)
    if valid_from >= valid_until:
        raise ContractError("coupon is not usable at the claim time")
    return valid_from, valid_until


def derive_coupon_state(
    *,
    status: CouponLifecycleStatus | str,
    claim_starts_at: datetime,
    claim_ends_at: datetime,
    issued_count: int,
    total_issue_limit: int,
    now: datetime,
) -> CouponDisplayState:
    try:
        lifecycle = CouponLifecycleStatus(status)
    except (TypeError, ValueError) as exc:
        raise ContractError("unsupported coupon status") from exc

    if lifecycle is CouponLifecycleStatus.DRAFT:
        return CouponDisplayState.DRAFT
    if lifecycle is CouponLifecycleStatus.STOPPED:
        return CouponDisplayState.STOPPED
    if lifecycle is CouponLifecycleStatus.ARCHIVED:
        return CouponDisplayState.ARCHIVED

    start, end = validate_time_window(
        starts_at=claim_starts_at,
        ends_at=claim_ends_at,
        field_prefix="claim",
    )
    current = require_aware_datetime(now, field="now")
    issued = _strict_non_negative_int(issued_count, field="issued_count")
    total = _strict_positive_int(total_issue_limit, field="total_issue_limit")
    if issued > total:
        raise ContractError("issued_count cannot exceed total_issue_limit")
    if current < start:
        return CouponDisplayState.SCHEDULED
    if current >= end:
        return CouponDisplayState.ENDED
    if issued >= total:
        return CouponDisplayState.SOLD_OUT
    return CouponDisplayState.ACTIVE


def validate_coupon_transition(
    current_status: CouponLifecycleStatus | str,
    target_status: CouponLifecycleStatus | str,
    *,
    claim_window_ended: bool = False,
) -> CouponLifecycleStatus:
    try:
        current = CouponLifecycleStatus(current_status)
        target = CouponLifecycleStatus(target_status)
    except (TypeError, ValueError) as exc:
        raise ContractError("unsupported coupon status") from exc
    if current is target:
        return target

    allowed = {
        CouponLifecycleStatus.DRAFT: {CouponLifecycleStatus.PUBLISHED},
        CouponLifecycleStatus.PUBLISHED: {CouponLifecycleStatus.STOPPED},
        CouponLifecycleStatus.STOPPED: {CouponLifecycleStatus.ARCHIVED},
        CouponLifecycleStatus.ARCHIVED: set(),
    }
    if target in allowed[current]:
        return target
    if (
        current is CouponLifecycleStatus.PUBLISHED
        and target is CouponLifecycleStatus.ARCHIVED
        and claim_window_ended
    ):
        return target
    raise ContractError(f"coupon status cannot transition from {current.value} to {target.value}")


def _normalized_rule_value(value: Any) -> Any:
    if isinstance(value, _StringEnum):
        return value.value
    if isinstance(value, datetime):
        return require_aware_datetime(value, field="coupon rule datetime").astimezone(UTC)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))
    return value


def validate_coupon_update(
    existing: Mapping[str, Any],
    proposed: Mapping[str, Any],
    *,
    existing_target_refs: Sequence[str] | None = None,
    proposed_target_refs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate post-claim immutability and the monotonic issue limit.

    Partial updates are supported: only fields present in ``proposed`` are
    compared.  The returned dictionary is a shallow copy suitable for the
    application layer to pass to its repository.
    """

    updated = dict(proposed)
    issued_count = _strict_non_negative_int(existing.get("issued_count", 0), field="issued_count")
    first_claim_at = existing.get("first_claim_at")
    rules_are_frozen = first_claim_at is not None or issued_count > 0

    if "total_issue_limit" in updated:
        new_limit = _strict_positive_int(updated["total_issue_limit"], field="total_issue_limit")
        if new_limit < issued_count:
            raise ContractError("total_issue_limit cannot be less than issued_count")
        current_limit = _strict_positive_int(existing.get("total_issue_limit"), field="total_issue_limit")
        if rules_are_frozen and new_limit < current_limit:
            raise ContractError("total_issue_limit can only increase after the first claim")

    if not rules_are_frozen:
        return updated

    fields_to_compare = FROZEN_RULE_FIELDS - {"target_refs"}
    for field in fields_to_compare:
        if field not in updated:
            continue
        if _normalized_rule_value(updated[field]) != _normalized_rule_value(existing.get(field)):
            raise ContractError(f"{field} is frozen after the first claim")

    old_targets = existing_target_refs
    if old_targets is None:
        old_targets = existing.get("target_refs")
    new_targets = proposed_target_refs
    if new_targets is None and "target_refs" in updated:
        new_targets = updated.get("target_refs")
    if new_targets is not None and _normalized_rule_value(new_targets) != _normalized_rule_value(old_targets or []):
        raise ContractError("target_refs is frozen after the first claim")
    return updated
