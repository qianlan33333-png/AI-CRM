from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aicrm_next.platform_foundation.rate_scope_cooldown import (
    RateScopeCooldownRequest,
    build_rate_scope_cooldown_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _DBAPIExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params) -> None:
        self.calls.append((" ".join(statement.split()), tuple(params)))


class _SQLAlchemyExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params) -> None:
        self.calls.append((" ".join(str(statement).split()), dict(params)))


def _request(*, key: str = "wecom:corp:app:send") -> RateScopeCooldownRequest:
    return RateScopeCooldownRequest(
        rate_scope_key=key,
        blocked_until=datetime(2026, 7, 26, 1, 0, tzinfo=timezone.utc),
        provider="wecom_group",
        corp_id="corp-1",
        app_id="app-1",
        operation="message.send",
        reason="http_429",
        source_attempt_id="attempt-1",
    )


def test_rate_scope_cooldown_port_supports_both_caller_transaction_styles() -> None:
    dbapi = _DBAPIExecutor()
    sqlalchemy = _SQLAlchemyExecutor()
    port = build_rate_scope_cooldown_port()

    port.persist_dbapi(dbapi, request=_request())
    port.persist_sqlalchemy(sqlalchemy, request=_request())

    assert dbapi.calls[0][0].startswith("INSERT INTO queue_rate_scope_cooldown")
    assert dbapi.calls[0][1][0] == "wecom:corp:app:send"
    assert sqlalchemy.calls[0][0].startswith("INSERT INTO queue_rate_scope_cooldown")
    assert sqlalchemy.calls[0][1]["source_attempt_id"] == "attempt-1"
    assert not hasattr(dbapi, "commit")
    assert not hasattr(sqlalchemy, "commit")


@pytest.mark.parametrize("method_name", ["persist_dbapi", "persist_sqlalchemy"])
def test_rate_scope_cooldown_port_rejects_empty_scope(method_name: str) -> None:
    executor = _DBAPIExecutor() if method_name == "persist_dbapi" else _SQLAlchemyExecutor()

    with pytest.raises(ValueError, match="rate_scope_key is required"):
        getattr(build_rate_scope_cooldown_port(), method_name)(executor, request=_request(key="  "))


def test_rate_scope_cooldown_sql_has_one_module_owner() -> None:
    owner_source = (
        ROOT / "aicrm_next/platform_foundation/rate_scope_cooldown/repository.py"
    ).read_text(encoding="utf-8")
    caller_sources = [
        ROOT / "aicrm_next/platform_foundation/execution_runtime/repository.py",
        ROOT / "aicrm_next/platform_foundation/external_effects/rate_limit.py",
    ]

    assert owner_source.count("INSERT INTO queue_rate_scope_cooldown") == 2
    assert all(
        "INSERT INTO queue_rate_scope_cooldown" not in path.read_text(encoding="utf-8")
        for path in caller_sources
    )
