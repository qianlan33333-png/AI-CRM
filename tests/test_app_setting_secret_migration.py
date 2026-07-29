from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from aicrm_next.platform.shared.secret_store import FileSecretStore, SecretStoreError, is_secret_reference
from scripts.ops import migrate_app_setting_secrets as migration_script
from scripts.ops.check_secret_reference_cutover import reconcile_secret_reference_cutover
from scripts.ops.migrate_app_setting_secrets import (
    migrate_app_setting_secrets,
    rollback_secret_reference,
)


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
    return engine


def _upsert(engine: Engine, key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (:key, :value, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"key": key, "value": value},
        )


def _value(engine: Engine, key: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}).first()
    return str((row or [""])[0] or "")


def test_secret_migration_dry_run_reports_metadata_only_and_writes_nothing(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    raw_db = "dry-run-db-secret-sentinel"
    raw_env = "dry-run-env-secret-sentinel"
    _upsert(engine, "WECOM_SECRET", raw_db)

    report = migrate_app_setting_secrets(
        engine=engine,
        store=FileSecretStore(root),
        environment={"WECOM_CONTACT_SECRET": raw_env},
        dry_run=True,
    )

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert raw_db not in rendered
    assert raw_env not in rendered
    assert _value(engine, "WECOM_SECRET") == raw_db
    assert _value(engine, "AICRM_SECRET_REFERENCE_CUTOVER") == ""
    assert not root.exists()
    assert report["dry_run"] is True
    assert report["plaintext_pending"] == 2
    assert {item["source"] for item in report["items"] if item["present"]} == {"app_settings", "environment"}
    assert all(set(item) == {"key", "source", "version", "present", "status"} for item in report["items"])


def test_filesystem_failure_happens_before_database_transaction(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    raw = "filesystem-failure-secret-sentinel"
    _upsert(engine, "WECOM_SECRET", raw)

    class FailingStore(FileSecretStore):
        def write(self, key: str, value: str, *, current_reference: str = "") -> str:
            raise SecretStoreError("injected filesystem failure")

    with pytest.raises(SecretStoreError, match="injected filesystem failure"):
        migrate_app_setting_secrets(
            engine=engine,
            store=FailingStore(tmp_path / "secrets"),
            environment={},
            dry_run=False,
        )

    assert _value(engine, "WECOM_SECRET") == raw
    assert _value(engine, "AICRM_SECRET_REFERENCE_CUTOVER") == ""


def test_database_failure_rolls_back_rows_after_immutable_file_write(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    raw = "database-failure-secret-sentinel"
    _upsert(engine, "WECOM_SECRET", raw)

    def fail_before_commit(_connection) -> None:
        raise RuntimeError("injected database failure")

    with pytest.raises(RuntimeError, match="injected database failure"):
        migrate_app_setting_secrets(
            engine=engine,
            store=FileSecretStore(root),
            environment={},
            dry_run=False,
            transaction_hook=fail_before_commit,
        )

    assert _value(engine, "WECOM_SECRET") == raw
    assert _value(engine, "AICRM_SECRET_REFERENCE_CUTOVER") == ""
    assert len(list((root / "WECOM_SECRET").iterdir())) == 1


def test_mixed_raw_reference_and_environment_rows_migrate_idempotently(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    existing_reference = store.write("WECOM_CONTACT_SECRET", "existing-reference-secret")
    _upsert(engine, "WECOM_SECRET", "raw-database-secret")
    _upsert(engine, "WECOM_CONTACT_SECRET", existing_reference)
    environment = {"SECRET_KEY": "environment-signing-secret"}

    first = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment=environment,
        dry_run=False,
    )
    version_files_after_first = sorted(path.relative_to(root) for path in root.glob("*/*"))
    second = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment=environment,
        dry_run=False,
    )

    assert first["migrated"] == 2
    assert first["plaintext_pending"] == 0
    assert second["migrated"] == 0
    assert second["already_referenced"] == 3
    assert sorted(path.relative_to(root) for path in root.glob("*/*")) == version_files_after_first
    for key in ("WECOM_SECRET", "WECOM_CONTACT_SECRET", "SECRET_KEY"):
        assert is_secret_reference(_value(engine, key))
    assert _value(engine, "AICRM_SECRET_REFERENCE_CUTOVER") == "true"
    rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "raw-database-secret" not in rendered
    assert "environment-signing-secret" not in rendered
    assert existing_reference not in rendered


@pytest.mark.skip(reason="removed with RAUTH shared-token replacement")
def test_legacy_shared_token_migration_generates_distinct_purpose_credentials(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    environment_file = tmp_path / "runtime.env"
    environment_file.write_text("EXISTING_FLAG='keep-me'\n", encoding="utf-8")
    os.chmod(environment_file, 0o600)
    legacy_token = "legacy-shared-internal-token"
    _upsert(engine, "AUTOMATION_INTERNAL_API_TOKEN", legacy_token)

    first = migrate_app_setting_secrets(
        engine=engine,
        store=FileSecretStore(root),
        environment={},
        dry_run=False,
        environment_file=environment_file,
    )
    split_credentials = [
        credential
        for credential in TOKEN_PURPOSES.values()
        if credential.purpose != "automation_worker"
    ]
    resolved = {
        credential.purpose: FileSecretStore(root).read(_value(engine, credential.setting_key))
        for credential in split_credentials
    }

    assert first["generated"] == len(split_credentials) == 5
    assert len(set(resolved.values())) == len(split_credentials)
    assert legacy_token not in set(resolved.values())
    assert all(is_secret_reference(_value(engine, credential.setting_key)) for credential in split_credentials)
    environment_body = environment_file.read_text(encoding="utf-8")
    assert all(f"{credential.setting_key}='secretref:file:{credential.setting_key}:" in environment_body for credential in split_credentials)
    assert legacy_token not in environment_body

    reconciled = reconcile_secret_reference_cutover(
        engine=engine,
        store=FileSecretStore(root),
        environment_file=environment_file,
    )
    assert reconciled["ok"] is True
    assert reconciled["missing_internal_token_purposes"] == []
    assert reconciled["duplicate_internal_token_purposes"] == []
    assert reconciled["legacy_internal_token_fallback_enabled"] is False

    monkeypatch.setenv(LEGACY_FALLBACK_ENABLED_KEY, "true")
    fallback_enabled = reconcile_secret_reference_cutover(
        engine=engine,
        store=FileSecretStore(root),
        environment_file=environment_file,
    )
    assert fallback_enabled["ok"] is False
    assert fallback_enabled["legacy_internal_token_fallback_enabled"] is True
    monkeypatch.delenv(LEGACY_FALLBACK_ENABLED_KEY)

    second = migrate_app_setting_secrets(
        engine=engine,
        store=FileSecretStore(root),
        environment={},
        dry_run=False,
        environment_file=environment_file,
    )
    assert second["generated"] == 0
    assert second["already_referenced"] == len(split_credentials) + 1

    store = FileSecretStore(root)
    mcp_collision = store.write("MCP_BEARER_TOKEN", "duplicate-purpose-token")
    identity_collision = store.write("IDENTITY_INTERNAL_API_TOKEN", "duplicate-purpose-token")
    _upsert(engine, "MCP_BEARER_TOKEN", mcp_collision)
    _upsert(engine, "IDENTITY_INTERNAL_API_TOKEN", identity_collision)
    collision_report = reconcile_secret_reference_cutover(
        engine=engine,
        store=store,
        environment_file=environment_file,
    )
    assert collision_report["ok"] is False
    assert ["identity", "mcp"] in collision_report["duplicate_internal_token_purposes"]


@pytest.mark.skip(reason="removed with RAUTH shared-token replacement")
def test_existing_cutover_repairs_duplicate_internal_token_and_redacts_historical_audit(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    environment_file = tmp_path / "runtime.env"
    shared_token = "legacy-shared-token-that-must-stop-being-universal"
    automation_reference = store.write("AUTOMATION_INTERNAL_API_TOKEN", shared_token)
    mcp_reference = store.write("MCP_BEARER_TOKEN", shared_token)
    _upsert(engine, "AUTOMATION_INTERNAL_API_TOKEN", automation_reference)
    _upsert(engine, "MCP_BEARER_TOKEN", mcp_reference)
    _upsert(engine, "AICRM_SECRET_REFERENCE_CUTOVER", "true")
    environment_file.write_text(
        "\n".join(
            (
                f"AUTOMATION_INTERNAL_API_TOKEN='{automation_reference}'",
                f"MCP_BEARER_TOKEN='{mcp_reference}'",
                f"AICRM_SECRET_STORE_DIR='{root}'",
                "AICRM_SECRET_REFERENCE_CUTOVER='true'",
                "",
            )
        ),
        encoding="utf-8",
    )
    os.chmod(environment_file, 0o600)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO admin_operation_logs (before_json, after_json) VALUES (:before_json, :after_json)"),
            {
                "before_json": json.dumps({"value": shared_token, "safe": "keep-me"}),
                "after_json": json.dumps({"nested": {"authorization": f"Bearer {shared_token}"}}),
            },
        )

    first = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment={},
        dry_run=False,
        environment_file=environment_file,
    )

    rotated_mcp_reference = _value(engine, "MCP_BEARER_TOKEN")
    assert shared_token not in json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert first["rotated_internal_tokens"] == 1
    assert first["internal_token_rotations_pending"] == 0
    assert first["audit_rows_redacted"] == 1
    assert first["audit_rows_redaction_pending"] == 0
    assert _value(engine, "AUTOMATION_INTERNAL_API_TOKEN") == automation_reference
    assert rotated_mcp_reference != mcp_reference
    assert store.read(rotated_mcp_reference) != shared_token
    assert f"MCP_BEARER_TOKEN='{rotated_mcp_reference}'" in environment_file.read_text(encoding="utf-8")
    with engine.connect() as conn:
        audit_row = conn.execute(text("SELECT before_json, after_json FROM admin_operation_logs LIMIT 1")).first()
    rendered_audit = "\n".join(str(value or "") for value in (audit_row or ()))
    assert shared_token not in rendered_audit
    assert "keep-me" in rendered_audit
    assert "[redacted]" in rendered_audit

    reconciled = reconcile_secret_reference_cutover(
        engine=engine,
        store=store,
        environment_file=environment_file,
    )
    assert reconciled["ok"] is True
    assert reconciled["duplicate_internal_token_purposes"] == []
    assert reconciled["unsafe_audit_hits"] == 0
    assert reconciled["missing_internal_token_purposes"] == []

    references_after_first = sorted(store.list_references())
    second = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment={},
        dry_run=False,
        environment_file=environment_file,
    )
    assert second["rotated_internal_tokens"] == 0
    assert second["audit_rows_redacted"] == 0
    assert _value(engine, "MCP_BEARER_TOKEN") == rotated_mcp_reference
    assert sorted(store.list_references()) == references_after_first


@pytest.mark.skip(reason="removed with RAUTH shared-token replacement")
def test_secret_migration_dry_run_reports_duplicate_rotation_and_audit_redaction_without_writes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    shared_token = "dry-run-shared-internal-token"
    automation_reference = store.write("AUTOMATION_INTERNAL_API_TOKEN", shared_token)
    mcp_reference = store.write("MCP_BEARER_TOKEN", shared_token)
    _upsert(engine, "AUTOMATION_INTERNAL_API_TOKEN", automation_reference)
    _upsert(engine, "MCP_BEARER_TOKEN", mcp_reference)
    _upsert(engine, "AICRM_SECRET_REFERENCE_CUTOVER", "true")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO admin_operation_logs (before_json, after_json) VALUES (:before_json, '{}')"),
            {"before_json": json.dumps({"value": shared_token})},
        )
    references_before = sorted(store.list_references())

    report = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment={},
        dry_run=True,
    )

    assert report["ok"] is False
    assert report["rotated_internal_tokens"] == 0
    assert report["internal_token_rotations_pending"] == 1
    assert report["audit_rows_redacted"] == 0
    assert report["audit_rows_redaction_pending"] == 1
    assert shared_token not in json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert _value(engine, "AUTOMATION_INTERNAL_API_TOKEN") == automation_reference
    assert _value(engine, "MCP_BEARER_TOKEN") == mcp_reference
    assert sorted(store.list_references()) == references_before
    with engine.connect() as conn:
        before_json = conn.execute(text("SELECT before_json FROM admin_operation_logs LIMIT 1")).scalar_one()
    assert shared_token in str(before_json)


@pytest.mark.skip(reason="removed with RAUTH shared-token replacement")
def test_reconciliation_blocks_incomplete_internal_token_split(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = FileSecretStore(tmp_path / "secrets")
    automation_reference = store.write("AUTOMATION_INTERNAL_API_TOKEN", "automation-only-token")
    _upsert(engine, "AUTOMATION_INTERNAL_API_TOKEN", automation_reference)
    _upsert(engine, "AICRM_SECRET_REFERENCE_CUTOVER", "true")

    report = reconcile_secret_reference_cutover(engine=engine, store=store)

    assert report["ok"] is False
    assert report["internal_token_split_required"] is True
    assert report["missing_internal_token_purposes"] == [
        "archive",
        "callback",
        "group_broadcast",
        "identity",
        "mcp",
    ]


def test_secret_reference_can_roll_back_to_an_existing_immutable_version(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = FileSecretStore(tmp_path / "secrets")
    previous = store.write("WECOM_SECRET", "previous-secret-version")
    current = store.write("WECOM_SECRET", "current-secret-version", current_reference=previous)
    _upsert(engine, "WECOM_SECRET", current)

    report = rollback_secret_reference(
        engine=engine,
        store=store,
        key="WECOM_SECRET",
        reference=previous,
    )

    assert _value(engine, "WECOM_SECRET") == previous
    assert store.read(_value(engine, "WECOM_SECRET")) == "previous-secret-version"
    assert report == {
        "key": "WECOM_SECRET",
        "present": True,
        "source": "app_settings",
        "status": "rolled_back",
        "version": previous.rsplit(":", 1)[-1],
    }
    assert previous not in json.dumps(report, sort_keys=True)


def test_reconciliation_counts_plaintext_unresolved_audit_and_permission_failures(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    raw = "unsafe-audit-secret-sentinel"
    _upsert(engine, "WECOM_SECRET", raw)
    migrate_app_setting_secrets(engine=engine, store=store, environment={}, dry_run=False)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO admin_operation_logs (before_json, after_json) VALUES (:before_json, '{}')"),
            {"before_json": json.dumps({"value": raw})},
        )

    unsafe = reconcile_secret_reference_cutover(engine=engine, store=store)
    assert unsafe["unsafe_audit_hits"] == 1
    assert unsafe["ok"] is False
    assert raw not in json.dumps(unsafe, ensure_ascii=False, sort_keys=True)

    with engine.begin() as conn:
        conn.execute(text("UPDATE admin_operation_logs SET before_json = '{\"value\": \"[redacted]\"}'"))
    safe = reconcile_secret_reference_cutover(engine=engine, store=store)
    assert safe["ok"] is True
    assert safe["plaintext_sensitive_rows"] == 0
    assert safe["unresolved_refs"] == 0
    assert safe["unsafe_audit_hits"] == 0
    assert safe["permission_errors"] == 0

    reference = _value(engine, "WECOM_SECRET")
    version = reference.rsplit(":", 1)[-1]
    os.chmod(root / "WECOM_SECRET" / version, 0o644)
    permissions = reconcile_secret_reference_cutover(engine=engine, store=store)
    assert permissions["permission_errors"] >= 1
    assert permissions["ok"] is False


def test_migration_persists_only_non_sensitive_runtime_environment_values(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    env_file = tmp_path / "runtime.env"
    raw = "environment-file-secret-sentinel"
    env_file.write_text(f"WECOM_SECRET='{raw}'\nEXISTING_FLAG='keep-me'\n", encoding="utf-8")
    os.chmod(env_file, 0o600)

    report = migrate_app_setting_secrets(
        engine=engine,
        store=FileSecretStore(root),
        environment={"WECOM_SECRET": raw},
        dry_run=False,
        environment_file=env_file,
    )

    body = env_file.read_text(encoding="utf-8")
    assert f"AICRM_SECRET_STORE_DIR='{root}'" in body
    assert "AICRM_SECRET_REFERENCE_CUTOVER='true'" in body
    assert raw not in body
    assert "WECOM_SECRET='secretref:file:WECOM_SECRET:" in body
    assert "EXISTING_FLAG='keep-me'" in body
    assert report["environment_file_updated"] is True
    assert raw not in json.dumps(report, ensure_ascii=False, sort_keys=True)

    reconciled = reconcile_secret_reference_cutover(engine=engine, store=FileSecretStore(root), environment_file=env_file)
    assert reconciled["ok"] is True
    assert reconciled["plaintext_environment_entries"] == 0

    current_reference = _value(engine, "WECOM_SECRET")
    rotated_reference = FileSecretStore(root).write(
        "WECOM_SECRET",
        "rotated-environment-version",
        current_reference=current_reference,
    )
    env_file.write_text(f"WECOM_SECRET='{rotated_reference}'\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    mismatched = reconcile_secret_reference_cutover(engine=engine, store=FileSecretStore(root), environment_file=env_file)
    assert mismatched["environment_reference_mismatches"] == 1
    assert mismatched["ok"] is False

    env_file.write_text(f"WECOM_SECRET='{raw}'\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    unsafe = reconcile_secret_reference_cutover(engine=engine, store=FileSecretStore(root), environment_file=env_file)
    assert unsafe["plaintext_environment_entries"] == 1
    assert unsafe["ok"] is False

    os.chmod(env_file, 0o644)
    bad_permissions = reconcile_secret_reference_cutover(engine=engine, store=FileSecretStore(root), environment_file=env_file)
    assert bad_permissions["environment_permission_errors"] == 1
    assert bad_permissions["ok"] is False


def test_migration_collapses_duplicate_sensitive_environment_assignments(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    key = "AICRM_AUTH_SESSION_HASH_PEPPER"
    raw = "duplicate-environment-secret-sentinel"
    reference = store.write(key, "canonical-mcp-secret")
    _upsert(engine, key, reference)
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        f"export {key}='{reference}'\n{key}='{raw}'\nEXISTING_FLAG='keep-me'\n",
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)

    report = migrate_app_setting_secrets(
        engine=engine,
        store=store,
        environment={key: raw},
        dry_run=False,
        environment_file=env_file,
    )

    body = env_file.read_text(encoding="utf-8")
    assignments = [line for line in body.splitlines() if line.lstrip().removeprefix("export ").startswith(f"{key}=")]
    assert assignments == [f"export {key}='{reference}'"]
    assert raw not in body
    assert "EXISTING_FLAG='keep-me'" in body
    assert report["environment_file_updated"] is True

    reconciled = reconcile_secret_reference_cutover(engine=engine, store=store, environment_file=env_file)
    assert reconciled["ok"] is True
    assert reconciled["plaintext_environment_entries"] == 0


def test_migration_cli_failure_never_prints_exception_text(monkeypatch, tmp_path: Path, capsys) -> None:
    raw = "cli-exception-secret-sentinel"

    def fail_migration(**_kwargs):
        raise RuntimeError(raw)

    monkeypatch.setattr(migration_script, "get_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(migration_script, "migrate_app_setting_secrets", fail_migration)

    exit_code = migration_script.main(
        [
            "--execute",
            "--secret-store-dir",
            str(tmp_path / "secrets"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert raw not in output
    assert json.loads(output) == {"error": "RuntimeError", "ok": False}
