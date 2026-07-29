from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_group_invite_join_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "work.weixin.qq.com" or not parsed.path.startswith("/gm/"):
        raise ValueError("群邀请链接必须是 https://work.weixin.qq.com/gm/... 地址")
    return url


def normalize_http_url(value: Any, *, field_name: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} 必须是有效的 HTTP(S) 地址")
    return url


class ImageUpsertRequest(BaseModel):
    name: str | None = None
    file_name: str = "fixture.png"
    content_type: str = "image/png"
    mime_type: str | None = None
    file_size: int = 0
    width: int = 1
    height: int = 1
    data_url: str = "data:image/png;base64,ZmFrZQ=="
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    category: str = ""
    enabled: bool | None = None
    ai_metadata: dict[str, Any] = Field(default_factory=dict)


class ImageFromUrlRequest(BaseModel):
    url: str
    name: str | None = None
    tags: list[str] = Field(default_factory=list)


class ImageFromBase64Request(BaseModel):
    data_base64: str = ""
    data_url: str = ""
    name: str | None = None
    file_name: str = "base64.png"
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_data_url_alias(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data_base64 = str(values.get("data_base64") or "").strip()
        data_url = str(values.get("data_url") or "").strip()
        if not data_base64 and data_url:
            _, separator, body = data_url.partition(",")
            values = {**values, "data_base64": body if separator else data_url}
        if not str(values.get("data_base64") or "").strip():
            raise ValueError("data_base64 or data_url is required")
        return values


class AttachmentUpsertRequest(BaseModel):
    name: str | None = None
    file_name: str = "attachment.bin"
    mime_type: str = "application/octet-stream"
    file_size: int = 0
    data_base64: str = ""
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class MiniprogramUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    title: str | None = None
    appid: str | None = None
    page_path: str | None = Field(default=None, alias="pagepath")
    thumb_image_id: str | int | None = None
    thumb_media_id: str | None = None
    resolve_thumb_media: bool = True
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_page_path_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            updated = dict(values)
            if "pagepath" not in updated and "page_path" in updated:
                updated["pagepath"] = updated.get("page_path")
            if "appid" not in updated and "app_id" in updated:
                updated["appid"] = updated.get("app_id")
            return updated
        return values


class GroupInviteUpsertRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    description: str | None = None
    pic_url: str | None = None
    join_url: str | None = None
    config_id: str | None = None
    state: str | None = None
    chat_id_list: list[str] | None = None
    auto_create_room: bool | None = None
    room_base_name: str | None = None
    room_base_id: int | None = None
    enabled: bool | None = None
    chat_id: str | None = None
    binding_status: str | None = None

    @field_validator("join_url")
    @classmethod
    def validate_join_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_group_invite_join_url(value)

    @field_validator("pic_url")
    @classmethod
    def validate_pic_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_http_url(value, field_name="卡片封面链接")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip()
        if len(normalized.encode("utf-8")) > 128:
            raise ValueError("群邀请卡片标题不能超过 128 字节")
        return normalized

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip()
        if len(normalized.encode("utf-8")) > 512:
            raise ValueError("群邀请卡片描述不能超过 512 字节")
        return normalized

    @field_validator("binding_status")
    @classmethod
    def validate_binding_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip().lower()
        if normalized not in {"pending", "ready", "invalid"}:
            raise ValueError("binding_status 必须是 pending、ready 或 invalid")
        return normalized


class GroupInviteBindingEnsureRequest(BaseModel):
    chat_id: str
    group_name: str = ""
    owner_userid: str = ""
    owner_name: str = ""
    member_count: int = 0

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("chat_id 不能为空")
        return normalized


class GroupInviteBindingUpdateRequest(BaseModel):
    join_url: str
    enabled: bool = True

    @field_validator("join_url")
    @classmethod
    def validate_join_url(cls, value: str) -> str:
        normalized = normalize_group_invite_join_url(value)
        if not normalized:
            raise ValueError("群邀请链接不能为空")
        return normalized
