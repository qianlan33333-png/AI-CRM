from __future__ import annotations

from sqlalchemy import create_engine, text

import pytest

from aicrm_next.platform.admin_config.config_release_repository import ConfigReleaseRepository
from aicrm_next.platform.admin_config.config_releases import ConfigReleaseService
from aicrm_next.runtime_configuration import RUNTIME_CONFIG_CUTOVER_KEYS_KEY
from scripts.ops.manage_runtime_config_release import (
    RuntimeConfigOperationError,
    activate_runtime_config,
    required_activation_confirmation,
    required_rollback_confirmation,
    required_stage_confirmation,
    rollback_runtime_config,
    runtime_config_inventory,
    stage_runtime_config,
)


SHA = "a" * 40


def _service(tmp_path, *, environment=None):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'runtime-config.sqlite3'}", future=True)
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
                CREATE TABLE config_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_key TEXT NOT NULL UNIQUE,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    changes_json TEXT NOT NULL DEFAULT '{}',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    validation_errors_json TEXT NOT NULL DEFAULT '[]',
                    checksum TEXT NOT NULL,
                    based_on_release_id INTEGER,
                    rollback_of_release_id INTEGER,
                    created_by TEXT NOT NULL,
                    published_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    validated_at TEXT,
                    published_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_config_releases_profile_published
                ON config_releases(profile_id)
                WHERE status = 'published'
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE deployment_profile_state (
                    profile_id TEXT PRIMARY KEY,
                    core_version TEXT NOT NULL,
                    config_schema_version INTEGER NOT NULL,
                    activation_mode TEXT NOT NULL,
                    enabled_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    runtime_roles_json TEXT NOT NULL DEFAULT '[]',
                    active_config_release_id INTEGER,
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    repo = ConfigReleaseRepository(engine=engine)
    return ConfigReleaseService(repo, environment=environment or {}), engine


def test_inventory_redacts_values_and_blocks_raw_secret(tmp_path) -> None:
    environment = {
        "WECOM_CORP_ID": "ww-private-corp-id",
        "WECOM_SECRET": "never-print-this-secret",
    }
    service, _engine = _service(tmp_path, environment=environment)

    report = runtime_config_inventory(
        service=service,
        environment=environment,
        requested_keys=("WECOM_CORP_ID", "WECOM_SECRET"),
    )

    assert report["ok"] is False
    assert report["stage_changes"] == {"WECOM_CORP_ID": "ww-private-corp-id"}
    assert report["blockers"] == [
        {"key": "WECOM_SECRET", "error": "raw_secret_must_be_migrated_first"}
    ]
    assert "ww-private-corp-id" not in str(report["items"])
    assert "never-print-this-secret" not in str(report)


def test_stage_normalizes_boolean_semantics_and_publishes_without_cutover(tmp_path) -> None:
    environment = {"AICRM_INTERNAL_EVENTS_ENABLED": "1"}
    service, engine = _service(tmp_path, environment=environment)

    preview = stage_runtime_config(
        service=service,
        environment=environment,
        requested_keys=("AICRM_INTERNAL_EVENTS_ENABLED",),
        operator="tester",
        release_sha=SHA,
        confirmation="",
        execute=False,
    )
    assert preview["required_confirmation"] == required_stage_confirmation(SHA)

    published = stage_runtime_config(
        service=service,
        environment=environment,
        requested_keys=("AICRM_INTERNAL_EVENTS_ENABLED",),
        operator="tester",
        release_sha=SHA,
        confirmation=required_stage_confirmation(SHA),
        execute=True,
    )
    assert published["published"] is True
    assert published["shadow_equivalent_count"] == 1
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = 'AICRM_INTERNAL_EVENTS_ENABLED'")
        ).scalar_one() == "true"
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        ).first() is None


def test_activation_is_bounded_exactly_confirmed_and_rollback_restores_catalog(tmp_path) -> None:
    environment = {
        "WECOM_CORP_ID": "ww-current",
        "WECOM_AGENT_ID": "1000002",
    }
    service, engine = _service(tmp_path, environment=environment)
    staged = stage_runtime_config(
        service=service,
        environment=environment,
        requested_keys=("WECOM_CORP_ID", "WECOM_AGENT_ID"),
        operator="stager",
        release_sha=SHA,
        confirmation=required_stage_confirmation(SHA),
        execute=True,
    )
    assert staged["published"] is True
    keys = ("WECOM_AGENT_ID", "WECOM_CORP_ID")
    preview = activate_runtime_config(
        service=service,
        environment=environment,
        requested_keys=keys,
        capability_id="",
        operator="activator",
        release_sha=SHA,
        confirmation="",
        execute=False,
    )
    assert preview["required_confirmation"] == required_activation_confirmation(SHA, keys)
    with pytest.raises(RuntimeConfigOperationError, match="confirmation"):
        activate_runtime_config(
            service=service,
            environment=environment,
            requested_keys=keys,
            capability_id="",
            operator="activator",
            release_sha=SHA,
            confirmation="wrong",
            execute=True,
        )

    activated = activate_runtime_config(
        service=service,
        environment=environment,
        requested_keys=keys,
        capability_id="",
        operator="activator",
        release_sha=SHA,
        confirmation=preview["required_confirmation"],
        execute=True,
    )
    assert activated["active_key_count"] == 2
    activation_release_id = activated["release_id"]
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        ).scalar_one() == "WECOM_AGENT_ID,WECOM_CORP_ID"

    rollback_confirmation = required_rollback_confirmation(SHA, activation_release_id)
    rolled_back = rollback_runtime_config(
        service=service,
        release_id=activation_release_id,
        operator="rollback-operator",
        release_sha=SHA,
        confirmation=rollback_confirmation,
        execute=True,
    )
    assert rolled_back["rollback_of_release_id"] == activation_release_id
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        ).first() is None


def test_activation_rejects_unstaged_or_oversized_batches(tmp_path) -> None:
    environment = {"WECOM_CORP_ID": "ww-current"}
    service, _engine = _service(tmp_path, environment=environment)
    with pytest.raises(RuntimeConfigOperationError, match="no staged value"):
        activate_runtime_config(
            service=service,
            environment=environment,
            requested_keys=("WECOM_CORP_ID",),
            capability_id="",
            operator="tester",
            release_sha=SHA,
            confirmation="",
            execute=False,
        )

    stage_runtime_config(
        service=service,
        environment=environment,
        requested_keys=("WECOM_CORP_ID",),
        operator="tester",
        release_sha=SHA,
        confirmation=required_stage_confirmation(SHA),
        execute=True,
    )
    with pytest.raises(RuntimeConfigOperationError, match="max_keys=0"):
        activate_runtime_config(
            service=service,
            environment=environment,
            requested_keys=("WECOM_CORP_ID",),
            capability_id="",
            operator="tester",
            release_sha=SHA,
            confirmation="",
            execute=False,
            max_keys=0,
        )
