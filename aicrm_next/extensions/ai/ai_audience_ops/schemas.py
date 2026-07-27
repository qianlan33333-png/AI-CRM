from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .constants import (
    AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT,
)


class PackageCreateRequest(BaseModel):
    package_key: str
    name: str
    status: str = "draft"
    natural_language_definition: str = ""
    refresh_mode: str = ""
    query_mode: str = "hybrid"
    identity_policy: str = "external_userid"
    parameters: dict[str, Any] = Field(default_factory=dict)
    incremental_enabled: bool = True
    daily_enabled: bool = False
    incremental_interval_seconds: int = Field(default=180, ge=60, le=86400)
    daily_refresh_time: str = "02:00"
    timezone: str = "Asia/Shanghai"
    lookback_seconds: int = Field(default=600, ge=0, le=86400)
    sql_text: str = ""
    incremental_sql_text: str = ""
    snapshot_sql_text: str = ""
    ai_prompt: str = ""
    ai_rationale: str = ""
    natural_language_explanation: str = ""


class PackageVersionCreateRequest(BaseModel):
    incremental_sql_text: str = ""
    snapshot_sql_text: str = ""
    sql_text: str = ""
    simple_sql_text: str = ""
    simple_compiled_sql_text: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    ai_prompt: str = ""
    ai_rationale: str = ""
    natural_language_explanation: str = ""


class PackagePublishRequest(BaseModel):
    version_id: int | None = None


class PreviewRequest(BaseModel):
    sql_text: str = ""
    sql_kind: str = "incremental"
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=200)


class RefreshRequest(BaseModel):
    run_type: str = "incremental"
    params: dict[str, Any] = Field(default_factory=dict)
    row_limit: int = Field(
        default=AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
        ge=1,
        le=AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT,
    )


class SourceDirtyRequest(BaseModel):
    source_type: str
    source_key: str = ""
    identity_type: str = ""
    identity_value: str = ""
    occurred_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class OutboundSubscriptionCreateRequest(BaseModel):
    trigger_event_type: str = "entered"
    dispatch_mode: str = "per_member"
    target_type: str = "webhook"
    webhook_url: str
    headers: dict[str, Any] = Field(default_factory=dict)
    payload_template: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "execute"
    requires_approval: bool = False
    max_attempts: int = Field(default=5, ge=1, le=20)


class OutboundSubscriptionUpdateRequest(BaseModel):
    status: str | None = None
    webhook_url: str | None = None
    headers: dict[str, Any] | None = None
    payload_template: dict[str, Any] | None = None
    execution_mode: str | None = None
    requires_approval: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=20)


class InboundWebhookRequest(BaseModel):
    external_event_id: str
    member_event_id: int | None = None
    status: str = ""
    message: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)


class SimpleSenderRequest(BaseModel):
    sender_userid: str
    display_name: str = ""
    priority: int = Field(default=100, ge=1, le=10000)
    status: str = "active"


class SimpleSqlPreviewRequest(BaseModel):
    package_key: str = ""
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=200)


class SimpleSqlApplyRequest(BaseModel):
    package_key: str
    name: str
    natural_language_definition: str = ""
    refresh_mode: str = "manual"
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    senders: list[SimpleSenderRequest] = Field(default_factory=list)
    outbound_webhook_url: str = ""
    operator: str = ""
