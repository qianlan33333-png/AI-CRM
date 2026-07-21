"""PostgreSQL-native execution runtime control plane.

Revision ID: 0126_postgres_execution_runtime
Revises: 0125_execution_runtime_correctness
"""

from __future__ import annotations

from alembic import op


revision = "0126_postgres_execution_runtime"
down_revision = "0125_execution_runtime_correctness"
branch_labels = None
depends_on = None


LANE_CAPACITIES = {
    "internal_general": 4,
    "internal_financial": 1,
    "webhook_inbox": 4,
    "wecom_interactive": 4,
    "wecom_bulk": 1,
    "wecom_media": 2,
    "outbound_webhook": 4,
}


def _add_execution_columns() -> None:
    op.execute(
        """
        ALTER TABLE external_effect_job
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'wecom_interactive',
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ordering_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS fairness_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS rate_scope_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS worker_generation BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL DEFAULT 'queue-v1',
        ADD COLUMN IF NOT EXISTS provider_call_started_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        UPDATE external_effect_job
        SET execution_id = CASE
                WHEN execution_id = '' THEN 'exe_external_' || id::text
                ELSE execution_id
            END,
            available_at = COALESCE(
                available_at,
                GREATEST(scheduled_at, COALESCE(next_retry_at, scheduled_at))
            ),
            lane = CASE
                WHEN effect_type = 'wecom.media.upload' THEN 'wecom_media'
                WHEN effect_type = 'wecom.message.broadcast.send' THEN 'wecom_bulk'
                WHEN effect_type LIKE 'webhook.%'
                  OR effect_type IN ('feishu.webhook.notify', 'openclaw.context.push')
                    THEN 'outbound_webhook'
                ELSE COALESCE(NULLIF(lane, ''), 'wecom_interactive')
            END,
            ordering_key = CASE
                WHEN ordering_key = '' THEN COALESCE(NULLIF(target_id, ''), 'job:' || id::text)
                ELSE ordering_key
            END,
            fairness_key = CASE
                WHEN fairness_key = '' THEN COALESCE(NULLIF(business_id, ''), NULLIF(target_id, ''), 'default')
                ELSE fairness_key
            END,
            rate_scope_key = CASE
                WHEN rate_scope_key = ''
                    THEN adapter_name || ':' || operation || ':' || COALESCE(NULLIF(tenant_id, ''), 'aicrm')
                ELSE rate_scope_key
            END
        """
    )
    op.execute(
        """
        ALTER TABLE external_effect_attempt
        ADD COLUMN IF NOT EXISTS lease_token TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS request_hash TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS provider_call_started_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS worker_generation BIGINT NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        UPDATE external_effect_attempt attempt
        SET status = 'unknown_after_dispatch',
            response_summary_json = COALESCE(attempt.response_summary_json, '{}'::jsonb)
                || jsonb_build_object(
                    'provider_result_received', FALSE,
                    'historical_freeze_orphan', TRUE
                ),
            error_code = CASE
                WHEN attempt.error_code = '' THEN 'historical_freeze_orphan'
                ELSE attempt.error_code
            END,
            error_message = CASE
                WHEN attempt.error_message = ''
                THEN 'Open provider attempt was frozen after its job left dispatching state.'
                ELSE attempt.error_message
            END,
            completed_at = COALESCE(attempt.completed_at, CURRENT_TIMESTAMP)
        FROM external_effect_job job
        WHERE attempt.job_id = job.id
          AND attempt.status = 'dispatching'
          AND job.status <> 'dispatching'
        """
    )
    op.execute(
        """
        ALTER TABLE internal_event
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        UPDATE internal_event
        SET execution_id = CASE
            WHEN execution_id = '' THEN 'exe_internal_' || id::text
            ELSE execution_id
        END
        """
    )
    op.execute(
        """
        ALTER TABLE internal_event_consumer_run
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'internal_general',
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ordering_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS fairness_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS worker_generation BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL DEFAULT 'queue-v1'
        """
    )
    op.execute(
        """
        UPDATE internal_event_consumer_run run
        SET execution_id = CASE
                WHEN run.execution_id = '' THEN 'exe_internal_run_' || run.id::text
                ELSE run.execution_id
            END,
            parent_execution_id = CASE
                WHEN run.parent_execution_id = '' THEN event.execution_id
                ELSE run.parent_execution_id
            END,
            lane = CASE
                WHEN event.event_type LIKE 'payment.%'
                  OR event.event_type LIKE 'refund.%'
                  OR event.event_type LIKE 'order.%'
                    THEN 'internal_financial'
                ELSE 'internal_general'
            END,
            available_at = COALESCE(run.available_at, run.next_retry_at, run.created_at),
            ordering_key = CASE
                WHEN run.ordering_key = '' THEN event.aggregate_type || ':' || event.aggregate_id
                ELSE run.ordering_key
            END,
            fairness_key = CASE
                WHEN run.fairness_key = '' THEN event.event_type
                ELSE run.fairness_key
            END
        FROM internal_event event
        WHERE event.event_id = run.event_id
        """
    )
    op.execute(
        """
        ALTER TABLE internal_event_outbox
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'internal_general',
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ordering_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS fairness_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS worker_generation BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL DEFAULT 'queue-v1'
        """
    )
    op.execute(
        """
        UPDATE internal_event_outbox
        SET execution_id = CASE
                WHEN execution_id = '' THEN 'exe_internal_outbox_' || id::text
                ELSE execution_id
            END,
            available_at = COALESCE(available_at, next_retry_at, occurred_at),
            lane = CASE
                WHEN event_type LIKE 'payment.%'
                  OR event_type LIKE 'refund.%'
                  OR event_type LIKE 'order.%'
                    THEN 'internal_financial'
                ELSE 'internal_general'
            END,
            ordering_key = CASE
                WHEN ordering_key = '' THEN aggregate_type || ':' || aggregate_id
                ELSE ordering_key
            END,
            fairness_key = CASE WHEN fairness_key = '' THEN event_type ELSE fairness_key END
        """
    )
    op.execute(
        """
        ALTER TABLE webhook_inbox
        ADD COLUMN IF NOT EXISTS execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS parent_execution_id TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'webhook_inbox',
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ordering_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS fairness_key TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lease_token TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS worker_generation BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL DEFAULT 'queue-v1'
        """
    )
    op.execute(
        """
        UPDATE webhook_inbox
        SET execution_id = CASE
                WHEN execution_id = '' THEN 'exe_webhook_' || id::text
                ELSE execution_id
            END,
            available_at = COALESCE(available_at, next_retry_at, received_at),
            ordering_key = CASE
                WHEN ordering_key = ''
                    THEN provider || ':' || COALESCE(NULLIF(corp_id, ''), NULLIF(external_event_id, ''), id::text)
                ELSE ordering_key
            END,
            fairness_key = CASE
                WHEN fairness_key = '' THEN provider || ':' || event_family
                ELSE fairness_key
            END
        """
    )
    for table in (
        "external_effect_job",
        "internal_event_consumer_run",
        "internal_event_outbox",
        "webhook_inbox",
    ):
        op.execute(
            f"""
            ALTER TABLE {table}
            ALTER COLUMN available_at SET DEFAULT CURRENT_TIMESTAMP,
            ALTER COLUMN available_at SET NOT NULL
            """
        )
    lane_constraints = {
        "external_effect_job": (
            "ck_external_effect_job_runtime_lane",
            "lane IN ('wecom_interactive', 'wecom_bulk', 'wecom_media', 'outbound_webhook')",
        ),
        "internal_event_consumer_run": (
            "ck_internal_event_consumer_run_runtime_lane",
            "lane IN ('internal_general', 'internal_financial')",
        ),
        "internal_event_outbox": (
            "ck_internal_event_outbox_runtime_lane",
            "lane IN ('internal_general', 'internal_financial')",
        ),
        "webhook_inbox": (
            "ck_webhook_inbox_runtime_lane",
            "lane = 'webhook_inbox'",
        ),
    }
    for table, (constraint, predicate) in lane_constraints.items():
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = '{constraint}'
                      AND conrelid = '{table}'::regclass
                ) THEN
                    ALTER TABLE {table}
                    ADD CONSTRAINT {constraint} CHECK ({predicate});
                END IF;
            END;
            $$
            """
        )


def _create_control_plane() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_control (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            active_generation BIGINT NOT NULL DEFAULT 0,
            claim_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            CONSTRAINT ck_queue_runtime_active_generation
                CHECK (NOT claim_enabled OR active_generation > 0),
            rollout_mode TEXT NOT NULL DEFAULT 'standby'
                CHECK (rollout_mode IN ('blocked', 'standby', 'shadow', 'canary', 'execute')),
            global_max_in_flight INTEGER NOT NULL DEFAULT 20 CHECK (global_max_in_flight > 0),
            policy_version TEXT NOT NULL DEFAULT 'queue-v1',
            updated_by TEXT NOT NULL DEFAULT 'migration',
            updated_reason TEXT NOT NULL DEFAULT 'initial standby generation',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        INSERT INTO queue_runtime_control (
            singleton, active_generation, claim_enabled, rollout_mode,
            global_max_in_flight, policy_version, updated_by, updated_reason
        ) VALUES (
            TRUE, 0, FALSE, 'standby', 20, 'queue-v1', 'migration',
            'PR-2 installs in claimless standby mode'
        )
        ON CONFLICT (singleton) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_lane_policy (
            lane TEXT PRIMARY KEY,
            max_in_flight INTEGER NOT NULL CHECK (max_in_flight > 0),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            rollout_mode TEXT NOT NULL DEFAULT 'standby'
                CHECK (rollout_mode IN ('blocked', 'standby', 'shadow', 'canary', 'execute')),
            blocked_until TIMESTAMPTZ,
            policy_version TEXT NOT NULL DEFAULT 'queue-v1',
            updated_by TEXT NOT NULL DEFAULT 'migration',
            updated_reason TEXT NOT NULL DEFAULT 'initial lane policy',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for lane, capacity in LANE_CAPACITIES.items():
        mode = "blocked" if lane == "outbound_webhook" else "standby"
        op.execute(
            f"""
            INSERT INTO queue_lane_policy (
                lane, max_in_flight, enabled, rollout_mode, policy_version,
                updated_by, updated_reason
            ) VALUES (
                '{lane}', {capacity}, TRUE, '{mode}', 'queue-v1',
                'migration', 'locked default capacity'
            )
            ON CONFLICT (lane) DO NOTHING
            """
        )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_policy_snapshot (
            policy_version TEXT PRIMARY KEY,
            policy_json JSONB NOT NULL,
            created_by TEXT NOT NULL,
            created_reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        INSERT INTO queue_policy_snapshot (
            policy_version, policy_json, created_by, created_reason
        ) VALUES (
            'queue-v1',
            jsonb_build_object(
                'heartbeat_seconds', 10,
                'lease_ttl_seconds', 30,
                'fallback_drain_seconds', 30,
                'outbound_webhook_default', 'blocked',
                'lane_capacities', jsonb_build_object(
                    'internal_general', 4,
                    'internal_financial', 1,
                    'webhook_inbox', 4,
                    'wecom_interactive', 4,
                    'wecom_bulk', 1,
                    'wecom_media', 2,
                    'outbound_webhook', 4
                )
            ),
            'migration',
            'approved PostgreSQL execution runtime defaults'
        )
        ON CONFLICT (policy_version) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aicrm_reject_queue_policy_snapshot_mutation()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'queue_policy_snapshot is append-only';
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_queue_policy_snapshot_append_only ON queue_policy_snapshot")
    op.execute(
        """
        CREATE TRIGGER trg_queue_policy_snapshot_append_only
        BEFORE UPDATE OR DELETE ON queue_policy_snapshot
        FOR EACH ROW
        EXECUTE FUNCTION aicrm_reject_queue_policy_snapshot_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_fairness_cursor (
            lane TEXT NOT NULL REFERENCES queue_lane_policy(lane),
            fairness_key TEXT NOT NULL,
            last_claimed_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
            claim_count BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (lane, fairness_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_rate_scope_cooldown (
            rate_scope_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT '',
            corp_id TEXT NOT NULL DEFAULT '',
            app_id TEXT NOT NULL DEFAULT '',
            operation TEXT NOT NULL DEFAULT '',
            blocked_until TIMESTAMPTZ NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source_attempt_id TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_worker_heartbeat (
            service_name TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            queue_kind TEXT NOT NULL,
            generation BIGINT NOT NULL,
            release_sha TEXT NOT NULL,
            rollout_mode TEXT NOT NULL,
            listener_connected BOOLEAN NOT NULL DEFAULT FALSE,
            last_notification_at TIMESTAMPTZ,
            last_drain_at TIMESTAMPTZ,
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service_name, worker_id)
        )
        """
    )


def _create_indexes_and_notifications() -> None:
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_effect_execution_id ON external_effect_job (execution_id) WHERE execution_id <> ''",
        "CREATE INDEX IF NOT EXISTS idx_external_effect_lane_due ON external_effect_job (lane, available_at, priority, id) WHERE status IN ('queued', 'failed_retryable') AND hold_reason = ''",
        "CREATE INDEX IF NOT EXISTS idx_external_effect_ordering_active ON external_effect_job (lane, ordering_key, lease_expires_at) WHERE status = 'dispatching'",
        "CREATE INDEX IF NOT EXISTS idx_external_effect_rate_scope ON external_effect_job (rate_scope_key, available_at) WHERE status IN ('queued', 'failed_retryable')",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_event_execution_id ON internal_event (execution_id) WHERE execution_id <> ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_run_execution_id ON internal_event_consumer_run (execution_id) WHERE execution_id <> ''",
        "CREATE INDEX IF NOT EXISTS idx_internal_run_lane_due ON internal_event_consumer_run (lane, available_at, id) WHERE status IN ('pending', 'failed_retryable') AND hold_reason = ''",
        "CREATE INDEX IF NOT EXISTS idx_internal_run_ordering_active ON internal_event_consumer_run (lane, ordering_key, lease_expires_at) WHERE status = 'running'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_outbox_execution_id ON internal_event_outbox (execution_id) WHERE execution_id <> ''",
        "CREATE INDEX IF NOT EXISTS idx_internal_outbox_lane_due ON internal_event_outbox (lane, available_at, id) WHERE status IN ('pending', 'failed_retryable') AND hold_reason = ''",
        "CREATE INDEX IF NOT EXISTS idx_internal_outbox_ordering_active ON internal_event_outbox (lane, ordering_key, lease_expires_at) WHERE status = 'running'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_inbox_execution_id ON webhook_inbox (execution_id) WHERE execution_id <> ''",
        "CREATE INDEX IF NOT EXISTS idx_webhook_inbox_lane_due ON webhook_inbox (lane, available_at, received_at, id) WHERE status IN ('received', 'failed_retryable') AND hold_reason = ''",
        "CREATE INDEX IF NOT EXISTS idx_webhook_inbox_ordering_active ON webhook_inbox (lane, ordering_key, lease_expires_at) WHERE status = 'processing'",
        "CREATE INDEX IF NOT EXISTS idx_queue_worker_heartbeat_freshness ON queue_worker_heartbeat (service_name, heartbeat_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_queue_rate_scope_blocked_until ON queue_rate_scope_cooldown (blocked_until)",
    )
    for statement in statements:
        op.execute(statement)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aicrm_notify_queue_wakeup()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
            wake_lane TEXT;
        BEGIN
            wake_lane := COALESCE(NULLIF(NEW.lane, ''), 'all');
            IF wake_lane !~ '^[a-z0-9_.-]{1,80}$' THEN
                wake_lane := 'all';
            END IF;
            PERFORM pg_notify(
                'aicrm_queue_wakeup',
                json_build_object(
                    'queue_kind', COALESCE(NULLIF(TG_ARGV[0], ''), 'all'),
                    'lane', wake_lane
                )::text
            );
            RETURN NEW;
        END;
        $$
        """
    )
    trigger_specs = (
        ("external_effect_job", "external_effect"),
        ("internal_event_consumer_run", "internal_event"),
        ("internal_event_outbox", "internal_outbox"),
        ("webhook_inbox", "webhook_inbox"),
    )
    for table, queue_kind in trigger_specs:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_queue_wakeup ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_queue_wakeup
            AFTER INSERT OR UPDATE OF status, available_at, hold_reason ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION aicrm_notify_queue_wakeup('{queue_kind}')
            """
        )


def upgrade() -> None:
    _add_execution_columns()
    _create_control_plane()
    _create_indexes_and_notifications()


def downgrade() -> None:
    for table in (
        "webhook_inbox",
        "internal_event_outbox",
        "internal_event_consumer_run",
        "external_effect_job",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_queue_wakeup ON {table}")
    op.execute("DROP FUNCTION IF EXISTS aicrm_notify_queue_wakeup()")
    for index in (
        "idx_queue_rate_scope_blocked_until",
        "idx_queue_worker_heartbeat_freshness",
        "idx_webhook_inbox_lane_due",
        "idx_webhook_inbox_ordering_active",
        "uq_webhook_inbox_execution_id",
        "idx_internal_outbox_lane_due",
        "idx_internal_outbox_ordering_active",
        "uq_internal_outbox_execution_id",
        "idx_internal_run_ordering_active",
        "idx_internal_run_lane_due",
        "uq_internal_run_execution_id",
        "uq_internal_event_execution_id",
        "idx_external_effect_rate_scope",
        "idx_external_effect_ordering_active",
        "idx_external_effect_lane_due",
        "uq_external_effect_execution_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")
    for table in (
        "queue_worker_heartbeat",
        "queue_rate_scope_cooldown",
        "queue_fairness_cursor",
        "queue_policy_snapshot",
        "queue_lane_policy",
        "queue_runtime_control",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS aicrm_reject_queue_policy_snapshot_mutation()")
    for table, columns in (
        (
            "webhook_inbox",
            (
                "policy_version",
                "worker_generation",
                "heartbeat_at",
                "lease_expires_at",
                "lease_token",
                "fairness_key",
                "ordering_key",
                "available_at",
                "lane",
                "parent_execution_id",
                "execution_id",
            ),
        ),
        (
            "internal_event_outbox",
            (
                "policy_version",
                "worker_generation",
                "heartbeat_at",
                "lease_expires_at",
                "fairness_key",
                "ordering_key",
                "available_at",
                "lane",
                "parent_execution_id",
                "execution_id",
            ),
        ),
        (
            "internal_event_consumer_run",
            (
                "policy_version",
                "worker_generation",
                "heartbeat_at",
                "lease_expires_at",
                "fairness_key",
                "ordering_key",
                "available_at",
                "lane",
                "parent_execution_id",
                "execution_id",
            ),
        ),
        ("internal_event", ("parent_execution_id", "execution_id")),
        (
            "external_effect_attempt",
            ("worker_generation", "provider_call_started_at", "request_hash", "lease_token"),
        ),
        (
            "external_effect_job",
            (
                "provider_call_started_at",
                "policy_version",
                "worker_generation",
                "heartbeat_at",
                "rate_scope_key",
                "fairness_key",
                "ordering_key",
                "available_at",
                "lane",
                "parent_execution_id",
                "execution_id",
            ),
        ),
    ):
        for column in columns:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
