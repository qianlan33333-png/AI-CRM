from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.insights.data_health import checks as health_checks
from aicrm_next.insights.data_health.checks import _static_guard_result
from aicrm_next.insights.data_health.dto import DataHealthCheckResult, DataHealthSummary
from aicrm_next.insights.data_health.external_effect_provenance import (
    external_effect_backlog_sql,
    wecom_welcome_window_closed_business_rejection_sql,
)
from aicrm_next.platform.platform_foundation.external_effects.repo_contract import (
    _health_classification_code,
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


class _MappingsResult:
    def __init__(self, row: dict[str, object]):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _HealthSession:
    def __init__(self, row: dict[str, object]):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _statement):
        return _MappingsResult(self._row)


def _delivery_results(monkeypatch, row: dict[str, object]):
    monkeypatch.setattr(health_checks, "database_schema_available", lambda: True)
    monkeypatch.setattr(health_checks, "get_session_factory", lambda: lambda: _HealthSession(row))
    return {item.check_id: item for item in health_checks._external_effect_delivery_checks()}


def test_due_retryable_is_warning_within_sla_and_block_after_sla(monkeypatch) -> None:
    within = _delivery_results(
        monkeypatch,
        {"failed_retryable_count": 1, "due_retryable_count": 1, "oldest_failed_retryable_age_seconds": 30},
    )["external_effect_due_retryable_backlog"]
    assert within.status == "warn"
    assert within.gate_decision == "warn"

    overdue = _delivery_results(
        monkeypatch,
        {
            "failed_retryable_count": 1,
            "due_retryable_count": 1,
            "oldest_failed_retryable_age_seconds": health_checks.EXTERNAL_EFFECT_RETRYABLE_SLA_SECONDS + 1,
        },
    )["external_effect_due_retryable_backlog"]
    assert overdue.status == "fail"
    assert overdue.gate_decision == "block"


def test_known_41051_is_warning_but_unclassified_terminal_blocks(monkeypatch) -> None:
    known = _delivery_results(
        monkeypatch,
        {"wecom_welcome_window_closed_business_rejection_count": 1},
    )["external_effect_unclassified_terminal_recent"]
    assert known.status == "warn"
    assert known.gate_decision == "warn"
    assert known.reason_code == "external_effect_classified_terminal_history"

    unknown = _delivery_results(
        monkeypatch,
        {"recent_failed_terminal_count": 1, "historical_failed_terminal_count": 1},
    )["external_effect_unclassified_terminal_recent"]
    assert unknown.status == "fail"
    assert unknown.gate_decision == "block"
    assert unknown.reason_code == "external_effect_unclassified_terminal_recent"


def test_external_effect_health_classification_is_strict_and_fail_closed() -> None:
    assert _health_classification_code(
        "failed_terminal",
        "wecom_error_41051",
        {"errcode": "41051", "wecom_send_executed": False},
    ) == "known_business_terminal_wecom_welcome_window_closed"
    assert _health_classification_code(
        "failed_terminal",
        "wecom_error_41051",
        {"errcode": "41051"},
    ) == "unclassified_terminal"
    assert _health_classification_code("blocked", "policy_blocked", {}) == "unclassified_blocked"
    assert _health_classification_code("failed_retryable", "provider_timeout", {}) == "retryable"
