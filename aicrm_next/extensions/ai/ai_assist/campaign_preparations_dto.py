from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aicrm_next.platform.shared.dynamic_miniprogram_card import DynamicMiniprogramCardV1
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data


class CampaignPreparationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignPreparationIdentityV1(CampaignPreparationModel):
    external_userid: str = Field(default="", max_length=240)
    unionid: str = Field(default="", max_length=240)
    mobile: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def require_identity(self) -> "CampaignPreparationIdentityV1":
        if not any((self.external_userid, self.unionid, self.mobile)):
            raise ValueError("at least one identity is required")
        return self


class CampaignPreparationRowV1(CampaignPreparationModel):
    row_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    identity: CampaignPreparationIdentityV1
    content_text: str = Field(min_length=1, max_length=4000)
    card: DynamicMiniprogramCardV1
    group: str = Field(default="", max_length=32)
    reason_code: str = Field(default="", max_length=160)
    analysis: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_private_analysis(self) -> "CampaignPreparationRowV1":
        if redact_sensitive_data(self.analysis) != self.analysis:
            raise ValueError("analysis contains private or secret fields")
        return self


class CreateCampaignPreparationRequestV1(CampaignPreparationModel):
    schema_version: Literal["external_campaign_preparation.v1"] = "external_campaign_preparation.v1"
    idempotency_key: str = Field(min_length=8, max_length=240)
    strategy_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    strategy_version: int = Field(ge=1)
    context_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    md_source_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    run_key: str = Field(default="", max_length=160)
    owner_userid: str = Field(min_length=1, max_length=240)
    scheduled_for: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=240)
    rows: list[CampaignPreparationRowV1] = Field(min_length=1, max_length=5000)

    @field_validator("context_hash", "md_source_hash")
    @classmethod
    def lowercase_hash(cls, value: str) -> str:
        return value.lower()


class CommitCampaignPreparationRequestV1(CampaignPreparationModel):
    preparation_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("preparation_hash")
    @classmethod
    def lowercase_hash(cls, value: str) -> str:
        return value.lower()


__all__ = [
    "CampaignPreparationIdentityV1",
    "CampaignPreparationRowV1",
    "CommitCampaignPreparationRequestV1",
    "CreateCampaignPreparationRequestV1",
]
