from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text

from aicrm_next.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)


def test_admin_audit_owner_supports_dbapi_and_sqlalchemy_transactions(next_pg_schema) -> None:
    database_url = os.environ["DATABASE_URL"]
    port = build_admin_audit_port()

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        port.append_dbapi(
            connection,
            record=AdminAuditRecord(
                operator="dbapi-worker",
                action_type="queue_recover",
                target_type="queue",
                target_id="queue-1",
                before={"status": "failed"},
                after={"status": "pending"},
            ),
        )
        dbapi_row = connection.execute(
            "SELECT * FROM admin_operation_logs WHERE target_id = %s",
            ("queue-1",),
        ).fetchone()
        connection.rollback()

    sqlalchemy_url = database_url
    if sqlalchemy_url.startswith("postgresql://"):
        sqlalchemy_url = "postgresql+psycopg://" + sqlalchemy_url[len("postgresql://") :]
    engine = create_engine(sqlalchemy_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            port.append_sqlalchemy(
                connection,
                dialect_name="postgresql",
                record=AdminAuditRecord(
                    operator="admin-user",
                    action_type="config_publish",
                    target_type="config_release",
                    target_id="release-1",
                    before={"version": 1},
                    after={"version": 2},
                ),
            )
            sqlalchemy_row = connection.execute(
                text("SELECT * FROM admin_operation_logs WHERE target_id = :target_id"),
                {"target_id": "release-1"},
            ).mappings().one()
            transaction.rollback()
    finally:
        engine.dispose()

    assert dbapi_row["before_json"] == {"status": "failed"}
    assert dbapi_row["after_json"] == {"status": "pending"}
    assert sqlalchemy_row["before_json"] == {"version": 1}
    assert sqlalchemy_row["after_json"] == {"version": 2}
