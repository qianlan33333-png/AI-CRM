from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.insights.data_health.checks import _static_guard_result
from aicrm_next.insights.data_health.dto import DataHealthCheckResult, DataHealthSummary
from aicrm_next.insights.data_health.external_effect_provenance import (
    external_effect_backlog_sql,
    wecom_welcome_chat_started_business_rejection_sql,
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


def test_welcome_chat_started_business_rejection_is_fail_closed() -> None:
    predicate = wecom_welcome_chat_started_business_rejection_sql("job")

    assert "wecom_error_41051" in predicate
    assert "channel_entry.process_channel_entry" in predicate
    assert "provider_result_received IS TRUE" in predicate
    assert "provider_error_classification" in predicate
    assert "wecom_send_executed" in predicate
    assert "chat_started_graph.status = 'terminal'" in predicate
    assert "SELECT COUNT(*)" in predicate


def test_welcome_chat_started_business_rejection_is_excluded_from_terminal_health() -> None:
    query = external_effect_backlog_sql(terminal_lookback_hours=24)

    assert "AS welcome_chat_started_business_rejection" in query
    assert query.count("AND NOT welcome_chat_started_business_rejection") == 2
    assert "AS welcome_chat_started_business_rejection_count" in query
