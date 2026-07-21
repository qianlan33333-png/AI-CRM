from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from aicrm_next.platform_foundation.command_bus.models import CommandContext

DEFAULT_TENANT_ID = "aicrm"

ExternalEffectStatus = Literal[
    "planned",
    "approved",
    "queued",
    "dispatching",
    "succeeded",
    "simulated",
    "unknown_after_dispatch",
    "failed_retryable",
    "failed_terminal",
    "blocked",
    "cancelled",
    "expired",
]
ExternalEffectExecutionMode = Literal["disabled", "shadow", "plan_only", "execute", "execute_dryrun"]
ExternalEffectAttemptStatus = Literal[
    "dispatching",
    "succeeded",
    "simulated",
    "unknown_after_dispatch",
    "failed_retryable",
    "failed_terminal",
    "blocked",
    "skipped",
]

WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH = "webhook.questionnaire_submission.push"
WEBHOOK_ORDER_PAID_PUSH = "webhook.order_paid.push"
WEBHOOK_CUSTOMER_AUTOMATION_RETRY = "webhook.customer_automation.retry"
WEBHOOK_CUSTOMER_AUTOMATION_RETRY_DUE = "webhook.customer_automation.retry_due"
WEBHOOK_GENERIC_PUSH = "webhook.generic.push"
AI_ASSIST_CAMPAIGN_MESSAGE_PLAN = "ai_assist.campaign.message.plan"
AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK = "ai_assist.campaign.message.loopback"
WECOM_MESSAGE_PRIVATE_SEND = "wecom.message.private.send"
WECOM_MESSAGE_GROUP_SEND = "wecom.message.group.send"
WECOM_MESSAGE_BROADCAST_SEND = "wecom.message.broadcast.send"
WECOM_WELCOME_MESSAGE_SEND = "wecom.welcome_message.send"
WECOM_CONTACT_TAG_MARK = "wecom.contact.tag.mark"
WECOM_CONTACT_TAG_UNMARK = "wecom.contact.tag.unmark"
WECOM_PROFILE_UPDATE = "wecom.profile.update"
WECOM_EXTERNAL_CONTACT_DETAIL_FETCH = "wecom.external_contact.detail.fetch"
GROUP_OPS_MESSAGE_LOOPBACK = "group_ops.message.loopback"
GROUP_OPS_WEBHOOK_ACTION_LOOPBACK = "group_ops.webhook.action.loopback"
PAYMENT_WECHAT_ORDER_QUERY = "payment.wechat.order.query"
PAYMENT_WECHAT_REFUND_REQUEST = "payment.wechat.refund.request"
PAYMENT_WECHAT_REFUND_QUERY = "payment.wechat.refund.query"
PAYMENT_ALIPAY_ORDER_QUERY = "payment.alipay.order.query"
PAYMENT_ALIPAY_REFUND_QUERY = "payment.alipay.refund.query"
FEISHU_WEBHOOK_NOTIFY = "feishu.webhook.notify"
OPENCLAW_CONTEXT_PUSH = "openclaw.context.push"
MEDIA_STORAGE_UPLOAD = "media.storage.upload"
WECOM_MEDIA_UPLOAD = "wecom.media.upload"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def public_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExternalEffectCreateRequest:
    effect_type: str
    adapter_name: str
    operation: str
    target_type: str
    target_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    payload_summary: dict[str, Any] = field(default_factory=dict)
    context: CommandContext = field(default_factory=CommandContext)
    business_type: str = ""
    business_id: str = ""
    source_module: str = ""
    source_event_id: str = ""
    source_command_id: str = ""
    risk_level: str = "medium"
    requires_approval: bool = False
    execution_mode: ExternalEffectExecutionMode = "execute"
    scheduled_at: datetime | None = None
    available_at: datetime | None = None
    priority: int = 100
    max_attempts: int = 5
    tenant_id: str = DEFAULT_TENANT_ID
    status: ExternalEffectStatus = "queued"
    idempotency_key: str = ""
    correlation_id: str = ""
    execution_id: str = ""
    parent_execution_id: str = ""
    lane: str = ""
    ordering_key: str = ""
    fairness_key: str = ""
    rate_scope_key: str = ""

    def __post_init__(self) -> None:
        """Strip CAS-owned canary fields from every ordinary enqueue path."""

        if not str(self.effect_type or "").strip().startswith("wecom."):
            return
        payload = dict(self.payload or {})
        summary = dict(self.payload_summary or {})
        if str(payload.get("execution_scope") or "").strip() == "allowlisted_canary":
            payload.pop("execution_scope", None)
        summary.pop("canary_authorization", None)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "payload_summary", summary)


@dataclass(frozen=True)
class ExternalEffectJob:
    id: int = 0
    created_on_plan: bool = False
    tenant_id: str = DEFAULT_TENANT_ID
    effect_type: str = ""
    adapter_name: str = ""
    operation: str = ""
    target_type: str = ""
    target_id: str = ""
    business_type: str = ""
    business_id: str = ""
    source_module: str = ""
    source_route: str = ""
    source_event_id: str = ""
    source_command_id: str = ""
    trace_id: str = ""
    request_id: str = ""
    correlation_id: str = ""
    execution_id: str = ""
    parent_execution_id: str = ""
    idempotency_key: str = ""
    actor_id: str = ""
    actor_type: str = "system"
    risk_level: str = "medium"
    requires_approval: bool = False
    execution_mode: ExternalEffectExecutionMode = "execute"
    payload_json: dict[str, Any] = field(default_factory=dict)
    payload_summary_json: dict[str, Any] = field(default_factory=dict)
    status: ExternalEffectStatus = "queued"
    row_version: int = 1
    priority: int = 100
    scheduled_at: str = ""
    lane: str = ""
    available_at: str = ""
    ordering_key: str = ""
    fairness_key: str = ""
    rate_scope_key: str = ""
    attempt_count: int = 0
    max_attempts: int = 5
    next_retry_at: str = ""
    locked_at: str = ""
    locked_by: str = ""
    lease_token: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    worker_generation: int = 0
    policy_version: str = ""
    dispatch_started_at: str = ""
    provider_call_started_at: str = ""
    last_attempt_id: str = ""
    last_error_code: str = ""
    last_error_message: str = ""
    side_effect_executed: bool = False
    provider_result_received: bool = False
    result_summary_json: dict[str, Any] = field(default_factory=dict)
    reconciliation_required: bool = False
    created_at: str = ""
    updated_at: str = ""
    approved_at: str = ""
    executed_at: str = ""
    completed_at: str = ""
    cancelled_at: str = ""
    cancel_requested_at: str = ""
    cancel_requested_by: str = ""
    cancel_reason: str = ""
    hold_reason: str = ""
    hold_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalEffectAttempt:
    id: int = 0
    attempt_id: str = field(default_factory=lambda: "eea_" + uuid4().hex)
    job_id: int = 0
    adapter_name: str = ""
    adapter_mode: str = "none"
    operation: str = ""
    trace_id: str = ""
    request_id: str = ""
    lease_token: str = ""
    request_hash: str = ""
    provider_call_started_at: str = ""
    worker_generation: int = 0
    status: ExternalEffectAttemptStatus = "skipped"
    request_summary_json: dict[str, Any] = field(default_factory=dict)
    response_summary_json: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalEffectTestReceipt:
    id: int = 0
    receipt_id: str = field(default_factory=lambda: "eer_" + uuid4().hex)
    event_id: str = ""
    job_id: int = 0
    effect_type: str = ""
    trace_id: str = ""
    idempotency_key: str = ""
    target_type: str = ""
    target_id: str = ""
    business_type: str = ""
    business_id: str = ""
    request_method: str = "POST"
    request_path: str = ""
    headers_summary_json: dict[str, Any] = field(default_factory=dict)
    payload_summary_json: dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""
    body_json: dict[str, Any] = field(default_factory=dict)
    signature_valid: bool | None = None
    response_status: int = 200
    received_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalEffectDispatchResult:
    status: ExternalEffectAttemptStatus
    adapter_mode: str = "none"
    request_summary: dict[str, Any] = field(default_factory=dict)
    response_summary: dict[str, Any] = field(default_factory=dict)
    provider_result: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    retry_after_seconds: int | None = None
    real_external_call_executed: bool = False
    provider_result_received: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "simulated"}
