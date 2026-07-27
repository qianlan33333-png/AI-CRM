from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import uuid4

from .command_bus.models import utcnow_iso

SideEffectPlanStatus = Literal["planned", "approved", "executing", "executed", "failed", "cancelled", "blocked"]


@dataclass(frozen=True)
class SideEffectPlan:
    side_effect_plan_id: str = field(default_factory=lambda: uuid4().hex)
    command_id: str = ""
    effect_type: str = ""
    adapter_name: str = ""
    adapter_mode: str = "none"
    target_type: str = ""
    target_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: SideEffectPlanStatus = "planned"
    risk_level: str = "none"
    requires_approval: bool = False
    created_at: str = field(default_factory=utcnow_iso)
    approved_at: str = ""
    executed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemorySideEffectPlanRepository:
    def __init__(self) -> None:
        self._plans: list[SideEffectPlan] = []

    def create_plan(self, **kwargs: Any) -> SideEffectPlan:
        plan = SideEffectPlan(**kwargs)
        self._plans.append(plan)
        return plan

    def list_plans(self) -> list[SideEffectPlan]:
        return list(self._plans)
