from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResolvePersonIdentityRequest(BaseModel):
    external_userid: str | None = None
    mobile: str | None = None
    openid: str | None = None
    unionid: str | None = None


class BindMobileToExternalContactRequest(BaseModel):
    external_userid: str
    mobile: str
    owner_userid: str | None = None
    bind_by_userid: str | None = None
    customer_name: str | None = None
    force_rebind: bool = False


class ContactPoint(BaseModel):
    type: str
    value: str
    verified: bool = False


class IdentityResolution(BaseModel):
    person_id: str | None
    external_userid: str | None
    mobile: str | None
    openid: str | None = None
    unionid: str | None = None
    customer_name: str | None = None
    remark: str | None = None
    description: str | None = None
    mobile_source: str | None = None
    binding_status: str = Field(default="unknown")
    owner_userid: str | None = None
    identity_map_id: int | None = None
    follow_user_userid: str | None = None
    matched_by: str | None = None
    contact_points: list[ContactPoint] = Field(default_factory=list)


IdentityResolveStatus = Literal["resolved", "not_found", "pending", "conflict"]


class IdentityResolveResult(BaseModel):
    status: IdentityResolveStatus
    identity: IdentityResolution | None = None
    reason: str = ""
    matched_fields: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    pending_count: int = 0
