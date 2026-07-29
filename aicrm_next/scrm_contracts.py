from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, runtime_checkable


CONTRACT_SCHEMA_VERSION = 1
DataScope = Literal["own", "team", "all"]
RelationshipStatus = Literal["active", "transferred", "lost", "deleted"]
AudienceCombine = Literal["all", "any"]
AudienceSnapshotMode = Literal["live", "frozen"]
ContentKind = Literal["text", "image", "file", "link", "miniprogram", "group_invite"]
CampaignStatus = Literal["draft", "approved", "scheduled", "running", "completed", "partial", "failed", "cancelled"]
ApprovalStatus = Literal["not_required", "pending", "approved", "rejected"]
ReceiptStatus = Literal["accepted", "delivered", "failed_retryable", "failed_terminal", "unknown"]

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,191}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,127}$")
_AUDIENCE_OPERATORS = frozenset({"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "exists"})


class ContractValidationError(ValueError):
    pass


def _required_identifier(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ContractValidationError(f"{field_name} must be a stable identifier")
    return normalized


def _positive_version(value: int, field_name: str) -> int:
    normalized = int(value or 0)
    if normalized < 1:
        raise ContractValidationError(f"{field_name} must be positive")
    return normalized


@dataclass(frozen=True)
class CustomerRelationship:
    relationship_id: str
    customer_id: str
    corp_id: str
    employee_userid: str
    external_userid: str = ""
    unionid: str = ""
    status: RelationshipStatus = "active"
    data_scope: DataScope = "own"
    lifecycle_stage: str = ""
    source: str = ""
    tag_ids: tuple[str, ...] = ()
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("relationship_id", "customer_id", "corp_id", "employee_userid"):
            _required_identifier(getattr(self, field_name), field_name)
        if not (str(self.external_userid or "").strip() or str(self.unionid or "").strip()):
            raise ContractValidationError("external_userid or unionid is required")
        if self.status not in {"active", "transferred", "lost", "deleted"}:
            raise ContractValidationError("invalid relationship status")
        if self.data_scope not in {"own", "team", "all"}:
            raise ContractValidationError("invalid relationship data_scope")
        _positive_version(self.version, "version")
        if len(set(self.tag_ids)) != len(self.tag_ids):
            raise ContractValidationError("tag_ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AudienceFilter:
    field: str
    operator: str
    value: Any = None

    def __post_init__(self) -> None:
        if not _FIELD_NAME.fullmatch(str(self.field or "").strip()):
            raise ContractValidationError("audience filter field is invalid")
        if self.operator not in _AUDIENCE_OPERATORS:
            raise ContractValidationError(f"unsupported audience operator: {self.operator}")
        if self.operator != "exists" and self.value is None:
            raise ContractValidationError("audience filter value is required")


@dataclass(frozen=True)
class AudienceSpec:
    audience_id: str
    name: str
    filters: tuple[AudienceFilter, ...]
    owner_capability_id: str
    data_scope: DataScope = "all"
    combine: AudienceCombine = "all"
    snapshot_mode: AudienceSnapshotMode = "live"
    version: int = 1
    estimated_count: int | None = None
    created_by: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.audience_id, "audience_id")
        _required_identifier(self.owner_capability_id, "owner_capability_id")
        if not str(self.name or "").strip():
            raise ContractValidationError("audience name is required")
        if not self.filters:
            raise ContractValidationError("audience filters are required")
        if self.data_scope not in {"own", "team", "all"}:
            raise ContractValidationError("invalid audience data_scope")
        if self.combine not in {"all", "any"}:
            raise ContractValidationError("invalid audience combine mode")
        if self.snapshot_mode not in {"live", "frozen"}:
            raise ContractValidationError("invalid audience snapshot mode")
        _positive_version(self.version, "version")
        if self.estimated_count is not None and int(self.estimated_count) < 0:
            raise ContractValidationError("estimated_count cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContentItem:
    kind: ContentKind
    text: str = ""
    asset_ref: str = ""
    title: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"text", "image", "file", "link", "miniprogram", "group_invite"}:
            raise ContractValidationError("unsupported content kind")
        if self.kind == "text" and not str(self.text or "").strip():
            raise ContractValidationError("text content is required")
        if self.kind in {"image", "file", "miniprogram", "group_invite"} and not str(self.asset_ref or "").strip():
            raise ContractValidationError(f"{self.kind} content requires asset_ref")
        if self.kind == "link" and not (str(self.title or "").strip() and str(self.url or "").strip()):
            raise ContractValidationError("link content requires title and url")


@dataclass(frozen=True)
class ContentBundle:
    bundle_id: str
    name: str
    items: tuple[ContentItem, ...]
    version: int = 1
    channel: str = "wecom"
    created_by: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.bundle_id, "bundle_id")
        if not str(self.name or "").strip():
            raise ContractValidationError("content bundle name is required")
        if not self.items:
            raise ContractValidationError("content bundle must contain at least one item")
        if len(self.items) > 10:
            raise ContractValidationError("content bundle cannot contain more than 10 items")
        _positive_version(self.version, "version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CampaignExecution:
    execution_id: str
    campaign_id: str
    audience_id: str
    audience_version: int
    content_bundle_id: str
    content_bundle_version: int
    idempotency_key: str
    status: CampaignStatus = "draft"
    approval_status: ApprovalStatus = "pending"
    target_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    scheduled_at: str = ""
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("execution_id", "campaign_id", "audience_id", "content_bundle_id", "idempotency_key"):
            _required_identifier(getattr(self, field_name), field_name)
        _positive_version(self.audience_version, "audience_version")
        _positive_version(self.content_bundle_version, "content_bundle_version")
        if self.status not in {"draft", "approved", "scheduled", "running", "completed", "partial", "failed", "cancelled"}:
            raise ContractValidationError("invalid campaign status")
        if self.approval_status not in {"not_required", "pending", "approved", "rejected"}:
            raise ContractValidationError("invalid approval status")
        counts = [self.target_count, self.succeeded_count, self.failed_count, self.skipped_count]
        if any(int(value) < 0 for value in counts):
            raise ContractValidationError("campaign counts cannot be negative")
        if self.succeeded_count + self.failed_count + self.skipped_count > self.target_count:
            raise ContractValidationError("campaign outcome counts exceed target_count")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderReceipt:
    receipt_id: str
    provider: str
    effect_type: str
    effect_id: str
    attempt_id: str
    idempotency_key: str
    status: ReceiptStatus
    provider_code: str = ""
    provider_message: str = ""
    provider_request_id: str = ""
    raw_payload_ref: str = ""
    received_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("receipt_id", "provider", "effect_type", "effect_id", "attempt_id", "idempotency_key"):
            _required_identifier(getattr(self, field_name), field_name)
        if self.status not in {"accepted", "delivered", "failed_retryable", "failed_terminal", "unknown"}:
            raise ContractValidationError("invalid provider receipt status")
        if len(str(self.provider_message or "")) > 500:
            raise ContractValidationError("provider_message exceeds 500 characters")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class CustomerRelationshipQueryPort(Protocol):
    def get_relationship(self, relationship_id: str) -> CustomerRelationship | None: ...


@runtime_checkable
class AudienceResolvePort(Protocol):
    def resolve_customer_ids(self, spec: AudienceSpec) -> tuple[str, ...]: ...


@runtime_checkable
class ContentBundleQueryPort(Protocol):
    def get_content_bundle(self, bundle_id: str, version: int) -> ContentBundle | None: ...


@runtime_checkable
class CampaignExecutionPort(Protocol):
    def get_execution(self, execution_id: str) -> CampaignExecution | None: ...

    def save_execution(self, execution: CampaignExecution) -> CampaignExecution: ...


@runtime_checkable
class ProviderReceiptPort(Protocol):
    def record_receipt(self, receipt: ProviderReceipt) -> ProviderReceipt: ...


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ApprovalStatus",
    "AudienceFilter",
    "AudienceResolvePort",
    "AudienceSpec",
    "CampaignExecution",
    "CampaignExecutionPort",
    "ContentBundle",
    "ContentBundleQueryPort",
    "ContentItem",
    "ContractValidationError",
    "CustomerRelationship",
    "CustomerRelationshipQueryPort",
    "ProviderReceipt",
    "ProviderReceiptPort",
]
