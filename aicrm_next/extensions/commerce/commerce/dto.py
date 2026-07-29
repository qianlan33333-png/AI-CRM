from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ProductUpsertRequest(BaseModel):
    product_code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    price_cents: int = 0
    currency: str = "CNY"
    enabled: bool = True
    status: str = "active"
    page_slug: str | None = None
    cover_image_id: str | None = None
    detail_image_ids: list[str] = Field(default_factory=list)
    detail_sections: list[dict[str, Any]] = Field(default_factory=list)
    buy_button_text: str = "立即购买"
    completion_redirect_enabled: bool = False
    completion_redirect_url: str = ""
    completion_target: dict[str, Any] | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    require_mobile: bool = False
    lead_program_id: int | None = None
    lead_channel_id: int | None = None
    slices: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_product_code_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        updated = dict(values)
        nested_product = updated.get("product")
        if "product_code" not in updated:
            if isinstance(nested_product, dict):
                nested_code = nested_product.get("code") or nested_product.get("product_code")
                if nested_code:
                    updated["product_code"] = nested_code
            elif updated.get("code"):
                updated["product_code"] = updated.get("code")
        if "title" not in updated and updated.get("name"):
            updated["title"] = updated.get("name")
        if "price_cents" not in updated and updated.get("amount_total") is not None:
            updated["price_cents"] = updated.get("amount_total")
        if "buy_button_text" not in updated and updated.get("cta_text"):
            updated["buy_button_text"] = updated.get("cta_text")
        if "enabled" not in updated and updated.get("status"):
            updated["enabled"] = str(updated.get("status")).strip().lower() == "active"
        if "status" not in updated and "enabled" in updated:
            updated["status"] = "active" if bool(updated.get("enabled")) else "disabled"
        return updated


class BuyerIdentity(BaseModel):
    mobile: str | None = None
    external_userid: str | None = None
    openid: str | None = None
    unionid: str | None = None


class CheckoutRequest(BaseModel):
    product_code: str
    buyer_identity: BuyerIdentity = Field(default_factory=BuyerIdentity)
    quantity: int = 1
    return_url: str | None = None


class PaymentNotifyRequest(BaseModel):
    order_no: str
    payment_status: Literal["paid", "failed", "pending"] = "paid"
    transaction_id: str | None = None
    provider_payload: dict[str, Any] = Field(default_factory=dict)
