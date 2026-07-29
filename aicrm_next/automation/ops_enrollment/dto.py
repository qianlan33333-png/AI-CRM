from __future__ import annotations

from pydantic import BaseModel, Field


class UserOpsFilters(BaseModel):
    wecom_status: str = ""
    mobile_binding_status: str = ""
    activation_bucket: str = ""
    class_term_no: str = ""
    tag: str = ""
    keyword: str = ""
    mobile: str = ""
    owner_userid: str = ""


class UserOpsListRequest(BaseModel):
    filters: UserOpsFilters = Field(default_factory=UserOpsFilters)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class BatchSendRequest(BaseModel):
    selection_mode: str = "manual"
    filters: UserOpsFilters = Field(default_factory=UserOpsFilters)
    selected_ids: list[int] = Field(default_factory=list)
    excluded_ids: list[int] = Field(default_factory=list)
    content: str = ""
    images: list[dict] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    include_do_not_disturb: bool = False
    confirm: bool = False
    operator: str = "fixture-admin"
    target_source: str = ""
    target_source_id: int | None = None


class DoNotDisturbRequest(BaseModel):
    unionid: str = ""
    reason_code: str = "manual_set"
    reason_text: str = "运营设置"
    action: str = ""
    is_active: bool | None = None
    operator: str = "fixture-admin"


class BroadcastPreviewMessage(BaseModel):
    text: str = ""


class BroadcastPreviewRequest(BaseModel):
    filters: UserOpsFilters = Field(default_factory=UserOpsFilters)
    message: BroadcastPreviewMessage = Field(default_factory=BroadcastPreviewMessage)
    selection_mode: str = "all_filtered"
    selected_ids: list[int] = Field(default_factory=list)
    excluded_ids: list[int] = Field(default_factory=list)
    include_do_not_disturb: bool = False
    operator: str = "fixture-admin"


class ExportPreviewRequest(BaseModel):
    filters: UserOpsFilters = Field(default_factory=UserOpsFilters)
    fields: list[str] = Field(default_factory=list)
    operator: str = "fixture-admin"
