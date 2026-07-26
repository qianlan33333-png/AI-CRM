from __future__ import annotations

import sys
import types

import pytest

from aicrm_next.shared import db_session


@pytest.fixture(autouse=True)
def reset_engine_cache():
    db_session.reset_engine_cache_for_tests()
    yield
    db_session.reset_engine_cache_for_tests()


def test_get_engine_and_session_factory_reuse_cached_engine(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'shared.db'}"

    engine = db_session.get_engine(database_url)
    assert db_session.get_engine(database_url) is engine

    session_factory = db_session.get_session_factory(database_url)
    assert db_session.get_session_factory(database_url) is session_factory
    assert session_factory.kw["bind"] is engine


def test_postgres_engine_uses_pool_env_and_application_name(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "3.5")
    monkeypatch.setenv("DB_POOL_RECYCLE", "600")
    monkeypatch.setenv("DB_APPLICATION_NAME", "aicrm-next-test")

    engine = db_session.get_engine("postgres://user:pass@db.internal:5432/aicrm")

    assert engine is not None
    assert calls == [
        (
            "postgresql+psycopg://user:pass@db.internal:5432/aicrm",
            {
                "future": True,
                "pool_pre_ping": True,
                "pool_size": 7,
                "max_overflow": 2,
                "pool_timeout": 3.5,
                "pool_recycle": 600,
                "connect_args": {"application_name": "aicrm-next-test"},
            },
        )
    ]


def test_postgres_web_engine_applies_bounded_query_and_idle_transaction_timeouts(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)
    monkeypatch.setenv("DB_APPLICATION_NAME", "aicrm-next-web")

    db_session.get_engine("postgresql://user:pass@db.internal:5432/aicrm")

    assert calls[0][1]["connect_args"] == {
        "application_name": "aicrm-next-web",
        "options": "-c statement_timeout=30s -c idle_in_transaction_session_timeout=60s",
    }


def test_postgres_background_engine_does_not_inherit_web_timeouts(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)
    monkeypatch.setenv("DB_APPLICATION_NAME", "aicrm-next-queue-internal")

    db_session.get_engine("postgresql://user:pass@db.internal:5432/aicrm")

    assert calls[0][1]["connect_args"] == {
        "application_name": "aicrm-next-queue-internal",
    }


def test_get_pool_settings_redacts_database_url_and_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "9")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "4")
    monkeypatch.setenv("DB_POOL_RECYCLE", "900")
    monkeypatch.setenv("DB_APPLICATION_NAME", "aicrm-next-diagnostics")

    settings = db_session.get_pool_settings("postgres://user:super-secret@db.internal:5432/aicrm")

    assert settings == {
        "database_scheme": "postgresql+psycopg",
        "is_sqlite": False,
        "application_name": "aicrm-next-diagnostics",
        "pool_size": 9,
        "max_overflow": 1,
        "pool_timeout": 4.0,
        "pool_recycle": 900,
    }
    assert "super-secret" not in str(settings)
    assert "db.internal" not in str(settings)
    assert "user" not in str(settings)


def test_database_application_name_uses_libpq_fallback_and_sanitizes(monkeypatch) -> None:
    monkeypatch.delenv("DB_APPLICATION_NAME", raising=False)
    monkeypatch.setenv("PGAPPNAME", "aicrm next / cron:sync")

    assert db_session.database_application_name() == "aicrm-next-cron:sync"


def test_engine_initialization_log_does_not_include_database_secret(monkeypatch) -> None:
    class FakeEngine:
        def dispose(self) -> None:
            pass

    logs: list[str] = []

    def fake_info(message: str, *args, **kwargs) -> None:
        logs.append(message % args)

    monkeypatch.setattr(db_session, "create_engine", lambda url, **kwargs: FakeEngine())
    monkeypatch.setattr(db_session.LOGGER, "info", fake_info)
    monkeypatch.setenv("DB_APPLICATION_NAME", "aicrm-next-test")

    db_session.get_engine("postgresql://user:super-secret@db.internal:5432/aicrm")

    log_text = "\n".join(logs)
    assert "initializing SQLAlchemy engine" in log_text
    assert "aicrm-next-test" in log_text
    assert "super-secret" not in log_text
    assert "db.internal" not in log_text
    assert "postgresql://user" not in log_text


def test_sqlite_engine_does_not_receive_postgres_pool_kwargs(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)

    db_session.get_engine("sqlite:///:memory:")

    assert calls == [("sqlite:///:memory:", {"future": True})]


def test_reset_engine_cache_disposes_cached_engines(monkeypatch) -> None:
    disposed: list[bool] = []

    class FakeEngine:
        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(db_session, "create_engine", lambda url, **kwargs: FakeEngine())

    db_session.get_engine("postgresql://user:pass@db.internal:5432/aicrm")
    db_session.reset_engine_cache_for_tests()

    assert disposed == [True]


def test_connect_pooled_postgres_falls_back_for_psycopg_test_double(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeConnection:
        pass

    def connect(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeConnection()

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)

    connection = db_session.connect_pooled_postgres("postgresql://test/test")

    assert isinstance(connection, FakeConnection)
    assert calls == [("postgresql://test/test", {"autocommit": False})]


def test_pooled_psycopg_connection_uses_cursor_row_factory_without_mutating_driver(monkeypatch) -> None:
    from psycopg.rows import dict_row

    cursor_calls: list[dict[str, object]] = []
    executed: list[tuple[str, object | None]] = []

    class FakeCursor:
        def execute(self, query: str, params: object | None = None):
            executed.append((query, params))
            return self

    class FakeDriverConnection:
        row_factory = "tuple_row"

        def cursor(self, **kwargs):
            cursor_calls.append(kwargs)
            return FakeCursor()

    class FakePooledConnection:
        def __init__(self) -> None:
            self.driver_connection = FakeDriverConnection()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    pooled = FakePooledConnection()

    class FakeEngine:
        def raw_connection(self):
            return pooled

    monkeypatch.setattr(db_session, "get_engine", lambda *args, **kwargs: FakeEngine())

    connection = db_session.PooledPsycopgConnection("postgresql://test/test")
    cursor = connection.execute("SELECT 1 WHERE id = %s", (1,))

    assert isinstance(cursor, FakeCursor)
    assert executed == [("SELECT 1 WHERE id = %s", (1,))]
    assert cursor_calls == [{"row_factory": dict_row}]
    assert pooled.driver_connection.row_factory == "tuple_row"


def test_session_scope_cleanup_error_does_not_mask_business_exception(monkeypatch) -> None:
    class FailingCleanupSession:
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(db_session, "get_session_factory", lambda *args, **kwargs: lambda: FailingCleanupSession())

    with pytest.raises(RuntimeError, match="business failed"):
        with db_session.session_scope():
            raise RuntimeError("business failed")


def test_get_db_cleanup_error_does_not_mask_business_exception(monkeypatch) -> None:
    class FailingCleanupSession:
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(db_session, "get_session_factory", lambda *args, **kwargs: lambda: FailingCleanupSession())
    dependency = db_session.get_db()
    next(dependency)

    with pytest.raises(RuntimeError, match="business failed"):
        dependency.throw(RuntimeError("business failed"))
