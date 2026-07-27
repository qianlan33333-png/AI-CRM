from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.shared import postgres_connection, runtime, signed_context, signed_session


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_shared_runtime_setting_reads_env_without_flask(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "next-runtime-secret")
    assert runtime.runtime_setting("AICRM_NEXT_ACTION_TOKEN_SECRET") == "next-runtime-secret"
    assert runtime.runtime_setting("MISSING_RUNTIME_SETTING", "fallback") == "fallback"


def test_shared_signed_context_has_no_flask_config_imports(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "shared-sidebar-secret")

    token = signed_context.build_sidebar_product_context_token(external_userid="wm_shared_001")
    result = signed_context.load_sidebar_product_context_token(token)

    assert result["ok"] is True
    owner_token = signed_context.build_sidebar_owner_context_token(
        viewer_userid="viewer_001",
        external_userid="external_001",
        session_id="session_001",
        corp_id="ww-test",
    )
    owner_result = signed_context.load_sidebar_owner_context_token(owner_token)
    assert owner_result["ok"] is True
    assert owner_result["context"]["viewer_userid"] == "viewer_001"
    source = Path("aicrm_next/platform/shared/signed_context.py").read_text(encoding="utf-8")
    assert "current_app" not in source
    assert "flask" not in source


def test_shared_signed_session_has_no_flask_config_imports(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "shared-session-secret")

    token = signed_session.sign_session_payload({"wecom_userid": "viewer_001", "iat": 4102444800})
    result = signed_session.verify_session_payload(token, max_age_seconds=4102444800)

    assert result is not None
    assert result["wecom_userid"] == "viewer_001"
    source = Path("aicrm_next/platform/shared/signed_session.py").read_text(encoding="utf-8")
    assert "current_app" not in source
    assert "flask" not in source


def test_postgres_connection_uses_runtime_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://next:next@127.0.0.1:5432/aicrm_next")
    assert postgres_connection._database_url() == "postgresql://next:next@127.0.0.1:5432/aicrm_next"


def test_postgres_db_session_reuses_contextvar_connection(monkeypatch) -> None:
    created: list[_FakeConnection] = []

    def fake_connect() -> postgres_connection.PostgresConnection:
        raw = _FakeConnection()
        created.append(raw)
        return postgres_connection.PostgresConnection(raw)

    monkeypatch.setattr(postgres_connection, "_connect", fake_connect)

    with postgres_connection.db_session() as outer:
        assert postgres_connection.get_db() is outer
        with postgres_connection.db_session() as inner:
            assert inner is outer

    assert len(created) == 1
    assert created[0].closed is True


def test_postgres_connection_has_no_flask_imports() -> None:
    source = Path("aicrm_next/platform/shared/postgres_connection.py").read_text(encoding="utf-8")
    assert "current_app" not in source
    assert "has_app_context" not in source
    assert "from flask" not in source


def test_runtime_requirements_do_not_depend_on_flask() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "\nflask" not in f"\n{requirements}"
