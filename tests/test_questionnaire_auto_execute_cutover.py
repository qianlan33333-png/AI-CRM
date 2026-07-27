from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.shared.release_cutovers import (
    QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT,
    QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/versions/0109_questionnaire_continuation_auto_execute.py"


def test_questionnaire_auto_execute_cutover_is_shared_by_runtime_health_and_reconciliation() -> None:
    from aicrm_next.insights.data_health.checks import QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL
    from aicrm_next.extensions.forms.questionnaire.reconciliation import _QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL

    assert QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT == "2026-07-13T16:20:00Z"
    assert QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL == "TIMESTAMPTZ '2026-07-13 16:20:00+00'"
    assert QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL == QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL
    assert _QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL == QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL


def test_cutover_migration_audits_and_skips_only_historical_questionnaire_consumers() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]

    assert 'revision = "0109_questionnaire_auto_execute"' in source
    assert 'down_revision = "0108_customer_read_model_refresh"' in source
    assert "INSERT INTO internal_event_consumer_attempt" in upgrade
    assert "UPDATE internal_event_consumer_run run" in upgrade
    assert "event.event_type = 'questionnaire.submitted'" in upgrade
    assert "event.created_at <" in upgrade
    assert "run.status IN ('pending', 'failed_retryable')" in upgrade
    assert "questionnaire_shadow_before_auto_execute_cutover" in upgrade
    for consumer_name in (
        "questionnaire_projection_consumer",
        "questionnaire_webhook_consumer",
        "questionnaire_tag_consumer",
        "automation_questionnaire_consumer",
        "customer_summary_consumer",
    ):
        assert consumer_name in upgrade
    assert "external_effect_job" not in upgrade
    assert "DELETE FROM" not in upgrade


def test_cutover_downgrade_never_reopens_historical_external_work() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade", 1)[1]

    assert "pass" in downgrade
    assert "UPDATE internal_event_consumer_run" not in downgrade
