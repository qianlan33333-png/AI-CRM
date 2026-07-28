from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import validate_private_payload
from .dto import ReferenceSnapshot, RunDetailView, RunSummary, StrategySummary


ContextMode = Literal["execution", "retrospective", "optimization", "history"]
ProposalStatus = Literal["pending", "accepted", "rejected"]
ProposalDecision = Literal["accept", "reject"]


def markdown_sha256(markdown: str) -> str:
    return hashlib.sha256(str(markdown or "").encode("utf-8")).hexdigest()


class StrategyContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StrategyMarkdownDocumentV1(StrategyContextModel):
    markdown: str = Field(default="", max_length=200_000)
    sha256: str = Field(default="", max_length=64)
    generated_at: datetime | None = None
    source: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_hash(self) -> "StrategyMarkdownDocumentV1":
        expected = markdown_sha256(self.markdown)
        if self.sha256 and self.sha256.lower() != expected:
            raise ValueError("document sha256 does not match markdown")
        self.sha256 = expected
        return self


class OperationCycleExecutionContractV1(StrategyContextModel):
    schema_version: Literal["operation_cycle_execution_contract.v1"] = "operation_cycle_execution_contract.v1"
    review_required: Literal[True] = True
    auto_approve_allowed: Literal[False] = False
    direct_broadcast_jobs_allowed: Literal[False] = False
    required_review_status: Literal["pending_review"] = "pending_review"
    required_run_status: Literal["draft"] = "draft"
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    allowed_owner_userids: list[str] = Field(default_factory=list, max_length=50)
    max_weekly_private_messages: int = Field(default=2, ge=0, le=100)
    required_checks: list[str] = Field(default_factory=list, max_length=100)
    custom_rules: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_owner_userids", "required_checks")
    @classmethod
    def unique_text_items(cls, value: list[str]) -> list[str]:
        cleaned = [str(item or "").strip() for item in value if str(item or "").strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("execution contract list items must be unique")
        return cleaned


class OperationCycleStrategyDocumentPackV1(StrategyContextModel):
    schema_version: Literal["operation_cycle_strategy_document_pack.v1"] = (
        "operation_cycle_strategy_document_pack.v1"
    )
    execution_guide: StrategyMarkdownDocumentV1 = Field(default_factory=StrategyMarkdownDocumentV1)
    copy_guide: StrategyMarkdownDocumentV1 = Field(default_factory=StrategyMarkdownDocumentV1)
    measurement_guide: StrategyMarkdownDocumentV1 = Field(default_factory=StrategyMarkdownDocumentV1)
    execution_contract: OperationCycleExecutionContractV1 = Field(
        default_factory=OperationCycleExecutionContractV1
    )

    @model_validator(mode="after")
    def reject_private_content(self) -> "OperationCycleStrategyDocumentPackV1":
        validate_private_payload(self.model_dump(mode="json"))
        return self


class StrategyTargetVersionV1(StrategyContextModel):
    version_label: str = Field(default="", max_length=120)
    objective: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)
    document_pack: OperationCycleStrategyDocumentPackV1


class StrategyChangeProposalV1(StrategyContextModel):
    schema_version: Literal["operation_cycle_strategy_change_proposal.v1"] = (
        "operation_cycle_strategy_change_proposal.v1"
    )
    strategy_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    base_strategy_version: int = Field(ge=1)
    source_run_key: str = Field(default="", max_length=160)
    conclusion: str = Field(min_length=1, max_length=6000)
    hypothesis: str = Field(default="", max_length=4000)
    actions: list[str] = Field(min_length=1, max_length=100)
    target_version: StrategyTargetVersionV1
    evidence: list[ReferenceSnapshot] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_proposal(self) -> "StrategyChangeProposalV1":
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("proposal actions must be unique")
        validate_private_payload(self.model_dump(mode="json"))
        return self


class StrategyChangeDecisionRequest(StrategyContextModel):
    decision: ProposalDecision
    note: str = Field(min_length=1, max_length=2000)


class StrategyVersionContextView(StrategyContextModel):
    strategy_key: str
    version: int
    version_label: str = ""
    objective: str = ""
    definition: dict[str, Any] = Field(default_factory=dict)
    governance_status: str = "confirmed"
    document_pack: OperationCycleStrategyDocumentPackV1 = Field(
        default_factory=OperationCycleStrategyDocumentPackV1
    )
    version_hash: str = ""
    confirmed_by: str = ""
    confirmed_at: datetime | None = None
    confirmation_note: str = ""


class StrategyChangeProposalView(StrategyContextModel):
    proposal_id: str
    proposal_hash: str
    strategy_key: str
    base_strategy_version: int
    source_run_key: str = ""
    status: ProposalStatus = "pending"
    proposal: StrategyChangeProposalV1
    submitted_by: str = ""
    created_at: datetime | None = None
    decided_by: str = ""
    decided_at: datetime | None = None
    decision_note: str = ""
    applied_strategy_version: int | None = None


class OperationCycleContextIndexItem(StrategyContextModel):
    strategy: StrategySummary
    execution_version: int
    document_pack_complete: bool = False
    pending_proposal_count: int = 0


class OperationCycleContextIndexView(StrategyContextModel):
    ok: bool = True
    items: list[OperationCycleContextIndexItem] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


class OperationCycleSystemFactProjectionV1(StrategyContextModel):
    plan_id: str
    run_key: str = ""
    approved_at: datetime | None = None
    task_count: int = 0
    finalized_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    last_delivery_at: datetime | None = None
    source_priority: Literal["system"] = "system"


class OperationCycleStrategyContextView(StrategyContextModel):
    ok: bool = True
    mode: ContextMode
    strategy: StrategySummary
    execution: StrategyVersionContextView
    recent_runs: list[RunDetailView] = Field(default_factory=list)
    proposals: list[StrategyChangeProposalView] = Field(default_factory=list)
    history: list[RunSummary] = Field(default_factory=list)
    system_facts: list[OperationCycleSystemFactProjectionV1] = Field(default_factory=list)
    limit: int = 3
    offset: int = 0


class StrategyChangeProposalListView(StrategyContextModel):
    ok: bool = True
    strategy_key: str
    items: list[StrategyChangeProposalView] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


__all__ = [
    "ContextMode",
    "OperationCycleContextIndexItem",
    "OperationCycleContextIndexView",
    "OperationCycleExecutionContractV1",
    "OperationCycleStrategyContextView",
    "OperationCycleSystemFactProjectionV1",
    "OperationCycleStrategyDocumentPackV1",
    "StrategyChangeDecisionRequest",
    "StrategyChangeProposalListView",
    "StrategyChangeProposalV1",
    "StrategyChangeProposalView",
    "StrategyMarkdownDocumentV1",
    "StrategyTargetVersionV1",
    "StrategyVersionContextView",
    "markdown_sha256",
]
