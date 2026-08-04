from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aicrm_next.extensions.hxc.operation_cycles.action_dto import (
    OperationCycleActionEventV1,
    OperationCycleSkillV1,
    OperationRunnerHeartbeatV1,
    PrepareBroadcastActionResultV1,
)
from aicrm_next.extensions.hxc.operation_cycles.action_repository import (
    InMemoryOperationCycleActionRepository,
)
from aicrm_next.extensions.hxc.operation_cycles.action_service import (
    OperationCycleActionError,
    claim_action,
    get_action_result,
    get_current_action,
    heartbeat_runner,
    record_action_event,
    start_action,
)
from aicrm_next.extensions.hxc.operation_cycles.domain import (
    OperationCycleConflictError,
)
from aicrm_next.extensions.hxc.operation_cycles.dto import StrategySummary
from aicrm_next.extensions.hxc.operation_cycles.strategy_context_dto import (
    StrategyVersionContextView,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)


def _skill() -> OperationCycleSkillV1:
    return OperationCycleSkillV1.model_validate(
        {
            "schema_version": "operation_cycle_skill.v1",
            "skill_key": "hxc_monday_broadcast.v1",
            "actions": [
                {
                    "action_key": "prepare_broadcast",
                    "title": "启动周一群发准备",
                    "objective": "生成经人工确认的群发明细",
                    "codex_prompt": "重新查询真实母集，人工确认前不得提交计划。",
                    "required_local_bindings": [
                        "excel_workspace",
                        "huangyoucan_data",
                        "hxc_knowledge_vault",
                    ],
                    "completion_type": "campaign_preparation_commit",
                    "prerequisites": [],
                    "result_schema": {},
                },
                {
                    "action_key": "post_send_review",
                    "title": "启动发送后复盘",
                    "objective": "完成聚合复盘",
                    "codex_prompt": "只读取聚合发送事实。",
                    "required_local_bindings": ["hxc_knowledge_vault"],
                    "completion_type": "operation_cycle_review",
                    "prerequisites": ["prepare_broadcast"],
                    "result_schema": {},
                },
            ],
        }
    )


class _ContextRepo:
    def __init__(self) -> None:
        self.version = 2
        self.skill = _skill()

    def get_strategy_summary(self, strategy_key: str):
        return StrategySummary(
            strategy_key=strategy_key,
            title="周一群发",
            cadence="每周一",
            timezone="Asia/Shanghai",
            current_version=self.version,
        )

    def get_execution_version(self, strategy_key: str):
        return StrategyVersionContextView(
            strategy_key=strategy_key,
            version=self.version,
            version_label=f"v{self.version}",
            objective="周一激活",
            governance_status="confirmed",
            operation_skill=self.skill,
        )

    def list_proposals(self, strategy_key: str, *, statuses=(), limit=200, offset=0):
        return [
            SimpleNamespace(
                proposal_id="ocprop_safe",
                proposal=SimpleNamespace(
                    target_version=SimpleNamespace(operation_skill=self.skill),
                ),
            )
        ]


class _PlanEvidence:
    def __init__(self) -> None:
        self.terminal = False
        self.verify_calls: list[dict] = []

    def verify_prepare_result(self, **kwargs):
        if self.terminal:
            raise AssertionError("terminal plan must not be re-verified as pending_review")
        self.verify_calls.append(kwargs)
        return {"ok": True}

    def get_plan_state(self, plan_id: str):
        return {
            "plan_id": plan_id,
            "review_status": "approved" if self.terminal else "pending_review",
            "run_status": "completed" if self.terminal else "draft",
            "delivery_terminal": self.terminal,
            "sent_count": 8,
            "failed_count": 2,
        }


def _heartbeat(repo: InMemoryOperationCycleActionRepository, *, now=NOW) -> None:
    heartbeat_runner(
        OperationRunnerHeartbeatV1(
            runner_id="mac-studio-1",
            connector_version="connector/1",
            codex_version="codex-cli 1.2.3",
            compatibility_status="ready",
            binding_keys=["excel_workspace", "huangyoucan_data", "hxc_knowledge_vault"],
        ),
        principal_id="api_client:operation_runner",
        repo=repo,
        now=now,
    )


def _start(repo, context_repo, plan, *, key="start-prepare-1"):
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    return start_action(
        "hxc_monday_full_activation",
        current["action_key"],
        {
            "schema_version": "operation_cycle_action_start.v1",
            "run_key": current["run_key"],
            "parent_request_id": current.get("retry_parent_request_id") or "",
        },
        idempotency_key=key,
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )


def _bind_and_start_turn(repo, context_repo, request_id: str, *, now=NOW):
    claim = claim_action("mac-studio-1", repo=repo, context_repo=context_repo, now=now)
    assert claim["request"]["request_id"] == request_id
    lease = claim["lease_token"]
    record_action_event(
        request_id,
        OperationCycleActionEventV1(
            event_type="thread_bound",
            lease_token=lease,
            thread_id="thread-local-1",
        ),
        event_id=f"{request_id}:thread",
        repo=repo,
        now=now,
    )
    record_action_event(
        request_id,
        OperationCycleActionEventV1(
            event_type="turn_started",
            lease_token=lease,
            thread_id="thread-local-1",
            turn_id="turn-local-1",
        ),
        event_id=f"{request_id}:turn",
        repo=repo,
        now=now,
    )
    return lease


def test_skill_hash_is_deterministic_and_rejects_local_paths() -> None:
    first = _skill()
    second = _skill()
    assert first.skill_hash == second.skill_hash
    assert len(first.skill_hash) == 64
    payload = first.model_dump(mode="json")
    payload["skill_hash"] = "0" * 64
    with pytest.raises(ValueError, match="skill_hash"):
        OperationCycleSkillV1.model_validate(payload)
    payload = first.model_dump(mode="json")
    payload["skill_hash"] = ""
    payload["actions"][0]["codex_prompt"] = "读取 /Users/example/private.xlsx"
    with pytest.raises(ValueError, match="local artifact path"):
        OperationCycleSkillV1.model_validate(payload)
    payload = first.model_dump(mode="json")
    payload["skill_hash"] = ""
    payload["actions"][0]["prerequisites"] = ["post_send_review"]
    with pytest.raises(ValueError, match="acyclic"):
        OperationCycleSkillV1.model_validate(payload)


def test_runner_offline_incompatible_and_missing_bindings_block_start() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert current["enabled"] is False
    assert current["disabled_reason"] == "runner_offline"

    heartbeat_runner(
        {
            "runner_id": "mac-studio-1",
            "connector_version": "connector/1",
            "codex_version": "wrong",
            "compatibility_status": "incompatible",
            "binding_keys": [],
        },
        principal_id="api_client:operation_runner",
        repo=repo,
        now=NOW,
    )
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert current["disabled_reason"] == "runner_incompatible"


def test_prepare_review_cycle_is_review_gated_idempotent_and_aggregate_only() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)

    started = _start(repo, context_repo, plan)
    duplicated = _start(repo, context_repo, plan)
    assert duplicated["request_id"] == started["request_id"]
    assert duplicated["reused"] is True
    assert get_action_result(started["request_id"], repo=repo)["final_result"] is None

    lease = _bind_and_start_turn(repo, context_repo, started["request_id"])
    result = PrepareBroadcastActionResultV1(
        conclusion="已重查实时母集并由人工确认最终 Excel。",
        total_count=10,
        segment_counts={"A": 3, "B": 3, "C": 2, "D": 2},
        excel_sha256="a" * 64,
        preparation_id="ecprep_safe",
        plan_id="plan_campaign_safe",
        ai_assistant_href="/admin/cloud-orchestrator/plans/plan_campaign_safe",
    )
    record_action_event(
        started["request_id"],
        OperationCycleActionEventV1(
            event_type="completed",
            lease_token=lease,
            result=result,
        ),
        event_id="prepare:completed",
        repo=repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert len(plan.verify_calls) == 1
    # A network retry may arrive after the action has reached a terminal state
    # and after the current-action pointer has moved to the AI assistant. The
    # same idempotency key must still resolve to the original request.
    original_request = repo.get_request(started["request_id"])
    assert original_request is not None
    terminal_retry = start_action(
        "hxc_monday_full_activation",
        "prepare_broadcast",
        {"run_key": original_request.run_key},
        idempotency_key="start-prepare-1",
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW + timedelta(seconds=30),
    )
    assert terminal_retry["request_id"] == started["request_id"]
    assert terminal_retry["status"] == "completed"
    assert terminal_retry["reused"] is True
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert current["action_kind"] == "ai_assistant"
    assert current["href"].endswith("plan_campaign_safe")

    plan.terminal = True
    replayed = record_action_event(
        started["request_id"],
        OperationCycleActionEventV1(
            event_type="completed",
            lease_token=lease,
            result=result,
        ),
        event_id="prepare:completed",
        repo=repo,
        plan_evidence_port=plan,
        now=NOW + timedelta(seconds=10),
    )
    assert replayed["reused"] is True
    assert len(plan.verify_calls) == 1
    review_current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert review_current["action_key"] == "post_send_review"
    review_started = start_action(
        "hxc_monday_full_activation",
        "post_send_review",
        {"run_key": review_current["run_key"]},
        idempotency_key="start-review-1",
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    review_lease = _bind_and_start_turn(repo, context_repo, review_started["request_id"])
    record_action_event(
        review_started["request_id"],
        {
            "event_type": "completed",
            "lease_token": review_lease,
            "result": {
                "schema_version": "operation_cycle_action_result.v1",
                "action_key": "post_send_review",
                "conclusion": "已完成聚合复盘，下一版 Skill 等待人工确认。",
                "sent_count": 8,
                "failed_count": 2,
                "proposal_id": "ocprop_safe",
                "proposed_skill_hash": context_repo.skill.skill_hash,
            },
        },
        event_id="review:completed",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    next_cycle = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert next_cycle["action_kind"] == "start_new_cycle"


def test_strategy_drift_is_returned_to_connector_as_hard_block() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)
    _start(repo, context_repo, plan)
    context_repo.version = 3
    claim = claim_action("mac-studio-1", repo=repo, context_repo=context_repo, now=NOW)
    assert claim["claimed"] is True
    assert claim["action"] is None
    assert claim["context_summary"] == {"blocked_code": "strategy_version_drift"}


def test_expired_lease_and_private_final_result_are_rejected() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)
    started = _start(repo, context_repo, plan)
    claim = claim_action("mac-studio-1", repo=repo, context_repo=context_repo, now=NOW)
    with pytest.raises(OperationCycleConflictError, match="lease"):
        record_action_event(
            started["request_id"],
            {
                "event_type": "thread_bound",
                "lease_token": claim["lease_token"],
                "thread_id": "thread-local-1",
            },
            event_id="late-thread",
            repo=repo,
            now=NOW + timedelta(seconds=61),
        )
    with pytest.raises(ValueError, match="phone number"):
        PrepareBroadcastActionResultV1(
            conclusion="请联系 13800138000",
            total_count=1,
            segment_counts={"A": 1, "B": 0, "C": 0, "D": 0},
            excel_sha256="a" * 64,
            preparation_id="ecprep_safe",
            plan_id="plan_safe",
            ai_assistant_href="/admin/cloud-orchestrator/plans/plan_safe",
        )
    with pytest.raises(ValueError, match="exactly A"):
        PrepareBroadcastActionResultV1(
            conclusion="聚合结论",
            total_count=1,
            segment_counts={"A": 1},
            excel_sha256="a" * 64,
            preparation_id="ecprep_safe",
            plan_id="plan_safe",
        )
    with pytest.raises(ValueError, match="does not match"):
        PrepareBroadcastActionResultV1(
            conclusion="聚合结论",
            total_count=1,
            segment_counts={"A": 1, "B": 0, "C": 0, "D": 0},
            excel_sha256="a" * 64,
            preparation_id="ecprep_safe",
            plan_id="plan_safe",
            ai_assistant_href="javascript:alert(1)",
        )


def test_claim_is_bound_to_heartbeat_oauth_principal() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)
    with pytest.raises(OperationCycleConflictError, match="runner_principal_mismatch"):
        heartbeat_runner(
            OperationRunnerHeartbeatV1(
                runner_id="mac-studio-1",
                connector_version="connector/1",
                codex_version="codex-cli 1.2.3",
                compatibility_status="ready",
                binding_keys=["excel_workspace"],
            ),
            principal_id="api_client:different-operation-runner",
            repo=repo,
            now=NOW,
        )
    _start(repo, context_repo, plan)
    with pytest.raises(OperationCycleConflictError, match="runner_principal_mismatch"):
        claim_action(
            "mac-studio-1",
            principal_id="api_client:different-operation-runner",
            repo=repo,
            context_repo=context_repo,
            now=NOW,
        )


def test_failed_action_requires_explicit_parent_retry() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)
    started = _start(repo, context_repo, plan)
    claim = claim_action("mac-studio-1", repo=repo, context_repo=context_repo, now=NOW)
    record_action_event(
        started["request_id"],
        {
            "event_type": "failed",
            "lease_token": claim["lease_token"],
            "failure_code": "codex_app_server_unavailable",
        },
        event_id="failed-1",
        repo=repo,
        now=NOW,
    )
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert current["retry_parent_request_id"] == started["request_id"]
    with pytest.raises(OperationCycleActionError, match="explicit_retry_parent_required"):
        start_action(
            "hxc_monday_full_activation",
            "prepare_broadcast",
            {"run_key": current["run_key"]},
            idempotency_key="retry-without-parent",
            actor_id="human:admin",
            repo=repo,
            context_repo=context_repo,
            plan_evidence_port=plan,
            now=NOW,
        )


def test_request_hash_ignores_server_creation_time_only() -> None:
    repo = InMemoryOperationCycleActionRepository()
    values = {
        "request_id": "ocact_hash_safe",
        "strategy_key": "hxc_monday_full_activation",
        "run_key": "hxc_monday_20260803",
        "action_key": "prepare_broadcast",
        "action_title": "启动周一群发准备",
        "strategy_version": 2,
        "context_hash": "a" * 64,
        "skill_key": "hxc_monday_broadcast.v1",
        "skill_hash": "b" * 64,
        "runner_id": "mac-studio-1",
        "parent_request_id": "",
        "created_by": "human:admin",
        "created_at": NOW,
    }
    first, reused = repo.create_request(values, idempotency_key="same-intent")
    assert reused is False
    retry_values = {**values, "created_at": NOW + timedelta(seconds=30)}
    second, reused = repo.create_request(retry_values, idempotency_key="same-intent")
    assert reused is True
    assert second.request_id == first.request_id


def test_failed_post_send_review_requires_explicit_parent_retry() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)
    prepared = _start(repo, context_repo, plan, key="prepare-before-review-failure")
    prepare_lease = _bind_and_start_turn(repo, context_repo, prepared["request_id"])
    record_action_event(
        prepared["request_id"],
        OperationCycleActionEventV1(
            event_type="completed",
            lease_token=prepare_lease,
            result=PrepareBroadcastActionResultV1(
                conclusion="已确认最终 Excel。",
                total_count=4,
                segment_counts={"A": 1, "B": 1, "C": 1, "D": 1},
                excel_sha256="c" * 64,
                preparation_id="ecprep_review_retry",
                plan_id="plan_review_retry",
            ),
        ),
        event_id="prepare-review-retry:completed",
        repo=repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    plan.terminal = True
    current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    review = start_action(
        "hxc_monday_full_activation",
        "post_send_review",
        {"run_key": current["run_key"]},
        idempotency_key="review-that-fails",
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    review_lease = _bind_and_start_turn(repo, context_repo, review["request_id"])
    record_action_event(
        review["request_id"],
        {
            "event_type": "failed",
            "lease_token": review_lease,
            "failure_code": "review_context_unavailable",
        },
        event_id="review:failed",
        repo=repo,
        now=NOW,
    )
    retry = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    assert retry["action_key"] == "post_send_review"
    assert retry["retry_parent_request_id"] == review["request_id"]
    with pytest.raises(OperationCycleActionError, match="explicit_retry_parent_required"):
        start_action(
            "hxc_monday_full_activation",
            "post_send_review",
            {"run_key": retry["run_key"]},
            idempotency_key="review-retry-without-parent",
            actor_id="human:admin",
            repo=repo,
            context_repo=context_repo,
            plan_evidence_port=plan,
            now=NOW,
        )


def test_failed_prepare_in_second_cycle_retries_that_run_not_completed_history() -> None:
    repo = InMemoryOperationCycleActionRepository()
    context_repo = _ContextRepo()
    plan = _PlanEvidence()
    _heartbeat(repo)

    prepared = _start(repo, context_repo, plan, key="first-cycle-prepare")
    prepare_lease = _bind_and_start_turn(repo, context_repo, prepared["request_id"])
    record_action_event(
        prepared["request_id"],
        OperationCycleActionEventV1(
            event_type="completed",
            lease_token=prepare_lease,
            result=PrepareBroadcastActionResultV1(
                conclusion="第一轮最终 Excel 已确认。",
                total_count=4,
                segment_counts={"A": 1, "B": 1, "C": 1, "D": 1},
                excel_sha256="d" * 64,
                preparation_id="ecprep_first_cycle",
                plan_id="plan_first_cycle",
            ),
        ),
        event_id="first-cycle:prepare-completed",
        repo=repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    plan.terminal = True
    review_current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    review = start_action(
        "hxc_monday_full_activation",
        "post_send_review",
        {"run_key": review_current["run_key"]},
        idempotency_key="first-cycle-review",
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )
    review_lease = _bind_and_start_turn(repo, context_repo, review["request_id"])
    record_action_event(
        review["request_id"],
        {
            "event_type": "completed",
            "lease_token": review_lease,
            "result": {
                "schema_version": "operation_cycle_action_result.v1",
                "action_key": "post_send_review",
                "conclusion": "第一轮聚合复盘完成。",
                "sent_count": 8,
                "failed_count": 2,
            },
        },
        event_id="first-cycle:review-completed",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW,
    )

    _heartbeat(repo, now=NOW + timedelta(days=7))
    second_current = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW + timedelta(days=7),
    )
    second = start_action(
        "hxc_monday_full_activation",
        "prepare_broadcast",
        {"run_key": second_current["run_key"]},
        idempotency_key="second-cycle-prepare",
        actor_id="human:admin",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW + timedelta(days=7),
    )
    second_claim = claim_action(
        "mac-studio-1",
        repo=repo,
        context_repo=context_repo,
        now=NOW + timedelta(days=7),
    )
    record_action_event(
        second["request_id"],
        {
            "event_type": "failed",
            "lease_token": second_claim["lease_token"],
            "failure_code": "codex_app_server_unavailable",
        },
        event_id="second-cycle:failed",
        repo=repo,
        now=NOW + timedelta(days=7),
    )

    retry = get_current_action(
        "hxc_monday_full_activation",
        repo=repo,
        context_repo=context_repo,
        plan_evidence_port=plan,
        now=NOW + timedelta(days=7),
    )
    assert retry["action_key"] == "prepare_broadcast"
    assert retry["run_key"] == second_current["run_key"]
    assert retry["retry_parent_request_id"] == second["request_id"]
