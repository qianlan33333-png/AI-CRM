from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform.platform_foundation.execution_runtime.cutover import GenerationCASConflict
from aicrm_next.platform.platform_foundation.execution_runtime.lane_rollout import (
    AiAutomationLaneRolloutRepository,
)
from aicrm_next.platform.platform_foundation.execution_runtime.repository import ExecutionRuntimeRepository
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


@pytest.fixture(autouse=True)
def _blocked_ai_lanes() -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 1,
                claim_enabled = TRUE,
                rollout_mode = 'execute',
                policy_version = 'queue-v2-production-all-g1',
                external_claim_scope = 'all'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET rollout_mode = 'blocked',
                max_in_flight = 4,
                enabled = TRUE,
                policy_version = 'queue-v2-production-all-g1',
                blocked_until = NULL
            WHERE lane IN ('ai_generation', 'wecom_ai_assistant_bulk')
            """
        )


def _generation_job() -> dict:
    unique = uuid4().hex
    return ExternalEffectService().plan_effect(
        effect_type="ai.agent.generate",
        adapter_name="ai_agent_generation",
        operation="generate",
        target_type="automation_agent_webhook_item",
        target_id=unique,
        business_type="lane_rollout_test",
        business_id=unique,
        payload={"item_id": 1, "role_prompt": "role", "task_prompt": "task"},
        idempotency_key=f"lane-rollout-{unique}",
        status="queued",
        lane="ai_generation",
        execution_mode="execute",
    )


def test_canary_requires_exact_reviewed_backlog_and_appends_audit() -> None:
    job = _generation_job()
    repository = AiAutomationLaneRolloutRepository(_database_url())
    common = {
        "lane": "ai_generation",
        "expected_generation": 1,
        "expected_policy_version": "queue-v2-production-all-g1",
        "expected_mode": "blocked",
        "target_mode": "canary",
        "expected_capacity": 4,
        "target_capacity": 4,
        "max_open_jobs": 1,
    }

    with pytest.raises(GenerationCASConflict, match="backlog IDs"):
        repository.plan(**common)

    plan = repository.apply(
        **common,
        expected_open_job_ids=(int(job["id"]),),
        actor="pytest",
        reason="one reviewed generation canary",
    )

    assert plan.applied is True
    assert plan.backlog.open_job_ids == (int(job["id"]),)
    with _connect() as connection:
        lane = connection.execute(
            "SELECT rollout_mode, max_in_flight FROM queue_lane_policy WHERE lane = 'ai_generation'"
        ).fetchone()
        audit = connection.execute(
            "SELECT * FROM queue_lane_rollout_audit WHERE transition_id = %s",
            (plan.transition_id,),
        ).fetchone()
    assert lane == {"rollout_mode": "canary", "max_in_flight": 4}
    assert audit["from_mode"] == "blocked"
    assert audit["to_mode"] == "canary"
    assert audit["backlog_snapshot_json"]["open_job_ids"] == [int(job["id"])]


def test_canary_claims_only_the_reviewed_job_version() -> None:
    reviewed = _generation_job()
    rollout = AiAutomationLaneRolloutRepository(_database_url())
    rollout.apply(
        lane="ai_generation",
        expected_generation=1,
        expected_policy_version="queue-v2-production-all-g1",
        expected_mode="blocked",
        target_mode="canary",
        expected_capacity=4,
        target_capacity=4,
        expected_open_job_ids=(int(reviewed["id"]),),
        max_open_jobs=1,
        actor="pytest",
        reason="one reviewed generation canary",
    )
    future = _generation_job()
    runtime = ExecutionRuntimeRepository(_database_url())

    claim = runtime.claim_external_effect_one(
        lane="ai_generation",
        worker_id="reviewed-canary",
        generation=1,
    )

    assert claim is not None
    assert claim.item_id == int(reviewed["id"])
    assert runtime.claim_external_effect_one(
        lane="ai_generation",
        worker_id="future-row-must-wait",
        generation=1,
    ) is None
    with _connect() as connection:
        future_status = connection.execute(
            "SELECT status FROM external_effect_job WHERE id = %s",
            (int(future["id"]),),
        ).fetchone()
    assert future_status == {"status": "queued"}


def test_execute_promotion_requires_empty_or_bounded_backlog() -> None:
    repository = AiAutomationLaneRolloutRepository(_database_url())
    with _connect() as connection:
        connection.execute(
            "UPDATE queue_lane_policy SET rollout_mode = 'canary' WHERE lane = 'ai_generation'"
        )
    job = _generation_job()

    with pytest.raises(GenerationCASConflict, match="reviewed rollout limit"):
        repository.plan(
            lane="ai_generation",
            expected_generation=1,
            expected_policy_version="queue-v2-production-all-g1",
            expected_mode="canary",
            target_mode="execute",
            expected_capacity=4,
            target_capacity=4,
            max_open_jobs=0,
        )

    plan = repository.plan(
        lane="ai_generation",
        expected_generation=1,
        expected_policy_version="queue-v2-production-all-g1",
        expected_mode="canary",
        target_mode="execute",
        expected_capacity=4,
        target_capacity=4,
        expected_open_job_ids=(int(job["id"]),),
        max_open_jobs=1,
    )
    assert plan.backlog.open_count == 1


def test_fail_closed_rollback_preserves_capacity() -> None:
    repository = AiAutomationLaneRolloutRepository(_database_url())
    with _connect() as connection:
        connection.execute(
            "UPDATE queue_lane_policy SET rollout_mode = 'execute', max_in_flight = 8 WHERE lane = 'ai_generation'"
        )

    plan = repository.apply(
        lane="ai_generation",
        expected_generation=1,
        expected_policy_version="queue-v2-production-all-g1",
        expected_mode="execute",
        target_mode="blocked",
        expected_capacity=8,
        target_capacity=8,
        max_open_jobs=0,
        actor="pytest",
        reason="fail closed rollback",
    )

    assert plan.to_mode == "blocked"
    assert plan.to_capacity == 8


def test_rollout_cli_is_dry_run_by_default_and_requires_authorization(monkeypatch, capsys) -> None:
    from scripts.ops import promote_ai_automation_lane

    fake_plan = SimpleNamespace(
        lane="ai_generation",
        to_mode="canary",
        as_dict=lambda: {
            "lane": "ai_generation",
            "from_mode": "blocked",
            "to_mode": "canary",
            "applied": False,
        },
    )

    class Repository:
        def plan(self, **_kwargs):
            return fake_plan

        def apply(self, **_kwargs):  # pragma: no cover - authorization fails first
            raise AssertionError("apply must not run without authorization")

    monkeypatch.setattr(promote_ai_automation_lane, "AiAutomationLaneRolloutRepository", Repository)
    monkeypatch.setattr(
        promote_ai_automation_lane,
        "_provider_preflight",
        lambda _lane, _mode: {"required": True, "ready": True, "blocking_reasons": []},
    )
    args = [
        "--lane", "ai_generation",
        "--expected-generation", "1",
        "--expected-policy-version", "queue-v2-production-all-g1",
        "--expected-mode", "blocked",
        "--target-mode", "canary",
        "--expected-capacity", "4",
        "--target-capacity", "4",
        "--expected-open-job-id", "4500",
        "--max-open-jobs", "1",
        "--actor", "pytest",
        "--reason", "reviewed canary",
    ]

    assert promote_ai_automation_lane.main(args) == 0
    assert '"applied": false' in capsys.readouterr().out

    monkeypatch.delenv(promote_ai_automation_lane.AUTHORIZATION_ENV, raising=False)
    with pytest.raises(RuntimeError, match=promote_ai_automation_lane.AUTHORIZATION_ENV):
        promote_ai_automation_lane.main(
            args
            + [
                "--apply",
                "--confirmation",
                "PROMOTE_AI_AUTOMATION_LANE_AI_GENERATION_BLOCKED_TO_CANARY_G1",
            ]
        )
