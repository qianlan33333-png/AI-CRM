from __future__ import annotations

from typing import Any

from .domain import validate_private_payload
from .dto import (
    OperationCycleSnapshotV1,
    RunListView,
    StrategyListView,
)
from .repository import OperationCycleRepository, build_operation_cycle_repository


def _safe_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit or 50), 100)), max(0, int(offset or 0))


def report_operation_cycle(
    snapshot: OperationCycleSnapshotV1 | dict[str, Any],
    *,
    idempotency_key: str,
    reporter_id: str = "",
    client_id: str = "",
    repo: OperationCycleRepository | None = None,
) -> dict[str, Any]:
    validated = (
        snapshot
        if isinstance(snapshot, OperationCycleSnapshotV1)
        else OperationCycleSnapshotV1.model_validate(snapshot)
    )
    # Keep the application boundary safe even when a test or internal caller
    # constructs a model without using the HTTP validation path.
    validate_private_payload(validated.model_dump(mode="json"))
    repository = repo or build_operation_cycle_repository()
    receipt = repository.save_snapshot(
        validated,
        idempotency_key=idempotency_key,
        reporter_id=reporter_id,
        client_id=client_id,
    )
    return receipt.model_dump(mode="json")


def list_strategies(
    *,
    repo: OperationCycleRepository | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    safe_limit, safe_offset = _safe_page(limit, offset)
    repository = repo or build_operation_cycle_repository()
    return StrategyListView(
        items=repository.list_strategy_summaries(limit=safe_limit, offset=safe_offset),
        limit=safe_limit,
        offset=safe_offset,
    ).model_dump(mode="json")


def get_strategy(
    strategy_key: str,
    *,
    repo: OperationCycleRepository | None = None,
) -> dict[str, Any] | None:
    repository = repo or build_operation_cycle_repository()
    detail = repository.get_strategy_detail(str(strategy_key or "").strip())
    return detail.model_dump(mode="json") if detail else None


def list_strategy_runs(
    strategy_key: str,
    *,
    repo: OperationCycleRepository | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    safe_limit, safe_offset = _safe_page(limit, offset)
    normalized_strategy_key = str(strategy_key or "").strip()
    repository = repo or build_operation_cycle_repository()
    return RunListView(
        strategy_key=normalized_strategy_key,
        items=repository.list_run_summaries(
            normalized_strategy_key,
            limit=safe_limit,
            offset=safe_offset,
        ),
        limit=safe_limit,
        offset=safe_offset,
    ).model_dump(mode="json")


def get_run(
    run_key: str,
    *,
    repo: OperationCycleRepository | None = None,
) -> dict[str, Any] | None:
    repository = repo or build_operation_cycle_repository()
    detail = repository.get_run_detail(str(run_key or "").strip())
    return detail.model_dump(mode="json") if detail else None


# Explicit query aliases keep the application language aligned with the
# repository while the HTTP API retains its shorter route-oriented names.
list_strategy_summaries = list_strategies
get_strategy_detail = get_strategy
list_run_summaries = list_strategy_runs
get_run_detail = get_run
