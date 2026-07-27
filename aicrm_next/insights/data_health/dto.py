from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


HealthStatus = Literal["ok", "warn", "fail", "not_applicable"]


class DataHealthCheckResult(BaseModel):
    check_id: str
    title: str
    status: HealthStatus
    severity: Literal["red", "yellow", "green", "gray"]
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""


class DataHealthSummary(BaseModel):
    ok: bool
    overall_status: HealthStatus
    counts: dict[str, int]
    checks: list[DataHealthCheckResult]
