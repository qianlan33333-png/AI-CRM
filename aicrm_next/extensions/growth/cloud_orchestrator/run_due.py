from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.external_calls import InMemoryExternalCallAttemptRepository
from aicrm_next.platform_foundation.external_effects import AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, WECOM_MESSAGE_PRIVATE_SEND, ExternalEffectService
from aicrm_next.platform_foundation.external_effects.models import public_datetime, utcnow
from aicrm_next.platform_foundation.external_effects.test_receiver import TEST_RECEIVER_PATH_PREFIX, canonical_payload_hash
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan
from aicrm_next.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting

from .campaigns_read import build_campaign_read_repository

PREVIEW_SOURCE_STATUS = "next_run_due_preview"
PLAN_SOURCE_STATUS = "next_run_due_plan"
ROUTE_OWNER = "ai_crm_next"
ADAPTER_MODE = "real_blocked"
DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 1000
AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE_KEY = "AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE"
AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE_KEY = "AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE",
        "AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE",
    }
)


class CloudCampaignRunDueInputError(ValueError):
    pass


@dataclass(frozen=True)
class CloudCampaignRunDueCommand:
    command_id: str = field(default_factory=lambda: "cmd_cloud_run_due_" + uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = "timer"
    actor_type: str = "timer"
    batch_size: int = DEFAULT_BATCH_SIZE
    dry_run: bool = True
    source_route: str = ""
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    now: str = ""
    test_only: bool = False
    test_receiver_base_url: str = ""
    receiver_response_status: int = 200

    command_name = "cloud_orchestrator.campaign.run_due"

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "batch_size": self.batch_size,
            "dry_run": self.dry_run,
            "source_route": self.source_route,
            "trace_id": self.trace_id,
            "requested_at": self.requested_at,
            "now": self.now,
            "test_only": self.test_only,
            "test_receiver_base_url": self.test_receiver_base_url,
            "receiver_response_status": self.receiver_response_status,
        }


@dataclass(frozen=True)
class PreviewCloudCampaignRunDueCommand(CloudCampaignRunDueCommand):
    command_name = "cloud_orchestrator.campaign.run_due.preview"


@dataclass(frozen=True)
class PlanCloudCampaignRunDueCommand(CloudCampaignRunDueCommand):
    force_plan: bool = True
    command_name = "cloud_orchestrator.campaign.run_due.plan"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["force_plan"] = self.force_plan
        return payload


_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
_external_call_attempts = InMemoryExternalCallAttemptRepository()
_command_bus = CommandBus()


def reset_run_due_fixture_state() -> None:
    global _audit_ledger, _side_effect_plans, _external_call_attempts, _command_bus
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _external_call_attempts = InMemoryExternalCallAttemptRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_handlers()


def get_run_due_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_run_due_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def get_run_due_external_call_attempts() -> list[dict[str, Any]]:
    return [attempt.to_dict() for attempt in _external_call_attempts.list_attempts()]


def execute_cloud_campaign_run_due_command(command: CloudCampaignRunDueCommand) -> dict[str, Any]:
    _validate_command(command)
    platform_command = Command(
        command_name=command.command_name,
        payload=command.to_payload(),
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        context=CommandContext(
            actor_id=command.actor_id,
            actor_type=command.actor_type,
            trace_id=command.trace_id,
            source_route=command.source_route,
            dry_run=False,
        ),
    )
    result = _command_bus.execute(platform_command)
    if result.status == "failed":
        raise CloudCampaignRunDueInputError(result.error or "cloud campaign run-due command failed")
    return _response_from_result(result, dict(result.payload))


def _register_handlers() -> None:
    _command_bus.register(PreviewCloudCampaignRunDueCommand.command_name, _handle_preview)
    _command_bus.register(PlanCloudCampaignRunDueCommand.command_name, _handle_plan)


def _validate_command(command: CloudCampaignRunDueCommand) -> None:
    if not command.command_id.strip():
        raise CloudCampaignRunDueInputError("command_id is required")
    if command.command_name == PlanCloudCampaignRunDueCommand.command_name and not command.idempotency_key.strip():
        raise CloudCampaignRunDueInputError("idempotency_key is required")
    if not command.source_route.strip():
        raise CloudCampaignRunDueInputError("source_route is required")
    if int(command.batch_size) < 1 or int(command.batch_size) > MAX_BATCH_SIZE:
        raise CloudCampaignRunDueInputError("batch_size must be between 1 and 1000")


def normalize_batch_size(value: Any, *, default: int = DEFAULT_BATCH_SIZE) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudCampaignRunDueInputError("batch_size must be an integer") from exc
    if parsed < 1 or parsed > MAX_BATCH_SIZE:
        raise CloudCampaignRunDueInputError("batch_size must be between 1 and 1000")
    return parsed


def _repo() -> Any:
    return build_campaign_read_repository()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _command_now(payload: dict[str, Any]) -> datetime:
    parsed = _parse_datetime(payload.get("now"))
    return parsed or utcnow()


def _member_is_due(member: dict[str, Any], now: datetime) -> bool:
    due_at = _parse_datetime(member.get("next_due_at"))
    return bool(due_at and due_at <= now)


def _member_current_step_index(member: dict[str, Any]) -> int:
    value = member.get("current_step_index")
    try:
        return int(value) if value not in (None, "") else -1
    except (TypeError, ValueError):
        return -1


def _next_step_for_member(steps: list[dict[str, Any]], member: dict[str, Any]) -> dict[str, Any]:
    next_index = _member_current_step_index(member) + 1
    for step in steps:
        try:
            if int(step.get("step_index") or 0) == next_index:
                return step
        except (TypeError, ValueError):
            continue
    return {}


def _due_candidates(batch_size: int, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    due_now = now or utcnow()
    if due_now.tzinfo is None:
        due_now = due_now.replace(tzinfo=timezone.utc)
    try:
        repo = _repo()
        campaigns, _total = repo.list_campaigns(limit=batch_size, offset=0)
    except Exception as exc:
        return [], {"candidate_generation_status": "degraded", "error": str(exc)}

    candidates: list[dict[str, Any]] = []
    for campaign in campaigns:
        campaign_code = str(campaign.get("campaign_code") or "").strip()
        if not campaign_code:
            continue
        try:
            steps_payload = repo.list_steps(campaign_code) or {}
        except Exception as exc:
            candidates.append(
                {
                    "campaign_code": campaign_code,
                    "campaign_id": campaign.get("id"),
                    "status": "degraded",
                    "error": str(exc),
                    "estimated_actions": 0,
                }
            )
            continue
        steps = list(steps_payload.get("steps") or [])
        member_offset = 0
        while len(candidates) < batch_size:
            try:
                members_payload = repo.list_members(campaign_code, status="pending", limit=batch_size, offset=member_offset) or {}
            except Exception as exc:
                candidates.append(
                    {
                        "campaign_code": campaign_code,
                        "campaign_id": campaign.get("id"),
                        "status": "degraded",
                        "error": str(exc),
                        "estimated_actions": 0,
                    }
                )
                break
            members = list(members_payload.get("members") or members_payload.get("rows") or [])
            if not members:
                break
            for member in members:
                if not _member_is_due(member, due_now):
                    continue
                next_step = _next_step_for_member(steps, member)
                if not next_step:
                    continue
                candidates.append(
                    {
                        "campaign_code": campaign_code,
                        "campaign_id": campaign.get("id"),
                        "owner_userid": campaign.get("owner_userid"),
                        "campaign_trace_id": campaign.get("trace_id"),
                        "member_id": member.get("member_id"),
                        "unionid": member.get("unionid"),
                        "external_contact_id": member.get("external_contact_id"),
                        "member_status": member.get("status"),
                        "current_step_index": member.get("current_step_index"),
                        "next_step_index": next_step.get("step_index", 0),
                        "next_due_at": member.get("next_due_at") or "",
                        "next_step": next_step,
                        "estimated_actions": 1,
                    }
                )
                if len(candidates) >= batch_size:
                    return candidates, {"candidate_generation_status": "ready"}
            member_offset += len(members)
            total = int(members_payload.get("total") or 0)
            if total:
                if member_offset >= total:
                    break
                continue
            if len(members) < batch_size:
                break
            break
    return candidates, {"candidate_generation_status": "ready"}


def _enabled(name: str) -> bool:
    return managed_runtime_bool(name, False)


def _is_loopback_test_mode(command: Command) -> bool:
    return bool(command.payload.get("test_only")) or _enabled(AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE_KEY)


def _send_mode(command: Command) -> str:
    if _is_loopback_test_mode(command):
        return "loopback"
    mode = managed_runtime_setting(
        AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE_KEY,
        "",
    ).strip().lower()
    if mode in {"wecom", "wecom_private", "private_message"}:
        return "wecom_private"
    return "shadow"


def _receiver_response_status(command: Command) -> int:
    try:
        status = int(command.payload.get("receiver_response_status") or 200)
    except (TypeError, ValueError):
        status = 200
    return status if status in {200, 400, 500} else 200


def _candidate_target_id(candidate: dict[str, Any]) -> tuple[str, str]:
    unionid = str(candidate.get("unionid") or "").strip()
    if unionid:
        return unionid, "unionid"
    member_id = str(candidate.get("member_id") or "").strip()
    if member_id:
        return member_id, "member_id"
    return str(candidate.get("external_contact_id") or "").strip(), "external_contact_id"


def _candidate_stable_idempotency_key(effect_type: str, candidate: dict[str, Any]) -> str:
    campaign_code = str(candidate.get("campaign_code") or "").strip()
    member_id = str(candidate.get("member_id") or "").strip()
    if not member_id:
        member_id, _kind = _candidate_target_id(candidate)
    step_index = int(candidate.get("next_step_index") or 0)
    return f"cloud_campaign_run_due:{effect_type}:{campaign_code}:{member_id}:{step_index}"


def _loopback_payload_for_candidate(
    *,
    command: Command,
    candidate: dict[str, Any],
    loopback_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign_code = str(candidate.get("campaign_code") or "").strip()
    target_id, target_id_kind = _candidate_target_id(candidate)
    step_index = int(candidate.get("next_step_index") or 0)
    idempotency_key = _candidate_stable_idempotency_key(AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, candidate)
    body = {
        "synthetic": bool(loopback_mode),
        "source": "ai_assist_campaign_run_due",
        "effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
        "campaign_code": campaign_code,
        "campaign_id": candidate.get("campaign_id"),
        "target_type": "campaign_member",
        "target_id": target_id,
        "target_id_kind": target_id_kind,
        "business_type": "ai_assist_campaign",
        "business_id": campaign_code,
        "step_index": step_index,
        "trace_id": command.context.trace_id,
        "idempotency_key": idempotency_key,
        "test_only": bool(loopback_mode),
        "wecom_send_executed": False,
    }
    payload: dict[str, Any] = {"body": body}
    if loopback_mode:
        base_url = str(command.payload.get("test_receiver_base_url") or "").rstrip("/")
        payload_hash = canonical_payload_hash(body)
        payload.update(
            {
                "webhook_url": f"{base_url}{TEST_RECEIVER_PATH_PREFIX}" if base_url else "",
                "receiver_response_status": _receiver_response_status(command),
                "test_receiver_expires_at": public_datetime(utcnow() + timedelta(hours=12)),
                "execution_scope": "test_loopback",
                "is_test": True,
                "expected_payload_hash": payload_hash,
            }
        )
    summary = {
        "campaign_code": campaign_code,
        "campaign_id": candidate.get("campaign_id"),
        "target_type": "campaign_member",
        "target_id": target_id,
        "target_id_kind": target_id_kind,
        "step_index": step_index,
        "execution_scope": str(payload.get("execution_scope") or ""),
        "is_test": bool(payload.get("is_test")),
        "webhook_url_present": bool(payload.get("webhook_url")),
        "expected_payload_hash": str(payload.get("expected_payload_hash") or ""),
    }
    return payload, {"idempotency_key": idempotency_key, "payload_summary": summary}


def _wecom_private_payload_for_candidate(
    *,
    command: Command,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign_code = str(candidate.get("campaign_code") or "").strip()
    unionid = str(candidate.get("unionid") or "").strip()
    external_userid = str(candidate.get("external_contact_id") or "").strip()
    step = candidate.get("next_step") if isinstance(candidate.get("next_step"), dict) else {}
    step_index = int(candidate.get("next_step_index") or step.get("step_index") or 0)
    content_payload = step.get("content_payload_json") if isinstance(step.get("content_payload_json"), dict) else {}
    content_text = str(step.get("content_text") or content_payload.get("content_text") or "").strip()
    idempotency_key = _candidate_stable_idempotency_key(WECOM_MESSAGE_PRIVATE_SEND, candidate)
    attachments = content_payload.get("attachments") if isinstance(content_payload.get("attachments"), list) else []
    payload = {
        "channel": "wecom_private",
        "owner_userid": str(candidate.get("owner_userid") or "").strip(),
        "sender": str(candidate.get("owner_userid") or "").strip(),
        "target_unionid": unionid,
        "unionid": unionid,
        "external_userids": [external_userid],
        "content_text": content_text,
        "attachments": attachments,
        "campaign_code": campaign_code,
        "campaign_id": candidate.get("campaign_id"),
        "member_id": candidate.get("member_id"),
        "step_index": step_index,
        "wecom_send_executed": False,
    }
    payload_summary = {
        "campaign_code": campaign_code,
        "campaign_id": candidate.get("campaign_id"),
        "target_type": "unionid",
        "target_id": unionid,
        "owner_userid": str(candidate.get("owner_userid") or "").strip(),
        "member_id": candidate.get("member_id"),
        "step_index": step_index,
        "content_text_length": len(content_text),
        "attachment_count": len(attachments),
        "wecom_send_executed": False,
    }
    return payload, {"idempotency_key": idempotency_key, "payload_summary": payload_summary}


def _plan_external_effect_jobs(*, command: Command, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not candidates:
        return [], []
    send_mode = _send_mode(command)
    loopback_mode = send_mode == "loopback"
    service = ExternalEffectService()
    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for candidate in candidates:
        campaign_code = str(candidate.get("campaign_code") or "").strip()
        target_id, _target_kind = _candidate_target_id(candidate)
        if not campaign_code or not target_id:
            continue
        if send_mode == "wecom_private":
            external_userid = str(candidate.get("external_contact_id") or "").strip()
            if not external_userid:
                continue
            effect_type = WECOM_MESSAGE_PRIVATE_SEND
            adapter_name = "wecom_private_message"
            target_type = "unionid"
            payload, meta = _wecom_private_payload_for_candidate(command=command, candidate=candidate)
            execution_mode = "execute"
            status = "queued"
            requires_approval = True
        else:
            effect_type = AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK
            adapter_name = "outbound_webhook"
            target_type = "campaign_member"
            payload, meta = _loopback_payload_for_candidate(command=command, candidate=candidate, loopback_mode=loopback_mode)
            execution_mode = "execute" if loopback_mode else "shadow"
            status = "queued" if loopback_mode else "planned"
            requires_approval = False
        try:
            jobs.append(
                service.plan_effect(
                    effect_type=effect_type,
                    adapter_name=adapter_name,
                    operation="post",
                    target_type=target_type,
                    target_id=target_id,
                    business_type="ai_assist_campaign",
                    business_id=campaign_code,
                    payload=payload,
                    payload_summary=dict(meta["payload_summary"]),
                    context=command.context,
                    source_module="cloud_orchestrator.run_due",
                    source_event_id=campaign_code,
                    source_command_id=command.command_id,
                    risk_level="medium",
                    requires_approval=requires_approval,
                    execution_mode=execution_mode,
                    status=status,
                    idempotency_key=str(meta["idempotency_key"]),
                )
            )
        except Exception as exc:
            errors.append({"campaign_code": campaign_code, "target_id": target_id, "error": str(exc)})
    return jobs, errors


def _estimated_actions(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    count = sum(int(item.get("estimated_actions") or 0) for item in candidates)
    return {
        "planned_message_count": count,
        "runtime_execution_count": 0,
        "wecom_send_count": 0,
        "blocked_external_call_count": count,
    }


def _handle_preview(command: Command) -> dict[str, Any]:
    batch_size = normalize_batch_size(command.payload.get("batch_size"))
    candidates, diagnostics = _due_candidates(batch_size, now=_command_now(command.payload))
    return {
        "source_status": PREVIEW_SOURCE_STATUS,
        "run_due_status": "preview_only",
        "candidates": candidates,
        "candidate_count": len(candidates),
        "estimated_actions": _estimated_actions(candidates),
        "dry_run": True,
        "planned_count": 0,
        "external_effect_job_ids": [],
        "external_effect_jobs": [],
        "external_effect_planned_count": 0,
        "processed_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": len(candidates),
        **diagnostics,
    }


def _handle_plan(command: Command) -> dict[str, Any]:
    batch_size = normalize_batch_size(command.payload.get("batch_size"))
    candidates, diagnostics = _due_candidates(batch_size, now=_command_now(command.payload))
    external_effect_jobs, external_effect_errors = _plan_external_effect_jobs(command=command, candidates=candidates)
    plan = _create_run_due_side_effect_plan(command=command, candidates=candidates, diagnostics=diagnostics)
    attempt = _external_call_attempts.record_attempt(
        adapter_name="cloud_orchestrator_runtime",
        adapter_mode=ADAPTER_MODE,
        operation="campaign.run_due",
        request_id=command.command_id,
        trace_id=command.context.trace_id,
        side_effect_plan_id=plan.side_effect_plan_id,
        status="blocked",
        request_summary={
            "batch_size": batch_size,
            "candidate_count": len(candidates),
            "dry_run": command.payload.get("dry_run", True),
        },
        response_summary={
            "blocked": True,
            "real_external_call_executed": False,
            "campaign_runtime_executed": False,
            "automation_runtime_executed": False,
            "wecom_send_executed": False,
        },
        error_code="real_blocked",
        error_message="Cloud campaign run-due is plan-only in Next safe mode.",
    )
    return {
        "source_status": PLAN_SOURCE_STATUS,
        "run_due_status": "planned_blocked",
        "candidates": candidates,
        "candidate_count": len(candidates),
        "estimated_actions": _estimated_actions(candidates),
        "dry_run": bool(command.payload.get("dry_run", True)),
        "force_plan": bool(command.payload.get("force_plan", True)),
        "processed_count": 0,
        "planned_count": len(candidates),
        "external_effect_job_ids": [job.get("id") for job in external_effect_jobs if job.get("id")],
        "external_effect_jobs": external_effect_jobs,
        "external_effect_planned_count": len(external_effect_jobs),
        "external_effect_plan_errors": external_effect_errors,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": len(candidates),
        "side_effect_plan": _plan_response(plan),
        "external_call_attempt": attempt.to_dict(),
        **diagnostics,
    }


def _create_run_due_side_effect_plan(*, command: Command, candidates: list[dict[str, Any]], diagnostics: dict[str, Any]) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type="cloud_orchestrator.campaign.run_due",
        adapter_name="cloud_orchestrator_runtime",
        adapter_mode=ADAPTER_MODE,
        target_type="cloud_campaign_due_candidates",
        target_id="batch",
        payload={
            "payload_summary": {
                "batch_size": command.payload.get("batch_size"),
                "candidate_count": len(candidates),
                "candidate_generation_status": diagnostics.get("candidate_generation_status"),
            },
            "real_external_call_executed": False,
            "campaign_runtime_executed": False,
            "automation_runtime_executed": False,
            "wecom_send_executed": False,
        },
        status="blocked",
        risk_level="high",
        requires_approval=True,
    )


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = False
    payload["campaign_runtime_executed"] = False
    payload["automation_runtime_executed"] = False
    payload["wecom_send_executed"] = False
    return payload


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="cloud_campaign_due_candidates",
        target_id="batch",
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "fallback_used": False,
            "adapter_mode": ADAPTER_MODE,
            "real_external_call_executed": False,
            "campaign_runtime_executed": False,
            "automation_runtime_executed": False,
            "wecom_send_executed": False,
            "candidate_count": (result.payload or {}).get("candidate_count", 0),
        },
    )


def _audit_event_for(command_id: str) -> dict[str, Any]:
    for event in reversed(get_run_due_audit_events()):
        if event.get("command_id") == command_id:
            return event
    return {}


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    source_status = str(payload.pop("source_status", "") or PLAN_SOURCE_STATUS)
    response = {
        "ok": result.status == "completed",
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": source_status,
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "adapter_mode": ADAPTER_MODE,
        "real_external_call_executed": False,
        "campaign_runtime_executed": False,
        "automation_runtime_executed": False,
        "wecom_send_executed": False,
        "audit_recorded": True,
        "audit_event": _audit_event_for(result.command_id),
        "command_result_status": result.status,
        "actor": {"id": result.actor_id, "type": result.actor_type},
        "source_route": result.source_route,
        "trace_id": result.trace_id,
        "dry_run": bool(result.payload.get("dry_run", True)),
    }
    response.update(payload)
    return response


def diagnostics_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": PLAN_SOURCE_STATUS,
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "adapter_mode": ADAPTER_MODE,
        "allowed_methods": ["POST", "OPTIONS"],
        "real_external_call_executed": False,
        "campaign_runtime_executed": False,
        "automation_runtime_executed": False,
        "wecom_send_executed": False,
        "side_effect_plan": {
            "effect_type": "cloud_orchestrator.campaign.run_due",
            "adapter_name": "cloud_orchestrator_runtime",
            "adapter_mode": ADAPTER_MODE,
            "requires_approval": True,
            "real_external_call_executed": False,
            "campaign_runtime_executed": False,
            "automation_runtime_executed": False,
            "wecom_send_executed": False,
            "payload_summary": {},
        },
    }


reset_run_due_fixture_state()
