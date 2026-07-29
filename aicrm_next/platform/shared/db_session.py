from __future__ import annotations

import os
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, TypedDict

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aicrm_next.platform.shared.config import Settings, get_settings
from aicrm_next.platform.shared.query_telemetry import (
    InstrumentedDbApiCursor,
    begin_query,
    finish_query,
    install_sqlalchemy_query_telemetry,
)
from aicrm_next.platform.shared.runtime import raw_database_url
from aicrm_next.platform.shared.safe_logging import safe_log_exception

LOGGER = logging.getLogger(__name__)
_APPLICATION_NAME_FORBIDDEN = re.compile(r"[^A-Za-z0-9_.:-]+")
_WEB_DATABASE_APPLICATION_NAME = "aicrm-next-web"
_WEB_DATABASE_OPTIONS = (
    "-c statement_timeout=30s "
    "-c idle_in_transaction_session_timeout=60s"
)


@dataclass(frozen=True)
class _EngineCacheKey:
    database_url: str
    pool_size: int
    max_overflow: int
    pool_timeout: float
    pool_recycle: int
    application_name: str


_ENGINE_CACHE: dict[_EngineCacheKey, Engine] = {}
_SESSION_FACTORY_CACHE: dict[_EngineCacheKey, sessionmaker[Session]] = {}


class PoolSettings(TypedDict):
    database_scheme: str
    is_sqlite: bool
    application_name: str
    pool_size: int
    max_overflow: int
    pool_timeout: float
    pool_recycle: int


def _env_int(name: str, *, default: int) -> int:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        return default
    return int(value)


def _env_float(name: str, *, default: float) -> float:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        return default
    return float(value)


def _raw_configured_database_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return raw_database_url() or settings.database_url


def database_application_name(*, default: str = "aicrm-next-web") -> str:
    """Return a safe PostgreSQL client label without request or customer data."""

    configured = str(
        os.getenv("DB_APPLICATION_NAME")
        or os.getenv("PGAPPNAME")
        or default
        or "aicrm-next"
    ).strip()
    normalized = _APPLICATION_NAME_FORBIDDEN.sub("-", configured).strip("-.")
    return (normalized or "aicrm-next")[:63]


def get_sqlalchemy_database_url(database_url: str | None = None, settings: Settings | None = None) -> str:
    url = str(database_url or _raw_configured_database_url(settings) or "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith(("sqlite://", "sqlite+pysqlite://"))


def _database_scheme(database_url: str) -> str:
    return database_url.split("://", 1)[0] if "://" in database_url else ""


def _cache_key(database_url: str | None = None, settings: Settings | None = None) -> _EngineCacheKey:
    return _EngineCacheKey(
        database_url=get_sqlalchemy_database_url(database_url, settings),
        pool_size=_env_int("DB_POOL_SIZE", default=5),
        max_overflow=_env_int("DB_MAX_OVERFLOW", default=0),
        pool_timeout=_env_float("DB_POOL_TIMEOUT", default=5.0),
        pool_recycle=_env_int("DB_POOL_RECYCLE", default=1800),
        application_name=database_application_name(),
    )


def _pool_settings_from_key(key: _EngineCacheKey) -> PoolSettings:
    return {
        "database_scheme": _database_scheme(key.database_url),
        "is_sqlite": _is_sqlite_url(key.database_url),
        "application_name": key.application_name,
        "pool_size": key.pool_size,
        "max_overflow": key.max_overflow,
        "pool_timeout": key.pool_timeout,
        "pool_recycle": key.pool_recycle,
    }


def get_pool_settings(database_url: str | None = None, settings: Settings | None = None) -> PoolSettings:
    """Return non-sensitive pool settings for diagnostics and tests."""

    return _pool_settings_from_key(_cache_key(database_url, settings))


def _postgres_connect_args(application_name: str) -> dict[str, str]:
    connect_args = {"application_name": application_name}
    if application_name == _WEB_DATABASE_APPLICATION_NAME:
        connect_args["options"] = _WEB_DATABASE_OPTIONS
    return connect_args


def get_engine(database_url: str | None = None, settings: Settings | None = None) -> Engine:
    key = _cache_key(database_url, settings)
    engine = _ENGINE_CACHE.get(key)
    if engine is not None:
        return engine

    kwargs: dict[str, object] = {"future": True}
    if not _is_sqlite_url(key.database_url):
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_size": key.pool_size,
                "max_overflow": key.max_overflow,
                "pool_timeout": key.pool_timeout,
                "pool_recycle": key.pool_recycle,
                "connect_args": _postgres_connect_args(key.application_name),
            }
        )
    pool_settings = _pool_settings_from_key(key)
    LOGGER.info(
        "initializing SQLAlchemy engine application_name=%s pool_size=%s max_overflow=%s pool_timeout=%s pool_recycle=%s database_scheme=%s is_sqlite=%s",
        pool_settings["application_name"],
        pool_settings["pool_size"],
        pool_settings["max_overflow"],
        pool_settings["pool_timeout"],
        pool_settings["pool_recycle"],
        pool_settings["database_scheme"],
        pool_settings["is_sqlite"],
    )
    engine = create_engine(key.database_url, **kwargs)
    if isinstance(engine, Engine):
        install_sqlalchemy_query_telemetry(engine)
    _ENGINE_CACHE[key] = engine
    return engine


def get_session_factory(database_url: str | None = None, settings: Settings | None = None) -> sessionmaker[Session]:
    key = _cache_key(database_url, settings)
    session_factory = _SESSION_FACTORY_CACHE.get(key)
    if session_factory is not None:
        return session_factory
    session_factory = sessionmaker(bind=get_engine(database_url, settings), future=True)
    _SESSION_FACTORY_CACHE[key] = session_factory
    return session_factory


@contextmanager
def session_scope(
    database_url: str | None = None,
    settings: Settings | None = None,
    *,
    commit: bool = False,
) -> Iterator[Session]:
    session = get_session_factory(database_url, settings)()
    try:
        yield session
        if commit:
            session.commit()
        else:
            try:
                session.rollback()
            except Exception as exc:
                safe_log_exception(
                    LOGGER,
                    "failed to rollback SQLAlchemy session after session_scope success",
                    exc,
                    level=logging.WARNING,
                )
    except Exception:
        try:
            session.rollback()
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "failed to rollback SQLAlchemy session after session_scope exception",
                exc,
                level=logging.WARNING,
            )
        raise
    finally:
        try:
            session.close()
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "failed to close SQLAlchemy session in session_scope",
                exc,
                level=logging.WARNING,
            )


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    rolled_back = False
    try:
        yield session
    except Exception:
        try:
            session.rollback()
            rolled_back = True
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "failed to rollback SQLAlchemy session after get_db exception",
                exc,
                level=logging.WARNING,
            )
        raise
    finally:
        if not rolled_back:
            try:
                session.rollback()
            except Exception as exc:
                safe_log_exception(
                    LOGGER,
                    "failed to rollback SQLAlchemy session in get_db cleanup",
                    exc,
                    level=logging.WARNING,
                )
        try:
            session.close()
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "failed to close SQLAlchemy session in get_db cleanup",
                exc,
                level=logging.WARNING,
            )


def connect_raw_postgres(database_url: str, *, autocommit: bool = False):
    import psycopg

    return psycopg.connect(database_url, autocommit=autocommit)


def _psycopg_can_back_sqlalchemy() -> bool:
    try:
        import psycopg
    except Exception:
        return False
    return bool(getattr(psycopg, "paramstyle", None)) and callable(getattr(psycopg, "connect", None))


class PooledPsycopgConnection:
    def __init__(self, database_url: str | None = None) -> None:
        self._pooled = get_engine(database_url).raw_connection()
        self._conn = getattr(self._pooled, "driver_connection", None) or getattr(self._pooled, "connection", None)
        if self._conn is None:
            self._conn = self._pooled

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()

    def _raw_cursor(self):
        try:
            from psycopg.rows import dict_row

            return self._conn.cursor(row_factory=dict_row)
        except Exception as exc:
            safe_log_exception(LOGGER, "failed to open pooled psycopg dict cursor", exc, level=logging.DEBUG)
            return self._conn.cursor()

    def cursor(self) -> InstrumentedDbApiCursor:
        return InstrumentedDbApiCursor(self._raw_cursor())

    def execute(self, query: str, params: object | None = None):
        cursor = self._raw_cursor()
        execution = begin_query(query)
        try:
            result = cursor.execute(query, params)
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        return result

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._pooled.close()


def connect_pooled_postgres(database_url: str | None = None):
    if not _psycopg_can_back_sqlalchemy():
        raw_url = database_url or raw_database_url()
        if raw_url:
            return connect_raw_postgres(raw_url, autocommit=False)
    return PooledPsycopgConnection(database_url)


def reset_engine_cache_for_tests() -> None:
    for engine in list(_ENGINE_CACHE.values()):
        engine.dispose()
    _SESSION_FACTORY_CACHE.clear()
    _ENGINE_CACHE.clear()
