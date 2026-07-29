from __future__ import annotations

from typing import Any

from .domain import compute_snapshot_hash, validate_private_payload
from .strategy_context_dto import (
    ContextMode,
    OperationCycleContextIndexView,
    OperationCycleStrategyContextView,
    StrategyChangeDecisionRequest,
    StrategyChangeProposalListView,
    StrategyChangeProposalV1,
)
from .strategy_context_repository import StrategyContextRepository, build_strategy_context_repository


def _safe_page(limit: int, offset: int, *, default: int, maximum: int) -> tuple[int, int]:
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = default
    try:
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        parsed_offset = 0
    return max(1, min(parsed_limit, maximum)), max(0, parsed_offset)


def get_context_index(
    *,
    limit: int = 50,
    offset: int = 0,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any]:
    safe_limit, safe_offset = _safe_page(limit, offset, default=50, maximum=100)
    repository = repo or build_strategy_context_repository()
    return OperationCycleContextIndexView(
        items=repository.list_context_index(limit=safe_limit, offset=safe_offset),
        limit=safe_limit,
        offset=safe_offset,
    ).model_dump(mode="json")


def get_strategy_context(
    strategy_key: str,
    *,
    mode: ContextMode = "execution",
    limit: int = 3,
    offset: int = 0,
    filters: dict[str, Any] | None = None,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any] | None:
    repository = repo or build_strategy_context_repository()
    strategy = repository.get_strategy_summary(str(strategy_key or "").strip())
    execution = repository.get_execution_version(str(strategy_key or "").strip())
    if strategy is None or execution is None:
        return None
    safe_limit, safe_offset = _safe_page(
        limit,
        offset,
        default=3 if mode != "history" else 50,
        maximum=100,
    )
    recent_runs = []
    proposals = []
    history = []
    system_facts = []
    if mode in {"retrospective", "optimization"}:
        recent_runs = repository.list_recent_run_details(strategy.strategy_key, limit=min(safe_limit, 3))
        system_facts = repository.list_system_facts(strategy.strategy_key, limit=3)
    if mode == "optimization":
        proposals = repository.list_proposals(
            strategy.strategy_key,
            limit=100,
            offset=0,
        )
    if mode == "history":
        history = repository.list_history(
            strategy.strategy_key,
            limit=safe_limit,
            offset=safe_offset,
            filters=dict(filters or {}),
        )
    execution_payload = execution.model_dump(mode="json")
    return OperationCycleStrategyContextView(
        mode=mode,
        strategy=strategy,
        strategy_version=execution.version,
        context_hash=compute_snapshot_hash(execution_payload),
        execution=execution,
        recent_runs=recent_runs,
        proposals=proposals,
        history=history,
        system_facts=system_facts,
        limit=safe_limit,
        offset=safe_offset,
    ).model_dump(mode="json")


def create_strategy_change_proposal(
    payload: StrategyChangeProposalV1 | dict[str, Any],
    *,
    idempotency_key: str,
    submitted_by: str,
    client_id: str,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any]:
    proposal = payload if isinstance(payload, StrategyChangeProposalV1) else StrategyChangeProposalV1.model_validate(payload)
    validate_private_payload(proposal.model_dump(mode="json"))
    result = (repo or build_strategy_context_repository()).create_proposal(
        proposal,
        idempotency_key=idempotency_key,
        submitted_by=submitted_by,
        client_id=client_id,
    )
    return {"ok": True, **result.model_dump(mode="json")}


def list_strategy_change_proposals(
    strategy_key: str,
    *,
    status: str = "",
    limit: int = 50,
    offset: int = 0,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any]:
    safe_limit, safe_offset = _safe_page(limit, offset, default=50, maximum=100)
    statuses = tuple(item for item in (part.strip() for part in str(status or "").split(",")) if item)
    invalid = set(statuses) - {"pending", "accepted", "rejected"}
    if invalid:
        raise ValueError("invalid_strategy_proposal_status")
    items = (repo or build_strategy_context_repository()).list_proposals(
        str(strategy_key or "").strip(),
        statuses=statuses,
        limit=safe_limit,
        offset=safe_offset,
    )
    return StrategyChangeProposalListView(
        strategy_key=str(strategy_key or "").strip(),
        items=items,
        limit=safe_limit,
        offset=safe_offset,
    ).model_dump(mode="json")


def decide_strategy_change_proposal(
    proposal_id: str,
    payload: StrategyChangeDecisionRequest | dict[str, Any],
    *,
    decided_by: str,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any]:
    decision = payload if isinstance(payload, StrategyChangeDecisionRequest) else StrategyChangeDecisionRequest.model_validate(payload)
    result = (repo or build_strategy_context_repository()).decide_proposal(
        str(proposal_id or "").strip(),
        decision=decision.decision,
        note=decision.note,
        decided_by=decided_by,
    )
    return {"ok": True, **result.model_dump(mode="json")}


def execution_context_contract(
    strategy_key: str,
    *,
    repo: StrategyContextRepository | None = None,
) -> dict[str, Any] | None:
    execution = (repo or build_strategy_context_repository()).get_execution_version(
        str(strategy_key or "").strip()
    )
    if execution is None:
        return None
    payload = execution.model_dump(mode="json")
    return {
        "strategy_key": execution.strategy_key,
        "strategy_version": execution.version,
        "context_hash": compute_snapshot_hash(payload),
        "document_pack": execution.document_pack.model_dump(mode="json"),
        "execution_contract": execution.document_pack.execution_contract.model_dump(mode="json"),
    }


__all__ = [
    "create_strategy_change_proposal",
    "decide_strategy_change_proposal",
    "execution_context_contract",
    "get_context_index",
    "get_strategy_context",
    "list_strategy_change_proposals",
]
