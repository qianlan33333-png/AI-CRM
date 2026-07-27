from __future__ import annotations

from aicrm_next.platform.shared.runtime import raw_database_url
from contextlib import contextmanager
from typing import Any, Iterator


def database_url() -> str:
    return raw_database_url()


def has_database_url() -> bool:
    value = database_url()
    return value.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))


@contextmanager
def connect() -> Iterator[Any]:
    import psycopg
    from psycopg.rows import dict_row

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://") :]
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
