"""Group Ops durable effect graphs and media dependencies.

Revision ID: 0127_group_ops_durable_effect_graph
Revises: 0126_postgres_execution_runtime
"""

from __future__ import annotations

from alembic import op


revision = "0127_group_ops_durable_effect_graph"
down_revision = "0126_postgres_execution_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_group_ops_effect_graph (
            id BIGSERIAL PRIMARY KEY,
            execution_id TEXT NOT NULL,
            parent_execution_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            plan_id BIGINT NOT NULL DEFAULT 0,
            node_id BIGINT NOT NULL DEFAULT 0,
            source_version BIGINT NOT NULL DEFAULT 1,
            version_fingerprint TEXT NOT NULL DEFAULT '',
            scheduled_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting_dependencies',
            final_effect_job_id BIGINT,
            actor_id TEXT NOT NULL DEFAULT '',
            superseded_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_group_ops_effect_graph_execution UNIQUE (execution_id),
            CONSTRAINT uq_group_ops_effect_graph_idempotency UNIQUE (idempotency_key),
            CONSTRAINT ck_group_ops_effect_graph_source_kind CHECK (
                source_kind IN ('direct_send', 'plan_node', 'trusted_webhook', 'webhook_action')
            ),
            CONSTRAINT ck_group_ops_effect_graph_status CHECK (
                status IN ('waiting_dependencies', 'ready', 'superseded', 'cancelled', 'terminal')
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_group_ops_effect_material (
            id BIGSERIAL PRIMARY KEY,
            graph_id BIGINT NOT NULL REFERENCES automation_group_ops_effect_graph(id) ON DELETE CASCADE,
            material_key TEXT NOT NULL,
            role TEXT NOT NULL,
            library_kind TEXT NOT NULL DEFAULT 'image',
            library_material_id BIGINT NOT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_group_ops_effect_material_key UNIQUE (graph_id, material_key),
            CONSTRAINT ck_group_ops_effect_material_role CHECK (
                role IN ('image', 'file', 'miniprogram', 'card_cover')
            ),
            CONSTRAINT ck_group_ops_effect_material_kind CHECK (
                library_kind IN ('image', 'attachment', 'miniprogram')
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_group_ops_effect_dependency (
            id BIGSERIAL PRIMARY KEY,
            graph_id BIGINT NOT NULL REFERENCES automation_group_ops_effect_graph(id) ON DELETE CASCADE,
            material_id BIGINT NOT NULL REFERENCES automation_group_ops_effect_material(id) ON DELETE CASCADE,
            prerequisite_effect_job_id BIGINT NOT NULL,
            dependent_effect_job_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            provider_media_id TEXT NOT NULL DEFAULT '',
            completed_attempt_id TEXT NOT NULL DEFAULT '',
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_group_ops_effect_dependency_prerequisite UNIQUE (
                prerequisite_effect_job_id, dependent_effect_job_id
            ),
            CONSTRAINT ck_group_ops_effect_dependency_status CHECK (
                status IN ('waiting', 'succeeded', 'failed', 'cancelled')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_ops_effect_graph_plan_version
        ON automation_group_ops_effect_graph (
            plan_id, node_id, source_kind, source_version DESC, scheduled_at, id DESC
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_ops_effect_graph_active_schedule
        ON automation_group_ops_effect_graph (scheduled_at, id)
        WHERE status IN ('waiting_dependencies', 'ready')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_ops_effect_dependency_waiting
        ON automation_group_ops_effect_dependency (prerequisite_effect_job_id, graph_id)
        WHERE status = 'waiting'
        """
    )
    op.execute(
        """
        ALTER TABLE broadcast_jobs
        ADD COLUMN IF NOT EXISTS external_effect_job_id BIGINT,
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS execution_owner TEXT NOT NULL DEFAULT 'legacy_frozen'
        """
    )
    op.execute("ALTER TABLE broadcast_jobs DROP CONSTRAINT IF EXISTS broadcast_jobs_status_check")
    op.execute(
        """
        ALTER TABLE broadcast_jobs
        ADD CONSTRAINT broadcast_jobs_status_check CHECK (status IN (
            'waiting_approval', 'queued', 'claimed', 'dispatching', 'delegated',
            'sent', 'simulated', 'failed', 'failed_retryable', 'failed_terminal',
            'blocked', 'cancelled', 'unknown_after_dispatch'
        ))
        """
    )
    op.execute("ALTER TABLE cloud_broadcast_plan_recipients DROP CONSTRAINT IF EXISTS cloud_broadcast_plan_recipients_send_status_check")
    op.execute(
        """
        ALTER TABLE cloud_broadcast_plan_recipients
        ADD CONSTRAINT cloud_broadcast_plan_recipients_send_status_check CHECK (send_status IN (
            'pending', 'queued', 'sending', 'dispatching', 'delegated', 'sent',
            'simulated', 'failed', 'failed_retryable', 'failed_terminal', 'blocked',
            'cancelled', 'unknown_after_dispatch'
        ))
        """
    )
    op.execute("ALTER TABLE cloud_broadcast_plan_recipient_messages DROP CONSTRAINT IF EXISTS cloud_broadcast_plan_recipient_messages_status_check")
    op.execute(
        """
        ALTER TABLE cloud_broadcast_plan_recipient_messages
        ADD CONSTRAINT cloud_broadcast_plan_recipient_messages_status_check CHECK (status IN (
            'pending', 'queued', 'dispatching', 'delegated', 'sent', 'simulated',
            'failed', 'failed_retryable', 'failed_terminal', 'blocked', 'skipped',
            'unknown_after_dispatch'
        ))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_external_effect_owner
        ON broadcast_jobs (external_effect_job_id)
        WHERE external_effect_job_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("UPDATE broadcast_jobs SET status = 'queued' WHERE status = 'delegated'")
    op.execute("UPDATE cloud_broadcast_plan_recipients SET send_status = 'queued' WHERE send_status = 'delegated'")
    op.execute("UPDATE cloud_broadcast_plan_recipient_messages SET status = 'queued' WHERE status = 'delegated'")
    op.execute("ALTER TABLE broadcast_jobs DROP CONSTRAINT IF EXISTS broadcast_jobs_status_check")
    op.execute(
        """
        ALTER TABLE broadcast_jobs
        ADD CONSTRAINT broadcast_jobs_status_check CHECK (status IN (
            'waiting_approval', 'queued', 'claimed', 'dispatching', 'sent', 'simulated',
            'failed', 'failed_retryable', 'failed_terminal', 'blocked', 'cancelled',
            'unknown_after_dispatch'
        ))
        """
    )
    op.execute("ALTER TABLE cloud_broadcast_plan_recipients DROP CONSTRAINT IF EXISTS cloud_broadcast_plan_recipients_send_status_check")
    op.execute(
        """
        ALTER TABLE cloud_broadcast_plan_recipients
        ADD CONSTRAINT cloud_broadcast_plan_recipients_send_status_check CHECK (send_status IN (
            'pending', 'queued', 'sending', 'dispatching', 'sent', 'simulated', 'failed',
            'failed_retryable', 'failed_terminal', 'blocked', 'cancelled',
            'unknown_after_dispatch'
        ))
        """
    )
    op.execute("ALTER TABLE cloud_broadcast_plan_recipient_messages DROP CONSTRAINT IF EXISTS cloud_broadcast_plan_recipient_messages_status_check")
    op.execute(
        """
        ALTER TABLE cloud_broadcast_plan_recipient_messages
        ADD CONSTRAINT cloud_broadcast_plan_recipient_messages_status_check CHECK (status IN (
            'pending', 'queued', 'dispatching', 'sent', 'simulated', 'failed',
            'failed_retryable', 'failed_terminal', 'blocked', 'skipped',
            'unknown_after_dispatch'
        ))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_broadcast_jobs_external_effect_owner")
    op.execute("ALTER TABLE broadcast_jobs DROP COLUMN IF EXISTS execution_owner")
    op.execute("ALTER TABLE broadcast_jobs DROP COLUMN IF EXISTS execution_id")
    op.execute("ALTER TABLE broadcast_jobs DROP COLUMN IF EXISTS external_effect_job_id")
    op.execute("DROP INDEX IF EXISTS idx_group_ops_effect_dependency_waiting")
    op.execute("DROP INDEX IF EXISTS idx_group_ops_effect_graph_active_schedule")
    op.execute("DROP INDEX IF EXISTS idx_group_ops_effect_graph_plan_version")
    op.execute("DROP TABLE IF EXISTS automation_group_ops_effect_dependency")
    op.execute("DROP TABLE IF EXISTS automation_group_ops_effect_material")
    op.execute("DROP TABLE IF EXISTS automation_group_ops_effect_graph")
