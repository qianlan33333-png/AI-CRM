from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

import yaml
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "0049_group_ops_workspace_governance.py"
ROUTE_MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"
GROUP_OPS_PACKAGE = ROOT / "aicrm_next" / "automation" / "automation_engine" / "group_ops"


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _upgrade_source() -> str:
    return _migration_source().split("def upgrade", 1)[1].split("def downgrade", 1)[0]


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("group_ops_workspace_governance_migration", MIGRATION_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_group_ops_workspace_governance_migration_is_executable_in_postgres_offline_mode(monkeypatch) -> None:
    module = _load_migration_module()
    buffer = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)

    monkeypatch.setattr(module, "op", operations)

    module.upgrade()

    generated_sql = buffer.getvalue()
    assert "CREATE TABLE IF NOT EXISTS group_ops_workspace_governance_reviews" in generated_sql
    assert "CREATE TABLE IF NOT EXISTS group_ops_workspace_governance_review_steps" in generated_sql
    assert "CREATE TABLE IF NOT EXISTS group_ops_workspace_allowlist_snapshots" in generated_sql
    assert "CREATE TABLE IF NOT EXISTS group_ops_workspace_gray_window_approvals" in generated_sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_ops_workspace_governance_reviews_review_id" in generated_sql


def test_group_ops_workspace_governance_migration_revision_chain() -> None:
    source = _migration_source()

    assert 'revision = "0049_group_ops_workspace_governance"' in source
    assert 'down_revision = "0048_group_ops_workspace_request_review_audit_action"' in source


def test_group_ops_workspace_governance_tables_fields_indexes_and_constraints() -> None:
    source = _migration_source()

    for expected in [
        "CREATE TABLE IF NOT EXISTS group_ops_workspace_governance_reviews",
        "CREATE TABLE IF NOT EXISTS group_ops_workspace_governance_review_steps",
        "CREATE TABLE IF NOT EXISTS group_ops_workspace_allowlist_snapshots",
        "CREATE TABLE IF NOT EXISTS group_ops_workspace_gray_window_approvals",
        "review_id TEXT NOT NULL",
        "draft_id TEXT NOT NULL REFERENCES group_ops_workspace_drafts(draft_id) ON DELETE CASCADE",
        "review_status TEXT NOT NULL DEFAULT 'governance_not_started'",
        "'governance_not_started'",
        "'approval_pending'",
        "'allowlist_pending'",
        "'gray_window_pending'",
        "'governance_approved'",
        "'governance_rejected'",
        "'governance_expired'",
        "requested_by TEXT NOT NULL DEFAULT ''",
        "approved_by TEXT NOT NULL DEFAULT ''",
        "rejected_by TEXT NOT NULL DEFAULT ''",
        "idempotency_key TEXT NOT NULL DEFAULT ''",
        "snapshot_hash TEXT NOT NULL DEFAULT ''",
        "sanitized_payload_hash TEXT NOT NULL DEFAULT ''",
        "audit_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "step_id TEXT NOT NULL",
        "step_type TEXT NOT NULL",
        "CHECK (step_type IN ('operator_approval', 'receiver_allowlist', 'gray_window'))",
        "step_status TEXT NOT NULL DEFAULT 'pending'",
        "CHECK (step_status IN ('pending', 'approved', 'rejected', 'expired'))",
        "actor_id TEXT NOT NULL DEFAULT ''",
        "actor_label TEXT NOT NULL DEFAULT ''",
        "metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "snapshot_id TEXT NOT NULL",
        "allowlist_hash TEXT NOT NULL DEFAULT ''",
        "allowlist_count INTEGER NOT NULL DEFAULT 0",
        "allowlist_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "source_reference_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "approval_id TEXT NOT NULL",
        "start_at TIMESTAMPTZ NOT NULL",
        "end_at TIMESTAMPTZ NOT NULL",
        "timezone TEXT NOT NULL DEFAULT 'UTC'",
        "window_status TEXT NOT NULL DEFAULT 'pending'",
        "CHECK (window_status IN ('pending', 'approved', 'rejected', 'expired'))",
        "CHECK (end_at > start_at)",
        "uq_group_ops_workspace_governance_reviews_review_id",
        "uq_group_ops_workspace_governance_reviews_idempotency",
        "idx_group_ops_workspace_governance_reviews_draft",
        "idx_group_ops_workspace_governance_reviews_status",
        "idx_group_ops_workspace_governance_reviews_expires_at",
        "uq_group_ops_workspace_governance_review_steps_step_id",
        "uq_group_ops_workspace_governance_review_steps_idempotency",
        "idx_group_ops_workspace_governance_review_steps_review",
        "idx_group_ops_workspace_governance_review_steps_type_status",
        "uq_group_ops_workspace_allowlist_snapshots_snapshot_id",
        "idx_group_ops_workspace_allowlist_snapshots_review",
        "idx_group_ops_workspace_allowlist_snapshots_hash",
        "idx_group_ops_workspace_allowlist_snapshots_expires_at",
        "uq_group_ops_workspace_gray_window_approvals_approval_id",
        "idx_group_ops_workspace_gray_window_approvals_review",
        "idx_group_ops_workspace_gray_window_approvals_status",
        "idx_group_ops_workspace_gray_window_approvals_window",
        "REFERENCES group_ops_workspace_governance_reviews(review_id) ON DELETE CASCADE",
    ]:
        assert expected in source


def test_group_ops_workspace_governance_schema_uses_only_safe_summary_hash_count_metadata_columns() -> None:
    upgrade_sql = _upgrade_source().lower()

    for expected in [
        "sanitized_payload_hash",
        "audit_metadata_json",
        "metadata_json",
        "allowlist_hash",
        "allowlist_count",
        "allowlist_summary_json",
        "source_reference_json",
        "snapshot_hash",
    ]:
        assert expected in upgrade_sql

    forbidden_schema_fragments = [
        "receiver_raw",
        "raw_receiver",
        "receiver_plaintext",
        "raw_external_userid",
        "external_userid",
        "phone",
        "mobile",
        "raw_chat",
        "raw_member",
        "openid",
        "unionid",
        "token",
        "secret",
        "authorization",
        "raw_message",
        "callback_body",
        "target_list_raw",
        "raw_target",
    ]

    assert {fragment for fragment in forbidden_schema_fragments if fragment in upgrade_sql} == set()


def test_group_ops_workspace_governance_migration_does_not_add_runtime_writers_or_execution_routes() -> None:
    migration_source = _migration_source()
    upgrade_sql = _upgrade_source()

    for forbidden in [
        "external_effect_job",
        "external_effect_attempt",
        "broadcast_job",
        "push_center_job",
        "internal_event",
        "requests.post",
        "httpx.",
    ]:
        assert forbidden not in upgrade_sql
        assert forbidden not in migration_source.split("def upgrade", 1)[1]

    manifest = yaml.safe_load(ROUTE_MANIFEST.read_text(encoding="utf-8"))
    retired_runtime_routes = [
        route
        for route in manifest["routes"]
        if "/api/admin/p1/group-ops-workspace" in route["path"]
        or route["path"] == "/admin/p1/group-ops-workspace"
    ]
    assert retired_runtime_routes == []

    group_ops_runtime_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in GROUP_OPS_PACKAGE.glob("*.py")
        if path.name not in {"__init__.py"}
    )
    assert "create_push_center" not in group_ops_runtime_sources
    assert "push_center_job_created=True" not in group_ops_runtime_sources
    assert '"approved": True' not in group_ops_runtime_sources
