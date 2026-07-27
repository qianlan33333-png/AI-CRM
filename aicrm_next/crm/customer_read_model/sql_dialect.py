from __future__ import annotations

from typing import Any


def is_sqlite_session(session: Any) -> bool:
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        return False
    bind = get_bind()
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower() == "sqlite"


def json_text_expression(column: str, key: str, *, sqlite: bool) -> str:
    if sqlite:
        return f"json_extract({column}, '$.{key}')"
    return f"{column} ->> '{key}'"
