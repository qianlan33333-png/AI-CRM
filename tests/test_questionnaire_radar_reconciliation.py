from __future__ import annotations

import subprocess
import sys

from aicrm_next.extensions.forms.questionnaire import reconciliation
from aicrm_next.extensions.forms.questionnaire.reconciliation import QuestionnaireRadarReconciliationService


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.row_factory = None
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=()):
        del params
        self.statements.append(str(statement))
        return _Result({"anomaly_count": 1})


def test_count_only_reconciliation_has_pii_free_read_only_lineage_counts(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(reconciliation, "connect_raw_postgres", lambda url: connection)

    result = QuestionnaireRadarReconciliationService(database_url="postgresql://test/test").diagnose()

    assert result["ok"] is True
    assert result["mode"] == "count_only"
    assert set(result["counts"]) == {
        "submission_without_outbox",
        "relayed_outbox_without_event",
        "event_without_required_webhook_effect",
        "event_without_required_tag_effect",
        "duplicate_questionnaire_effect",
        "effect_without_succeeded_planner",
        "succeeded_effect_without_succeeded_attempt",
        "succeeded_tag_effect_without_projection",
        "stale_legacy_retry_residue",
    }
    assert set(result["counts"].values()) == {1}
    assert result["historical_counts"] == {
        "event_without_required_webhook_effect": 1,
        "event_without_required_tag_effect": 1,
    }
    assert result["actionable_cutover_at"] == "2026-07-13T16:20:00Z"
    assert result["database_mutation_performed"] is False
    assert result["consumer_executed"] is False
    assert result["provider_executed"] is False
    assert result["real_external_call_executed"] is False
    assert result["pii_in_output"] is False
    statements = "\n".join(connection.statements).upper()
    assert "INSERT INTO" not in statements
    assert "UPDATE " not in statements
    assert "DELETE FROM" not in statements
    submission_query = reconciliation._ANOMALY_QUERIES["submission_without_outbox"]
    assert "TIMESTAMPTZ '2026-07-13 16:20:00+00'" in submission_query
    assert "FROM internal_event_outbox outbox" in submission_query
    assert "FROM internal_event event" in submission_query
    planner_query = reconciliation._ANOMALY_QUERIES["effect_without_succeeded_planner"]
    assert "JOIN internal_event event" in planner_query
    assert "event.event_type = 'questionnaire.submitted'" in planner_query
    residue_query = reconciliation._ANOMALY_QUERIES["stale_legacy_retry_residue"]
    assert "log.status IN ('planned', 'pending', 'queued')" in residue_query
    assert "log.retry_from_log_id IS NOT NULL OR" not in residue_query


def test_repair_requires_auditable_actor_and_reason_without_connecting(monkeypatch) -> None:
    monkeypatch.setattr(
        reconciliation,
        "connect_raw_postgres",
        lambda url: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    result = QuestionnaireRadarReconciliationService(database_url="postgresql://test/test").repair(
        actor="",
        reason="",
    )

    assert result["ok"] is False
    assert result["error"] == "actor_and_reason_required"
    assert result["database_mutation_performed"] is False
    assert result["consumer_executed"] is False
    assert result["provider_executed"] is False
    assert result["real_external_call_executed"] is False


def test_reconciliation_cli_help_exposes_explicit_repair_audit_fields() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/ops/reconcile_questionnaire_radar.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--repair" in completed.stdout
    assert "--actor" in completed.stdout
    assert "--reason" in completed.stdout
