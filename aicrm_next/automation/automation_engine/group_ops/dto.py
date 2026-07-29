from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class GroupOpsPlanListRequest(BaseModel):
    keyword: str = ""
    plan_type: str = ""
    operator_member_id: str = ""
    status: str = ""
    limit: int = 50
    offset: int = 0


class GroupOpsPlanCreateRequest(BaseModel):
    plan_code: str | None = None
    plan_name: str | None = None
    plan_type: str | None = None
    name: str | None = None
    type: str | None = None
    owner_userid: str | None = None
    operatorMemberId: str | None = None
    operator_member_id: str | None = None
    status: str = "draft"
    defaultActionType: str | None = None
    default_action_type: str | None = None
    allowNoSop: bool | None = None
    allow_no_sop: bool | None = None
    allowExternalRecipients: bool | None = None
    allow_external_recipients: bool | None = None
    boundGroupIds: list[str] = Field(default_factory=list)
    bound_group_ids: list[str] = Field(default_factory=list)
    boundAudienceIds: list[str] = Field(default_factory=list)
    bound_audience_ids: list[str] = Field(default_factory=list)
    description: str = ""
    operator: str = "system"


class GroupOpsPlanUpdateRequest(BaseModel):
    plan_code: str | None = None
    plan_name: str | None = None
    plan_type: str | None = None
    name: str | None = None
    type: str | None = None
    owner_userid: str | None = None
    operatorMemberId: str | None = None
    operator_member_id: str | None = None
    status: str | None = None
    defaultActionType: str | None = None
    default_action_type: str | None = None
    allowNoSop: bool | None = None
    allow_no_sop: bool | None = None
    allowExternalRecipients: bool | None = None
    allow_external_recipients: bool | None = None
    boundGroupIds: list[str] | None = None
    bound_group_ids: list[str] | None = None
    boundAudienceIds: list[str] | None = None
    bound_audience_ids: list[str] | None = None
    description: str | None = None
    operator: str = "system"


class GroupOpsBindGroupRequest(BaseModel):
    chat_id: str
    operator: str = "system"


class GroupOpsNodeRequest(BaseModel):
    day_index: int = 1
    scheduled_time: str = ""
    trigger_time_label: str = ""
    action_title: str = ""
    text_content: str = ""
    content_package_json: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Any] = Field(default_factory=list)
    sort_order: int = 0
    status: str = "active"
    operator: str = "system"


class GroupOpsGroupsRequest(BaseModel):
    keyword: str = ""
    owner_userid: str = ""
    plan_id: int | None = None
    bind_status: str = ""
    limit: int = 50
    offset: int = 0


class GroupOpsGroupSyncRequest(BaseModel):
    owner_userid: str
    limit: int = 100
    cursor: str = ""
    operator: str = "system"


class GroupChatPickerSyncRequest(BaseModel):
    owner_userid: str = ""
    keyword: str = ""
    limit: int = 200
    operator: str = "system"


class GroupOpsRunDueRequest(BaseModel):
    operator: str = ""
    allow_plan_ids: list[int] = Field(default_factory=list)
    allow_node_ids: list[int] = Field(default_factory=list)
    max_outbound_tasks: int = 0
    scheduled_at: str | None = None
    external_effect_test_loopback: bool = False
    test_receiver_response_status: int = 200
    test_receiver_base_url: str = ""


class GroupOpsWebhookReceiveRequest(BaseModel):
    idempotency_key: str = ""
    send_mode: str = "queued"
    scheduled_at: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    event: str = ""
    mode: str = ""
    source: str = ""
    sender: dict[str, Any] = Field(default_factory=dict)
    recipients: list[dict[str, Any]] = Field(default_factory=list)
    action: dict[str, Any] = Field(default_factory=dict)
    actions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    rule: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    external_effect_test_loopback: bool = False
    test_receiver_response_status: int = 200
    test_receiver_base_url: str = ""


class GroupOpsTokenBroadcastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", validation_alias=AliasChoices("text", "recommendation_text"))
    card_path: str = ""
    card_title: str = ""
    image_media_ids: list[str] = Field(default_factory=list)
    idempotency_key: str = ""


class GroupOpsMembersRequest(BaseModel):
    layer_key: str = ""
    source_type: str = ""
    keyword: str = ""
    limit: int = 50
    offset: int = 0


class GroupOpsMemberImportRequest(BaseModel):
    source_type: str = "external"
    group_ids: list[str] = Field(default_factory=list)
    audience_ids: list[str] = Field(default_factory=list)
    recipients: list[dict[str, Any]] = Field(default_factory=list)
    operator: str = "system"


class AudienceRuleCreateRequest(BaseModel):
    ruleKey: str | None = None
    rule_key: str | None = None
    displayName: str | None = None
    display_name: str | None = None
    description: str = ""
    ruleType: str | None = None
    rule_type: str | None = None
    owner: str = ""
    status: str = "active"


class AudienceRuleVersionCreateRequest(BaseModel):
    version: int
    executorType: str | None = None
    executor_type: str | None = None
    codeOrSql: str | None = None
    code_or_sql: str | None = None
    paramsSchema: dict[str, Any] = Field(default_factory=dict)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    outputSchema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    refreshPolicy: dict[str, Any] = Field(default_factory=dict)
    refresh_policy: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class AudienceRuleRunRequest(BaseModel):
    planId: int | str | None = None
    plan_id: int | str | None = None
    version: int
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int = 20
    layers: list[str] = Field(default_factory=list)


class GroupOpsSegmentationRequest(BaseModel):
    segmentationType: str | None = None
    segmentation_type: str | None = None
    ruleKey: str | None = None
    rule_key: str | None = None
    ruleVersion: int | None = None
    rule_version: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    layerActions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    layer_actions: dict[str, dict[str, Any]] = Field(default_factory=dict)


class GroupOpsExecutionsRequest(BaseModel):
    trigger_event_id: str = ""
    status: str = ""
    action_type: str = ""
    layer_key: str = ""
    recipient: str = ""
    start_at: str = ""
    end_at: str = ""
    limit: int = 50
    offset: int = 0
