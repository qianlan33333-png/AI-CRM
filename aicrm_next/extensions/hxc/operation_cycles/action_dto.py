from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .domain import validate_private_payload


ActionCompletionType = Literal["campaign_preparation_commit", "operation_cycle_review"]
ActionRequestStatus = Literal[
    "queued",
    "claimed",
    "thread_bound",
    "turn_started",
    "completed",
    "failed",
]


def _canonical_hash(payload: Any) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperationCycleSkillSafetyV1(ActionModel):
    schema_version: Literal["operation_cycle_skill_safety.v1"] = "operation_cycle_skill_safety.v1"
    crm_stores_intermediate_artifacts: Literal[False] = False
    crm_stores_local_paths: Literal[False] = False
    crm_stores_raw_conversation: Literal[False] = False
    start_external_effects: Literal["none"] = "none"
    send_requires_ai_assistant_approval: Literal[True] = True
    auto_approve_allowed: Literal[False] = False
    direct_broadcast_jobs_allowed: Literal[False] = False


class OperationCycleSkillActionV1(ActionModel):
    action_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=2000)
    codex_prompt: str = Field(min_length=1, max_length=80_000)
    required_local_bindings: list[str] = Field(default_factory=list, max_length=50)
    completion_type: ActionCompletionType
    prerequisites: list[str] = Field(default_factory=list, max_length=50)
    result_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_local_bindings", "prerequisites")
    @classmethod
    def validate_unique_keys(cls, values: list[str]) -> list[str]:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("action keys must be unique")
        for value in cleaned:
            if len(value) > 120 or not value.replace("_", "").replace("-", "").replace(".", "").isalnum():
                raise ValueError("action key must be a logical binding key")
        return cleaned


class OperationCycleSkillV1(ActionModel):
    schema_version: Literal["operation_cycle_skill.v1"] = "operation_cycle_skill.v1"
    skill_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    skill_hash: str = Field(default="", max_length=64)
    actions: list[OperationCycleSkillActionV1] = Field(min_length=1, max_length=20)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    safety: OperationCycleSkillSafetyV1 = Field(default_factory=OperationCycleSkillSafetyV1)

    @model_validator(mode="after")
    def validate_skill(self) -> "OperationCycleSkillV1":
        action_keys = [action.action_key for action in self.actions]
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("skill action_key values must be unique")
        known = set(action_keys)
        for action in self.actions:
            if action.action_key in action.prerequisites:
                raise ValueError("an action cannot depend on itself")
            if not set(action.prerequisites).issubset(known):
                raise ValueError("action prerequisite is not defined by the skill")
        pending = {action.action_key: set(action.prerequisites) for action in self.actions}
        while pending:
            ready = {key for key, dependencies in pending.items() if not dependencies}
            if not ready:
                raise ValueError("skill action prerequisites must be acyclic")
            pending = {
                key: dependencies - ready
                for key, dependencies in pending.items()
                if key not in ready
            }
        body = self.model_dump(mode="json", exclude={"skill_hash"})
        validate_private_payload(body)
        expected = _canonical_hash(body)
        if self.skill_hash and self.skill_hash.lower() != expected:
            raise ValueError("skill_hash does not match skill contents")
        self.skill_hash = expected
        return self


class OperationRunnerHeartbeatV1(ActionModel):
    schema_version: Literal["operation_cycle_runner_heartbeat.v1"] = (
        "operation_cycle_runner_heartbeat.v1"
    )
    runner_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    connector_version: str = Field(min_length=1, max_length=120)
    codex_version: str = Field(min_length=1, max_length=160)
    app_server_protocol: Literal["codex_app_server_jsonrpc_v2"] = "codex_app_server_jsonrpc_v2"
    compatibility_status: Literal["ready", "incompatible", "unavailable"]
    binding_keys: list[str] = Field(default_factory=list, max_length=100)
    max_concurrency: Literal[1] = 1

    @field_validator("binding_keys")
    @classmethod
    def validate_bindings(cls, values: list[str]) -> list[str]:
        cleaned = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
        for value in cleaned:
            if len(value) > 120 or not value.replace("_", "").replace("-", "").replace(".", "").isalnum():
                raise ValueError("binding_keys must contain logical keys only")
        return cleaned


class OperationRunnerHeartbeatView(ActionModel):
    ok: bool = True
    runner_id: str
    online: bool = True
    accepted_at: datetime
    heartbeat_interval_seconds: Literal[15] = 15
    offline_after_seconds: Literal[45] = 45


class OperationCycleActionStartV1(ActionModel):
    schema_version: Literal["operation_cycle_action_start.v1"] = "operation_cycle_action_start.v1"
    run_key: str = Field(default="", max_length=160, pattern=r"^[A-Za-z0-9_.:-]*$")
    parent_request_id: str = Field(default="", max_length=160)


class PrepareBroadcastActionResultV1(ActionModel):
    schema_version: Literal["operation_cycle_action_result.v1"] = "operation_cycle_action_result.v1"
    action_key: Literal["prepare_broadcast"] = "prepare_broadcast"
    conclusion: str = Field(min_length=1, max_length=6000)
    total_count: int = Field(ge=0, le=1_000_000)
    segment_counts: dict[str, int] = Field(default_factory=dict, max_length=50)
    excel_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    preparation_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    plan_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    plan_review_status: Literal["pending_review"] = "pending_review"
    plan_run_status: Literal["draft"] = "draft"
    broadcast_jobs: Literal[0] = 0
    ai_assistant_href: str = Field(default="", max_length=500)

    @field_validator("excel_sha256")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def validate_aggregates(self) -> "PrepareBroadcastActionResultV1":
        if set(self.segment_counts) != {"A", "B", "C", "D"}:
            raise ValueError("segment_counts must contain exactly A, B, C and D")
        if any(int(value) < 0 for value in self.segment_counts.values()):
            raise ValueError("segment counts cannot be negative")
        if self.segment_counts and sum(self.segment_counts.values()) != self.total_count:
            raise ValueError("segment counts must equal total_count")
        expected_href = f"/admin/cloud-orchestrator/plans/{self.plan_id}"
        if self.ai_assistant_href and self.ai_assistant_href != expected_href:
            raise ValueError("ai_assistant_href does not match plan_id")
        self.ai_assistant_href = expected_href
        validate_private_payload(self.model_dump(mode="json"))
        return self


class PostSendReviewActionResultV1(ActionModel):
    schema_version: Literal["operation_cycle_action_result.v1"] = "operation_cycle_action_result.v1"
    action_key: Literal["post_send_review"] = "post_send_review"
    conclusion: str = Field(min_length=1, max_length=6000)
    sent_count: int = Field(ge=0, le=1_000_000)
    failed_count: int = Field(ge=0, le=1_000_000)
    proposal_id: str = Field(
        default="",
        max_length=160,
        pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    proposed_skill_hash: str = Field(default="", max_length=64, pattern=r"^$|^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def reject_private_result(self) -> "PostSendReviewActionResultV1":
        validate_private_payload(self.model_dump(mode="json"))
        return self


OperationCycleActionResultV1 = Annotated[
    PrepareBroadcastActionResultV1 | PostSendReviewActionResultV1,
    Field(discriminator="action_key"),
]
ACTION_RESULT_ADAPTER = TypeAdapter(OperationCycleActionResultV1)


class OperationCycleActionClaimV1(ActionModel):
    schema_version: Literal["operation_cycle_action_claim.v1"] = "operation_cycle_action_claim.v1"
    runner_id: str = Field(min_length=1, max_length=160)
    wait_seconds: int = Field(default=25, ge=0, le=25)


class OperationCycleActionEventV1(ActionModel):
    schema_version: Literal["operation_cycle_action_event.v1"] = "operation_cycle_action_event.v1"
    event_type: Literal["thread_bound", "turn_started", "completed", "failed"]
    lease_token: str = Field(min_length=16, max_length=200)
    thread_id: str = Field(default="", max_length=240)
    turn_id: str = Field(default="", max_length=240)
    result: OperationCycleActionResultV1 | None = None
    failure_code: str = Field(default="", max_length=160, pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9_.:-]*$")

    @model_validator(mode="after")
    def validate_event_shape(self) -> "OperationCycleActionEventV1":
        if self.event_type == "thread_bound" and not self.thread_id:
            raise ValueError("thread_bound requires thread_id")
        if self.event_type == "turn_started" and (not self.thread_id or not self.turn_id):
            raise ValueError("turn_started requires thread_id and turn_id")
        if self.event_type == "completed" and self.result is None:
            raise ValueError("completed requires result")
        if self.event_type == "failed" and not self.failure_code:
            raise ValueError("failed requires failure_code")
        if self.event_type != "completed" and self.result is not None:
            raise ValueError("result is only accepted for completed events")
        return self


class OperationCycleActionRequestView(ActionModel):
    request_id: str
    strategy_key: str
    run_key: str
    action_key: str
    action_title: str
    strategy_version: int
    context_hash: str
    skill_key: str
    skill_hash: str
    status: ActionRequestStatus
    parent_request_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    final_result: dict[str, Any] | None = None
    failure_code: str = ""
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class OperationCycleActionClaimView(ActionModel):
    ok: bool = True
    claimed: bool
    request: OperationCycleActionRequestView | None = None
    lease_token: str = ""
    lease_expires_at: datetime | None = None
    action: OperationCycleSkillActionV1 | None = None
    local_binding_keys: list[str] = Field(default_factory=list)
    context_summary: dict[str, Any] = Field(default_factory=dict)


class OperationCycleCurrentActionView(ActionModel):
    ok: bool = True
    strategy_key: str
    run_key: str = ""
    action_kind: Literal["start", "ai_assistant", "start_review", "start_new_cycle", "none"]
    action_key: str = ""
    title: str
    enabled: bool = False
    disabled_reason: str = ""
    href: str = ""
    active_request_id: str = ""
    retry_parent_request_id: str = ""


class OperationCycleActionStartView(ActionModel):
    ok: bool = True
    request_id: str
    status: ActionRequestStatus
    message: Literal["已提交到本地 Codex"] = "已提交到本地 Codex"
    reused: bool = False


__all__ = [
    "ACTION_RESULT_ADAPTER",
    "ActionCompletionType",
    "ActionRequestStatus",
    "OperationCycleActionClaimV1",
    "OperationCycleActionClaimView",
    "OperationCycleActionEventV1",
    "OperationCycleActionRequestView",
    "OperationCycleActionResultV1",
    "OperationCycleActionStartV1",
    "OperationCycleActionStartView",
    "OperationCycleCurrentActionView",
    "OperationCycleSkillActionV1",
    "OperationCycleSkillSafetyV1",
    "OperationCycleSkillV1",
    "OperationRunnerHeartbeatV1",
    "OperationRunnerHeartbeatView",
    "PostSendReviewActionResultV1",
    "PrepareBroadcastActionResultV1",
]
