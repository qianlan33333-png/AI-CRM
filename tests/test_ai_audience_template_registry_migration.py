from __future__ import annotations

import importlib


migration = importlib.import_module("migrations.versions.0165_ai_audience_template_registry")


class _Inspector:
    def __init__(self, *, schema_exists: bool) -> None:
        self.schema_exists = schema_exists

    def has_schema(self, schema_name: str) -> bool:
        assert schema_name == "audience_read"
        return self.schema_exists


def test_existing_audience_read_schema_does_not_require_create_privilege(monkeypatch) -> None:
    connection = object()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda value: _Inspector(schema_exists=value is connection))
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration._ensure_audience_read_schema()

    assert statements == []


def test_missing_audience_read_schema_is_created(monkeypatch) -> None:
    connection = object()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda value: _Inspector(schema_exists=value is not connection))
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration._ensure_audience_read_schema()

    assert statements == ["CREATE SCHEMA audience_read"]
