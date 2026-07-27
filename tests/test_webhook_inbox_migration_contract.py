from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0054_webhook_inbox.py"


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_webhook_inbox_migration_keeps_generic_ingress_schema_contract() -> None:
    sql = _migration_text()
    required_columns = [
        "id BIGSERIAL PRIMARY KEY",
        "provider TEXT NOT NULL",
        "event_family TEXT NOT NULL",
        "route TEXT NOT NULL",
        "method TEXT NOT NULL DEFAULT 'POST'",
        "tenant_id TEXT NOT NULL DEFAULT 'aicrm'",
        "corp_id TEXT NOT NULL DEFAULT ''",
        "event_type TEXT NOT NULL DEFAULT ''",
        "change_type TEXT NOT NULL DEFAULT ''",
        "external_event_id TEXT NOT NULL DEFAULT ''",
        "idempotency_key TEXT NOT NULL",
        "raw_query_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "raw_headers_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "raw_body BYTEA",
        "payload_xml TEXT NOT NULL DEFAULT ''",
        "payload_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "payload_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "processing_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "status TEXT NOT NULL DEFAULT 'received'",
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "max_attempts INTEGER NOT NULL DEFAULT 8",
        "next_retry_at TIMESTAMPTZ",
        "locked_at TIMESTAMPTZ",
        "locked_by TEXT NOT NULL DEFAULT ''",
        "last_error_code TEXT NOT NULL DEFAULT ''",
        "last_error_message TEXT NOT NULL DEFAULT ''",
        "received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "started_at TIMESTAMPTZ",
        "finished_at TIMESTAMPTZ",
        "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "duplicate_count INTEGER NOT NULL DEFAULT 0",
    ]

    for column in required_columns:
        assert column in sql


def test_webhook_inbox_migration_locks_status_and_idempotency_contracts() -> None:
    sql = _migration_text()
    statuses = {
        "received",
        "processing",
        "succeeded",
        "failed_retryable",
        "failed_terminal",
        "dead_letter",
        "ignored",
    }
    status_block = re.search(r"CHECK \(status IN \((.*?)\)\)", sql, flags=re.DOTALL)

    assert status_block is not None
    assert set(re.findall(r"'([^']+)'", status_block.group(1))) == statuses
    assert "CONSTRAINT uq_webhook_inbox_tenant_provider_idempotency" in sql
    assert "UNIQUE (tenant_id, provider, idempotency_key)" in sql


def test_webhook_inbox_migration_has_due_claim_and_lookup_indexes() -> None:
    sql = _migration_text()

    assert "CREATE INDEX IF NOT EXISTS idx_webhook_inbox_due" in sql
    assert "ON webhook_inbox (provider, status, next_retry_at, locked_at, received_at, id)" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_webhook_inbox_event_family" in sql
    assert "ON webhook_inbox (provider, event_family, status, received_at DESC, id DESC)" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_webhook_inbox_external_event" in sql
    assert "WHERE external_event_id <> ''" in sql


def test_webhook_inbox_repository_reclaims_stale_processing_rows() -> None:
    source = (ROOT / "aicrm_next" / "platform" / "platform_foundation" / "webhook_inbox" / "repository.py").read_text(encoding="utf-8")

    assert "status = 'processing'" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "UPDATE webhook_inbox i" in source
    assert "locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'" in source
    assert 'status == "processing"' in source
    assert "timedelta(minutes=5)" in source
