from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from aicrm_next.admin_config.application import AdminConfigReadService, AdminConfigWriteCommand
from aicrm_next.admin_config.repository import AdminConfigRepository
from aicrm_next.message_archive import repo as message_archive_repo
from aicrm_next.message_archive import sync_service as message_archive_sync
from aicrm_next.questionnaire import repo as questionnaire_repo
from aicrm_next.shared import runtime_settings
from aicrm_next.shared import runtime as shared_runtime
from aicrm_next.shared.runtime_settings import runtime_setting, runtime_settings_request_scope
from aicrm_next.shared.secret_store import FileSecretStore, is_secret_reference, parse_secret_reference
from aicrm_next.shared.secret_store import SENSITIVE_SETTING_KEYS


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'settings.sqlite3'}", future=True)
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
                    operator TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL DEFAULT '',
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    return engine


def _db_value(engine: Engine, key: str) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}).scalar_one())


def test_sensitive_repository_writes_only_references_and_rotates_idempotently(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(root))
    repository = AdminConfigRepository(engine=engine)

    first = repository.upsert_app_setting(key="WECOM_SECRET", value="complete-secret-v1")
    unchanged = repository.upsert_app_setting(key="WECOM_SECRET", value="complete-secret-v1")
    rotated = repository.upsert_app_setting(key="WECOM_SECRET", value="complete-secret-v2")

    assert is_secret_reference(first["value"])
    assert unchanged["value"] == first["value"]
    assert rotated["value"] != first["value"]
    assert _db_value(engine, "WECOM_SECRET") == rotated["value"]
    assert "complete-secret" not in _db_value(engine, "WECOM_SECRET")
    assert FileSecretStore(root).read(first["value"]) == "complete-secret-v1"
    assert FileSecretStore(root).read(rotated["value"]) == "complete-secret-v2"
    assert len(list((root / "WECOM_SECRET").iterdir())) == 2

    plain = repository.upsert_app_setting(key="WECOM_CORP_ID", value="ww-public")
    assert plain["value"] == "ww-public"
    assert _db_value(engine, "WECOM_CORP_ID") == "ww-public"


def test_admin_write_command_migrates_raw_row_and_does_not_repeat_audit(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(tmp_path / "secrets"))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_SECRET', 'complete-legacy-secret')"))
    repository = AdminConfigRepository(engine=engine)
    command = AdminConfigWriteCommand(repository)

    first = command.execute({"WECOM_SECRET": "complete-legacy-secret"}, operator="security-test")
    second = command.execute({"WECOM_SECRET": "complete-legacy-secret"}, operator="security-test")

    assert len(first) == 1
    assert second == []
    assert first[0]["display_value"] == "[redacted]"
    assert first[0]["configured"] is True
    assert first[0]["version"]
    assert "value" not in first[0]
    assert "complete-legacy-secret" not in json.dumps(first)
    assert is_secret_reference(_db_value(engine, "WECOM_SECRET"))
    with engine.connect() as conn:
        audit_count = conn.execute(text("SELECT COUNT(*) FROM admin_operation_logs")).scalar_one()
        audit_json = conn.execute(text("SELECT after_json FROM admin_operation_logs LIMIT 1")).scalar_one()
    assert audit_count == 1
    assert "complete-legacy-secret" not in str(audit_json)


def test_runtime_setting_resolves_db_and_environment_references(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(root))
    store = FileSecretStore(root)
    db_reference = store.write("WECOM_SECRET", "complete-db-secret")
    env_reference = store.write("WECOM_CONTACT_SECRET", "complete-env-secret")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('WECOM_SECRET', :value)"),
            {"value": db_reference},
        )
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)
    monkeypatch.setenv("WECOM_CONTACT_SECRET", env_reference)

    assert runtime_setting("WECOM_SECRET") == "complete-db-secret"
    assert runtime_setting("WECOM_CONTACT_SECRET") == "complete-env-secret"


def test_typed_runtime_integer_uses_central_environment_fallback(monkeypatch) -> None:
    def unavailable_engine():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime_settings, "get_engine", unavailable_engine)
    monkeypatch.setenv("AICRM_TYPED_INTEGER", "600")

    assert runtime_settings.runtime_int(
        "AICRM_TYPED_INTEGER",
        50,
        minimum=1,
        maximum=500,
    ) == 500

    monkeypatch.setenv("AICRM_TYPED_INTEGER", "invalid")
    assert runtime_settings.runtime_int("AICRM_TYPED_INTEGER", 50, minimum=1) == 50


def test_environment_snapshot_is_detached_from_source_mapping() -> None:
    source = {"AICRM_FLAG": "before"}

    snapshot = runtime_settings.environment_snapshot(source, environment=source)
    source["AICRM_FLAG"] = "after"

    assert snapshot == {"AICRM_FLAG": "before"}


def test_managed_runtime_setting_preserves_environment_until_explicit_cutover(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('WECOM_CORP_ID', 'ww-published')")
        )
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)
    monkeypatch.setenv("WECOM_CORP_ID", "ww-environment")

    assert runtime_settings.managed_runtime_setting("WECOM_CORP_ID") == "ww-environment"
    assert runtime_settings.runtime_setting("WECOM_CORP_ID") == "ww-environment"

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('AICRM_RUNTIME_CONFIG_CUTOVER_KEYS', 'WECOM_CORP_ID')")
        )

    assert runtime_settings.managed_runtime_setting("WECOM_CORP_ID") == "ww-published"
    assert runtime_settings.runtime_setting("WECOM_CORP_ID") == "ww-published"


def test_gateway_secret_preserves_environment_then_cuts_over_to_secret_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(root))
    published_reference = FileSecretStore(root).write(
        "AICRM_HUANGYOUCAN_DB_PASSWORD",
        "published-password",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value) "
                "VALUES ('AICRM_HUANGYOUCAN_DB_PASSWORD', :value)"
            ),
            {"value": published_reference},
        )
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_PASSWORD", "environment-password")

    assert (
        runtime_settings.runtime_setting("AICRM_HUANGYOUCAN_DB_PASSWORD")
        == "environment-password"
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value) "
                "VALUES ('AICRM_RUNTIME_CONFIG_CUTOVER_KEYS', "
                "'AICRM_HUANGYOUCAN_DB_PASSWORD')"
            )
        )

    assert (
        runtime_settings.runtime_setting("AICRM_HUANGYOUCAN_DB_PASSWORD")
        == "published-password"
    )


def test_runtime_setting_preserves_valid_client_secret_reference(monkeypatch) -> None:
    reference = (
        "secretref:file:AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET:"
        "v1_0000000000000000_0123456789abcdef"
    )

    def unavailable_engine():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime_settings, "get_engine", unavailable_engine)
    monkeypatch.setenv("AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF", reference)

    assert (
        runtime_setting("AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF")
        == reference
    )


def test_runtime_settings_request_scope_uses_one_database_snapshot(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('AICRM_PUBLIC_BASE_URL', 'https://example.invalid')")
        )
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)
    monkeypatch.setenv("SECRET_KEY", "request-scoped-signing-secret")
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if "app_settings" in statement:
            statements.append(statement)

    with runtime_settings_request_scope():
        assert runtime_setting("AICRM_PUBLIC_BASE_URL") == "https://example.invalid"
        assert runtime_setting("SECRET_KEY") == "request-scoped-signing-secret"
        assert runtime_setting("MISSING_SETTING", "fallback") == "fallback"
        assert runtime_setting("AICRM_PUBLIC_BASE_URL") == "https://example.invalid"

    assert len(statements) == 1
    assert "SELECT key, value FROM app_settings" in statements[0]

    assert runtime_setting("AICRM_PUBLIC_BASE_URL") == "https://example.invalid"
    assert len(statements) == 2


def test_runtime_settings_request_scope_can_be_invalidated_after_write(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('RUNTIME_FLAG', 'before')"))
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)

    with runtime_settings_request_scope():
        assert runtime_setting("RUNTIME_FLAG") == "before"
        with engine.begin() as conn:
            conn.execute(text("UPDATE app_settings SET value = 'after' WHERE key = 'RUNTIME_FLAG'"))
        runtime_settings.invalidate_runtime_settings_request_snapshot()
        assert runtime_setting("RUNTIME_FLAG") == "after"


def test_nested_runtime_settings_scope_reuses_outer_snapshot(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('RUNTIME_FLAG', 'before')"))
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)

    with runtime_settings_request_scope():
        assert runtime_setting("RUNTIME_FLAG") == "before"
        with runtime_settings_request_scope():
            with engine.begin() as conn:
                conn.execute(text("UPDATE app_settings SET value = 'after' WHERE key = 'RUNTIME_FLAG'"))
            assert runtime_setting("RUNTIME_FLAG") == "before"
        assert runtime_setting("RUNTIME_FLAG") == "before"

    assert runtime_setting("RUNTIME_FLAG") == "after"


def test_legacy_shared_runtime_facade_resolves_secret_references(monkeypatch, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(root))
    reference = FileSecretStore(root).write("SECRET_KEY", "complete-signing-secret")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('SECRET_KEY', :value)"), {"value": reference})
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)

    assert shared_runtime.runtime_setting("SECRET_KEY") == "complete-signing-secret"
    assert shared_runtime.require_signing_secret("SECRET_KEY", local_fallback="unused") == b"complete-signing-secret"


def test_runtime_setting_allows_expand_mode_but_rejects_raw_secret_after_cutover(monkeypatch, tmp_path: Path, caplog) -> None:
    engine = _engine(tmp_path)
    raw_secret = "complete-legacy-secret"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_SECRET', :value)"), {"value": raw_secret})
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)

    assert runtime_setting("WECOM_SECRET", "missing") == raw_secret

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('AICRM_SECRET_REFERENCE_CUTOVER', 'true')")
        )
    caplog.clear()
    assert runtime_setting("WECOM_SECRET", "missing") == "missing"
    assert raw_secret not in caplog.text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_settings WHERE key = 'WECOM_SECRET'"))
    monkeypatch.setenv("WECOM_SECRET", raw_secret)
    assert runtime_setting("WECOM_SECRET", "missing") == "missing"
    assert runtime_setting("WECOM_CORP_ID", "ww-default") == "ww-default"


def test_managed_sensitive_environment_value_fails_closed_when_secret_cutover_is_environment_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value) VALUES "
                "('AICRM_SECRET_REFERENCE_CUTOVER', 'false')"
            )
        )
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)
    monkeypatch.setenv("AICRM_SECRET_REFERENCE_CUTOVER", "true")
    monkeypatch.setenv("WECOM_SECRET", "complete-legacy-secret")

    assert runtime_setting("WECOM_SECRET", "missing") == "missing"


def test_runtime_setting_fails_closed_for_unreadable_reference_without_logging_it(monkeypatch, tmp_path: Path, caplog) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "secrets"
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(root))
    unreadable = "secretref:file:WECOM_SECRET:v1_0000000000000000_0123456789abcdef"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_SECRET', :value)"), {"value": unreadable})
    monkeypatch.setattr(runtime_settings, "get_engine", lambda: engine)

    assert runtime_setting("WECOM_SECRET", "missing") == "missing"
    assert unreadable not in caplog.text


class _ReadRepository:
    def __init__(self, reference: str) -> None:
        self.reference = reference

    def get_app_setting(self, key: str):
        if key == "WECOM_SECRET":
            return {"key": key, "value": self.reference, "updated_at": "2026-07-10T07:00:00Z"}
        return None

    def latest_audit_map(self, **_kwargs):
        return {}

    def list_audit_logs(self, **_kwargs):
        return []


def test_admin_read_payload_exposes_only_mask_presence_version_and_timestamp() -> None:
    reference = "secretref:file:WECOM_SECRET:v1_0000000000000000_0123456789abcdef"
    payload = AdminConfigReadService(_ReadRepository(reference)).list_app_settings(query="WECOM_SECRET", scope="")  # type: ignore[arg-type]
    row = payload["rows"][0]

    assert row["key"] == "WECOM_SECRET"
    assert row["value"] == ""
    assert row["display_value"] == "[redacted]"
    assert row["configured"] is True
    assert row["version"] == parse_secret_reference(reference).version
    assert row["updated_at"] == "2026-07-10T07:00:00Z"
    assert reference not in json.dumps(payload, ensure_ascii=False)


def test_direct_secret_consumers_use_shared_runtime_resolution(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def resolve(key: str, default: str = "") -> str:
        seen.append((key, default))
        return "resolved-secret"

    monkeypatch.setattr(message_archive_repo, "runtime_setting", resolve)
    monkeypatch.setattr(message_archive_sync, "runtime_setting", resolve)
    monkeypatch.setattr(questionnaire_repo, "runtime_setting", resolve)
    questionnaire_repository = object.__new__(questionnaire_repo.PostgresQuestionnaireReadRepository)

    assert message_archive_repo.read_archive_app_setting("WECOM_ARCHIVE_SECRET") == "resolved-secret"
    assert message_archive_sync._setting("WECOM_ARCHIVE_SECRET") == "resolved-secret"
    assert questionnaire_repository.get_app_setting("QUESTIONNAIRE_SUBMIT_WEBHOOK_TOKEN") == "resolved-secret"
    assert seen == [
        ("WECOM_ARCHIVE_SECRET", ""),
        ("WECOM_ARCHIVE_SECRET", ""),
        ("QUESTIONNAIRE_SUBMIT_WEBHOOK_TOKEN", ""),
    ]


def test_sensitive_settings_never_use_direct_literal_environment_reads() -> None:
    root = Path(__file__).resolve().parents[1] / "aicrm_next"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for key in sorted(SENSITIVE_SETTING_KEYS):
            if f'os.getenv("{key}"' in source or f"os.getenv('{key}'" in source:
                violations.append(f"{path.relative_to(root.parent)}:{key}")

    assert violations == []
