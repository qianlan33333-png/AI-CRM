from __future__ import annotations

from pydantic import BaseModel, Field


class ListCustomersRequest(BaseModel):
    owner_userid: str | None = None
    tag: str | None = None
    status: str | None = None
    is_bound: str | None = None
    mobile: str | None = None
    keyword: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class CustomerDetailRequest(BaseModel):
    external_userid: str | None = None
    unionid: str | None = None


class CustomerTimelineRequest(BaseModel):
    external_userid: str | None = None
    unionid: str | None = None
    event_type: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class CustomerContextRequest(BaseModel):
    unionid: str | None = None
    external_userid: str | None = None
    mobile: str | None = None
    user_id: str | None = None
    owner_userid: str | None = None
    require_owner_scope: bool = False
    owner_verified: bool = False
    include_activity: bool = True
    recent_message_limit: int = Field(default=20, ge=1, le=100)
    timeline_limit: int = Field(default=20, ge=1, le=100)


CustomerChatContextRequest = CustomerContextRequest


class RecentMessagesRequest(BaseModel):
    external_userid: str | None = None
    unionid: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
