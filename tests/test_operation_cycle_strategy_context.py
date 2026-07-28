from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicrm_next.extensions.hxc.operation_cycles.domain import OperationCycleConflictError
from aicrm_next.extensions.hxc.operation_cycles.dto import OperationCycleSnapshotV1
from aicrm_next.extensions.hxc.operation_cycles.repository import InMemoryOperationCycleRepository
from aicrm_next.extensions.hxc.operation_cycles.strategy_context import (
    create_strategy_change_proposal,
    decide_strategy_change_proposal,
    execution_context_contract,
    get_context_index,
    get_strategy_context,
    list_strategy_change_proposals,
)
from aicrm_next.extensions.hxc.operation_cycles.strategy_context_dto import (
    StrategyChangeProposalV1,
)
from aicrm_next.extensions.hxc.operation_cycles.strategy_context_repository import (
    InMemoryStrategyContextRepository,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "operation_cycles" / "hxc_monday_20260713_snapshot.json"


def _snapshot() -> OperationCycleSnapshotV1:
    return OperationCycleSnapshotV1.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _repo() -> InMemoryStrategyContextRepository:
    operation_repo = InMemoryOperationCycleRepository()
    operation_repo.save_snapshot(
        _snapshot(),
        idempotency_key="strategy-context-seed",
        reporter_id="pytest",
        client_id="pytest",
    )
    return InMemoryStrategyContextRepository(operation_repo)


def _proposal(*, base_version: int | None = None, suffix: str = "next") -> StrategyChangeProposalV1:
    effective_base = base_version or _snapshot().strategy.version
    return StrategyChangeProposalV1.model_validate(
        {
            "strategy_key": _snapshot().strategy.strategy_key,
            "base_strategy_version": effective_base,
            "source_run_key": _snapshot().run.run_key,
            "conclusion": "上轮执行说明需要把准备与入审拆成两个可核验阶段。",
            "hypothesis": "批量解析与一次事务写入可以把入审控制在十分钟内。",
            "actions": ["批量解析身份与规则", "只创建 pending_review/draft 计划"],
            "target_version": {
                "version_label": suffix,
                "objective": "十分钟内生成可审阅计划",
                "definition": {"preparation_sla_seconds": 360, "commit_sla_seconds": 120},
                "document_pack": {
                    "execution_guide": {"markdown": "# 执行指南\n\n先 prepare，再 commit。", "source": "approved-proposal"},
                    "copy_guide": {"markdown": "# 话术指南\n\n使用已确认逐人话术。", "source": "approved-proposal"},
                    "measurement_guide": {"markdown": "# 衡量指南\n\n记录阶段耗时与守恒。", "source": "approved-proposal"},
                    "execution_contract": {
                        "allowed_owner_userids": ["HuangYouCan"],
                        "required_checks": ["identity", "policy", "path", "material", "persist"],
                        "custom_rules": {"unique_cid": True},
                    },
                },
            },
            "evidence": [],
        }
    )


def test_execution_context_uses_only_confirmed_version_and_document_hashes() -> None:
    repo = _repo()
    before = execution_context_contract(_snapshot().strategy.strategy_key, repo=repo)
    assert before is not None
    assert before["strategy_version"] == _snapshot().strategy.version

    created = create_strategy_change_proposal(
        _proposal(),
        idempotency_key="proposal-v2",
        submitted_by="api_client:campaign-agent",
        client_id="campaign-agent",
        repo=repo,
    )
    still_before = execution_context_contract(_snapshot().strategy.strategy_key, repo=repo)
    assert still_before is not None
    assert still_before["strategy_version"] == _snapshot().strategy.version

    accepted = decide_strategy_change_proposal(
        created["proposal_id"],
        {"decision": "accept", "note": "确认作为下一版正式执行策略"},
        decided_by="admin:operator",
        repo=repo,
    )
    assert accepted["status"] == "accepted"
    assert accepted["applied_strategy_version"] == _snapshot().strategy.version + 1

    execution = execution_context_contract(_snapshot().strategy.strategy_key, repo=repo)
    assert execution is not None
    assert execution["strategy_version"] == _snapshot().strategy.version + 1
    assert len(execution["context_hash"]) == 64
    assert execution["execution_contract"]["review_required"] is True
    assert execution["execution_contract"]["direct_broadcast_jobs_allowed"] is False
    assert execution["document_pack"]["copy_guide"]["sha256"]


def test_proposal_idempotency_and_stale_base_version_are_conflicts() -> None:
    repo = _repo()
    first = create_strategy_change_proposal(
        _proposal(),
        idempotency_key="same-key",
        submitted_by="agent",
        client_id="agent",
        repo=repo,
    )
    repeated = create_strategy_change_proposal(
        _proposal(),
        idempotency_key="same-key",
        submitted_by="agent",
        client_id="agent",
        repo=repo,
    )
    assert repeated["proposal_id"] == first["proposal_id"]

    stale = create_strategy_change_proposal(
        _proposal(suffix="alternative-v2"),
        idempotency_key="stale-key",
        submitted_by="agent",
        client_id="agent",
        repo=repo,
    )
    decide_strategy_change_proposal(
        first["proposal_id"],
        {"decision": "accept", "note": "采用第一份"},
        decided_by="admin",
        repo=repo,
    )
    with pytest.raises(OperationCycleConflictError, match="strategy_base_version_conflict"):
        decide_strategy_change_proposal(
            stale["proposal_id"],
            {"decision": "accept", "note": "尝试采用过期提案"},
            decided_by="admin",
            repo=repo,
        )


def test_context_modes_default_to_three_runs_and_include_proposal_governance() -> None:
    repo = _repo()
    for index in range(2, 6):
        payload = _snapshot().model_dump(mode="json")
        payload["report_id"] = f"context-run-{index}"
        payload["run"]["run_key"] = f"context_run_{index}"
        payload["run"]["label"] = f"第 {index} 轮"
        payload["snapshot_revision"] = 1
        repo.operation_repo.save_snapshot(
            OperationCycleSnapshotV1.model_validate(payload),
            idempotency_key=f"context-run-{index}",
        )
    create_strategy_change_proposal(
        _proposal(),
        idempotency_key="optimization-pending",
        submitted_by="agent",
        client_id="agent",
        repo=repo,
    )

    execution = get_strategy_context(_snapshot().strategy.strategy_key, mode="execution", repo=repo)
    retrospective = get_strategy_context(_snapshot().strategy.strategy_key, mode="retrospective", repo=repo)
    optimization = get_strategy_context(_snapshot().strategy.strategy_key, mode="optimization", repo=repo)
    history = get_strategy_context(
        _snapshot().strategy.strategy_key,
        mode="history",
        limit=2,
        offset=1,
        repo=repo,
    )

    assert execution is not None and execution["recent_runs"] == []
    assert retrospective is not None and len(retrospective["recent_runs"]) == 3
    assert optimization is not None and len(optimization["recent_runs"]) == 3
    assert len(optimization["proposals"]) == 1
    assert history is not None and len(history["history"]) == 2

    index = get_context_index(repo=repo)
    assert index["items"][0]["pending_proposal_count"] == 1
    proposals = list_strategy_change_proposals(_snapshot().strategy.strategy_key, repo=repo)
    assert proposals["items"][0]["status"] == "pending"


def test_strategy_document_pack_rejects_private_identifiers() -> None:
    payload = _proposal().model_dump(mode="json")
    payload["target_version"]["document_pack"]["copy_guide"]["markdown"] = "联系 13800138000"
    payload["target_version"]["document_pack"]["copy_guide"]["sha256"] = ""
    with pytest.raises(ValueError, match="phone number is forbidden"):
        StrategyChangeProposalV1.model_validate(payload)
