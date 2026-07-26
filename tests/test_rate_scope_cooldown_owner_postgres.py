from __future__ import annotations

from datetime import datetime, timezone
import os

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text

from aicrm_next.platform_foundation.rate_scope_cooldown import (
    RateScopeCooldownRequest,
    build_rate_scope_cooldown_port,
)


def test_rate_scope_cooldown_owner_preserves_monotonic_deadline_across_callers(next_pg_schema) -> None:
    database_url = os.environ["DATABASE_URL"]
    port = build_rate_scope_cooldown_port()
    later = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)
    earlier = datetime(2026, 7, 26, 5, 0, tzinfo=timezone.utc)

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        port.persist_dbapi(
            connection,
            request=RateScopeCooldownRequest(
                rate_scope_key="wecom:corp-1:app-1:message.send",
                blocked_until=later,
                provider="wecom-first",
                reason="http_429",
                source_attempt_id="attempt-dbapi",
            ),
        )
        connection.commit()

    sqlalchemy_url = database_url
    if sqlalchemy_url.startswith("postgresql://"):
        sqlalchemy_url = "postgresql+psycopg://" + sqlalchemy_url[len("postgresql://") :]
    engine = create_engine(sqlalchemy_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            port.persist_sqlalchemy(
                connection,
                request=RateScopeCooldownRequest(
                    rate_scope_key="wecom:corp-1:app-1:message.send",
                    blocked_until=earlier,
                    provider="wecom-second",
                    reason="rate_limited",
                    source_attempt_id="attempt-sqlalchemy",
                ),
            )
            row = connection.execute(
                text(
                    "SELECT blocked_until, provider, reason, source_attempt_id "
                    "FROM queue_rate_scope_cooldown WHERE rate_scope_key = :rate_scope_key"
                ),
                {"rate_scope_key": "wecom:corp-1:app-1:message.send"},
            ).mappings().one()
            transaction.rollback()
    finally:
        engine.dispose()

    assert row["blocked_until"] == later
    assert row["provider"] == "wecom-second"
    assert row["reason"] == "rate_limited"
    assert row["source_attempt_id"] == "attempt-sqlalchemy"
