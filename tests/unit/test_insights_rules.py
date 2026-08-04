from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.insights.data_health.checks import _static_guard_result
from aicrm_next.insights.data_health.dto import DataHealthCheckResult, DataHealthSummary


pytestmark = pytest.mark.unit


def test_static_health_guard_maps_violations_to_red_failure() -> None:
    result = _static_guard_result(
        check_id="current_contract",
        title="Current contract",
        violations=["missing owner"],
        ok_summary="ok",
        remediation="assign owner",
    )
    assert result.status == "fail"
    assert result.severity == "red"
    assert result.evidence["violation_count"] == 1


def test_health_summary_uses_current_status_vocabulary() -> None:
    check = DataHealthCheckResult(
        check_id="current_contract",
        title="Current contract",
        status="ok",
        severity="green",
        summary="healthy",
    )
    summary = DataHealthSummary(ok=True, overall_status="ok", counts={"ok": 1}, checks=[check])
    assert summary.checks[0].check_id == "current_contract"
    with pytest.raises(ValidationError):
        DataHealthCheckResult(
            check_id="invalid",
            title="Invalid",
            status="unknown",
            severity="green",
            summary="invalid",
        )
