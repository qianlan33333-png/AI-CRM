from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ServicePeriodProductCreateRequest(BaseModel):
    product_code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    price_cents: int = Field(ge=0)
    currency: str = "CNY"
    status: str = "active"
    duration_days: int = Field(gt=0)
    membership_config_id: str = ""
    membership_config_name: str = ""
    slices: list[dict[str, Any]] = Field(default_factory=list)
    completion_redirect_enabled: bool = False
    completion_redirect_url: str = ""
    completion_target: dict[str, Any] | None = None
    require_mobile: bool = False
    lead_channel_id: int | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_product_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        updated = dict(values)
        if "title" not in updated and updated.get("name"):
            updated["title"] = updated.get("name")
        if "price_cents" not in updated and updated.get("amount_total") is not None:
            updated["price_cents"] = updated.get("amount_total")
        return updated


class ServicePeriodProductUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    price_cents: int | None = Field(default=None, ge=0)
    currency: str | None = None
    status: str | None = None
    duration_days: int | None = Field(default=None, gt=0)
    membership_config_id: str | None = None
    membership_config_name: str | None = None
    slices: list[dict[str, Any]] | None = None
    completion_redirect_enabled: bool | None = None
    completion_redirect_url: str | None = None
    completion_target: dict[str, Any] | None = None
    require_mobile: bool | None = None
    lead_channel_id: int | None = None
    metadata_json: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_product_code_updates(cls, values: Any) -> Any:
        if isinstance(values, dict) and ("product_code" in values or "code" in values):
            raise ValueError("product_code cannot be changed after create")
        return values
