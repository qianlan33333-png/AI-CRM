"""Durable media dependencies for channel welcome messages.

Revision ID: 0130_welcome_media_dependencies
Revises: 0129_identity_customer_event_driven
"""

from __future__ import annotations

from alembic import op


revision = "0130_welcome_media_dependencies"
down_revision = "0129_identity_customer_event_driven"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_welcome_effect_graph (
            id BIGSERIAL PRIMARY KEY,
            execution_id TEXT NOT NULL,
            parent_execution_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL,
            channel_id BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'waiting_dependencies',
            final_effect_job_id BIGINT REFERENCES external_effect_job(id) ON DELETE SET NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            cancel_reason TEXT NOT NULL DEFAULT '',
            cancelled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_channel_welcome_graph_execution UNIQUE (execution_id),
            CONSTRAINT uq_channel_welcome_graph_idempotency UNIQUE (idempotency_key),
            CONSTRAINT ck_channel_welcome_graph_status CHECK (
                status IN ('waiting_dependencies', 'ready', 'cancelled', 'terminal')
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_welcome_effect_dependency (
            id BIGSERIAL PRIMARY KEY,
            graph_id BIGINT NOT NULL REFERENCES channel_welcome_effect_graph(id) ON DELETE CASCADE,
            material_key TEXT NOT NULL,
            msgtype TEXT NOT NULL,
            library_kind TEXT NOT NULL,
            library_material_id BIGINT NOT NULL,
            upload_kind TEXT NOT NULL,
            attachment_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            prerequisite_effect_job_id BIGINT NOT NULL REFERENCES external_effect_job(id) ON DELETE CASCADE,
            dependent_effect_job_id BIGINT NOT NULL REFERENCES external_effect_job(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'waiting',
            provider_media_id TEXT NOT NULL DEFAULT '',
            completed_attempt_id TEXT NOT NULL DEFAULT '',
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_channel_welcome_dependency_material UNIQUE (graph_id, material_key),
            CONSTRAINT uq_channel_welcome_dependency_jobs UNIQUE (
                prerequisite_effect_job_id, dependent_effect_job_id
            ),
            CONSTRAINT ck_channel_welcome_dependency_msgtype CHECK (
                msgtype IN ('image', 'file', 'miniprogram')
            ),
            CONSTRAINT ck_channel_welcome_dependency_kind CHECK (
                library_kind IN ('image', 'attachment', 'miniprogram')
            ),
            CONSTRAINT ck_channel_welcome_dependency_status CHECK (
                status IN ('waiting', 'succeeded', 'failed', 'cancelled')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_channel_welcome_graph_active
        ON channel_welcome_effect_graph (created_at, id)
        WHERE status IN ('waiting_dependencies', 'ready')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_channel_welcome_dependency_waiting
        ON channel_welcome_effect_dependency (prerequisite_effect_job_id, graph_id)
        WHERE status = 'waiting'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_channel_welcome_dependency_waiting")
    op.execute("DROP INDEX IF EXISTS idx_channel_welcome_graph_active")
    op.execute("DROP TABLE IF EXISTS channel_welcome_effect_dependency")
    op.execute("DROP TABLE IF EXISTS channel_welcome_effect_graph")
