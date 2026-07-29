from __future__ import annotations

from sqlalchemy import create_engine, text

import pytest

from aicrm_next.platform.admin_config.application_support import APP_SETTING_DEFINITIONS, EXTRA_SETTING_DEFINITIONS
from aicrm_next.platform.admin_config.config_definitions import CONFIG_DEFINITIONS, get_config_definition
from aicrm_next.platform.admin_config.config_release_repository import ConfigReleaseRepository
from aicrm_next.platform.admin_config.config_releases import ConfigReleaseService
from aicrm_next.platform.admin_config.repository import AdminConfigRepository
from aicrm_next.platform.admin_config.schema import CONFIG_SCHEMA
from aicrm_next.runtime_configuration import RUNTIME_CONFIG_CUTOVER_KEYS_KEY
from aicrm_next.platform.shared.sensitive_data import SECRET_MASK


WECOM_SECRET_REFERENCE = (
    "secretref:file:WECOM_SECRET:v1_0000000000000000_0123456789abcdef"
)


def _service(tmp_path) -> tuple[ConfigReleaseService, object]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'config-releases.sqlite3'}", future=True)
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
    return ConfigReleaseService(ConfigReleaseRepository(engine=engine)), engine


def test_config_definitions_are_typed_owned_and_secret_aware() -> None:
    assert len(CONFIG_DEFINITIONS) == len({definition.key for definition in CONFIG_DEFINITIONS})
    defined_keys = {definition.key for definition in CONFIG_DEFINITIONS}
    schema_keys = {key for section in CONFIG_SCHEMA.values() for key in (section.get("fields") or {})}
    metadata_keys = {str(item["key"]) for item in APP_SETTING_DEFINITIONS} | set(EXTRA_SETTING_DEFINITIONS)
    assert schema_keys | metadata_keys == defined_keys
    assert len(CONFIG_DEFINITIONS) >= 150
    assert all(definition.capability_id for definition in CONFIG_DEFINITIONS)
    assert all(definition.section for definition in CONFIG_DEFINITIONS)

    secret = get_config_definition("WECOM_SECRET")
    assert secret is not None
    assert secret.sensitive is True
    assert secret.storage == "secret_store"
    with pytest.raises(ValueError, match="secret reference"):
        secret.validate("raw-secret")
    with pytest.raises(ValueError, match="must target WECOM_SECRET"):
        secret.validate(
            "secretref:file:OTHER_SECRET:v1_0000000000000000_0123456789abcdef"
        )
    assert secret.validate(WECOM_SECRET_REFERENCE) == WECOM_SECRET_REFERENCE

    internal_events = get_config_definition("AICRM_INTERNAL_EVENTS_ENABLED")
    assert internal_events is not None
    assert internal_events.capability_id == "core.platform"
    assert internal_events.validate("yes") == "true"
    with pytest.raises(ValueError, match="boolean"):
        internal_events.validate("maybe")

    worker_batch = get_config_definition("AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE")
    assert worker_batch is not None
    assert worker_batch.validate("500") == "500"
    with pytest.raises(ValueError, match="at most 500"):
        worker_batch.validate("501")

    payment_mode = get_config_definition("AICRM_NEXT_WECHAT_PAY_MODE")
    assert payment_mode is not None
    assert payment_mode.capability_id == "extension.commerce"
    assert payment_mode.validate("staging") == "staging"
    with pytest.raises(ValueError, match="must be one of"):
        payment_mode.validate("live")

    questionnaire_push = get_config_definition(
        "AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED"
    )
    assert questionnaire_push is not None
    assert questionnaire_push.capability_id == "extension.forms"


def test_config_release_validates_and_publishes_atomically(tmp_path) -> None:
    service, engine = _service(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_CORP_ID', 'ww-before')"))

    draft = service.create_draft({"WECOM_CORP_ID": "ww-after"}, operator="tester")
    assert draft["status"] == "draft"
    validated = service.validate(draft["id"])
    assert validated["status"] == "validated"

    published = service.publish(
        draft["id"],
        expected_checksum=draft["checksum"],
        operator="publisher",
    )
    assert published["status"] == "published"
    assert published["before"]["WECOM_CORP_ID"] == {"exists": True, "value": "ww-before"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT value FROM app_settings WHERE key = 'WECOM_CORP_ID'" )).scalar_one() == "ww-after"
    state = service.profile_state()
    assert state["active_config_release_id"] == draft["id"]
    assert state["activation_mode"] == "observe"


def test_config_release_rollback_creates_new_published_release(tmp_path) -> None:
    service, engine = _service(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_CORP_ID', 'ww-original')"))

    first = service.create_draft({"WECOM_CORP_ID": "ww-next"}, operator="tester")
    service.validate(first["id"])
    published = service.publish(first["id"], expected_checksum=first["checksum"], operator="publisher")
    rolled_back = service.rollback(published["id"], operator="rollback-operator")

    assert rolled_back["status"] == "published"
    assert rolled_back["rollback_of_release_id"] == published["id"]
    assert service.get(published["id"])["status"] == "superseded"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT value FROM app_settings WHERE key = 'WECOM_CORP_ID'" )).scalar_one() == "ww-original"


def test_config_release_rejects_disabled_capability_raw_secret_and_stale_checksum(tmp_path) -> None:
    service, _engine = _service(tmp_path)

    with pytest.raises(ValueError, match="capability is disabled"):
        service.create_draft({"WECHAT_PAY_ENABLED": "true"}, operator="tester")
    with pytest.raises(ValueError, match="secret reference"):
        service.create_draft({"WECOM_SECRET": "raw-secret"}, operator="tester")

    draft = service.create_draft({"WECOM_CORP_ID": "ww-next"}, operator="tester")
    service.validate(draft["id"])
    with pytest.raises(ValueError, match="checksum changed"):
        service.publish(draft["id"], expected_checksum="stale", operator="publisher")


def test_public_config_release_never_returns_secret_reference(tmp_path) -> None:
    service, _engine = _service(tmp_path)
    draft = service.create_draft(
        {"WECOM_SECRET": WECOM_SECRET_REFERENCE},
        operator="tester",
    )

    assert draft["changes"]["WECOM_SECRET"] == SECRET_MASK
    assert "secretref:" not in str(draft)


def test_config_release_shadow_compare_uses_environment_fallback_without_exposing_secrets(tmp_path) -> None:
    service, _engine = _service(tmp_path)
    service = ConfigReleaseService(
        service.repo,
        environment={"AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE": "true"},
    )
    draft = service.create_draft(
        {"AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE": "true"},
        operator="tester",
    )

    comparison = service.shadow_compare(draft["id"])

    assert comparison["ok"] is True
    assert comparison["equivalent_count"] == 1
    assert comparison["rows"][0]["before_source"] == "environment"
    assert comparison["secrets_redacted"] is True


def test_managed_config_cutover_requires_staged_shadow_equivalence(tmp_path) -> None:
    base, engine = _service(tmp_path)
    service = ConfigReleaseService(
        base.repo,
        environment={"WECOM_CORP_ID": "ww-environment"},
    )

    staged = service.create_draft(
        {"WECOM_CORP_ID": "ww-environment"},
        operator="stager",
    )
    comparison = service.shadow_compare(staged["id"])
    assert comparison["rows"][0]["before_source"] == "environment_compat"
    assert comparison["rows"][0]["equivalent"] is True
    service.publish(staged["id"], expected_checksum=staged["checksum"], operator="publisher")

    activation = service.create_draft(
        {RUNTIME_CONFIG_CUTOVER_KEYS_KEY: "WECOM_CORP_ID"},
        operator="stager",
    )
    assert service.validate(activation["id"])["status"] == "validated"
    service.publish(
        activation["id"],
        expected_checksum=activation["checksum"],
        operator="publisher",
    )

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        ).scalar_one() == "WECOM_CORP_ID"


def test_managed_config_cutover_rejects_mismatch_unknown_and_same_release_activation(tmp_path) -> None:
    base, _engine = _service(tmp_path)
    service = ConfigReleaseService(
        base.repo,
        environment={"WECOM_CORP_ID": "ww-environment"},
    )

    mismatch = service.create_draft({"WECOM_CORP_ID": "ww-different"}, operator="stager")
    service.publish(mismatch["id"], expected_checksum=mismatch["checksum"], operator="publisher")

    activation = service.create_draft(
        {RUNTIME_CONFIG_CUTOVER_KEYS_KEY: "WECOM_CORP_ID"},
        operator="stager",
    )
    failed = service.validate(activation["id"])
    assert failed["status"] == "validation_failed"
    assert "differs from the environment compatibility source" in str(failed["validation_errors"])

    unknown = service.create_draft(
        {RUNTIME_CONFIG_CUTOVER_KEYS_KEY: "AICRM_UNKNOWN"},
        operator="stager",
    )
    assert service.validate(unknown["id"])["status"] == "validation_failed"

    combined = service.create_draft(
        {
            "WECOM_AGENT_ID": "1000002",
            RUNTIME_CONFIG_CUTOVER_KEYS_KEY: "WECOM_AGENT_ID",
        },
        operator="stager",
    )
    combined_result = service.validate(combined["id"])
    assert combined_result["status"] == "validation_failed"
    assert "stage WECOM_AGENT_ID in an earlier release" in str(
        combined_result["validation_errors"]
    )


def test_cutover_publish_rejects_staged_value_changed_after_validation(
    tmp_path,
) -> None:
    base, engine = _service(tmp_path)
    service = ConfigReleaseService(
        base.repo,
        environment={"WECOM_CORP_ID": "ww-environment"},
    )
    staged = service.create_draft(
        {"WECOM_CORP_ID": "ww-environment"},
        operator="stager",
    )
    service.publish(
        staged["id"],
        expected_checksum=staged["checksum"],
        operator="publisher",
    )
    activation = service.create_draft(
        {RUNTIME_CONFIG_CUTOVER_KEYS_KEY: "WECOM_CORP_ID"},
        operator="stager",
    )

    class RacingRepository(ConfigReleaseRepository):
        def publish(self, **kwargs):
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE app_settings SET value = 'ww-raced' "
                        "WHERE key = 'WECOM_CORP_ID'"
                    )
                )
            return super().publish(**kwargs)

    racing = ConfigReleaseService(
        RacingRepository(engine=engine),
        environment={"WECOM_CORP_ID": "ww-environment"},
    )
    with pytest.raises(ValueError, match="changed after validation: WECOM_CORP_ID"):
        racing.publish(
            activation["id"],
            expected_checksum=activation["checksum"],
            operator="publisher",
        )


def test_legacy_admin_write_is_blocked_after_key_cutover(tmp_path) -> None:
    _service_instance, engine = _service(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value) VALUES "
                "(:catalog, 'WECOM_CORP_ID'), ('WECOM_CORP_ID', 'ww-published')"
            ),
            {"catalog": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        )

    repository = AdminConfigRepository(engine=engine)
    with pytest.raises(ValueError, match="already owned by Config Release"):
        repository.upsert_app_setting(key="WECOM_CORP_ID", value="ww-legacy")
    with pytest.raises(ValueError, match="must be changed through Config Release"):
        repository.upsert_app_setting(
            key=RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
            value="",
        )

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT value FROM app_settings WHERE key = 'WECOM_CORP_ID'")
        ).scalar_one() == "ww-published"
