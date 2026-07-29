"""add campaign preparations and operation-cycle strategy governance.

Revision ID: 0156_campaign_preparation_context
Revises: 0155_private_message_contact_absence_ack_scope
"""

from __future__ import annotations

from alembic import op


revision = "0156_campaign_preparation_context"
down_revision = "0155_private_message_contact_absence_ack_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE operation_cycle_strategy_versions
        ADD COLUMN IF NOT EXISTS governance_status TEXT NOT NULL DEFAULT 'observed'
            CHECK (governance_status IN ('observed','legacy_confirmed','confirmed')),
        ADD COLUMN IF NOT EXISTS confirmed_by TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS confirmation_note TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        UPDATE operation_cycle_strategy_versions version
        SET governance_status = 'legacy_confirmed',
            confirmed_at = COALESCE(version.effective_from, version.created_at)
        FROM operation_cycle_strategies strategy
        WHERE version.strategy_id = strategy.id
          AND version.version = strategy.current_version
          AND version.governance_status = 'observed'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_strategy_version_documents (
            id BIGSERIAL PRIMARY KEY,
            strategy_version_id BIGINT NOT NULL
                REFERENCES operation_cycle_strategy_versions(id) ON DELETE CASCADE,
            schema_version TEXT NOT NULL
                CHECK (schema_version = 'operation_cycle_strategy_document_pack.v1'),
            execution_guide_markdown TEXT NOT NULL DEFAULT '',
            execution_guide_sha256 TEXT NOT NULL CHECK (length(execution_guide_sha256) = 64),
            execution_guide_generated_at TIMESTAMPTZ,
            execution_guide_source TEXT NOT NULL DEFAULT '',
            copy_guide_markdown TEXT NOT NULL DEFAULT '',
            copy_guide_sha256 TEXT NOT NULL CHECK (length(copy_guide_sha256) = 64),
            copy_guide_generated_at TIMESTAMPTZ,
            copy_guide_source TEXT NOT NULL DEFAULT '',
            measurement_guide_markdown TEXT NOT NULL DEFAULT '',
            measurement_guide_sha256 TEXT NOT NULL CHECK (length(measurement_guide_sha256) = 64),
            measurement_guide_generated_at TIMESTAMPTZ,
            measurement_guide_source TEXT NOT NULL DEFAULT '',
            execution_contract_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(execution_contract_json) = 'object'),
            document_pack_hash TEXT NOT NULL CHECK (length(document_pack_hash) = 64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_strategy_version_documents UNIQUE (strategy_version_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_strategy_change_proposals (
            proposal_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            strategy_id BIGINT NOT NULL REFERENCES operation_cycle_strategies(id) ON DELETE CASCADE,
            base_strategy_version INTEGER NOT NULL CHECK (base_strategy_version > 0),
            source_run_key TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL,
            proposal_hash TEXT NOT NULL CHECK (length(proposal_hash) = 64),
            proposal_json JSONB NOT NULL CHECK (jsonb_typeof(proposal_json) = 'object'),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','accepted','rejected')),
            submitted_by TEXT NOT NULL DEFAULT '',
            client_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_by TEXT NOT NULL DEFAULT '',
            decided_at TIMESTAMPTZ,
            decision_note TEXT NOT NULL DEFAULT '',
            applied_strategy_version INTEGER,
            CONSTRAINT uq_operation_cycle_strategy_proposal_idempotency
                UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_strategy_proposals_lookup "
        "ON operation_cycle_strategy_change_proposals (strategy_id, status, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS external_campaign_preparations (
            preparation_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            idempotency_key TEXT NOT NULL,
            preparation_hash TEXT NOT NULL CHECK (length(preparation_hash) = 64),
            source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
            strategy_key TEXT NOT NULL,
            strategy_version INTEGER NOT NULL CHECK (strategy_version > 0),
            context_hash TEXT NOT NULL CHECK (length(context_hash) = 64),
            run_key TEXT NOT NULL DEFAULT '',
            owner_userid TEXT NOT NULL,
            scheduled_for TIMESTAMPTZ NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            display_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'preparing'
                CHECK (status IN ('preparing','ready','blocked','committed','expired')),
            input_count INTEGER NOT NULL DEFAULT 0 CHECK (input_count >= 0),
            eligible_count INTEGER NOT NULL DEFAULT 0 CHECK (eligible_count >= 0),
            skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
            counts_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(counts_json) = 'object'),
            blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb
                CHECK (jsonb_typeof(blockers_json) = 'array'),
            timings_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(timings_json) = 'object'),
            sql_batch_count INTEGER NOT NULL DEFAULT 0 CHECK (sql_batch_count >= 0),
            plan_id TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ NOT NULL,
            committed_at TIMESTAMPTZ,
            CONSTRAINT uq_external_campaign_preparation_idempotency UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS external_campaign_preparation_recipients (
            id BIGSERIAL PRIMARY KEY,
            preparation_id TEXT NOT NULL REFERENCES external_campaign_preparations(preparation_id) ON DELETE CASCADE,
            row_key TEXT NOT NULL,
            identity_external_userid TEXT NOT NULL DEFAULT '',
            identity_unionid TEXT NOT NULL DEFAULT '',
            identity_mobile_normalized TEXT NOT NULL DEFAULT '',
            resolved_external_userid TEXT NOT NULL DEFAULT '',
            resolved_unionid TEXT NOT NULL DEFAULT '',
            resolved_owner_userid TEXT NOT NULL DEFAULT '',
            identity_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (identity_status IN ('pending','resolved','unmatched','identity_conflict','duplicate')),
            policy_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (policy_status IN ('pending','eligible','not_following','dnd','frequency_capped','duplicate_touch')),
            row_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (row_status IN ('pending','eligible','skipped','blocked')),
            reason_code TEXT NOT NULL DEFAULT '',
            content_text TEXT NOT NULL,
            dynamic_card_json JSONB NOT NULL CHECK (jsonb_typeof(dynamic_card_json) = 'object'),
            analysis_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(analysis_json) = 'object'),
            row_hash TEXT NOT NULL CHECK (length(row_hash) = 64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_external_campaign_preparation_row UNIQUE (preparation_id, row_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_campaign_preparation_recipient_status "
        "ON external_campaign_preparation_recipients (preparation_id, row_status, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_campaign_preparation_card_id "
        "ON external_campaign_preparation_recipients "
        "(preparation_id, (dynamic_card_json->>'card_id'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_campaign_preparation_cid "
        "ON external_campaign_preparation_recipients "
        "(preparation_id, (dynamic_card_json->>'cid'))"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_plan_links (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            strategy_key TEXT NOT NULL,
            strategy_version INTEGER NOT NULL CHECK (strategy_version > 0),
            run_key TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            preparation_id TEXT NOT NULL DEFAULT '',
            approved_at TIMESTAMPTZ,
            task_count INTEGER NOT NULL DEFAULT 0 CHECK (task_count >= 0),
            finalized_count INTEGER NOT NULL DEFAULT 0 CHECK (finalized_count >= 0),
            sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            last_delivery_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_plan_link UNIQUE (tenant_id, plan_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_plan_links_run "
        "ON operation_cycle_plan_links (tenant_id, strategy_key, run_key)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_system_facts (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            plan_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_key TEXT NOT NULL,
            fact_json JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(fact_json) = 'object'),
            occurred_at TIMESTAMPTZ,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_system_fact UNIQUE (tenant_id, event_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_system_facts_plan "
        "ON operation_cycle_system_facts (tenant_id, plan_id, occurred_at, id)"
    )


def downgrade() -> None:
    # Rollback is feature-flag/code-only.  The immutable governance history and
    # preparation idempotency records deliberately survive a release rollback.
    pass
