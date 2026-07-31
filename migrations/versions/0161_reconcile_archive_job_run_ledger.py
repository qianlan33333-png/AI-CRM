"""Reconcile archive runs stranded by the DBAPI timestamptz binding defect.

Revision ID: 0161_reconcile_archive_job_run_ledger
Revises: 0160_ai_audience_wecom_contacts_view_repair
"""

from __future__ import annotations

from alembic import op


revision = "0161_reconcile_archive_job_run_ledger"
down_revision = "0160_ai_audience_wecom_contacts_view_repair"
branch_labels = None
depends_on = None


RECONCILE_STALE_ARCHIVE_RUNS_SQL = """
UPDATE sync_runs
SET status = 'failed',
    error_message = 'job_run_ledger_finish_failed_reconciled',
    raw_response = jsonb_build_object(
        'reconciled', TRUE,
        'reconciliation_reason', 'job_run_finish_timestamptz_parameter_type_mismatch',
        'core_sync_outcome', 'unknown',
        'reconciled_at', CURRENT_TIMESTAMP
    ),
    finished_at = created_at
WHERE status = 'running'
  AND owner_userid = 'HuangYouCan'
  AND start_time AT TIME ZONE 'Asia/Shanghai' = TIMESTAMP '2000-01-01 00:00:00'
  AND end_time AT TIME ZONE 'Asia/Shanghai' = TIMESTAMP '2099-12-31 23:59:59'
  AND COALESCE(cursor, '') = ''
  AND fetched_count = 0
  AND inserted_count = 0
  AND raw_response = '{}'::jsonb
  AND COALESCE(error_message, '') = ''
  AND finished_at IS NULL
  AND created_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes'
"""


def upgrade() -> None:
    op.execute(RECONCILE_STALE_ARCHIVE_RUNS_SQL)


def downgrade() -> None:
    # Reopening reconciled audit records as running would recreate false state.
    pass
