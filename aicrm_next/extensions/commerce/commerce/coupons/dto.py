from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import (
    CouponChoiceMode,
    CouponValidityMode,
    validate_issue_limits,
    validate_time_window,
    validate_validity_configuration,
)


class CouponUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=45)
    discount_amount_total: int = Field(strict=True, gt=0)
    total_issue_limit: int = Field(strict=True, gt=0)
    per_user_issue_limit: int = Field(default=1, strict=True, gt=0)
    claim_starts_at: datetime
    claim_ends_at: datetime
    validity_mode: CouponValidityMode
    use_starts_at: datetime | None = None
    use_ends_at: datetime | None = None
    relative_validity_days: int | None = Field(default=None, strict=True, gt=0)
    instructions: str = Field(default="", max_length=200)
    target_refs: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        return value.strip()

    @field_validator("target_refs")
    @classmethod
    def normalize_target_refs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            target_ref = str(raw_value or "").strip()
            if not target_ref:
                raise ValueError("target_refs cannot contain blank values")
            if target_ref in seen:
                continue
            normalized.append(target_ref)
            seen.add(target_ref)
        if not normalized:
            raise ValueError("target_refs is required")
        return normalized

    @model_validator(mode="after")
    def validate_rules(self) -> CouponUpsertRequest:
        validate_issue_limits(
            total_issue_limit=self.total_issue_limit,
            per_user_issue_limit=self.per_user_issue_limit,
        )
        validate_time_window(
            starts_at=self.claim_starts_at,
            ends_at=self.claim_ends_at,
            field_prefix="claim",
        )
        validate_validity_configuration(
            validity_mode=self.validity_mode,
            claim_ends_at=self.claim_ends_at,
            use_starts_at=self.use_starts_at,
            use_ends_at=self.use_ends_at,
            relative_validity_days=self.relative_validity_days,
        )
        return self


class CouponChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CouponChoiceMode = CouponChoiceMode.NONE
    claim_no: str | None = None

    @field_validator("claim_no")
    @classmethod
    def normalize_claim_no(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_choice(self) -> CouponChoice:
        if self.mode is CouponChoiceMode.CLAIM and not self.claim_no:
            raise ValueError("claim_no is required when mode is claim")
        if self.mode is not CouponChoiceMode.CLAIM and self.claim_no:
            raise ValueError("claim_no is only allowed when mode is claim")
        return self


class CouponClaimRequest(BaseModel):
    """The claim action has no user-configurable business fields.

    The API obtains canonical unionid from the OAuth session and idempotency
    from the required request header.  This DTO deliberately rejects arbitrary
    identifiers supplied by the browser.
    """

    model_config = ConfigDict(extra="forbid")


class CouponProductOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_ref: str = Field(min_length=1)
    product_type: str = Field(pattern=r"^(standard_product|service_period)$")
    title: str = Field(min_length=1)
    amount_total: int = Field(strict=True, gt=0)
    currency: str = Field(default="CNY", pattern=r"^CNY$")
    status: str
    duration_days: int | None = Field(default=None, strict=True, gt=0)
    purchase_url: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
