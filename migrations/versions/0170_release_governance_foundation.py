"""Add forward-compatible release governance and external-effect provenance.

Revision ID: 0170_release_governance_foundation
Revises: 0169_operation_cycle_codex_actions
"""

from __future__ import annotations

from alembic import op


revision = "0170_release_governance_foundation"
down_revision = "0169_operation_cycle_codex_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_release_compatibility (
            revision TEXT PRIMARY KEY,
            parent_revision TEXT NOT NULL DEFAULT '',
            change_kind TEXT NOT NULL
                CHECK (change_kind IN ('baseline', 'expand', 'contract')),
            compatibility_epoch INTEGER NOT NULL CHECK (compatibility_epoch > 0),
            previous_runtime_compatible BOOLEAN NOT NULL,
            downgrade_policy TEXT NOT NULL
                CHECK (downgrade_policy IN ('forward_only', 'reversible')),
            release_sha TEXT NOT NULL DEFAULT 'unknown'
                CHECK (release_sha = 'unknown' OR release_sha ~ '^[0-9a-f]{40}$'),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(metadata_json) = 'object'),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        INSERT INTO schema_release_compatibility (
            revision, parent_revision, change_kind, compatibility_epoch,
            previous_runtime_compatible, downgrade_policy, metadata_json
        ) VALUES
            (
                '0169_operation_cycle_codex_actions', '', 'baseline', 1,
                TRUE, 'forward_only', '{"history":"baseline_before_release_governance"}'::jsonb
            ),
            (
                '0170_release_governance_foundation',
                '0169_operation_cycle_codex_actions', 'expand', 1,
                TRUE, 'forward_only', '{"schema_version":"migration_compatibility.v1"}'::jsonb
            )
        ON CONFLICT (revision) DO NOTHING
        """
    )
    op.execute(
        """
        ALTER TABLE external_effect_job
        ADD COLUMN IF NOT EXISTS created_release_sha TEXT NOT NULL DEFAULT 'unknown',
        ADD COLUMN IF NOT EXISTS processed_release_sha TEXT NOT NULL DEFAULT 'unknown',
        ADD COLUMN IF NOT EXISTS health_classification_code TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        ALTER TABLE external_effect_job
        DROP CONSTRAINT IF EXISTS ck_external_effect_job_created_release_sha,
        ADD CONSTRAINT ck_external_effect_job_created_release_sha
            CHECK (created_release_sha = 'unknown' OR created_release_sha ~ '^[0-9a-f]{40}$') NOT VALID,
        DROP CONSTRAINT IF EXISTS ck_external_effect_job_processed_release_sha,
        ADD CONSTRAINT ck_external_effect_job_processed_release_sha
            CHECK (processed_release_sha = 'unknown' OR processed_release_sha ~ '^[0-9a-f]{40}$') NOT VALID
        """
    )
    op.execute("ALTER TABLE external_effect_job VALIDATE CONSTRAINT ck_external_effect_job_created_release_sha")
    op.execute("ALTER TABLE external_effect_job VALIDATE CONSTRAINT ck_external_effect_job_processed_release_sha")
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        ADD COLUMN IF NOT EXISTS processed_release_sha TEXT NOT NULL DEFAULT 'unknown'
        """
    )
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        DROP CONSTRAINT IF EXISTS ck_external_effect_attempt_processed_release_sha,
        ADD CONSTRAINT ck_external_effect_attempt_processed_release_sha
            CHECK (processed_release_sha = 'unknown' OR processed_release_sha ~ '^[0-9a-f]{40}$') NOT VALID
        """
    )
    op.execute("ALTER TABLE external_effect_attempt VALIDATE CONSTRAINT ck_external_effect_attempt_processed_release_sha")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_external_effect_job_release_health
        ON external_effect_job (processed_release_sha, status, updated_at DESC)
        WHERE status IN ('failed_retryable', 'failed_terminal', 'blocked', 'unknown_after_dispatch')
        """
    )


def downgrade() -> None:
    # Forward-only release evidence must survive an application rollback.
    pass
