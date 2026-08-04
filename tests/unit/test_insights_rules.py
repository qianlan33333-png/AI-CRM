from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.insights.data_health.checks import _static_guard_result
from aicrm_next.insights.data_health.dto import DataHealthCheckResult, DataHealthSummary
from aicrm_next.insights.data_health.external_effect_provenance import (
    external_effect_backlog_sql,
    wecom_welcome_window_closed_business_rejection_sql,
)


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


def test_welcome_41051_requires_strict_provider_and_no_send_provenance() -> None:
    predicate = " ".join(wecom_welcome_window_closed_business_rejection_sql("job").split())

    assert "job.last_error_code" in predicate
    assert "wecom_error_41051" in predicate
    assert "job.effect_type" in predicate
    assert "wecom.welcome_message.send" in predicate
    assert "job.attempt_count = 1" in predicate
    assert "job.max_attempts = 1" in predicate
    assert "job.provider_result_received IS TRUE" in predicate
    assert "welcome_window_attempt.response_summary_json ->> 'errcode'" in predicate
    assert "welcome_window_attempt.response_summary_json ->> 'wecom_send_executed'" in predicate
    assert "= 'false'" in predicate


def test_external_effect_health_counts_welcome_41051_as_business_outcome() -> None:
    query = " ".join(external_effect_backlog_sql(terminal_lookback_hours=24).split())

    assert "AS wecom_welcome_window_closed_business_rejection" in query
    assert "AND NOT wecom_welcome_window_closed_business_rejection" in query
    assert "AS wecom_welcome_window_closed_business_rejection_count" in query
