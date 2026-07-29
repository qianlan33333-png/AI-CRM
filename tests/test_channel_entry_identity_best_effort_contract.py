from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0088_channel_entry_identity_best_effort.py"
BACKOFF_MIGRATION = ROOT / "migrations" / "versions" / "0092_channel_entry_runtime_identity_backoff.py"
LIFECYCLE_MANIFEST = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
REPOSITORY_OWNERSHIP = ROOT / "docs" / "architecture" / "repository_ownership.yml"


def test_channel_entry_identity_best_effort_migration_adds_diagnostic_columns() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "Revision ID: 0088_channel_entry_identity_best_effort" in sql
    assert 'down_revision = "0087_merge_order_identity_repair_head"' in sql
    assert "ALTER TABLE IF EXISTS wecom_external_contact_event_logs" in sql
    assert "identity_sync_status TEXT NOT NULL DEFAULT ''" in sql
    assert "identity_sync_error_code TEXT NOT NULL DEFAULT ''" in sql
    assert "identity_sync_error_message TEXT NOT NULL DEFAULT ''" in sql
    assert "identity_sync_response_json JSONB NOT NULL DEFAULT '{}'::jsonb" in sql


def test_channel_entry_identity_best_effort_migration_adds_runtime_buffer_without_polluting_contact_fact() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS automation_channel_entry_runtime" in sql
    assert "external_userid TEXT NOT NULL DEFAULT ''" in sql
    assert "follow_user_userid TEXT NOT NULL DEFAULT ''" in sql
    assert "identity_status TEXT NOT NULL DEFAULT 'pending'" in sql
    assert "ux_channel_entry_runtime_event" in sql
    assert "ON automation_channel_entry_runtime (corp_id, external_userid, follow_user_userid, scene_value)" in sql
    assert "WHERE external_userid <> '' AND scene_value <> ''" in sql
    assert "ALTER TABLE IF EXISTS automation_channel_contact" not in sql
    assert "DROP COLUMN IF EXISTS external_userid" not in sql


def test_channel_entry_identity_best_effort_runtime_table_is_governed() -> None:
    manifest = yaml.safe_load(LIFECYCLE_MANIFEST.read_text(encoding="utf-8"))
    runtime_entry = manifest["tables"]["automation_channel_entry_runtime"]

    assert runtime_entry["domain"] == "channel_entry"
    assert runtime_entry["lifecycle"] == "queue"
    assert runtime_entry["write_owner"] == "aicrm_next.channels.channel_entry.repo"
    assert "aicrm_next.channels.channel_entry.repo" in runtime_entry["read_owners"]
    assert runtime_entry["migration_source"].startswith("0088_channel_entry_identity_best_effort")
    assert "0092_channel_entry_runtime_identity_backoff" in runtime_entry["migration_source"]
    assert runtime_entry["pii_level"] == "internal_contact"

    registry = yaml.safe_load(REPOSITORY_OWNERSHIP.read_text(encoding="utf-8"))
    channel_entry_repo = registry["repositories"]["aicrm_next/channels/channel_entry/repo.py"]

    assert "automation_channel_entry_runtime" in channel_entry_repo["table_writes"]


def test_channel_entry_runtime_identity_backoff_migration_adds_due_controls() -> None:
    sql = BACKOFF_MIGRATION.read_text(encoding="utf-8")

    assert "Revision ID: 0092_channel_entry_runtime_identity_backoff" in sql
    assert 'down_revision = "0091_retire_wechat_pay_order_identity_repair"' in sql
    assert "ADD COLUMN IF NOT EXISTS identity_attempt_count INTEGER NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN IF NOT EXISTS identity_next_attempt_at TIMESTAMPTZ" in sql
    assert "ADD COLUMN IF NOT EXISTS identity_last_error TEXT NOT NULL DEFAULT ''" in sql
    assert "idx_channel_entry_runtime_identity_due" in sql
