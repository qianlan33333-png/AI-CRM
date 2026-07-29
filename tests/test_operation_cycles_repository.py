from __future__ import annotations

import pytest

from aicrm_next.extensions.hxc.operation_cycles.application import (
    get_run,
    get_strategy,
    list_strategies,
    list_strategy_runs,
    report_operation_cycle,
)
from aicrm_next.extensions.hxc.operation_cycles.domain import OperationCycleConflictError
from aicrm_next.extensions.hxc.operation_cycles.dto import OperationCycleSnapshotV1, ReferenceSnapshot
from aicrm_next.extensions.hxc.operation_cycles.repository import (
    InMemoryOperationCycleRepository,
    PostgresOperationCycleRepository,
)
from tests.test_operation_cycles_domain import snapshot_payload


def snapshot(*, revision: int = 1, report_id: str | None = None) -> OperationCycleSnapshotV1:
    payload = snapshot_payload()
    payload["snapshot_revision"] = revision
    payload["report_id"] = report_id or f"monday-20260713-r{revision}"
    return OperationCycleSnapshotV1.model_validate(payload)


def test_report_is_idempotent_and_preserves_reporter_audit() -> None:
    repo = InMemoryOperationCycleRepository()
    first = report_operation_cycle(
        snapshot(),
        idempotency_key="monday-20260713-r1",
        reporter_id="ops-reporter",
        client_id="ops-client",
        repo=repo,
    )
    second = report_operation_cycle(
        snapshot(),
        idempotency_key="monday-20260713-r1",
        reporter_id="other-reporter",
        client_id="other-client",
        repo=repo,
    )
    detail = get_run("hxc.monday.activation.20260713", repo=repo)

    assert first["projection_updated"] is True
    assert second == {**first, "projection_updated": False}
    assert repo.snapshot_count == 1
    assert detail is not None
    assert detail["snapshot"]["reporter_id"] == "ops-reporter"
    assert detail["snapshot"]["client_id"] == "ops-client"


def test_idempotency_key_reuse_with_changed_payload_conflicts() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="same-key", repo=repo)
    changed = snapshot()
    changed.run.label = "changed"

    with pytest.raises(OperationCycleConflictError) as exc:
        report_operation_cycle(changed, idempotency_key="same-key", repo=repo)
    assert exc.value.code == "idempotency_payload_mismatch"


def test_revision_regression_and_equal_revision_conflict() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(revision=2), idempotency_key="r2", repo=repo)

    with pytest.raises(OperationCycleConflictError) as regression:
        report_operation_cycle(snapshot(revision=1), idempotency_key="r1", repo=repo)
    assert regression.value.code == "snapshot_revision_regression"

    equal = snapshot(revision=2, report_id="alternate-r2")
    equal.retrospective.conclusion = "different revision payload"
    with pytest.raises(OperationCycleConflictError) as conflict:
        report_operation_cycle(equal, idempotency_key="alternate-r2", repo=repo)
    assert conflict.value.code == "snapshot_revision_conflict"


def test_newer_revision_updates_projection_and_read_models() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="r1", repo=repo)
    updated = snapshot(revision=2)
    updated.execution_stage = "postmortem"
    updated.data_status = "mature"
    updated.next_iteration.summary = "下一轮采用新埋点"
    updated.documents.broadcast_details.markdown = "# 本轮数据\n\n有效发送 845。"
    updated.documents.retrospective_details.markdown = "# 本轮复盘\n\n发送事实可核验，行为归因待补齐。"
    updated.documents.execution_strategy.markdown = "# 下周执行策略\n\n先补归因，再验证差异。"
    updated.references.append(
        ReferenceSnapshot(
            reference_key="ai-assistant-plan:monday-20260713",
            reference_type="other",
            label="周一激活计划",
            source_system="cloud_orchestrator_plan",
            source_id="hxc-monday-20260713-plan",
            href="/admin/cloud-orchestrator/plans/hxc-monday-20260713-plan",
            evidence_hash="",
            data_status="unknown",
        )
    )
    report_operation_cycle(updated, idempotency_key="r2", repo=repo)

    strategies = list_strategies(repo=repo)
    strategy = get_strategy("hxc.monday.activation", repo=repo)
    runs = list_strategy_runs("hxc.monday.activation", repo=repo)
    run = get_run("hxc.monday.activation.20260713", repo=repo)

    assert strategies["ok"] is True
    assert strategies["items"][0]["latest_run_key"] == updated.run.run_key
    assert strategies["items"][0]["next_iteration_summary"] == "下一轮采用新埋点"
    assert strategy is not None and strategy["strategy"]["run_count"] == 1
    assert strategy["sources"][0]["reference_key"] == "broadcast-summary"
    assert strategy["documents"]["broadcast_details"]["markdown"].startswith("# 本轮数据")
    assert strategy["documents"]["retrospective_details"]["markdown"].startswith("# 本轮复盘")
    assert strategy["assistant_plans"][0]["source_id"] == "hxc-monday-20260713-plan"
    assert runs["items"][0]["snapshot_revision"] == 2
    assert run is not None and run["run"]["execution_stage"] == "postmortem"
    assert run["run"]["plan_version"] == "review-v2"
    assert run["run"]["fact_conflict"] is True
    assert run["documents"]["execution_strategy"]["markdown"].startswith("# 下周执行策略")


def test_postgres_strategy_history_includes_formal_plan_links(monkeypatch) -> None:
    repo = PostgresOperationCycleRepository(session_factory=lambda: None)
    strategy_row = {
        "strategy_key": "hxc.monday.activation",
        "title": "每周一全量用户激活",
        "current_version": 1,
        "run_count": 2,
    }
    plan_id = "plan_campaign_from_preparation"

    monkeypatch.setattr(repo, "_one", lambda *_args, **_kwargs: strategy_row)
    monkeypatch.setattr(repo, "list_run_summaries", lambda *_args, **_kwargs: [])

    def fake_all(statement: str, _params: dict) -> list[dict]:
        if "operation_cycle_plan_links" not in statement:
            return []
        assert "NOT EXISTS" in statement
        assert "cloud_orchestrator_plan" in statement
        return [
            {
                "reference_key": "plan-link:generated",
                "reference_type": "other",
                "label": "hxc_monday_abcd_20260728 · AI 助手计划",
                "source_system": "cloud_orchestrator_plan",
                "source_id": plan_id,
                "href": "",
                "evidence_hash": "",
                "data_status": "unknown",
            }
        ]

    monkeypatch.setattr(repo, "_all", fake_all)

    detail = repo.get_strategy_detail("hxc.monday.activation")

    assert detail is not None
    assert [item.source_id for item in detail.assistant_plans] == [plan_id]


def test_strategy_version_and_run_strategy_version_are_immutable() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="r1", repo=repo)
    changed_version_payload = snapshot(revision=2)
    changed_version_payload.strategy.objective = "changed without version bump"

    with pytest.raises(OperationCycleConflictError) as version_conflict:
        report_operation_cycle(changed_version_payload, idempotency_key="r2", repo=repo)
    assert version_conflict.value.code == "strategy_version_payload_mismatch"


def test_same_report_id_with_changed_payload_conflicts() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="r1", repo=repo)
    changed = snapshot()
    changed.run.label = "changed"

    with pytest.raises(OperationCycleConflictError) as conflict:
        report_operation_cycle(changed, idempotency_key="new-key", repo=repo)
    assert conflict.value.code == "report_id_payload_mismatch"


def test_blocked_attempt_cannot_be_rewritten_as_completed() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="r1", repo=repo)
    rewritten = snapshot(revision=2)
    rewritten.attempts[0].status = "completed"

    with pytest.raises(OperationCycleConflictError) as conflict:
        report_operation_cycle(rewritten, idempotency_key="r2", repo=repo)
    assert conflict.value.code == "blocked_attempt_mutation"


def test_progress_after_block_requires_new_direct_child_attempt() -> None:
    repo = InMemoryOperationCycleRepository()
    initial = snapshot()
    initial.execution_stage = "preflight"
    initial.delivery_status = "not_started"
    initial.attempts = [initial.attempts[0]]
    initial.stages = [initial.stages[0]]
    report_operation_cycle(initial, idempotency_key="r1", repo=repo)

    without_recovery = snapshot(revision=2)
    without_recovery.attempts = [without_recovery.attempts[0]]
    without_recovery.stages = [without_recovery.stages[0]]
    with pytest.raises(OperationCycleConflictError) as conflict:
        report_operation_cycle(without_recovery, idempotency_key="r2-no-recovery", repo=repo)
    assert conflict.value.code == "recovery_attempt_required"

    recovered = snapshot(revision=2)
    receipt = report_operation_cycle(recovered, idempotency_key="r2-recovered", repo=repo)
    assert receipt["accepted_revision"] == 2


def test_execution_stage_cannot_regress_or_rewrite_terminal_stage() -> None:
    repo = InMemoryOperationCycleRepository()
    report_operation_cycle(snapshot(), idempotency_key="r1", repo=repo)

    regressed = snapshot(revision=2)
    regressed.execution_stage = "preflight"
    with pytest.raises(OperationCycleConflictError) as stage_regression:
        report_operation_cycle(regressed, idempotency_key="r2-regressed", repo=repo)
    assert stage_regression.value.code == "execution_stage_regression"

    rewritten = snapshot(revision=2)
    rewritten.stages[1].status = "blocked"
    with pytest.raises(OperationCycleConflictError) as terminal_mutation:
        report_operation_cycle(rewritten, idempotency_key="r2-rewritten", repo=repo)
    assert terminal_mutation.value.code == "terminal_stage_mutation"


@pytest.mark.parametrize("delivery_status", ["dispatching", "failed"])
def test_draft_plan_conflicts_with_any_actual_delivery_attempt(delivery_status: str) -> None:
    repo = InMemoryOperationCycleRepository()
    item = snapshot()
    item.delivery_status = delivery_status
    item.funnel.effective_sent_count.status = "unknown"
    item.funnel.effective_sent_count.value = None
    item.retrospective.data_conflicts = []
    report_operation_cycle(item, idempotency_key=f"draft-{delivery_status}", repo=repo)

    detail = get_run(item.run.run_key, repo=repo)
    assert detail is not None
    assert detail["run"]["fact_conflict"] is True
