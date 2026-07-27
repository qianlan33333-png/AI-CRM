from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/versions/0103_broadcast_delivery_state_machine.py"


def test_broadcast_delivery_state_machine_migration_is_chained_and_conservative() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0103_broadcast_delivery_state_machine"' in source
    assert 'down_revision = "0102_questionnaire_radar_invariants"' in source
    for column in (
        "dispatch_started_at",
        "side_effect_executed",
        "provider_result_received",
        "result_summary_json",
        "reconciliation_required",
        "completed_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
    assert "ADD COLUMN IF NOT EXISTS broadcast_job_id" in source
    assert "uq_outbound_tasks_broadcast_job" in source
    assert "'dispatching'" in source
    assert "'unknown_after_dispatch'" in source
    assert "idx_broadcast_jobs_reconciliation" in source
    assert "WHERE status IN ('queued', 'claimed', 'failed_retryable')" in source
    assert "UPDATE broadcast_jobs" in source
    assert "WHERE status = 'claimed'" in source
    assert "SET status = 'unknown_after_dispatch'" in source
    assert "UPDATE broadcast_jobs SET status = 'blocked' WHERE status IN ('dispatching', 'unknown_after_dispatch')" in source
    for table_name in (
        "outbound_tasks",
        "broadcast_jobs",
        "cloud_broadcast_plan_recipients",
        "cloud_broadcast_plan_recipient_messages",
    ):
        assert f'if _has_table("{table_name}")' in source


def test_broadcast_delivery_state_machine_schema_is_available_after_upgrade(next_pg_schema) -> None:
    with get_session_factory()() as session:
        columns = {
            row["column_name"]
            for row in session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'broadcast_jobs'
                    """
                )
            ).mappings()
        }
        constraints = {
            row["conname"]: row["definition"]
            for row in session.execute(
                text(
                    """
                    SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
                    FROM pg_constraint c
                    WHERE c.conrelid IN (
                        'broadcast_jobs'::regclass,
                        'cloud_broadcast_plan_recipients'::regclass,
                        'cloud_broadcast_plan_recipient_messages'::regclass
                    )
                    """
                )
            ).mappings()
        }
        indexes = {
            row["indexname"]: row["indexdef"]
            for row in session.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = 'broadcast_jobs'
                    """
                )
            ).mappings()
        }

    assert {
        "dispatch_started_at",
        "side_effect_executed",
        "provider_result_received",
        "result_summary_json",
        "reconciliation_required",
        "completed_at",
    } <= columns
    assert "dispatching" in constraints["broadcast_jobs_status_check"]
    assert "unknown_after_dispatch" in constraints["broadcast_jobs_status_check"]
    assert "unknown_after_dispatch" in constraints["cloud_broadcast_plan_recipients_send_status_check"]
    assert "unknown_after_dispatch" in constraints["cloud_broadcast_plan_recipient_messages_status_check"]
    assert "dispatching" not in indexes["idx_broadcast_jobs_reclaim_due"]
    assert "unknown_after_dispatch" not in indexes["idx_broadcast_jobs_reclaim_due"]
    assert "idx_broadcast_jobs_reconciliation" in indexes
