from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field


class SendContentPackage(BaseModel):
    content_text: str = ""
    image_library_ids: list[Any] = Field(default_factory=list)
    miniprogram_library_ids: list[Any] = Field(default_factory=list)
    attachment_library_ids: list[Any] = Field(default_factory=list)
    group_invite_library_ids: list[Any] = Field(default_factory=list)


class SendContentValidateRequest(BaseModel):
    content_package: SendContentPackage = Field(default_factory=SendContentPackage)
    text_enabled: bool = True
    require_body: bool = True


class SendContentPreviewRequest(BaseModel):
    content_package: SendContentPackage = Field(default_factory=SendContentPackage)
    text_enabled: bool = True
    require_body: bool = True


class MaterialAssetsValidateRequest(BaseModel):
    content_package: SendContentPackage = Field(default_factory=SendContentPackage)
    channel: str = "send_content"
    text_enabled: bool = True
    require_body: bool = False


class MaterialPickerListRequest(BaseModel):
    type: Literal["image", "miniprogram", "attachment", "group_invite"]
    q: str = ""
    enabled_only: bool = True
    limit: int = 50
    offset: int = 0
