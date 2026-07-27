from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RadarLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="")
    target_type: str = Field(default="link")
    original_url: str = Field(default="")
    media_item_id: str = Field(default="")
    preview_mode: str = Field(default="")
    enabled: bool = True
    auth_required: bool = True
    source_channel: str = Field(default="")
    campaign_id: str = Field(default="")
    staff_id: str = Field(default="")
    created_by: str = Field(default="")


class RadarLinkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    target_type: str | None = None
    original_url: str | None = None
    media_item_id: str | None = None
    preview_mode: str | None = None
    enabled: bool | None = None
    auth_required: bool | None = None
    source_channel: str | None = None
    campaign_id: str | None = None
    staff_id: str | None = None
    created_by: str | None = None
