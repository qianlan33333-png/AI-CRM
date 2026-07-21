from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4


BackgroundJobStatus = Literal["pending", "running", "succeeded", "failed", "dead_lettered"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def public_datetime(value: datetime | str | None = None) -> str:
    if value is None:
        value = utcnow()
    if isinstance(value, str):
        return value
    dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_idempotency_key(*, source_route: str, payload: dict[str, Any], explicit_key: str = "") -> str:
    explicit = str(explicit_key or "").strip()
    if explicit:
        return explicit
    encoded = json.dumps({"source_route": source_route, "payload": payload}, ensure_ascii=False, sort_keys=True, default=str)
    return "bgj_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class BackgroundJobContract:
    job_type: str
    source_route: str
    idempotency_key: str
    payload_schema_version: int = 1
    attempt_count: int = 0
    next_run_at: str = ""
    status: BackgroundJobStatus = "pending"
    external_effect_key: str = ""
    audit_context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=public_datetime)
    updated_at: str = field(default_factory=public_datetime)
    last_error: str = ""
    error_code: str = ""
    job_id: str = field(default_factory=lambda: "bgj_" + uuid4().hex)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackgroundJobHandlerResult:
    status: BackgroundJobStatus = "succeeded"
    result_summary: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    last_error: str = ""
    retry_after_seconds: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class WebhookIntakeResult:
    ok: bool
    created: bool = False
    duplicate: bool = False
    job: BackgroundJobContract | None = None
    error_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class WebhookRouteContract:
    path: str
    methods: tuple[str, ...]
    route_name: str
    expected_external_effects: str
    expected_data_source: str
    external_effects_rationale: str


class BackgroundJobQueue:
    def __init__(self) -> None:
        self._jobs_by_idempotency_key: dict[str, BackgroundJobContract] = {}

    def enqueue(self, job: BackgroundJobContract) -> tuple[BackgroundJobContract, bool]:
        existing = self._jobs_by_idempotency_key.get(job.idempotency_key)
        if existing is not None:
            return existing, False
        self._jobs_by_idempotency_key[job.idempotency_key] = job
        return job, True

    def get(self, idempotency_key: str) -> BackgroundJobContract | None:
        return self._jobs_by_idempotency_key.get(idempotency_key)

    def list_jobs(self) -> list[BackgroundJobContract]:
        return list(self._jobs_by_idempotency_key.values())

    def list_due(self) -> list[BackgroundJobContract]:
        return [job for job in self.list_jobs() if job.status in {"pending", "failed"}]

    def update(self, job: BackgroundJobContract) -> BackgroundJobContract:
        self._jobs_by_idempotency_key[job.idempotency_key] = job
        return job


class BackgroundJobWorker:
    def __init__(self, queue: BackgroundJobQueue, handler: Callable[[BackgroundJobContract], BackgroundJobHandlerResult]):
        self._queue = queue
        self._handler = handler

    def dispatch_one(self, job: BackgroundJobContract, *, max_attempts: int = 3) -> BackgroundJobContract:
        running = self._queue.update(replace(job, status="running", updated_at=public_datetime()))
        try:
            result = self._handler(running)
        except Exception as exc:
            result = BackgroundJobHandlerResult(status="failed", error_code=type(exc).__name__, last_error=str(exc))

        attempt_count = int(running.attempt_count or 0) + 1
        if result.status == "succeeded":
            updated = replace(
                running,
                status="succeeded",
                attempt_count=attempt_count,
                updated_at=public_datetime(),
                last_error="",
                error_code="",
            )
        else:
            terminal = result.status == "dead_lettered" or attempt_count >= int(max_attempts or 1)
            updated = replace(
                running,
                status="dead_lettered" if terminal else "failed",
                attempt_count=attempt_count,
                next_run_at="" if terminal else public_datetime(),
                updated_at=public_datetime(),
                last_error=result.last_error or "background job failed",
                error_code=result.error_code or "background_job_failed",
            )
        return self._queue.update(updated)

    def run_due(self, *, max_attempts: int = 3) -> dict[str, Any]:
        candidates = self._queue.list_due()
        items = [self.dispatch_one(job, max_attempts=max_attempts) for job in candidates]
        return {
            "ok": True,
            "candidate_count": len(candidates),
            "processed_count": len(items),
            "succeeded_count": sum(1 for item in items if item.status == "succeeded"),
            "failed_count": sum(1 for item in items if item.status == "failed"),
            "dead_lettered_count": sum(1 for item in items if item.status == "dead_lettered"),
            "real_external_call_executed": False,
            "items": [item.to_dict() for item in items],
        }


def enqueue_webhook_job(
    queue: BackgroundJobQueue,
    *,
    job_type: str,
    source_route: str,
    payload: dict[str, Any] | None,
    signature_valid: bool,
    payload_schema_version: int = 1,
    external_effect_key: str = "",
    audit_context: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> WebhookIntakeResult:
    if not signature_valid:
        return WebhookIntakeResult(ok=False, error_code="invalid_signature", message="Webhook signature validation failed.")
    if not payload:
        return WebhookIntakeResult(ok=False, error_code="invalid_payload", message="Webhook payload is required.")
    key = make_idempotency_key(source_route=source_route, payload=payload, explicit_key=idempotency_key)
    job = BackgroundJobContract(
        job_type=job_type,
        source_route=source_route,
        idempotency_key=key,
        payload_schema_version=payload_schema_version,
        external_effect_key=external_effect_key,
        audit_context={**dict(audit_context or {}), "real_external_call_executed": False},
        payload=dict(payload),
    )
    stored, created = queue.enqueue(job)
    return WebhookIntakeResult(ok=True, created=created, duplicate=not created, job=stored)


def webhook_route_contracts() -> tuple[WebhookRouteContract, ...]:
    W = WebhookRouteContract
    return (
        W("/admin/webhook-inbox", ("GET",), "api.admin_webhook_inbox_page", "none", "read_model", "admin page renders webhook inbox queue state only"),
        W("/api/admin/webhook-inbox/metrics", ("GET",), "webhook_inbox_metrics", "none", "read_model", "read-only webhook inbox queue metrics"),
        W("/api/admin/webhook-inbox/items", ("GET",), "list_webhook_inbox_items", "none", "read_model", "read-only webhook inbox queue items"),
        W("/api/admin/webhook-inbox/{inbox_id}", ("GET",), "get_webhook_inbox_item", "none", "read_model", "read-only webhook inbox processing chain"),
        W(
            "/api/admin/webhook-inbox/{inbox_id}/retry",
            ("POST",),
            "retry_webhook_inbox_item",
            "none",
            "command",
            "admin command only requeues stored webhook inbox row",
        ),
        W(
            "/api/admin/webhook-inbox/{inbox_id}/skip",
            ("POST",),
            "skip_webhook_inbox_item",
            "none",
            "command",
            "admin command only marks stored webhook inbox row ignored",
        ),
        W(
            "/api/admin/webhook-inbox/{inbox_id}/dispatch",
            ("POST",),
            "dispatch_webhook_inbox_item",
            "staging_disabled",
            "command",
            "admin dispatch previews read-only or accepts one versioned durable queue command; it never invokes a handler or provider",
        ),
        W(
            "/api/admin/webhook-inbox/run-due",
            ("POST",),
            "run_webhook_inbox_due",
            "staging_disabled",
            "command",
            "admin run-due previews read-only or accepts one versioned durable queue command; the listener-owned worker claims later",
        ),
        W(
            "/api/admin/wecom/callback/reconciliation",
            ("GET",),
            "wecom_callback_reconciliation",
            "none",
            "read_model",
            "read-only WeCom callback queue reconciliation",
        ),
        W(
            "/wecom/external-contact/callback",
            ("GET", "HEAD", "OPTIONS", "POST"),
            "external_contact_callback",
            "staging_disabled",
            "external_adapter",
            "WeCom callback remains staging-disabled and queues/records before effects",
        ),
        W(
            "/api/h5/wechat-pay/oauth/callback",
            ("GET",),
            "api.h5_wechat_pay_oauth_callback",
            "staging_disabled",
            "read_model",
            "OAuth callback adapter remains staging-disabled",
        ),
        W(
            "/api/h5/wechat-pay/notify",
            ("POST",),
            "api.h5_wechat_pay_notify",
            "none",
            "command",
            "payment notify records transaction/internal event before outbound effects",
        ),
        W(
            "/api/h5/wechat/oauth/callback",
            ("GET",),
            "wechat_oauth_callback",
            "staging_disabled",
            "read_model",
            "OAuth callback adapter remains staging-disabled",
        ),
        W(
            "/api/h5/wechat/oauth/callback",
            ("OPTIONS",),
            "wechat_oauth_callback_options",
            "staging_disabled",
            "read_model",
            "OAuth preflight mirrors callback boundary",
        ),
        W(
            "/api/h5/radar/oauth/callback",
            ("GET",),
            "radar_oauth_callback",
            "staging_disabled",
            "read_model",
            "Radar OAuth callback adapter remains staging-disabled",
        ),
        W(
            "/auth/wecom/callback",
            ("GET", "OPTIONS"),
            "auth_wecom_callback",
            "staging_disabled",
            "read_model",
            "WeCom auth callback adapter remains staging-disabled",
        ),
        W(
            "/api/sidebar/oauth/callback",
            ("GET",),
            "sidebar_oauth_callback",
            "staging_disabled",
            "read_model",
            "Sidebar OAuth callback resolves viewer identity only and does not enqueue background jobs",
        ),
        W(
            "/api/admin/automation-conversion/group-ops/plans/{plan_id}/webhook",
            ("GET",),
            "get_group_ops_webhook_config",
            "none",
            "read_model",
            "admin/read configuration route only",
        ),
        W(
            "/api/automation/group-ops/webhooks/{webhook_key}",
            ("POST",),
            "receive_group_ops_webhook",
            "none",
            "command",
            "inbound group ops webhook records event/job before effects",
        ),
        W(
            "/api/admin/ai-audience/packages/{package_id}/webhooks",
            ("GET",),
            "api.admin_ai_audience_package_webhooks",
            "none",
            "read_model",
            "admin/read AI audience webhook configuration only",
        ),
        W(
            "/api/admin/ai-audience/packages/{package_id}/webhooks",
            ("PATCH",),
            "api.admin_ai_audience_package_webhooks_update",
            "none",
            "command",
            "admin webhook configuration update records settings only",
        ),
        W(
            "/api/ai/audience/packages/{package_key}/webhook",
            ("POST",),
            "api.ai_audience_inbound_webhook",
            "staging_disabled",
            "command",
            "AI audience Agent callback records inbound event by default; optional actions are planned through external effects only when explicitly enabled",
        ),
        W(
            "/api/ai/audience/test-agent/webhook",
            ("POST",),
            "api.ai_audience_test_agent_webhook",
            "staging_disabled",
            "command",
            "AI audience self-test Agent route is disabled by default and loops signed webhook payloads back into inbound planning only under explicit test gates",
        ),
        W(
            "/api/ai/agents/{agent_code}/audience-webhook",
            ("POST",),
            "api.ai_automation_agent_audience_webhook",
            "none",
            "command",
            "Automation Agent audience webhook records batch/items for queued execution and does not execute external effects directly",
        ),
        W(
            "/api/customers/automation/activation-webhook",
            ("OPTIONS",),
            "api_customer_automation_activation_webhook_options",
            "none",
            "read_model",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/activation-webhook",
            ("POST",),
            "api_customer_automation_activation_webhook",
            "none",
            "command",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/webhook-deliveries/{delivery_id:int}/retry",
            ("OPTIONS",),
            "api_customer_automation_webhook_delivery_retry_options",
            "none",
            "read_model",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/webhook-deliveries/{delivery_id:int}/retry",
            ("POST",),
            "api_plan_customer_automation_webhook_delivery_retry",
            "none",
            "command",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/webhook-deliveries/retry-due",
            ("OPTIONS",),
            "api_customer_automation_webhook_delivery_retry_due_options",
            "none",
            "read_model",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/webhook-deliveries/retry-due",
            ("POST",),
            "api_plan_customer_automation_webhook_delivery_retry_due",
            "none",
            "command",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customer-automation/activation-webhook",
            ("POST",),
            "activation_webhook",
            "none",
            "command",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/customers/automation/webhook-deliveries",
            ("GET",),
            "customer_automation_webhook_deliveries",
            "none",
            "read_model",
            "retired customer automation route returns 410 and creates no jobs",
        ),
        W(
            "/api/h5/wechat-pay/refund/notify",
            ("POST",),
            "wechat_refund_notify",
            "none",
            "command",
            "refund notify records transaction/internal event before outbound effects",
        ),
        W(
            "/api/wechat-pay/notify",
            ("POST",),
            "wechat_notify",
            "none",
            "command",
            "WeChat Pay notify records transaction/internal event before outbound effects",
        ),
        W("/api/alipay/notify", ("POST",), "alipay_notify", "none", "command", "Alipay notify records transaction/internal event before outbound effects"),
        W("/api/wechat-shop/notify", ("GET",), "wechat_shop_notify_verify", "none", "read_model", "verification route only"),
        W(
            "/api/wechat-shop/notify",
            ("POST",),
            "wechat_shop_notify",
            "none",
            "command",
            "shop notify records transaction/internal event before outbound effects",
        ),
        W("/api/wechat-pay/notify", ("OPTIONS",), "wechat_notify_options", "none", "read_model", "preflight route only"),
        W("/api/alipay/notify", ("OPTIONS",), "alipay_notify_options", "none", "read_model", "preflight route only"),
        W("/api/admin/webhooks/events", ("GET",), "list_admin_webhook_events", "none", "read_model", "read-only admin webhook inventory"),
        W(
            "/api/admin/webhooks/replay",
            ("POST",),
            "replay_admin_webhook",
            "none",
            "command",
            "admin replay enqueues/plans work without direct external effects",
        ),
        W("/api/admin/jobs/callbacks", ("GET",), "api_admin_jobs_callbacks", "none", "read_model", "read-only callback inventory"),
        W("/api/admin/jobs/webhook-deliveries", ("GET",), "api_admin_jobs_webhook_deliveries", "none", "read_model", "read-only delivery inventory"),
        W(
            "/api/admin/jobs/webhook-deliveries/run",
            ("POST",),
            "api_admin_jobs_webhook_deliveries_run",
            "none",
            "command",
            "retired customer webhook retry route returns disabled payload and creates no jobs",
        ),
        W(
            "/api/admin/jobs/webhook-deliveries/{delivery_id}/retry",
            ("POST",),
            "api_admin_jobs_webhook_delivery_retry",
            "none",
            "command",
            "retired customer webhook retry route returns disabled payload and creates no jobs",
        ),
    )
