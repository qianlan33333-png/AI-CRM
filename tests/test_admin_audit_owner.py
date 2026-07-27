from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, text

from aicrm_next.platform.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
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


def _record() -> AdminAuditRecord:
    return AdminAuditRecord(
        operator=" operator-1 ",
        action_type="config_publish",
        target_type="config_release",
        target_id="release-1",
        before={"enabled": False},
        after={"enabled": True, "nested": {"key": "值"}},
    )


def test_admin_audit_port_supports_both_transaction_styles_without_commit() -> None:
    dbapi = _DBAPIExecutor()
    sqlalchemy = _SQLAlchemyExecutor()
    port = build_admin_audit_port()

    port.append_dbapi(dbapi, record=_record())
    port.append_sqlalchemy(sqlalchemy, record=_record())

    assert dbapi.calls[0][0].startswith("INSERT INTO admin_operation_logs")
    assert dbapi.calls[0][1][0] == "operator-1"
    assert json.loads(dbapi.calls[0][1][5]) == {"enabled": True, "nested": {"key": "值"}}
    assert sqlalchemy.calls[0][0].startswith("INSERT INTO admin_operation_logs")
    assert "CAST(:before_json AS jsonb)" in sqlalchemy.calls[0][0]
    assert json.loads(sqlalchemy.calls[0][1]["before_json"]) == {"enabled": False}
    assert not hasattr(dbapi, "commit")
    assert not hasattr(sqlalchemy, "commit")


def test_admin_audit_sqlalchemy_owner_supports_sqlite_dialect() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE admin_operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        build_admin_audit_port().append_sqlalchemy(
            connection,
            dialect_name="sqlite",
            record=_record(),
        )
        row = connection.execute(
            text("SELECT operator, before_json, after_json FROM admin_operation_logs")
        ).mappings().one()

    assert row["operator"] == "operator-1"
    assert json.loads(row["before_json"]) == {"enabled": False}
    assert json.loads(row["after_json"])["nested"] == {"key": "值"}


def test_admin_operation_log_sql_has_one_module_owner() -> None:
    writers = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        if "INSERT INTO admin_operation_logs" in path.read_text(encoding="utf-8"):
            writers.append(path.relative_to(ROOT).as_posix())

    assert writers == ["aicrm_next/platform/platform_foundation/admin_audit/repository.py"]
