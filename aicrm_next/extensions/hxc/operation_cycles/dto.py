from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExecutionStage = Literal[
    "scheduled",
    "preflight",
    "decisioning",
    "dry_run",
    "review",
    "delivery",
    "observing",
    "postmortem",
    "closed",
]
ReviewStatus = Literal["not_created", "pending", "approved", "rejected", "cancelled"]
DeliveryStatus = Literal[
    "not_started",
    "waiting_window",
    "dispatching",
    "partial",
    "completed",
    "failed",
    "cancelled",
]
DataStatus = Literal["unavailable", "collecting", "early", "mature", "partial", "attribution_gap"]
OptimizationStatus = Literal["none", "draft", "pending_confirmation", "accepted", "rejected", "applied"]
ArtifactStatus = Literal["complete", "partial", "source_missing", "snapshot_only"]
StageStatus = Literal["running", "completed", "blocked"]
ValueStatus = Literal[
    "observed",
    "not_started",
    "not_due",
    "unknown",
    "not_applicable",
    "blocked",
    "instrumentation_missing",
    "partial_lower_bound",
]


class OperationCycleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StrategySnapshot(OperationCycleModel):
    strategy_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    cadence: str = Field(default="", max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    status: Literal["active", "paused", "archived", "draft"] = "active"
    version: int = Field(default=1, ge=1)
    version_label: str = Field(default="", max_length=120)
    objective: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)
    version_effective_from: datetime | None = None


class RunSnapshot(OperationCycleModel):
    run_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    label: str = Field(default="", max_length=200)
    objective: str = Field(default="", max_length=2000)
    plan_version: str = Field(default="", max_length=120)
    plan_status: str = Field(default="", max_length=80)
    plan_source: str = Field(default="", max_length=240)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    intended_send_at: datetime | None = None
    plan_scheduled_for: datetime | None = None
    first_sent_at: datetime | None = None
    last_sent_at: datetime | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> "RunSnapshot":
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("run.completed_at must not precede run.started_at")
        if self.first_sent_at and self.last_sent_at and self.last_sent_at < self.first_sent_at:
            raise ValueError("run.last_sent_at must not precede run.first_sent_at")
        return self


class AttemptSnapshot(OperationCycleModel):
    attempt_key: str = Field(min_length=1, max_length=160)
    parent_attempt_key: str | None = Field(default=None, max_length=160)
    status: StageStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    blocked_reason: str = Field(default="", max_length=2000)
    summary: dict[str, Any] = Field(default_factory=dict)


class StageSnapshot(OperationCycleModel):
    stage_key: str = Field(min_length=1, max_length=160)
    attempt_key: str = Field(min_length=1, max_length=160)
    stage: ExecutionStage
    status: StageStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    blocked_reason: str = Field(default="", max_length=2000)
    summary: dict[str, Any] = Field(default_factory=dict)


class FunnelValue(OperationCycleModel):
    status: ValueStatus = "unknown"
    value: int | None = Field(default=None, ge=0)
    data_source: str = Field(default="", max_length=240)
    limitation: str = Field(default="", max_length=2000)
    classification: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def validate_value_status(self) -> "FunnelValue":
        may_have_value = self.status in {"observed", "partial_lower_bound"}
        if may_have_value and self.value is None:
            raise ValueError(f"funnel value is required when status={self.status}")
        if not may_have_value and self.value is not None:
            raise ValueError(f"funnel value must be null when status={self.status}")
        return self


def _unknown_funnel_value() -> FunnelValue:
    return FunnelValue(status="unknown")


class FunnelSnapshot(OperationCycleModel):
    candidate_count: FunnelValue = Field(default_factory=_unknown_funnel_value)
    audited_count: FunnelValue = Field(default_factory=_unknown_funnel_value)
    recommended_send_count: FunnelValue = Field(default_factory=_unknown_funnel_value)
    planned_target_count: FunnelValue = Field(default_factory=_unknown_funnel_value)
    effective_sent_count: FunnelValue = Field(default_factory=_unknown_funnel_value)
    failed_count: FunnelValue = Field(default_factory=_unknown_funnel_value)


class MetricSnapshot(OperationCycleModel):
    metric_key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    numerator: float | None = Field(ge=0)
    denominator: float | None = Field(ge=0)
    value: float | None = None
    unit: str = Field(default="count", max_length=40)
    observation_window: str = Field(min_length=1, max_length=120)
    data_source: str = Field(min_length=1, max_length=240)
    data_quality: str = Field(min_length=1, max_length=120)
    limitations: list[str] = Field(min_length=1, max_length=30)
    is_causal: Literal[False] = False
    value_status: ValueStatus = "unknown"

    @model_validator(mode="after")
    def validate_denominator_and_value(self) -> "MetricSnapshot":
        if self.denominator == 0 and self.numerator not in {None, 0}:
            raise ValueError("metric denominator=0 cannot have a positive numerator")
        if self.value_status in {"observed", "partial_lower_bound"}:
            if self.numerator is None or self.denominator is None:
                raise ValueError("observed metric requires numerator and denominator")
        elif any(value is not None for value in (self.value, self.numerator, self.denominator)):
            raise ValueError(f"metric numeric values must be null when value_status={self.value_status}")
        return self


class RetrospectiveSnapshot(OperationCycleModel):
    conclusion: str = Field(default="", max_length=6000)
    observations: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=50)
    data_conflicts: list[str] = Field(default_factory=list, max_length=50)
    generated_at: datetime | None = None


class NextIterationSnapshot(OperationCycleModel):
    summary: str = Field(default="", max_length=6000)
    hypothesis: str = Field(default="", max_length=4000)
    actions: list[str] = Field(default_factory=list, max_length=50)
    status: OptimizationStatus = "none"
    confirmation_note: str = Field(default="", max_length=2000)
    applied_strategy_version: int | None = Field(default=None, ge=1)


class ReferenceSnapshot(OperationCycleModel):
    reference_key: str = Field(min_length=1, max_length=160)
    reference_type: Literal["broadcast_job", "push_center", "delivery_lineage", "report", "artifact", "other"]
    label: str = Field(default="", max_length=240)
    source_system: str = Field(default="", max_length=120)
    source_id: str = Field(default="", max_length=240)
    href: str = Field(default="", max_length=1000)
    evidence_hash: str = Field(default="", max_length=128)
    data_status: ValueStatus = "unknown"

    @field_validator("href")
    @classmethod
    def validate_href(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized and not normalized.startswith(("/admin/", "/api/admin/", "https://", "http://")):
            raise ValueError("reference href must be an admin path or http(s) URL")
        return normalized

    @model_validator(mode="after")
    def validate_evidence_hash(self) -> "ReferenceSnapshot":
        evidence_hash = self.evidence_hash.lower()
        if evidence_hash and (len(evidence_hash) != 64 or any(char not in "0123456789abcdef" for char in evidence_hash)):
            raise ValueError("reference evidence_hash must be a 64-character sha256 hex digest")
        if self.data_status == "observed" and not evidence_hash:
            raise ValueError(f"reference evidence_hash is required when data_status={self.data_status}")
        self.evidence_hash = evidence_hash
        return self


class MarkdownDocumentSnapshot(OperationCycleModel):
    """Opaque Agent-authored Markdown with only transport metadata."""

    markdown: str = Field(default="", max_length=200_000)
    generated_at: datetime | None = None


class OperationCycleDocumentsSnapshot(OperationCycleModel):
    """The three read-only weekly document slots exposed by the strategy detail page."""

    broadcast_details: MarkdownDocumentSnapshot = Field(default_factory=MarkdownDocumentSnapshot)
    retrospective_details: MarkdownDocumentSnapshot = Field(default_factory=MarkdownDocumentSnapshot)
    execution_strategy: MarkdownDocumentSnapshot = Field(default_factory=MarkdownDocumentSnapshot)


class OperationCycleSnapshotV1(OperationCycleModel):
    schema_version: Literal["operation_cycle_snapshot.v1"] = "operation_cycle_snapshot.v1"
    external_effects: Literal["none"] = "none"
    report_id: str = Field(min_length=1, max_length=200)
    snapshot_revision: int = Field(ge=1)
    tenant_id: Literal["aicrm"] = "aicrm"
    reported_at: datetime | None = None
    strategy: StrategySnapshot
    run: RunSnapshot
    execution_stage: ExecutionStage
    review_status: ReviewStatus
    delivery_status: DeliveryStatus
    data_status: DataStatus
    optimization_status: OptimizationStatus
    artifact_status: ArtifactStatus
    attempts: list[AttemptSnapshot] = Field(default_factory=list, max_length=100)
    stages: list[StageSnapshot] = Field(default_factory=list, max_length=200)
    funnel: FunnelSnapshot = Field(default_factory=FunnelSnapshot)
    metrics: list[MetricSnapshot] = Field(default_factory=list, max_length=200)
    retrospective: RetrospectiveSnapshot = Field(default_factory=RetrospectiveSnapshot)
    next_iteration: NextIterationSnapshot = Field(default_factory=NextIterationSnapshot)
    references: list[ReferenceSnapshot] = Field(default_factory=list, max_length=200)
    documents: OperationCycleDocumentsSnapshot = Field(default_factory=OperationCycleDocumentsSnapshot)

    @model_validator(mode="after")
    def validate_complete_snapshot(self) -> "OperationCycleSnapshotV1":
        from .domain import ensure_unique, validate_private_payload

        ensure_unique([item.attempt_key for item in self.attempts], field_name="attempts.attempt_key")
        ensure_unique([item.stage_key for item in self.stages], field_name="stages.stage_key")
        ensure_unique([item.metric_key for item in self.metrics], field_name="metrics.metric_key")
        ensure_unique([item.reference_key for item in self.references], field_name="references.reference_key")
        attempts = {item.attempt_key: item for item in self.attempts}
        for attempt in self.attempts:
            if attempt.parent_attempt_key == attempt.attempt_key:
                raise ValueError("attempt cannot be its own parent")
            if attempt.parent_attempt_key and attempt.parent_attempt_key not in attempts:
                raise ValueError(f"attempt parent does not exist: {attempt.parent_attempt_key}")
            if attempt.started_at and attempt.ended_at and attempt.ended_at < attempt.started_at:
                raise ValueError(f"attempt ended_at precedes started_at: {attempt.attempt_key}")
        for attempt in self.attempts:
            seen = {attempt.attempt_key}
            parent_key = attempt.parent_attempt_key
            while parent_key:
                if parent_key in seen:
                    raise ValueError(f"attempt parent cycle detected: {attempt.attempt_key}")
                seen.add(parent_key)
                parent_key = attempts[parent_key].parent_attempt_key
        for stage in self.stages:
            if stage.attempt_key not in attempts:
                raise ValueError(f"stage attempt does not exist: {stage.attempt_key}")
            if stage.started_at and stage.ended_at and stage.ended_at < stage.started_at:
                raise ValueError(f"stage ended_at precedes started_at: {stage.stage_key}")
        validate_private_payload(self.model_dump(mode="json"))
        return self


class StrategyVersionView(OperationCycleModel):
    version: int
    label: str = ""
    objective: str = ""
    definition: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    created_at: datetime | None = None


class RunSummary(OperationCycleModel):
    run_key: str
    strategy_key: str
    label: str = ""
    objective: str = ""
    plan_version: str = ""
    plan_status: str = ""
    plan_source: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    intended_send_at: datetime | None = None
    plan_scheduled_for: datetime | None = None
    first_sent_at: datetime | None = None
    last_sent_at: datetime | None = None
    execution_stage: ExecutionStage
    review_status: ReviewStatus
    delivery_status: DeliveryStatus
    data_status: DataStatus
    optimization_status: OptimizationStatus
    artifact_status: ArtifactStatus
    funnel: FunnelSnapshot = Field(default_factory=FunnelSnapshot)
    conclusion: str = ""
    snapshot_revision: int = 0
    received_at: datetime | None = None
    fact_conflict: bool = False


class StrategySummary(OperationCycleModel):
    strategy_key: str
    title: str
    description: str = ""
    cadence: str = ""
    timezone: str = "Asia/Shanghai"
    status: str = "active"
    current_version: int = 1
    run_count: int = 0
    latest_run_key: str = ""
    latest_run_label: str = ""
    latest_run_at: datetime | None = None
    execution_stage: ExecutionStage = "scheduled"
    review_status: ReviewStatus = "not_created"
    delivery_status: DeliveryStatus = "not_started"
    data_status: DataStatus = "unavailable"
    optimization_status: OptimizationStatus = "none"
    artifact_status: ArtifactStatus = "source_missing"
    funnel: FunnelSnapshot = Field(default_factory=FunnelSnapshot)
    conclusion: str = ""
    next_iteration_summary: str = ""


class SnapshotInfo(OperationCycleModel):
    report_id: str
    snapshot_revision: int
    snapshot_hash: str
    schema_version: str
    reporter_id: str = ""
    client_id: str = ""
    received_at: datetime | None = None


class OperationCycleReportReceipt(OperationCycleModel):
    ok: bool = True
    receipt_id: str
    strategy_key: str
    run_key: str
    accepted_revision: int
    projection_updated: bool
    snapshot_hash: str


class StrategyListView(OperationCycleModel):
    ok: bool = True
    items: list[StrategySummary] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


class StrategyDetailView(OperationCycleModel):
    ok: bool = True
    strategy: StrategySummary
    versions: list[StrategyVersionView] = Field(default_factory=list)
    trend: list[RunSummary] = Field(default_factory=list)
    sources: list[ReferenceSnapshot] = Field(default_factory=list)
    documents: OperationCycleDocumentsSnapshot = Field(default_factory=OperationCycleDocumentsSnapshot)
    assistant_plans: list[ReferenceSnapshot] = Field(default_factory=list)


class RunListView(OperationCycleModel):
    ok: bool = True
    strategy_key: str
    items: list[RunSummary] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


class RunDetailView(OperationCycleModel):
    ok: bool = True
    run: RunSummary
    attempts: list[AttemptSnapshot] = Field(default_factory=list)
    stages: list[StageSnapshot] = Field(default_factory=list)
    metrics: list[MetricSnapshot] = Field(default_factory=list)
    retrospective: RetrospectiveSnapshot = Field(default_factory=RetrospectiveSnapshot)
    next_iteration: NextIterationSnapshot = Field(default_factory=NextIterationSnapshot)
    references: list[ReferenceSnapshot] = Field(default_factory=list)
    documents: OperationCycleDocumentsSnapshot = Field(default_factory=OperationCycleDocumentsSnapshot)
    snapshot: SnapshotInfo
