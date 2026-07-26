from __future__ import annotations

from pathlib import Path

import yaml

from tools.check_repository_ownership import (
    check_repository_ownership,
    extract_repository_sql_access,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs" / "architecture" / "repository_ownership.yml"
MANIFEST_PATH = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
ADMIN_READ_REPO_PATH = ROOT / "aicrm_next" / "admin_read_model" / "repo.py"


def test_repository_ownership_current_registry_passes() -> None:
    assert check_repository_ownership(
        root=ROOT,
        registry_path=REGISTRY_PATH,
        manifest_path=MANIFEST_PATH,
    ) == []


def test_repository_ownership_targeted_declarations_are_complete() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    repositories = registry["repositories"]
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert repositories["aicrm_next/admin_config/repository.py"]["table_reads"] == [
        "admin_login_audit",
        "admin_operation_logs",
        "admin_user_roles",
        "admin_users",
        "admin_wecom_directory_members",
        "app_settings",
        "marketing_automation_configs",
        "marketing_automation_question_rules",
        "mcp_tool_settings",
        "questionnaire_options",
        "questionnaire_questions",
        "questionnaires",
    ]
    assert repositories["aicrm_next/admin_config/repository.py"]["table_writes"] == [
        "admin_login_audit",
        "admin_user_roles",
        "admin_users",
        "app_settings",
        "marketing_automation_configs",
        "marketing_automation_question_rules",
        "mcp_tool_settings",
    ]
    assert repositories["aicrm_next/admin_jobs/repository.py"]["table_reads"] == [
        "broadcast_job_events",
        "broadcast_jobs",
        "broadcast_queue_notification_settings",
        "outbound_tasks",
        "outbound_webhook_deliveries",
        "sync_runs",
        "wecom_external_contact_event_logs",
    ]
    assert repositories["aicrm_next/admin_jobs/repository.py"]["table_writes"] == [
        "broadcast_job_hourly_reports",
        "broadcast_jobs",
        "broadcast_queue_notification_settings",
        "outbound_webhook_deliveries",
    ]
    assert repositories["aicrm_next/admin_read_model/repo.py"]["table_reads"] == [
        "admin_operation_logs",
        "ai_audience_member_current",
        "archived_messages",
        "automation_agent_config",
        "automation_agent_llm_call_log",
        "automation_agent_output",
        "automation_agent_run",
        "broadcast_jobs",
        "external_effect_job",
        "internal_event",
        "outbound_tasks",
        "outbound_webhook_deliveries",
        "questionnaire_submissions",
        "reply_message_batch",
        "sync_runs",
        "wechat_pay_orders",
        "wecom_external_contact_event_logs",
        "wecom_external_contact_follow_users",
        "wecom_external_contact_identity_map",
    ]
    assert repositories["aicrm_next/platform_foundation/admin_audit/repository.py"] == {
        "capability_owner": "aicrm_next.platform_foundation.admin_audit",
        "table_reads": [],
        "table_writes": ["admin_operation_logs"],
    }
    assert manifest["tables"]["admin_operation_logs"]["write_owner"] == (
        "aicrm_next.platform_foundation.admin_audit"
    )
    assert manifest["tables"]["admin_operation_logs"]["write_owners"] == [
        "aicrm_next.platform_foundation.admin_audit"
    ]
    assert "aicrm_next.external_push.repo" in manifest["tables"]["domain_event_outbox"]["read_owners"]
    assert "aicrm_next.external_push.repo" in manifest["tables"]["external_push_delivery"]["read_owners"]
    assert repositories["aicrm_next/ai_assist/external_campaigns_repo.py"]["table_writes"] == [
        "broadcast_jobs",
    ]
    assert repositories["aicrm_next/ai_assist/external_campaigns_repo.py"]["table_reads"] == [
        "broadcast_jobs",
        "campaign_members",
        "campaign_segments",
        "campaign_steps",
        "campaigns",
        "crm_user_identity",
        "segments",
        "user_ops_do_not_disturb_next",
        "wecom_external_contact_follow_users",
        "wecom_external_contact_identity_map",
    ]
    assert repositories["aicrm_next/send_targets/repo.py"]["table_reads"] == [
        "crm_user_identity",
        "user_ops_do_not_disturb_next",
    ]
    assert repositories["aicrm_next/send_targets/repo.py"]["table_writes"] == []
    assert repositories["aicrm_next/external_push/repo.py"]["table_writes"] == [
        "domain_event_outbox",
        "external_push_delivery",
    ]
    assert repositories["aicrm_next/channel_entry/identity_bridge_repo.py"]["capability_owner"] == (
        "aicrm_next.identity_contact"
    )
    assert repositories["aicrm_next/identity_contact/write_repository.py"]["table_writes"] == [
        "crm_user_identity",
        "crm_user_identity_resolution_queue",
    ]
    assert repositories["aicrm_next/identity_contact/event_log_repository.py"] == {
        "capability_owner": "aicrm_next.identity_contact",
        "table_reads": ["wecom_external_contact_event_logs"],
        "table_writes": ["wecom_external_contact_event_logs"],
    }
    assert repositories["aicrm_next/customer_tags/projection_repository.py"] == {
        "capability_owner": "aicrm_next.customer_tags",
        "table_reads": [],
        "table_writes": ["contact_tags"],
    }
    assert "wecom_external_contact_event_logs" not in repositories["aicrm_next/channel_entry/repo.py"][
        "table_reads"
    ]
    assert "wecom_external_contact_event_logs" not in repositories["aicrm_next/channel_entry/repo.py"][
        "table_writes"
    ]
    assert "contact_tags" not in repositories["aicrm_next/channel_entry/repo.py"]["table_writes"]
    assert repositories["aicrm_next/identity_contact/resolution_queue_repository.py"]["table_reads"] == [
        "crm_user_identity_resolution_queue",
        "identity_resolution_completion_receipt",
    ]
    assert repositories["aicrm_next/identity_contact/resolution_queue_repository.py"]["table_writes"] == [
        "crm_user_identity_resolution_queue",
        "identity_resolution_completion_receipt",
    ]
    assert repositories["aicrm_next/customer_read_model/sidebar_profile_repository.py"] == {
        "capability_owner": "aicrm_next.customer_read_model",
        "table_reads": [],
        "table_writes": ["sidebar_customer_profile_fields"],
    }
    assert repositories["aicrm_next/sidebar_write/repo.py"]["table_writes"] == []
    assert "write_owners" not in manifest["tables"]["crm_user_identity"]
    assert "write_owners" not in manifest["tables"]["crm_user_identity_conflicts"]
    assert "aicrm_next.sidebar_write" not in manifest["tables"]["crm_user_identity_resolution_queue"][
        "write_owners"
    ]
    assert manifest["tables"]["crm_user_identity_resolution_queue"]["write_owners"] == [
        "aicrm_next.identity_contact",
    ]
    assert manifest["tables"]["identity_resolution_completion_receipt"]["write_owner"] == (
        "aicrm_next.identity_contact"
    )
    assert manifest["tables"]["wecom_external_contact_event_logs"]["write_owners"] == [
        "aicrm_next.identity_contact"
    ]
    assert manifest["tables"]["contact_tags"]["write_owner"] == "aicrm_next.customer_tags"
    assert manifest["tables"]["external_effect_job"]["write_owner"] == (
        "aicrm_next.platform_foundation.external_effects"
    )
    assert manifest["tables"]["external_effect_job"]["write_owners"] == [
        "aicrm_next.platform_foundation.external_effects"
    ]
    assert manifest["tables"]["external_effect_attempt"]["write_owner"] == (
        "aicrm_next.platform_foundation.external_effects"
    )
    assert manifest["tables"]["external_effect_attempt"]["write_owners"] == [
        "aicrm_next.platform_foundation.external_effects"
    ]
    assert repositories[
        "aicrm_next/platform_foundation/external_effects/runtime_write_repository.py"
    ]["table_writes"] == ["external_effect_attempt", "external_effect_job"]
    assert "external_effect_job" not in repositories[
        "aicrm_next/automation_engine/group_ops/durable_effects_repository.py"
    ]["table_writes"]
    assert "external_effect_job" not in repositories[
        "aicrm_next/channel_entry/welcome_media_effects_repository.py"
    ]["table_writes"]
    assert manifest["tables"]["contact_tags"]["write_owners"] == ["aicrm_next.customer_tags"]
    for path in (
        "aicrm_next/ai_audience_ops/repository.py",
        "aicrm_next/channel_entry/repo.py",
        "aicrm_next/message_archive/repo.py",
        "aicrm_next/questionnaire/repo.py",
    ):
        assert "crm_user_identity_resolution_queue" not in repositories[path]["table_writes"]


def test_identity_resolution_queue_write_sql_is_confined_to_logical_owner() -> None:
    offenders: list[str] = []

    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        access = extract_repository_sql_access(path)
        if "crm_user_identity_resolution_queue" not in access.table_writes:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        if relative_path.startswith("aicrm_next/identity_contact/"):
            continue
        if relative_path == "aicrm_next/channel_entry/identity_bridge_repo.py":
            continue
        offenders.append(relative_path)

    assert offenders == []


def test_canonical_identity_table_sql_writes_resolve_to_identity_contact_owner() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))["repositories"]
    canonical_tables = {"crm_user_identity", "crm_user_identity_conflicts"}
    offenders: list[str] = []

    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        access = extract_repository_sql_access(path)
        if not canonical_tables.intersection(access.table_writes):
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        if relative_path.startswith("aicrm_next/identity_contact/"):
            continue
        owner = str((registry.get(relative_path) or {}).get("capability_owner") or "")
        if owner != "aicrm_next.identity_contact":
            offenders.append(f"{relative_path}:{owner or 'undeclared'}")

    assert offenders == []


def test_external_contact_event_log_write_sql_is_confined_to_identity_contact() -> None:
    offenders: list[str] = []

    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        access = extract_repository_sql_access(path)
        if "wecom_external_contact_event_logs" not in access.table_writes:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        if not relative_path.startswith("aicrm_next/identity_contact/"):
            offenders.append(relative_path)

    assert offenders == []


def test_contact_tag_write_sql_is_confined_to_customer_tags() -> None:
    offenders: list[str] = []

    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        access = extract_repository_sql_access(path)
        if "contact_tags" not in access.table_writes:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        if not relative_path.startswith("aicrm_next/customer_tags/"):
            offenders.append(relative_path)

    assert offenders == []


def test_admin_read_count_allowlist_excludes_retired_tables() -> None:
    source = ADMIN_READ_REPO_PATH.read_text(encoding="utf-8")

    assert '"user_ops_deferred_jobs"' not in source
    assert '"message_batches"' not in source
    assert '"message_batch_items"' not in source


def test_repository_ownership_requires_every_repo_file(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", "")
    registry = _write_registry(tmp_path, repositories={})
    manifest = _write_manifest(tmp_path)

    violations = check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["repository_missing_ownership_declaration"]


def test_repository_ownership_blocks_retired_reads(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", 'SQL = "SELECT * FROM retired_table"\n')
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": ["retired_table"],
                "table_writes": [],
            }
        },
    )
    manifest = _write_manifest(tmp_path)

    violations = check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["repository_reads_retired_table"]


def test_repository_ownership_blocks_write_owner_mismatch(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", 'SQL = "INSERT INTO active_table (id) VALUES (1)"\n')
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": [],
                "table_writes": ["active_table"],
            }
        },
    )
    manifest = _write_manifest(tmp_path)

    violations = check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["repository_write_owner_mismatch"]


def test_repository_ownership_accepts_manifest_write_owner_prefix(tmp_path: Path) -> None:
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        'READ_SQL = "SELECT * FROM active_table"\nWRITE_SQL = "INSERT INTO active_table (id) VALUES (1)"\n',
    )
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": ["active_table"],
                "table_writes": ["active_table"],
            }
        },
    )
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo.repository")

    assert check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest) == []


def test_repository_ownership_accepts_manifest_write_owners(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", 'SQL = "INSERT INTO active_table (id) VALUES (1)"\n')
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": [],
                "table_writes": ["active_table"],
            }
        },
    )
    manifest = _write_manifest(
        tmp_path,
        extra_active_table_lines="""
    write_owners:
      - aicrm_next.other.repository
      - aicrm_next.demo.repository
""",
    )

    assert check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest) == []


def test_repository_sql_extractor_handles_ctes_multiline_and_all_write_verbs(tmp_path: Path) -> None:
    repository = tmp_path / "repo.py"
    _write(
        repository,
        '''
SQL = """WITH due AS (
    SELECT source.id FROM source_table source
    JOIN lookup_table lookup ON lookup.id = source.id
)
INSERT INTO target_table (id)
SELECT id FROM due
"""
UPDATE_SQL = "UPDATE target_table SET id = id WHERE id > 0"
DELETE_SQL = "DELETE FROM deletion_table WHERE id > 0"
''',
    )

    access = extract_repository_sql_access(repository)

    assert access.table_reads == frozenset({"lookup_table", "source_table"})
    assert access.table_writes == frozenset({"deletion_table", "target_table"})


def test_repository_sql_extractor_scopes_cte_names_to_each_statement(tmp_path: Path) -> None:
    repository = tmp_path / "repo.py"
    _write(
        repository,
        '''
CTE_SQL = """WITH shared_name AS (
    SELECT id FROM source_table
)
SELECT id FROM shared_name
"""
TABLE_SQL = "SELECT id FROM shared_name"
''',
    )

    access = extract_repository_sql_access(repository)

    assert access.table_reads == frozenset({"shared_name", "source_table"})


def test_repository_ownership_blocks_undeclared_sql_access(tmp_path: Path) -> None:
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        'SQL = "SELECT * FROM active_table"\n',
    )
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": [],
                "table_writes": [],
            }
        },
    )
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo")

    violations = check_repository_ownership(
        root=tmp_path,
        registry_path=registry,
        manifest_path=manifest,
    )

    assert [violation.rule for violation in violations] == [
        "repository_sql_access_missing_declaration"
    ]


def test_repository_ownership_blocks_declaration_without_literal_or_explicit_non_literal_access(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", "")
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": ["active_table"],
                "table_writes": [],
            }
        },
    )
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo")

    violations = check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest)

    assert [violation.rule for violation in violations] == [
        "repository_sql_declaration_without_access"
    ]


def test_repository_ownership_accepts_explicit_non_literal_access(tmp_path: Path) -> None:
    _write(tmp_path / "aicrm_next" / "demo" / "repo.py", "")
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": ["active_table"],
                "table_writes": [],
                "non_literal_table_reads": ["active_table"],
            }
        },
    )
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo")

    assert check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest) == []


def test_repository_ownership_blocks_stale_non_literal_access_exception(tmp_path: Path) -> None:
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        'SQL = "SELECT * FROM active_table"\n',
    )
    registry = _write_registry(
        tmp_path,
        repositories={
            "aicrm_next/demo/repo.py": {
                "capability_owner": "aicrm_next.demo",
                "table_reads": ["active_table"],
                "table_writes": [],
                "non_literal_table_reads": ["active_table"],
            }
        },
    )
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo")

    violations = check_repository_ownership(root=tmp_path, registry_path=registry, manifest_path=manifest)

    assert [violation.rule for violation in violations] == [
        "repository_stale_non_literal_access_exception"
    ]


def test_repository_ownership_requires_relation_lifecycle_or_explicit_exception(tmp_path: Path) -> None:
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        'SQL = "SELECT * FROM current_view"\n',
    )
    repositories = {
        "aicrm_next/demo/repo.py": {
            "capability_owner": "aicrm_next.demo",
            "table_reads": ["current_view"],
            "table_writes": [],
        }
    }
    registry = _write_registry(tmp_path, repositories=repositories)
    manifest = _write_manifest(tmp_path, active_write_owner="aicrm_next.demo")
    violations = check_repository_ownership(
        root=tmp_path,
        registry_path=registry,
        manifest_path=manifest,
    )
    assert [violation.rule for violation in violations] == [
        "repository_relation_missing_lifecycle"
    ]

    repositories["aicrm_next/demo/repo.py"]["non_table_relations"] = ["current_view"]
    registry = _write_registry(tmp_path, repositories=repositories)
    assert check_repository_ownership(
        root=tmp_path,
        registry_path=registry,
        manifest_path=manifest,
    ) == []


def _write_registry(tmp_path: Path, *, repositories: dict) -> Path:
    registry = tmp_path / "docs" / "architecture" / "repository_ownership.yml"
    lines = ["version: 1", "repositories:"]
    if not repositories:
        lines[-1] = "repositories: {}"
    for path, entry in repositories.items():
        lines.append(f"  {path}:")
        lines.append(f"    capability_owner: {entry['capability_owner']}")
        if entry.get("access_scope"):
            lines.append(f"    access_scope: {entry['access_scope']}")
        lines.append("    table_reads:")
        for table in entry["table_reads"]:
            lines.append(f"      - {table}")
        if not entry["table_reads"]:
            lines[-1] = "    table_reads: []"
        lines.append("    table_writes:")
        for table in entry["table_writes"]:
            lines.append(f"      - {table}")
        if not entry["table_writes"]:
            lines[-1] = "    table_writes: []"
        for field in (
            "non_table_relations",
            "optional_relations",
            "non_literal_table_reads",
            "non_literal_table_writes",
        ):
            if field not in entry:
                continue
            lines.append(f"    {field}:")
            for relation in entry[field]:
                lines.append(f"      - {relation}")
            if not entry[field]:
                lines[-1] = f"    {field}: []"
    _write(registry, "\n".join(lines) + "\n")
    return registry


def _write_manifest(
    tmp_path: Path,
    *,
    active_write_owner: str = "aicrm_next.other.repository",
    extra_active_table_lines: str = "",
) -> Path:
    manifest = tmp_path / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
    _write(
        manifest,
        f"""
version: 1
tables:
  active_table:
    domain: tests
    lifecycle: canonical
    write_owner: {active_write_owner}
{extra_active_table_lines.rstrip()}
    replacement: none
    drop_candidate: false
  retired_table:
    domain: tests
    lifecycle: retired
    replacement: active_table
    drop_candidate: false
""",
    )
    return manifest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
