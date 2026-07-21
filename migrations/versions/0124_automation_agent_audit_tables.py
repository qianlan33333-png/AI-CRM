"""repair canonical automation agent audit tables on upgraded databases.

Revision ID: 0124_agent_audit_tables
Revises: 0123_required_physical_schema_repair

The post-legacy baseline already contains the canonical table definitions, but
databases that were Alembic-managed before that baseline was introduced can be
stamped past the historical owners while the two audit tables are absent.  The
runtime writes both tables directly, so their absence makes the admin read
model and AI audience agent execution fail at runtime.

``run_id`` remains a logical correlation key.  The deployed
``automation_agent_run.run_id`` contract is not unique, and audit evidence may
outlive a run projection, so this repair intentionally does not invent a
foreign key that could reject or delete historical audit rows.
"""

from __future__ import annotations

from alembic import op


revision = "0124_agent_audit_tables"
down_revision = "0123_required_physical_schema_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_agent_output (
            id BIGSERIAL PRIMARY KEY,
            output_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            userid TEXT NOT NULL DEFAULT '',
            unionid TEXT NOT NULL DEFAULT '',
            agent_code TEXT NOT NULL DEFAULT '',
            output_type TEXT NOT NULL DEFAULT '',
            raw_output_text TEXT NOT NULL DEFAULT '',
            normalized_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            rendered_output_text TEXT NOT NULL DEFAULT '',
            target_agent_code TEXT NOT NULL DEFAULT '',
            target_pool TEXT NOT NULL DEFAULT '',
            confidence NUMERIC NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            need_human_review BOOLEAN NOT NULL DEFAULT FALSE,
            applied_status TEXT NOT NULL DEFAULT '',
            adopted_by TEXT NOT NULL DEFAULT '',
            adopted_action TEXT NOT NULL DEFAULT '',
            outcome_status TEXT NOT NULL DEFAULT '',
            outcome_value TEXT NOT NULL DEFAULT '',
            revision_of_output_id TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        ALTER TABLE automation_agent_output
            ADD COLUMN IF NOT EXISTS output_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS userid TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS output_type TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS raw_output_text TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS normalized_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS rendered_output_text TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS target_agent_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS target_pool TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS confidence NUMERIC NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS need_human_review BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS applied_status TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS adopted_by TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS adopted_action TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS outcome_status TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS outcome_value TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS revision_of_output_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_automation_agent_output_run_created
        ON automation_agent_output (run_id, created_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_automation_agent_output_unionid
        ON automation_agent_output (unionid)
        WHERE unionid <> ''
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_agent_llm_call_log (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL DEFAULT '',
            agent_code TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            prompt_hash TEXT NOT NULL DEFAULT '',
            request_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            response_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        ALTER TABLE automation_agent_llm_call_log
            ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS model TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS prompt_hash TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS request_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS response_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS latency_ms INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_automation_agent_llm_call_log_agent_created
        ON automation_agent_llm_call_log (agent_code, created_at DESC, id DESC)
        """
    )


def downgrade() -> None:
    # These tables contain durable provider and operator audit evidence.  Code
    # rollback must leave additive schema and all historical rows intact.
    return None
