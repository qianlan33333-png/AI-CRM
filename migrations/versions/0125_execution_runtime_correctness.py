"""external effect execution correctness primitives.

Revision ID: 0125_execution_runtime_correctness
Revises: 0124_agent_audit_tables
"""

from __future__ import annotations

from alembic import op


revision = "0125_execution_runtime_correctness"
down_revision = "0124_agent_audit_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE external_effect_job
        ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS cancel_requested_by TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS cancel_reason TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute("ALTER TABLE external_effect_attempt DROP CONSTRAINT IF EXISTS external_effect_attempt_status_check")
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        ADD CONSTRAINT external_effect_attempt_status_check
        CHECK (status IN (
            'dispatching', 'succeeded', 'simulated', 'unknown_after_dispatch',
            'failed_retryable', 'failed_terminal', 'blocked', 'skipped'
        ))
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_external_effect_attempt_open_job
        ON external_effect_attempt (job_id)
        WHERE status = 'dispatching'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_external_effect_job_cancel_requested
        ON external_effect_job (cancel_requested_at, id)
        WHERE cancel_requested_at IS NOT NULL AND status = 'dispatching'
        """
    )
    _add_history_freeze_columns()
    _create_history_classification_audit()
    _classify_and_hold_pre_cutover_rows()


def _add_history_freeze_columns() -> None:
    for table_name in (
        "external_effect_job",
        "internal_event_consumer_run",
        "internal_event_outbox",
        "webhook_inbox",
        "broadcast_jobs",
    ):
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS hold_reason TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS hold_at TIMESTAMPTZ
            """
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_external_effect_job_unheld_due
        ON external_effect_job (status, scheduled_at, next_retry_at, priority, id)
        WHERE hold_reason = '' AND status IN ('queued', 'failed_retryable')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_internal_event_consumer_run_unheld_due
        ON internal_event_consumer_run (status, next_retry_at, locked_at, id)
        WHERE hold_reason = '' AND status IN ('pending', 'running', 'failed_retryable')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_internal_event_outbox_unheld_due
        ON internal_event_outbox (status, next_retry_at, locked_at, id)
        WHERE hold_reason = '' AND status IN ('pending', 'running', 'failed_retryable')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webhook_inbox_unheld_due
        ON webhook_inbox (provider, status, next_retry_at, locked_at, id)
        WHERE hold_reason = '' AND status IN ('received', 'processing', 'failed_retryable')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_unheld_due
        ON broadcast_jobs (status, next_retry_at, lease_expires_at, scheduled_for, priority, id)
        WHERE hold_reason = '' AND status IN ('queued', 'claimed', 'failed_retryable')
        """
    )


def _create_history_classification_audit() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_history_classification (
            id BIGSERIAL PRIMARY KEY,
            freeze_revision TEXT NOT NULL,
            queue_kind TEXT NOT NULL CHECK (queue_kind IN (
                'external_effect', 'internal_event_consumer', 'internal_event_outbox',
                'webhook_inbox', 'broadcast_job'
            )),
            queue_row_id BIGINT NOT NULL,
            source_status TEXT NOT NULL,
            classification TEXT NOT NULL CHECK (classification IN (
                'terminal_readonly', 'safe_pre_provider', 'safe_retryable',
                'ambiguous_hold', 'inconsistent_quarantine'
            )),
            hold_reason TEXT NOT NULL DEFAULT '',
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            classified_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (freeze_revision, queue_kind, queue_row_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_history_classification_snapshot
        ON queue_history_classification (freeze_revision, queue_kind, classification, queue_row_id)
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_queue_history_classification_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
            RAISE EXCEPTION 'queue_history_classification is append-only';
        END;
        $function$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_queue_history_classification_immutable ON queue_history_classification")
    op.execute(
        """
        CREATE TRIGGER trg_queue_history_classification_immutable
        BEFORE UPDATE OR DELETE ON queue_history_classification
        FOR EACH ROW EXECUTE FUNCTION reject_queue_history_classification_mutation()
        """
    )


def _classify_and_hold_pre_cutover_rows() -> None:
    revision = "0125_execution_runtime_correctness"
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'external_effect', j.id, j.status,
            CASE
                WHEN j.status IN ('succeeded', 'simulated', 'failed_terminal', 'blocked', 'cancelled', 'expired')
                    THEN 'terminal_readonly'
                WHEN j.status IN ('dispatching', 'unknown_after_dispatch')
                    THEN 'ambiguous_hold'
                WHEN j.status IN ('queued', 'failed_retryable') AND j.attempt_count >= j.max_attempts
                    THEN 'inconsistent_quarantine'
                WHEN j.status = 'failed_retryable'
                     AND j.reconciliation_required = FALSE
                     AND (j.side_effect_executed = FALSE OR j.provider_result_received = TRUE)
                    THEN 'safe_retryable'
                WHEN j.status IN ('planned', 'approved', 'queued')
                     AND j.last_attempt_id = '' AND j.side_effect_executed = FALSE
                    THEN 'safe_pre_provider'
                ELSE 'ambiguous_hold'
            END,
            CASE
                WHEN j.status IN ('succeeded', 'simulated', 'failed_terminal', 'blocked', 'cancelled', 'expired') THEN ''
                ELSE 'history_frozen_at_0125'
            END,
            jsonb_build_object(
                'attempt_count', j.attempt_count,
                'max_attempts', j.max_attempts,
                'provider_boundary_started', j.dispatch_started_at IS NOT NULL,
                'provider_result_received', j.provider_result_received,
                'reconciliation_required', j.reconciliation_required
            )
        FROM external_effect_job j
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'internal_event_consumer', r.id, r.status,
            CASE
                WHEN r.status IN ('succeeded', 'failed_terminal', 'blocked', 'skipped') THEN 'terminal_readonly'
                WHEN r.status = 'running' THEN 'ambiguous_hold'
                WHEN r.status IN ('pending', 'failed_retryable') AND r.attempt_count >= r.max_attempts
                    THEN 'inconsistent_quarantine'
                WHEN r.status = 'failed_retryable' THEN 'safe_retryable'
                WHEN r.status = 'pending' AND r.attempt_count = 0 THEN 'safe_pre_provider'
                ELSE 'ambiguous_hold'
            END,
            CASE WHEN r.status IN ('succeeded', 'failed_terminal', 'blocked', 'skipped')
                 THEN '' ELSE 'history_frozen_at_0125' END,
            jsonb_build_object(
                'attempt_count', r.attempt_count,
                'max_attempts', r.max_attempts,
                'had_active_lease', r.lease_token <> '' OR r.locked_at IS NOT NULL
            )
        FROM internal_event_consumer_run r
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'internal_event_outbox', o.id, o.status,
            CASE
                WHEN o.status IN ('relayed', 'failed_terminal') THEN 'terminal_readonly'
                WHEN o.status = 'running' THEN 'ambiguous_hold'
                WHEN o.status IN ('pending', 'failed_retryable') AND o.attempt_count >= o.max_attempts
                    THEN 'inconsistent_quarantine'
                WHEN o.status = 'failed_retryable' THEN 'safe_retryable'
                WHEN o.status = 'pending' AND o.attempt_count = 0 THEN 'safe_pre_provider'
                ELSE 'ambiguous_hold'
            END,
            CASE WHEN o.status IN ('relayed', 'failed_terminal')
                 THEN '' ELSE 'history_frozen_at_0125' END,
            jsonb_build_object(
                'attempt_count', o.attempt_count,
                'max_attempts', o.max_attempts,
                'had_active_lease', o.lease_token <> '' OR o.locked_at IS NOT NULL
            )
        FROM internal_event_outbox o
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'webhook_inbox', i.id, i.status,
            CASE
                WHEN i.status IN ('succeeded', 'failed_terminal', 'dead_letter', 'ignored') THEN 'terminal_readonly'
                WHEN i.status = 'processing' THEN 'ambiguous_hold'
                WHEN i.status IN ('received', 'failed_retryable') AND i.attempt_count >= i.max_attempts
                    THEN 'inconsistent_quarantine'
                WHEN i.status = 'failed_retryable' THEN 'safe_retryable'
                WHEN i.status = 'received' AND i.attempt_count = 0 THEN 'safe_pre_provider'
                ELSE 'ambiguous_hold'
            END,
            CASE WHEN i.status IN ('succeeded', 'failed_terminal', 'dead_letter', 'ignored')
                 THEN '' ELSE 'history_frozen_at_0125' END,
            jsonb_build_object(
                'attempt_count', i.attempt_count,
                'max_attempts', i.max_attempts,
                'had_active_lock', i.locked_at IS NOT NULL
            )
        FROM webhook_inbox i
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'broadcast_job', b.id, b.status,
            CASE
                WHEN b.status IN ('sent', 'simulated', 'failed', 'failed_terminal', 'blocked', 'cancelled')
                    THEN 'terminal_readonly'
                WHEN b.status IN ('claimed', 'dispatching', 'unknown_after_dispatch')
                    THEN 'ambiguous_hold'
                WHEN b.status IN ('queued', 'failed_retryable') AND b.attempt_count >= b.max_attempts
                    THEN 'inconsistent_quarantine'
                WHEN b.status = 'failed_retryable'
                     AND b.reconciliation_required = FALSE
                     AND (b.side_effect_executed = FALSE OR b.provider_result_received = TRUE)
                    THEN 'safe_retryable'
                WHEN b.status IN ('waiting_approval', 'queued') AND b.attempt_count = 0
                    THEN 'safe_pre_provider'
                ELSE 'ambiguous_hold'
            END,
            CASE WHEN b.status IN ('sent', 'simulated', 'failed', 'failed_terminal', 'blocked', 'cancelled')
                 THEN '' ELSE 'history_frozen_at_0125' END,
            jsonb_build_object(
                'attempt_count', b.attempt_count,
                'max_attempts', b.max_attempts,
                'provider_boundary_started', b.dispatch_started_at IS NOT NULL,
                'provider_result_received', b.provider_result_received,
                'reconciliation_required', b.reconciliation_required
            )
        FROM broadcast_jobs b
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )
    for table_name, queue_kind in (
        ("external_effect_job", "external_effect"),
        ("internal_event_consumer_run", "internal_event_consumer"),
        ("internal_event_outbox", "internal_event_outbox"),
        ("webhook_inbox", "webhook_inbox"),
        ("broadcast_jobs", "broadcast_job"),
    ):
        op.execute(
            f"""
            UPDATE {table_name} target
            SET hold_reason = audit.hold_reason,
                hold_at = audit.classified_at
            FROM queue_history_classification audit
            WHERE audit.freeze_revision = '{revision}'
              AND audit.queue_kind = '{queue_kind}'
              AND audit.queue_row_id = target.id
              AND audit.classification <> 'terminal_readonly'
              AND target.hold_reason = ''
            """
        )


def downgrade() -> None:
    # A downgrade must never turn frozen history back into executable work.
    op.execute("UPDATE external_effect_job SET status = 'unknown_after_dispatch', reconciliation_required = TRUE WHERE hold_reason <> '' AND status = 'dispatching'")
    op.execute("UPDATE external_effect_job SET status = 'blocked' WHERE hold_reason <> '' AND status IN ('planned', 'approved', 'queued', 'failed_retryable')")
    op.execute("UPDATE internal_event_consumer_run SET status = 'blocked' WHERE hold_reason <> '' AND status IN ('pending', 'running', 'failed_retryable')")
    op.execute("UPDATE internal_event_outbox SET status = 'failed_terminal' WHERE hold_reason <> '' AND status IN ('pending', 'running', 'failed_retryable')")
    op.execute("UPDATE webhook_inbox SET status = 'dead_letter' WHERE hold_reason <> '' AND status IN ('received', 'processing', 'failed_retryable')")
    op.execute("UPDATE broadcast_jobs SET status = 'unknown_after_dispatch', reconciliation_required = TRUE WHERE hold_reason <> '' AND status = 'dispatching'")
    op.execute("UPDATE broadcast_jobs SET status = 'blocked' WHERE hold_reason <> '' AND status IN ('waiting_approval', 'queued', 'claimed', 'failed_retryable')")
    op.execute("DROP TRIGGER IF EXISTS trg_queue_history_classification_immutable ON queue_history_classification")
    op.execute("DROP TABLE IF EXISTS queue_history_classification")
    op.execute("DROP FUNCTION IF EXISTS reject_queue_history_classification_mutation()")
    for index_name in (
        "idx_external_effect_job_unheld_due",
        "idx_internal_event_consumer_run_unheld_due",
        "idx_internal_event_outbox_unheld_due",
        "idx_webhook_inbox_unheld_due",
        "idx_broadcast_jobs_unheld_due",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    for table_name in (
        "external_effect_job",
        "internal_event_consumer_run",
        "internal_event_outbox",
        "webhook_inbox",
        "broadcast_jobs",
    ):
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS hold_at")
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS hold_reason")
    op.execute(
        """
        UPDATE external_effect_attempt
        SET status = 'unknown_after_dispatch',
            error_code = CASE WHEN error_code = '' THEN 'migration_downgrade_open_attempt' ELSE error_code END,
            error_message = CASE
                WHEN error_message = '' THEN 'Open provider attempt was quarantined during migration downgrade.'
                ELSE error_message
            END,
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
        WHERE status = 'dispatching'
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_external_effect_job_cancel_requested")
    op.execute("DROP INDEX IF EXISTS uq_external_effect_attempt_open_job")
    op.execute("ALTER TABLE external_effect_attempt DROP CONSTRAINT IF EXISTS external_effect_attempt_status_check")
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        ADD CONSTRAINT external_effect_attempt_status_check
        CHECK (status IN (
            'succeeded', 'simulated', 'unknown_after_dispatch', 'failed_retryable',
            'failed_terminal', 'blocked', 'skipped'
        ))
        """
    )
    for column in (
        "cancel_reason",
        "cancel_requested_by",
        "cancel_requested_at",
        "row_version",
    ):
        op.execute(f"ALTER TABLE external_effect_job DROP COLUMN IF EXISTS {column}")
