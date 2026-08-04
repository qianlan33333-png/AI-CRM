from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


HealthStatus = Literal["ok", "warn", "fail", "not_applicable"]
GateDecision = Literal["pass", "warn", "block"]
CandidateRelation = bool | Literal["unknown"]


class DataHealthCheckResult(BaseModel):
    check_id: str
    title: str
    status: HealthStatus
    severity: Literal["red", "yellow", "green", "gray"]
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""
    gate_decision: GateDecision | None = None
    reason_code: str = ""
    owner: str = "insights_data_health"
    candidate_related: CandidateRelation = "unknown"
    first_observed_at: str = ""
    last_observed_at: str = ""
    replay_policy: str = "manual_after_remediation"

    @model_validator(mode="after")
    def _resolve_gate_decision(self) -> "DataHealthCheckResult":
        if self.gate_decision is None:
            self.gate_decision = "block" if self.status == "fail" else "warn" if self.status == "warn" else "pass"
        if not self.reason_code:
            self.reason_code = f"{self.check_id}_{self.gate_decision}"
        return self


class DataHealthSummary(BaseModel):
    ok: bool
    overall_status: HealthStatus
    counts: dict[str, int]
    checks: list[DataHealthCheckResult]
    gate_counts: dict[str, int] = Field(default_factory=dict)
    registry_sha256: str = ""
    registry_matches_manifest: bool = True
