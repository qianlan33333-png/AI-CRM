"""freeze legacy completion work before independent continuation fan-out.

Revision ID: 0131_external_effect_continuation_fanout
Revises: 0130_welcome_media_dependencies
"""

from __future__ import annotations

from alembic import op


revision = "0131_external_effect_continuation_fanout"
down_revision = "0130_welcome_media_dependencies"
branch_labels = None
depends_on = None


FREEZE_REASON = "pre_independent_continuation_fanout_requires_manual_classification"
LEGACY_CONSUMER = "external_effect_completion_continuation_consumer"


def upgrade() -> None:
    _classify_completion_outbox_history()
    _classify_legacy_consumer_history()
    _hold_nonterminal_completion_history()


def _classify_completion_outbox_history() -> None:
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'internal_event_outbox', outbox.id, outbox.status,
            CASE
                WHEN outbox.status IN ('relayed', 'failed_terminal') THEN 'terminal_readonly'
                WHEN outbox.status IN ('pending', 'running', 'failed_retryable')
                     AND outbox.attempt_count >= outbox.max_attempts
                    THEN 'inconsistent_quarantine'
                ELSE 'ambiguous_hold'
            END,
            CASE
                WHEN outbox.status IN ('relayed', 'failed_terminal') THEN ''
                ELSE '{FREEZE_REASON}'
            END,
            jsonb_build_object(
                'event_type', outbox.event_type,
                'legacy_fanout_contract', 'first_match_single_consumer',
                'automatic_replay_allowed', FALSE,
                'attempt_count', outbox.attempt_count,
                'max_attempts', outbox.max_attempts,
                'had_active_lease', outbox.lease_token <> '' OR outbox.locked_at IS NOT NULL
            )
        FROM internal_event_outbox outbox
        WHERE outbox.event_type = 'external_effect.completed'
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )


def _classify_legacy_consumer_history() -> None:
    op.execute(
        f"""
        INSERT INTO queue_history_classification (
            freeze_revision, queue_kind, queue_row_id, source_status,
            classification, hold_reason, evidence_json
        )
        SELECT
            '{revision}', 'internal_event_consumer', run.id, run.status,
            CASE
                WHEN run.status IN ('succeeded', 'failed_terminal', 'blocked', 'skipped')
                    THEN 'terminal_readonly'
                WHEN run.status IN ('pending', 'running', 'failed_retryable')
                     AND run.attempt_count >= run.max_attempts
                    THEN 'inconsistent_quarantine'
                ELSE 'ambiguous_hold'
            END,
            CASE
                WHEN run.status IN ('succeeded', 'failed_terminal', 'blocked', 'skipped') THEN ''
                ELSE '{FREEZE_REASON}'
            END,
            jsonb_build_object(
                'event_type', event.event_type,
                'consumer_name', run.consumer_name,
                'legacy_fanout_contract', 'first_match_single_consumer',
                'handler_alias_retained', TRUE,
                'automatic_replay_allowed', FALSE,
                'attempt_count', run.attempt_count,
                'max_attempts', run.max_attempts,
                'had_active_lease', run.lease_token <> '' OR run.locked_at IS NOT NULL
            )
        FROM internal_event_consumer_run run
        JOIN internal_event event ON event.event_id = run.event_id
        WHERE event.event_type = 'external_effect.completed'
          AND run.consumer_name = '{LEGACY_CONSUMER}'
        ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
        """
    )


def _hold_nonterminal_completion_history() -> None:
    op.execute(
        f"""
        UPDATE internal_event_outbox
        SET hold_reason = '{FREEZE_REASON}',
            hold_at = COALESCE(hold_at, CURRENT_TIMESTAMP)
        WHERE event_type = 'external_effect.completed'
          AND status IN ('pending', 'running', 'failed_retryable')
          AND hold_reason = ''
        """
    )
    op.execute(
        f"""
        UPDATE internal_event_consumer_run run
        SET hold_reason = '{FREEZE_REASON}',
            hold_at = COALESCE(run.hold_at, CURRENT_TIMESTAMP)
        FROM internal_event event
        WHERE event.event_id = run.event_id
          AND event.event_type = 'external_effect.completed'
          AND run.consumer_name = '{LEGACY_CONSUMER}'
          AND run.status IN ('pending', 'running', 'failed_retryable')
          AND run.hold_reason = ''
        """
    )


def downgrade() -> None:
    # Queue classifications are append-only and a downgrade must never release
    # held completion work into the historical first-match runtime.
    pass
