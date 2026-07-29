"""Persist ID-validation canary, fault-drill, and soak evidence.

Revision ID: 0136_queue_runtime_validation_soak
Revises: 0135_queue_scope_transition_audit
"""

from __future__ import annotations

from alembic import op


revision = "0136_queue_runtime_validation_soak"
down_revision = "0135_queue_scope_transition_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_lease_recovery_event (
            lease_event_id BIGSERIAL PRIMARY KEY,
            queue_kind TEXT NOT NULL CHECK (
                queue_kind IN (
                    'external_effect',
                    'internal_event',
                    'internal_outbox',
                    'webhook_inbox'
                )
            ),
            queue_row_id BIGINT NOT NULL,
            worker_generation INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL CHECK (
                error_code IN (
                    'lease_expired_before_dispatch',
                    'lease_expired_after_dispatch',
                    'lease_expired'
                )
            ),
            lease_expires_at TIMESTAMPTZ NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_queue_lease_recovery_occurrence UNIQUE (
                queue_kind, queue_row_id, lease_expires_at, error_code
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_lease_recovery_detected
        ON queue_runtime_lease_recovery_event (detected_at DESC)
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_lease_recovery_event_append_only
        ON queue_runtime_lease_recovery_event
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_lease_recovery_event_append_only
        BEFORE UPDATE OR DELETE ON queue_runtime_lease_recovery_event
        FOR EACH ROW
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_lease_recovery_event_reject_truncate
        ON queue_runtime_lease_recovery_event
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_lease_recovery_event_reject_truncate
        BEFORE TRUNCATE ON queue_runtime_lease_recovery_event
        FOR EACH STATEMENT
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_validation_evidence (
            evidence_id TEXT PRIMARY KEY,
            evidence_type TEXT NOT NULL CHECK (
                evidence_type IN (
                    'test_loopback',
                    'wecom_private',
                    'wecom_group',
                    'wecom_welcome',
                    'wecom_tag',
                    'wecom_profile',
                    'wecom_contact_detail',
                    'wecom_media',
                    'listener_reconnect',
                    'worker_restart',
                    'database_reconnect'
                )
            ),
            release_sha TEXT NOT NULL CHECK (length(release_sha) = 40),
            active_generation INTEGER NOT NULL CHECK (active_generation > 0),
            policy_version TEXT NOT NULL,
            execution_id TEXT NOT NULL DEFAULT '',
            job_id BIGINT,
            status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_validation_release_type_created
        ON queue_runtime_validation_evidence (release_sha, evidence_type, created_at DESC)
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_validation_evidence_append_only
        ON queue_runtime_validation_evidence
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_validation_evidence_append_only
        BEFORE UPDATE OR DELETE ON queue_runtime_validation_evidence
        FOR EACH ROW
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_validation_evidence_reject_truncate
        ON queue_runtime_validation_evidence
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_validation_evidence_reject_truncate
        BEFORE TRUNCATE ON queue_runtime_validation_evidence
        FOR EACH STATEMENT
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_soak_run (
            soak_id TEXT PRIMARY KEY,
            release_sha TEXT NOT NULL CHECK (length(release_sha) = 40),
            migration_revision TEXT NOT NULL,
            active_generation INTEGER NOT NULL CHECK (active_generation > 0),
            policy_version TEXT NOT NULL,
            external_claim_scope TEXT NOT NULL CHECK (external_claim_scope = 'allowlisted'),
            configuration_hash TEXT NOT NULL CHECK (length(configuration_hash) = 64),
            status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'invalidated')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            required_until TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            invalidated_at TIMESTAMPTZ,
            invalidated_reason TEXT NOT NULL DEFAULT '',
            baseline_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            latest_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            row_version BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_soak_single_running
        ON queue_runtime_soak_run ((1))
        WHERE status = 'running'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_soak_snapshot (
            snapshot_id TEXT PRIMARY KEY,
            soak_id TEXT NOT NULL REFERENCES queue_runtime_soak_run(soak_id),
            release_sha TEXT NOT NULL CHECK (length(release_sha) = 40),
            configuration_hash TEXT NOT NULL CHECK (length(configuration_hash) = 64),
            ok BOOLEAN NOT NULL,
            metrics_json JSONB NOT NULL,
            violations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_soak_snapshot_run_captured
        ON queue_runtime_soak_snapshot (soak_id, captured_at DESC)
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_soak_snapshot_append_only
        ON queue_runtime_soak_snapshot
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_soak_snapshot_append_only
        BEFORE UPDATE OR DELETE ON queue_runtime_soak_snapshot
        FOR EACH ROW
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_queue_runtime_soak_snapshot_reject_truncate
        ON queue_runtime_soak_snapshot
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_queue_runtime_soak_snapshot_reject_truncate
        BEFORE TRUNCATE ON queue_runtime_soak_snapshot
        FOR EACH STATEMENT
        EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        """
    )


def downgrade() -> None:
    # Validation evidence is intentionally retained across application rollback.
    pass
