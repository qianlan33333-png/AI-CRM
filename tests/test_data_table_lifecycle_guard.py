from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
CONFTST_PATH = ROOT / "tests" / "conftest.py"
NEXT_RUNTIME_ROOT = ROOT / "aicrm_next"
MIGRATIONS_ROOT = ROOT / "migrations" / "versions"

INITIAL_PR10_TABLES = {
    "automation_member",
    "automation_execution_trace",
    "conversion_dispatch_log",
    "user_ops_lead_pool_current",
    "user_ops_lead_pool_history",
    "user_ops_pool_current",
    "user_ops_pool_history",
    "user_ops_send_records",
    "user_ops_deferred_jobs",
    "message_batches",
    "message_batch_items",
    "marketing_automation_question_rules",
    "marketing_automation_configs",
}
EVENT_EFFECT_TABLES = {
    "domain_event_outbox",
    "external_push_delivery",
    "outbound_webhook_deliveries",
    "outbound_event_outbox",
    "internal_event_outbox",
    "internal_event",
    "internal_event_consumer_run",
    "internal_event_consumer_attempt",
    "external_effect_job",
    "external_effect_attempt",
}
CAMPAIGN_AUTOMATION_BOUNDARY_TABLES = {
    "segments",
    "segment_member_snapshots",
    "campaigns",
    "campaign_segments",
    "campaign_steps",
    "campaign_members",
    "automation_group_ops_plans",
    "automation_group_ops_plan_groups",
    "automation_group_ops_plan_nodes",
    "automation_group_ops_webhook_events",
    "automation_group_ops_plan_scope",
    "automation_group_ops_plan_member",
    "automation_group_ops_plan_segmentation",
    "automation_group_ops_trigger_event",
    "automation_group_ops_execution_log",
    "audience_rule",
    "audience_rule_version",
    "audience_rule_result",
}
LEGACY_AUTOMATION_PROGRAM_TABLES = {
    "automation_workflow_execution_item",
    "automation_workflow_execution",
    "automation_workflow_node_content_variant",
    "automation_workflow_node_content",
    "automation_workflow_node_transition",
    "automation_workflow_node",
    "automation_workflow_goal",
    "automation_workflow",
    "automation_task_plan_v2",
    "automation_stage_entry_v2",
    "automation_membership_v2",
    "automation_event_v2",
    "automation_member_audience_entry",
    "automation_program_member_stage_history",
    "automation_program_member",
    "automation_program_admission_attempt",
    "automation_program_channel_binding",
    "automation_program_config_block",
    "automation_operation_task",
    "automation_event",
    "automation_program",
}
MATERIAL_LIBRARY_TABLES = {
    "image_library",
    "image_library_variants",
    "miniprogram_library",
    "attachment_library",
}

SINGLE_OWNER_RUNTIME_WRITES = {
    "admin_operation_logs": (
        "aicrm_next.platform_foundation.admin_audit",
        {"aicrm_next/platform_foundation/admin_audit/repository.py"},
    ),
    "automation_channel": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/channel_write_repository.py"},
    ),
    "automation_channel_contact": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/repo.py"},
    ),
    "automation_channel_scene_alias": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/repo.py"},
    ),
    "automation_channel_assignee": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/repo.py"},
    ),
    "automation_channel_assignment_event": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/repo.py"},
    ),
    "automation_channel_qrcode_asset": (
        "aicrm_next.channel_entry",
        {"aicrm_next/channel_entry/repo.py"},
    ),
    "domain_event_outbox": (
        "aicrm_next.external_push",
        {"aicrm_next/external_push/repo.py"},
    ),
    "external_push_delivery": (
        "aicrm_next.external_push",
        {"aicrm_next/external_push/repo.py"},
    ),
    "outbound_webhook_deliveries": (
        "aicrm_next.admin_jobs",
        {"aicrm_next/admin_jobs/repository.py"},
    ),
    "queue_rate_scope_cooldown": (
        "aicrm_next.platform_foundation.rate_scope_cooldown",
        {"aicrm_next/platform_foundation/rate_scope_cooldown/repository.py"},
    ),
    "sync_runs": (
        "aicrm_next.platform_foundation.job_runs",
        {"aicrm_next/platform_foundation/job_runs/repository.py"},
    ),
    "webhook_inbox": (
        "aicrm_next.platform_foundation.webhook_inbox",
        {
            "aicrm_next/platform_foundation/webhook_inbox/repository.py",
            "aicrm_next/platform_foundation/webhook_inbox/runtime_write_repository.py",
        },
    ),
    "automation_agent_audit_log": (
        "aicrm_next.automation_engine",
        {"aicrm_next/automation_engine/agent_sqlalchemy_repository.py"},
    ),
    "automation_agent_idempotency": (
        "aicrm_next.automation_engine",
        {"aicrm_next/automation_engine/agent_sqlalchemy_repository.py"},
    ),
    "automation_agents": (
        "aicrm_next.automation_engine",
        {"aicrm_next/automation_engine/agent_sqlalchemy_repository.py"},
    ),
    "broadcast_job_events": (
        "aicrm_next.background_jobs.broadcast_queue_worker",
        {"aicrm_next/background_jobs/broadcast_queue_worker.py"},
    ),
    "external_effect_test_receipt": (
        "aicrm_next.platform_foundation.external_effects",
        {"aicrm_next/platform_foundation/external_effects/repo.py"},
    ),
    "external_push_config": (
        "aicrm_next.commerce",
        {"aicrm_next/commerce/repo.py"},
    ),
    "hxc_dashboard_broadcast_tasks": (
        "aicrm_next.hxc_dashboard",
        {"aicrm_next/hxc_dashboard/postgres_repo.py"},
    ),
    "outbound_tasks": (
        "aicrm_next.background_jobs.broadcast_queue_worker",
        {"aicrm_next/background_jobs/broadcast_queue_worker.py"},
    ),
}

OWNED_LIFECYCLES = {"canonical", "read_model", "event", "queue", "config"}


def _load_manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("tables"), dict)
    return data


def _table_entries() -> dict[str, dict[str, Any]]:
    data = _load_manifest()
    return data["tables"]


def _runtime_reference_patterns(table_name: str) -> list[re.Pattern[str]]:
    table = re.escape(table_name)
    return [
        re.compile(rf"\bFROM\s+{table}\b", re.IGNORECASE),
        re.compile(rf"\bJOIN\s+{table}\b", re.IGNORECASE),
        re.compile(rf"\bINSERT\s+INTO\s+{table}\b", re.IGNORECASE),
        re.compile(rf"\bUPDATE\s+{table}\b", re.IGNORECASE),
        re.compile(rf"\bDELETE\s+FROM\s+{table}\b", re.IGNORECASE),
        re.compile(rf"\bTRUNCATE\s+{table}\b", re.IGNORECASE),
        re.compile(rf"_table_exists\([^)]*[\"']{table_name}[\"']", re.IGNORECASE),
    ]


def _created_tables_from_migration(source: str) -> set[str]:
    created = set()
    created.update(
        match.group(1)
        for match in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            source,
            flags=re.IGNORECASE,
        )
    )
    created.update(
        match.group(1)
        for match in re.finditer(
            r"op\.create_table\(\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']",
            source,
        )
    )
    return created


def _migration_prefix(path: Path) -> int | None:
    match = re.match(r"(\d{4})_", path.name)
    if not match:
        return None
    return int(match.group(1))


def test_lifecycle_manifest_registers_pr10_scope() -> None:
    tables = _table_entries()
    required_tables = (
        INITIAL_PR10_TABLES
        | EVENT_EFFECT_TABLES
        | CAMPAIGN_AUTOMATION_BOUNDARY_TABLES
        | LEGACY_AUTOMATION_PROGRAM_TABLES
        | MATERIAL_LIBRARY_TABLES
    )
    missing = required_tables - set(tables)
    assert missing == set()

    for table_name, entry in tables.items():
        assert entry.get("domain"), table_name
        assert entry.get("lifecycle"), table_name
        assert "drop_candidate" in entry, table_name
        assert entry.get("replacement") or entry.get("lifecycle") != "retired", table_name
        if entry.get("lifecycle") in OWNED_LIFECYCLES:
            assert entry.get("write_owner"), table_name


def test_corrected_single_owner_tables_match_runtime_sql_writers() -> None:
    tables = _table_entries()
    runtime_sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(NEXT_RUNTIME_ROOT.rglob("*.py"))
    }

    for table_name, (expected_owner, expected_paths) in SINGLE_OWNER_RUNTIME_WRITES.items():
        entry = tables[table_name]
        declared_owners = {str(entry.get("write_owner") or "").strip()}
        declared_owners.update(
            str(owner or "").strip() for owner in entry.get("write_owners") or []
        )
        declared_owners.discard("")
        assert declared_owners == {expected_owner}, table_name

        write_pattern = re.compile(
            rf"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+{re.escape(table_name)}\b",
            flags=re.IGNORECASE,
        )
        actual_paths = {
            path
            for path, source in runtime_sources.items()
            if write_pattern.search(source)
        }
        assert actual_paths == expected_paths, table_name


def test_automation_agent_audit_tables_are_additive_versioned_evidence() -> None:
    tables = _table_entries()

    for table_name in (
        "automation_agent_output",
        "automation_agent_llm_call_log",
    ):
        entry = tables[table_name]
        assert entry["domain"] == "ai_audience_ops"
        assert entry["lifecycle"] == "audit"
        assert entry["write_owner"] == "aicrm_next.ai_audience_ops"
        assert entry["drop_candidate"] is False
        assert entry["migration_source"] == (
            "migrations/versions/0124_automation_agent_audit_tables.py"
        )


def test_retired_tables_have_no_next_runtime_sql_references() -> None:
    tables = _table_entries()
    retired_tables = sorted(
        table_name for table_name, entry in tables.items() if entry.get("lifecycle") == "retired"
    )
    assert retired_tables

    violations: list[str] = []
    runtime_files = sorted(NEXT_RUNTIME_ROOT.rglob("*.py"))
    runtime_sources: list[tuple[Path, str, str]] = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        runtime_sources.append((path, source, source.casefold()))
    for table_name in retired_tables:
        folded_table_name = table_name.casefold()
        patterns = _runtime_reference_patterns(table_name)
        for path, source, folded_source in runtime_sources:
            if folded_table_name not in folded_source:
                continue
            for pattern in patterns:
                if pattern.search(source):
                    violations.append(f"{path.relative_to(ROOT)} references retired table {table_name}")
                    break

    assert violations == []


def test_retired_tables_left_in_test_fixture_are_explicitly_justified() -> None:
    tables = _table_entries()
    conftest_source = CONFTST_PATH.read_text(encoding="utf-8")
    missing_policy = [
        table_name
        for table_name, entry in tables.items()
        if entry.get("lifecycle") == "retired"
        and table_name in conftest_source
        and not entry.get("test_fixture_policy")
    ]

    assert missing_policy == []


def test_future_create_table_migrations_must_register_tables() -> None:
    manifest = _load_manifest()
    tables = set(manifest["tables"])
    guard = manifest.get("migration_guard") or {}
    baseline_prefix = int(guard.get("migration_file_prefix_after") or 0)

    missing: dict[str, list[str]] = {}
    for path in sorted(MIGRATIONS_ROOT.glob("*.py")):
        prefix = _migration_prefix(path)
        if prefix is None or prefix <= baseline_prefix:
            continue
        created_tables = _created_tables_from_migration(path.read_text(encoding="utf-8"))
        unregistered = sorted(created_tables - tables)
        if unregistered:
            missing[path.name] = unregistered

    assert missing == {}
