"""Add AI Audience groups and enforce one-to-one automation binding.

Revision ID: 0162_ai_audience_groups_binding
Revises: 0161_reconcile_archive_job_run_ledger
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import (
    inspect_automation_bindings,
)


revision = "0162_ai_audience_groups_binding"
down_revision = "0161_reconcile_archive_job_run_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    report = inspect_automation_bindings(connection)
    if not report.ok:
        raise RuntimeError(
            "automation_binding_precheck_failed:"
            + json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_audience_package_group (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_ai_audience_package_group_name_nonempty
                CHECK (BTRIM(name) <> '')
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_audience_package_group_name_ci
        ON ai_audience_package_group (LOWER(BTRIM(name)))
        """
    )
    op.execute(
        """
        ALTER TABLE ai_audience_package
        ADD COLUMN IF NOT EXISTS group_id BIGINT
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_ai_audience_package_group'
            ) THEN
                ALTER TABLE ai_audience_package
                ADD CONSTRAINT fk_ai_audience_package_group
                FOREIGN KEY (group_id)
                REFERENCES ai_audience_package_group(id)
                ON DELETE RESTRICT;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_audience_package_group_active
        ON ai_audience_package (group_id, updated_at DESC, id DESC)
        WHERE status <> 'archived'
        """
    )

    for binding in report.bindings:
        connection.execute(
            text(
                """
                UPDATE automation_agent_runtime_config
                SET bound_package_key = :package_key,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :automation_id
                """
            ),
            {
                "automation_id": int(binding["automation_id"]),
                "package_key": str(binding["package_key"]),
            },
        )
        webhook_url = f"/api/ai/agents/{binding['agent_code']}/audience-webhook"
        subscription_status = "active" if binding["automation_status"] == "active" else "paused"
        subscription_ids = list(binding.get("subscription_ids") or [])
        if subscription_ids:
            connection.execute(
                text(
                    """
                    UPDATE ai_audience_outbound_subscription
                    SET status = :status,
                        trigger_event_type = 'entered',
                        dispatch_mode = 'per_run',
                        target_type = 'webhook',
                        webhook_url = :webhook_url,
                        headers_json = '{}'::jsonb,
                        payload_template_json = '{}'::jsonb,
                        execution_mode = 'execute',
                        requires_approval = FALSE,
                        max_attempts = 5,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :subscription_id
                    """
                ),
                {
                    "subscription_id": int(subscription_ids[0]),
                    "status": subscription_status,
                    "webhook_url": webhook_url,
                },
            )
        else:
            connection.execute(
                text(
                    """
                    INSERT INTO ai_audience_outbound_subscription (
                        package_id, status, trigger_event_type, dispatch_mode, target_type,
                        webhook_url, headers_json, payload_template_json, execution_mode,
                        requires_approval, max_attempts, created_at, updated_at
                    )
                    VALUES (
                        :package_id, :status, 'entered', 'per_run', 'webhook',
                        :webhook_url, '{}'::jsonb, '{}'::jsonb, 'execute',
                        FALSE, 5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "package_id": int(binding["package_id"]),
                    "status": subscription_status,
                    "webhook_url": webhook_url,
                },
            )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_agent_runtime_bound_package
        ON automation_agent_runtime_config (bound_package_key)
        WHERE status <> 'archived' AND BTRIM(bound_package_key) <> ''
        """
    )


def downgrade() -> None:
    # Release rollback uses the previous application SHA. The additive table,
    # nullable column, compatibility columns and one-to-one guard stay in place
    # so rollback never requires destructive database mutation.
    pass
