from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .action_dto import (
    OperationCycleActionClaimView,
    OperationCycleActionEventV1,
    OperationCycleActionStartV1,
    OperationCycleActionStartView,
    OperationCycleCurrentActionView,
    OperationCycleSkillActionV1,
    OperationCycleSkillV1,
    OperationRunnerHeartbeatV1,
    OperationRunnerHeartbeatView,
    PostSendReviewActionResultV1,
    PrepareBroadcastActionResultV1,
)
from .action_plan_port import (
    OperationCycleActionPlanEvidencePort,
    UnconfiguredOperationCycleActionPlanEvidencePort,
)
from .action_repository import (
    ACTIVE_STATUSES,
    OperationCycleActionRepository,
    build_operation_cycle_action_repository,
)
from .domain import OperationCycleConflictError
from .strategy_context import get_strategy_context
from .strategy_context_repository import StrategyContextRepository, build_strategy_context_repository


HEARTBEAT_INTERVAL_SECONDS = 15
RUNNER_OFFLINE_AFTER_SECONDS = 45
ACTION_LEASE_SECONDS = 60


class OperationCycleActionError(Exception):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        self.code = str(code or "operation_cycle_action_failed")
        self.status_code = int(status_code)
        super().__init__(self.code)


@dataclass(frozen=True)
class OperationCycleActionDependencies:
    plan_evidence_port: OperationCycleActionPlanEvidencePort


_DEPENDENCIES = OperationCycleActionDependencies(
    plan_evidence_port=UnconfiguredOperationCycleActionPlanEvidencePort()
)


def configure_operation_cycle_action_dependencies(
    dependencies: OperationCycleActionDependencies,
) -> None:
    global _DEPENDENCIES
    _DEPENDENCIES = dependencies


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(_text(name) or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Shanghai")


def _context(
    strategy_key: str,
    *,
    context_repo: StrategyContextRepository,
) -> dict[str, Any]:
    payload = get_strategy_context(
        strategy_key,
        mode="execution",
        limit=1,
        repo=context_repo,
    )
    if not payload:
        raise OperationCycleActionError("operation_cycle_strategy_not_found", status_code=404)
    execution = dict(payload.get("execution") or {})
    if _text(execution.get("governance_status")) not in {"confirmed", "legacy_confirmed"}:
        raise OperationCycleActionError("operation_cycle_strategy_not_confirmed")
    return payload


def _skill(context: dict[str, Any]) -> OperationCycleSkillV1:
    raw = dict((context.get("execution") or {}).get("operation_skill") or {})
    if not raw:
        raise OperationCycleActionError("operation_cycle_skill_not_configured")
    return OperationCycleSkillV1.model_validate(raw)


def _action(skill: OperationCycleSkillV1, action_key: str) -> OperationCycleSkillActionV1:
    action = next((item for item in skill.actions if item.action_key == action_key), None)
    if action is None:
        raise OperationCycleActionError("operation_cycle_action_not_found", status_code=404)
    return action


def _new_run_key(
    strategy_key: str,
    *,
    timezone_name: str,
    now: datetime,
    existing_run_keys: set[str],
) -> str:
    base = f"{strategy_key}_{now.astimezone(_timezone(timezone_name)).strftime('%Y%m%d')}"
    if base not in existing_run_keys:
        return base
    suffix = 2
    while f"{base}_r{suffix}" in existing_run_keys:
        suffix += 1
    return f"{base}_r{suffix}"


def heartbeat_runner(
    payload: OperationRunnerHeartbeatV1 | dict[str, Any],
    *,
    principal_id: str,
    repo: OperationCycleActionRepository | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    heartbeat = payload if isinstance(payload, OperationRunnerHeartbeatV1) else OperationRunnerHeartbeatV1.model_validate(payload)
    accepted_at = now or _utcnow()
    (repo or build_operation_cycle_action_repository()).heartbeat(
        heartbeat,
        principal_id=principal_id,
        now=accepted_at,
    )
    return OperationRunnerHeartbeatView(
        runner_id=heartbeat.runner_id,
        accepted_at=accepted_at,
    ).model_dump(mode="json")


def get_current_action(
    strategy_key: str,
    *,
    repo: OperationCycleActionRepository | None = None,
    context_repo: StrategyContextRepository | None = None,
    plan_evidence_port: OperationCycleActionPlanEvidencePort | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_strategy_key = _text(strategy_key)
    repository = repo or build_operation_cycle_action_repository()
    context_repository = context_repo or build_strategy_context_repository()
    context = _context(normalized_strategy_key, context_repo=context_repository)
    skill = _skill(context)
    requests = repository.list_strategy_requests(normalized_strategy_key, limit=200)
    active = next((item for item in requests if item.status in ACTIVE_STATUSES), None)
    if active:
        return OperationCycleCurrentActionView(
            strategy_key=normalized_strategy_key,
            run_key=active.run_key,
            action_kind="none",
            action_key=active.action_key,
            title="本地 Codex 任务执行中",
            enabled=False,
            disabled_reason="action_request_in_progress",
            active_request_id=active.request_id,
        ).model_dump(mode="json")

    latest_run_key = requests[0].run_key if requests else ""
    latest_run_requests = [item for item in requests if item.run_key == latest_run_key]
    completed_prepare = next(
        (
            item
            for item in latest_run_requests
            if item.status == "completed" and item.action_key == "prepare_broadcast" and item.final_result
        ),
        None,
    )
    completed_review = None
    if completed_prepare:
        completed_review = next(
            (
                item
                for item in latest_run_requests
                if item.status == "completed"
                and item.action_key == "post_send_review"
                and item.run_key == completed_prepare.run_key
            ),
            None,
        )
    plan_port = plan_evidence_port or _DEPENDENCIES.plan_evidence_port
    candidate_action: OperationCycleSkillActionV1
    action_kind = "start"
    run_key = ""
    retry_parent = ""

    if completed_prepare and not completed_review:
        plan_id = _text((completed_prepare.final_result or {}).get("plan_id"))
        plan_state = plan_port.get_plan_state(plan_id)
        if bool(plan_state.get("delivery_terminal")):
            candidate_action = _action(skill, "post_send_review")
            action_kind = "start_review"
            run_key = completed_prepare.run_key
            failed_review = next(
                (
                    item
                    for item in latest_run_requests
                    if item.status == "failed"
                    and item.action_key == "post_send_review"
                    and item.run_key == completed_prepare.run_key
                ),
                None,
            )
            retry_parent = failed_review.request_id if failed_review else ""
        elif (
            _text(plan_state.get("review_status")) == "pending_review"
            and _text(plan_state.get("run_status")) == "draft"
        ):
            return OperationCycleCurrentActionView(
                strategy_key=normalized_strategy_key,
                run_key=completed_prepare.run_key,
                action_kind="ai_assistant",
                title="去 AI 助手确认并发送",
                enabled=True,
                href=_text((completed_prepare.final_result or {}).get("ai_assistant_href"))
                or f"/admin/cloud-orchestrator/plans/{plan_id}",
            ).model_dump(mode="json")
        else:
            return OperationCycleCurrentActionView(
                strategy_key=normalized_strategy_key,
                run_key=completed_prepare.run_key,
                action_kind="none",
                title="等待发送进入终态",
                enabled=False,
                disabled_reason="delivery_not_terminal",
                href=f"/admin/cloud-orchestrator/plans/{plan_id}" if plan_id else "",
            ).model_dump(mode="json")
    else:
        candidate_action = _action(skill, "prepare_broadcast")
        failed = next(
            (
                item
                for item in latest_run_requests
                if item.status == "failed" and item.action_key == candidate_action.action_key
            ),
            None,
        )
        retry_parent = failed.request_id if failed else ""
        existing_run_keys = {item.run_key for item in requests}
        run_key = (
            failed.run_key
            if retry_parent
            else _new_run_key(
                normalized_strategy_key,
                timezone_name=_text((context.get("strategy") or {}).get("timezone")),
                now=now or _utcnow(),
                existing_run_keys=existing_run_keys,
            )
        )
        action_kind = "start_new_cycle" if completed_review else "start"

    runner, reason = repository.select_runner(
        candidate_action.required_local_bindings,
        now=now or _utcnow(),
        offline_after_seconds=RUNNER_OFFLINE_AFTER_SECONDS,
    )
    return OperationCycleCurrentActionView(
        strategy_key=normalized_strategy_key,
        run_key=run_key,
        action_kind=action_kind,
        action_key=candidate_action.action_key,
        title=candidate_action.title if action_kind != "start_new_cycle" else "开始新一轮",
        enabled=runner is not None,
        disabled_reason=reason,
        retry_parent_request_id=retry_parent,
    ).model_dump(mode="json")


def start_action(
    strategy_key: str,
    action_key: str,
    payload: OperationCycleActionStartV1 | dict[str, Any],
    *,
    idempotency_key: str,
    actor_id: str,
    repo: OperationCycleActionRepository | None = None,
    context_repo: StrategyContextRepository | None = None,
    plan_evidence_port: OperationCycleActionPlanEvidencePort | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _text(idempotency_key):
        raise OperationCycleActionError("idempotency_key_required", status_code=400)
    if len(_text(idempotency_key)) > 200:
        raise OperationCycleActionError("idempotency_key_too_long", status_code=400)
    request = payload if isinstance(payload, OperationCycleActionStartV1) else OperationCycleActionStartV1.model_validate(payload)
    repository = repo or build_operation_cycle_action_repository()
    context_repository = context_repo or build_strategy_context_repository()
    existing = repository.get_by_idempotency_key(_text(idempotency_key))
    if existing is not None:
        if existing.strategy_key != _text(strategy_key) or existing.action_key != _text(action_key):
            raise OperationCycleActionError("action_start_idempotency_mismatch", status_code=409)
        if request.run_key and request.run_key != existing.run_key:
            raise OperationCycleActionError("action_start_idempotency_mismatch", status_code=409)
        if request.parent_request_id != existing.parent_request_id:
            raise OperationCycleActionError("action_start_idempotency_mismatch", status_code=409)
        return OperationCycleActionStartView(
            request_id=existing.request_id,
            status=existing.status,
            reused=True,
        ).model_dump(mode="json")
    current = get_current_action(
        strategy_key,
        repo=repository,
        context_repo=context_repository,
        plan_evidence_port=plan_evidence_port,
        now=now,
    )
    active_request_id = _text(current.get("active_request_id"))
    if active_request_id:
        active = repository.get_request(active_request_id)
        if active is not None and active.action_key == _text(action_key):
            return OperationCycleActionStartView(
                request_id=active.request_id,
                status=active.status,
                reused=True,
            ).model_dump(mode="json")
    if _text(current.get("action_key")) != _text(action_key):
        raise OperationCycleActionError("operation_cycle_action_is_not_current")
    if not bool(current.get("enabled")):
        raise OperationCycleActionError(_text(current.get("disabled_reason")) or "operation_cycle_action_disabled")
    if request.run_key and request.run_key != _text(current.get("run_key")):
        raise OperationCycleActionError("operation_cycle_action_run_key_conflict")
    expected_parent = _text(current.get("retry_parent_request_id"))
    if expected_parent and request.parent_request_id != expected_parent:
        raise OperationCycleActionError("explicit_retry_parent_required")
    if not expected_parent and request.parent_request_id:
        raise OperationCycleActionError("unexpected_parent_request_id")

    context = _context(_text(strategy_key), context_repo=context_repository)
    skill = _skill(context)
    action = _action(skill, _text(action_key))
    runner, reason = repository.select_runner(
        action.required_local_bindings,
        now=now or _utcnow(),
        offline_after_seconds=RUNNER_OFFLINE_AFTER_SECONDS,
    )
    if runner is None:
        raise OperationCycleActionError(reason or "runner_unavailable")
    digest = hashlib.sha256(
        f"{_text(strategy_key)}\0{_text(action_key)}\0{_text(idempotency_key)}".encode("utf-8")
    ).hexdigest()
    created_at = now or _utcnow()
    action_request, reused = repository.create_request(
        {
            "request_id": f"ocact_{digest[:28]}",
            "strategy_key": _text(strategy_key),
            "run_key": request.run_key or _text(current.get("run_key")),
            "action_key": action.action_key,
            "action_title": action.title,
            "strategy_version": int(context.get("strategy_version") or 0),
            "context_hash": _text(context.get("context_hash")),
            "skill_key": skill.skill_key,
            "skill_hash": skill.skill_hash,
            "runner_id": _text(runner.get("runner_id")),
            "parent_request_id": request.parent_request_id,
            "created_by": _text(actor_id) or "admin",
            "created_at": created_at,
        },
        idempotency_key=_text(idempotency_key),
    )
    return OperationCycleActionStartView(
        request_id=action_request.request_id,
        status=action_request.status,
        reused=reused,
    ).model_dump(mode="json")


def claim_action(
    runner_id: str,
    *,
    principal_id: str = "",
    repo: OperationCycleActionRepository | None = None,
    context_repo: StrategyContextRepository | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    repository = repo or build_operation_cycle_action_repository()
    context_repository = context_repo or build_strategy_context_repository()
    claimed, lease_token, lease_expires_at = repository.claim(
        _text(runner_id),
        principal_id=_text(principal_id),
        now=now or _utcnow(),
        lease_seconds=ACTION_LEASE_SECONDS,
    )
    if claimed is None:
        return OperationCycleActionClaimView(claimed=False).model_dump(mode="json")
    context = _context(claimed.strategy_key, context_repo=context_repository)
    execution = dict(context.get("execution") or {})
    skill = _skill(context)
    blocked_code = ""
    if int(context.get("strategy_version") or 0) != claimed.strategy_version:
        blocked_code = "strategy_version_drift"
    elif _text(context.get("context_hash")) != claimed.context_hash:
        blocked_code = "strategy_context_hash_conflict"
    elif skill.skill_hash != claimed.skill_hash:
        blocked_code = "operation_cycle_skill_hash_conflict"
    action = None if blocked_code else _action(skill, claimed.action_key)
    context_summary = {
        "strategy_key": claimed.strategy_key,
        "run_key": claimed.run_key,
        "strategy_version": claimed.strategy_version,
        "context_hash": claimed.context_hash,
        "objective": _text(execution.get("objective")),
        "document_pack": execution.get("document_pack") or {},
    }
    if blocked_code:
        context_summary = {"blocked_code": blocked_code}
    return OperationCycleActionClaimView(
        claimed=True,
        request=claimed,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        action=action,
        local_binding_keys=action.required_local_bindings if action else [],
        context_summary=context_summary,
    ).model_dump(mode="json")


def record_action_event(
    request_id: str,
    payload: OperationCycleActionEventV1 | dict[str, Any],
    *,
    event_id: str,
    repo: OperationCycleActionRepository | None = None,
    context_repo: StrategyContextRepository | None = None,
    plan_evidence_port: OperationCycleActionPlanEvidencePort | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _text(event_id):
        raise OperationCycleActionError("idempotency_key_required", status_code=400)
    event = payload if isinstance(payload, OperationCycleActionEventV1) else OperationCycleActionEventV1.model_validate(payload)
    repository = repo or build_operation_cycle_action_repository()
    request = repository.get_request(_text(request_id))
    if request is None:
        raise OperationCycleActionError("operation_cycle_action_request_not_found", status_code=404)
    event_payload = event.model_dump(mode="json", exclude_none=True, exclude={"lease_token"})
    if repository.event_is_replay(
        _text(request_id),
        event_id=_text(event_id),
        event_payload=event_payload,
    ):
        updated, reused = repository.apply_event(
            _text(request_id),
            event_id=_text(event_id),
            lease_token=event.lease_token,
            event_type=event.event_type,
            event_payload=event_payload,
            now=now or _utcnow(),
        )
        return {
            "ok": True,
            "request_id": updated.request_id,
            "status": updated.status,
            "reused": reused,
            "real_external_call_executed": False,
        }
    if event.event_type == "completed":
        result = event.result
        if result is None or result.action_key != request.action_key:
            raise OperationCycleActionError("action_result_action_key_mismatch")
        plan_port = plan_evidence_port or _DEPENDENCIES.plan_evidence_port
        if isinstance(result, PrepareBroadcastActionResultV1):
            plan_port.verify_prepare_result(
                strategy_key=request.strategy_key,
                run_key=request.run_key,
                strategy_version=request.strategy_version,
                context_hash=request.context_hash,
                result=result.model_dump(mode="json"),
            )
        elif isinstance(result, PostSendReviewActionResultV1):
            prepare = next(
                (
                    item
                    for item in repository.list_strategy_requests(request.strategy_key, limit=200)
                    if item.run_key == request.run_key
                    and item.action_key == "prepare_broadcast"
                    and item.status == "completed"
                    and item.final_result
                ),
                None,
            )
            if prepare is None:
                raise OperationCycleActionError("prepare_action_result_not_found")
            plan_state = plan_port.get_plan_state(_text((prepare.final_result or {}).get("plan_id")))
            if not bool(plan_state.get("delivery_terminal")):
                raise OperationCycleActionError("delivery_not_terminal")
            if int(plan_state.get("sent_count") or 0) != result.sent_count:
                raise OperationCycleActionError("post_review_sent_count_mismatch")
            if int(plan_state.get("failed_count") or 0) != result.failed_count:
                raise OperationCycleActionError("post_review_failed_count_mismatch")
            if result.proposal_id:
                proposals = (context_repo or build_strategy_context_repository()).list_proposals(
                    request.strategy_key,
                    statuses=(),
                    limit=200,
                    offset=0,
                )
                proposal = next(
                    (item for item in proposals if item.proposal_id == result.proposal_id),
                    None,
                )
                if proposal is None:
                    raise OperationCycleActionError("post_review_proposal_not_found")
                proposed_skill = proposal.proposal.target_version.operation_skill
                if result.proposed_skill_hash and (
                    proposed_skill is None
                    or proposed_skill.skill_hash != result.proposed_skill_hash.lower()
                ):
                    raise OperationCycleActionError("post_review_skill_hash_mismatch")
    try:
        updated, reused = repository.apply_event(
            _text(request_id),
            event_id=_text(event_id),
            lease_token=event.lease_token,
            event_type=event.event_type,
            event_payload=event_payload,
            now=now or _utcnow(),
        )
    except OperationCycleConflictError:
        raise
    return {
        "ok": True,
        "request_id": updated.request_id,
        "status": updated.status,
        "reused": reused,
        "real_external_call_executed": False,
    }


def get_action_result(
    request_id: str,
    *,
    repo: OperationCycleActionRepository | None = None,
) -> dict[str, Any] | None:
    request = (repo or build_operation_cycle_action_repository()).get_request(_text(request_id))
    if request is None:
        return None
    return {
        "ok": True,
        "request_id": request.request_id,
        "strategy_key": request.strategy_key,
        "run_key": request.run_key,
        "action_key": request.action_key,
        "status": request.status,
        "final_result": request.final_result if request.status == "completed" else None,
        "failure_code": request.failure_code if request.status == "failed" else "",
        "completed_at": request.completed_at,
    }


def get_strategy_action_results(
    strategy_key: str,
    *,
    repo: OperationCycleActionRepository | None = None,
) -> list[dict[str, Any]]:
    requests = (repo or build_operation_cycle_action_repository()).list_strategy_requests(
        _text(strategy_key),
        limit=200,
    )
    latest_by_action: dict[str, dict[str, Any]] = {}
    for request in requests:
        if request.status != "completed" or not request.final_result:
            continue
        if request.action_key in latest_by_action:
            continue
        latest_by_action[request.action_key] = {
            "request_id": request.request_id,
            "run_key": request.run_key,
            "action_key": request.action_key,
            "action_title": request.action_title,
            "completed_at": request.completed_at,
            "result": request.final_result,
        }
    return list(latest_by_action.values())


__all__ = [
    "ACTION_LEASE_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "OperationCycleActionDependencies",
    "OperationCycleActionError",
    "RUNNER_OFFLINE_AFTER_SECONDS",
    "claim_action",
    "configure_operation_cycle_action_dependencies",
    "get_action_result",
    "get_current_action",
    "get_strategy_action_results",
    "heartbeat_runner",
    "record_action_event",
    "start_action",
]
