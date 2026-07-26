from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

from aicrm_next.capability_registry import CAPABILITY_SPECS, capability_for_job_type
from aicrm_next.deployment_profile import DeploymentProfile, RUNTIME_ROLES, deployment_profile_from_environment


JobLifecycle = Literal["active", "candidate", "legacy_successor"]
JobKind = Literal["queue", "scheduled_command"]
SchedulerExecution = Literal["not_applicable", "observe_only", "safe_command"]


@dataclass(frozen=True)
class JobSpec:
    job_type: str
    capability_id: str
    runtime_role: str
    kind: JobKind
    lifecycle: JobLifecycle
    handler_ref: str
    schedule: str = ""
    max_concurrency: int = 1
    idempotency_scope: str = "job_type+business_key"
    legacy_units: tuple[str, ...] = ()
    health_check: str = ""
    scheduler_execution: SchedulerExecution = "not_applicable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


JOB_SPECS: tuple[JobSpec, ...] = (
    JobSpec(
        "webhook_inbox.dispatch",
        "core.platform",
        "internal_worker",
        "queue",
        "legacy_successor",
        "execution_runtime:webhook_inbox",
        max_concurrency=4,
        legacy_units=("openclaw-wecom-callback-inbox-worker.service",),
        health_check="queue_worker_heartbeat:aicrm-webhook_inbox-runtime",
    ),
    JobSpec(
        "internal_event.dispatch",
        "core.platform",
        "internal_worker",
        "queue",
        "active",
        "execution_runtime:internal_event+internal_outbox",
        max_concurrency=8,
        legacy_units=("openclaw-internal-event-worker.timer",),
        health_check="queue_worker_heartbeat:aicrm-internal_event-runtime",
    ),
    JobSpec(
        "external_effect.dispatch",
        "core.platform",
        "external_worker",
        "queue",
        "active",
        "execution_runtime:external_effect",
        max_concurrency=4,
        legacy_units=("openclaw-external-effect-worker.timer",),
        health_check="queue_worker_heartbeat:aicrm-external_effect-runtime",
    ),
    JobSpec(
        "external_effect.reconcile",
        "core.platform",
        "scheduler",
        "scheduled_command",
        "candidate",
        "external_effects.jobs:complete_record_only_jobs",
        schedule="*/5 * * * *",
        legacy_units=("openclaw-external-effect-worker.timer",),
        health_check="external_effect_reconciliation",
        scheduler_execution="safe_command",
    ),
    JobSpec(
        "campaign.plan",
        "core.automation",
        "scheduler",
        "scheduled_command",
        "candidate",
        "background_jobs.broadcast_queue_worker:delegate_external_effects",
        schedule="* * * * *",
        legacy_units=("aicrm-next-broadcast-delegation.timer",),
        health_check="automation_plan_backlog",
        scheduler_execution="safe_command",
    ),
    JobSpec(
        "campaign.dispatch",
        "core.automation",
        "internal_worker",
        "queue",
        "candidate",
        "automation.campaign:delegate_external_effect",
        max_concurrency=4,
        legacy_units=("openclaw-broadcast-queue-worker.timer",),
        health_check="broadcast_delegation_backlog",
    ),
    JobSpec(
        "group_ops.plan",
        "core.automation",
        "scheduler",
        "scheduled_command",
        "candidate",
        "automation.group_ops:run_planning",
        schedule="* * * * *",
        legacy_units=("aicrm-next-group-ops-planning.timer",),
        health_check="group_ops_plan_backlog",
        scheduler_execution="safe_command",
    ),
    JobSpec(
        "ai_audience.refresh",
        "extension.ai",
        "scheduler",
        "scheduled_command",
        "candidate",
        "ai_audience:refresh_intent_clock",
        schedule="*/3 * * * *",
        legacy_units=("aicrm-ai-audience-daily-intent.timer", "openclaw-ai-audience-scheduler.timer"),
        health_check="ai_audience_refresh_intent",
        scheduler_execution="safe_command",
    ),
    JobSpec(
        "ai_agent.plan",
        "extension.ai",
        "internal_worker",
        "queue",
        "candidate",
        "automation_agents:plan",
        max_concurrency=2,
        health_check="automation_agent_backlog",
    ),
    JobSpec(
        "payment.reconcile",
        "extension.commerce",
        "scheduler",
        "scheduled_command",
        "candidate",
        "commerce:payment_reconcile",
        schedule="*/5 * * * *",
        legacy_units=("openclaw-wechat-pay-order-reconciliation-worker.timer",),
        health_check="payment_reconciliation",
        scheduler_execution="observe_only",
    ),
    JobSpec(
        "service_period.project",
        "extension.commerce",
        "internal_worker",
        "queue",
        "candidate",
        "service_period:project_entitlement",
        max_concurrency=2,
        health_check="service_period_projection",
    ),
)


class JobCatalog:
    def __init__(
        self,
        specs: tuple[JobSpec, ...] = JOB_SPECS,
        *,
        profile: DeploymentProfile | None = None,
    ) -> None:
        self._specs = specs
        self._profile = profile or deployment_profile_from_environment()
        errors = validate_job_catalog(specs)
        if errors:
            raise ValueError("invalid job catalog: " + "; ".join(errors))
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def active_specs(self, *, runtime_role: str = "") -> tuple[JobSpec, ...]:
        role = str(runtime_role or "").strip()
        return tuple(
            spec
            for spec in self._specs
            if self._profile.allows_runtime(spec.capability_id)
            and (not role or spec.runtime_role == role)
        )

    def register_handler(
        self,
        job_type: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        if job_type not in {spec.job_type for spec in self._specs}:
            raise KeyError(f"unknown job type: {job_type}")
        self._handlers[job_type] = handler

    def dispatch(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        runtime_role: str,
    ) -> dict[str, Any]:
        spec = next((item for item in self.active_specs(runtime_role=runtime_role) if item.job_type == job_type), None)
        if spec is None:
            raise PermissionError(f"job is not active for runtime role {runtime_role}: {job_type}")
        handler = self._handlers.get(job_type)
        if handler is None:
            raise RuntimeError(f"job handler is not registered: {job_type}")
        result = dict(handler(dict(payload or {})) or {})
        result.setdefault("job_type", job_type)
        result.setdefault("runtime_role", runtime_role)
        result.setdefault("real_external_call_executed", False)
        return result

    def summary(self) -> dict[str, Any]:
        active = self.active_specs()
        return {
            "schema_version": 1,
            "profile_id": self._profile.profile_id,
            "activation_mode": self._profile.activation_mode,
            "job_count": len(self._specs),
            "active_job_count": len(active),
            "jobs": [spec.to_dict() for spec in active],
        }


def validate_job_catalog(specs: tuple[JobSpec, ...] = JOB_SPECS) -> list[str]:
    errors: list[str] = []
    job_types = [spec.job_type for spec in specs]
    if len(job_types) != len(set(job_types)):
        errors.append("job types must be unique")
    declared = {job_type for capability in CAPABILITY_SPECS for job_type in capability.job_types}
    missing = sorted(declared - set(job_types))
    extra = sorted(set(job_types) - declared)
    if missing:
        errors.append("declared capability jobs missing from catalog: " + ", ".join(missing))
    if extra:
        errors.append("catalog jobs missing capability ownership: " + ", ".join(extra))
    for spec in specs:
        capability = capability_for_job_type(spec.job_type)
        if capability is None or capability.capability_id != spec.capability_id:
            errors.append(f"{spec.job_type}: capability owner mismatch")
        if spec.runtime_role not in RUNTIME_ROLES:
            errors.append(f"{spec.job_type}: unknown runtime role {spec.runtime_role}")
        if spec.runtime_role == "external_worker" and spec.job_type != "external_effect.dispatch":
            errors.append(f"{spec.job_type}: only external_effect.dispatch may use external_worker")
        if spec.kind == "scheduled_command" and not spec.schedule:
            errors.append(f"{spec.job_type}: scheduled command requires schedule")
        if spec.kind == "scheduled_command" and spec.scheduler_execution == "not_applicable":
            errors.append(f"{spec.job_type}: scheduled command requires an execution policy")
        if spec.kind != "scheduled_command" and spec.scheduler_execution != "not_applicable":
            errors.append(f"{spec.job_type}: queue job cannot declare scheduler execution")
        if spec.scheduler_execution == "safe_command" and spec.runtime_role != "scheduler":
            errors.append(f"{spec.job_type}: safe scheduler command must use scheduler role")
        if int(spec.max_concurrency or 0) < 1:
            errors.append(f"{spec.job_type}: max_concurrency must be positive")
    return errors


__all__ = [
    "JOB_SPECS",
    "JobCatalog",
    "JobSpec",
    "SchedulerExecution",
    "validate_job_catalog",
]
