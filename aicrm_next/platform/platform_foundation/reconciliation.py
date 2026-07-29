from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import uuid4

from .command_bus.models import utcnow_iso

ReconciliationRunStatus = Literal["running", "completed", "failed"]


@dataclass(frozen=True)
class ReconciliationRun:
    reconciliation_run_id: str = field(default_factory=lambda: uuid4().hex)
    capability_owner: str = ""
    source_name: str = ""
    target_name: str = ""
    status: ReconciliationRunStatus = "completed"
    source_count: int = 0
    target_count: int = 0
    diff_count: int = 0
    sample_diffs: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=utcnow_iso)
    completed_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryReconciliationRunRepository:
    def __init__(self) -> None:
        self._runs: list[ReconciliationRun] = []

    def record_run(self, **kwargs: Any) -> ReconciliationRun:
        run = ReconciliationRun(**kwargs)
        self._runs.append(run)
        return run

    def list_runs(self) -> list[ReconciliationRun]:
        return list(self._runs)
