from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .sql_linter import SqlValidationResult, extract_dependencies, lint_sql, normalize_sql


@dataclass(frozen=True)
class SqlExecutionPlan:
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    validation: SqlValidationResult = field(default_factory=lambda: SqlValidationResult(ok=False))
    dependencies: list[str] = field(default_factory=list)
    limit: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "params": dict(self.params),
            "validation": self.validation.to_dict(),
            "dependencies": list(self.dependencies),
            "limit": self.limit,
        }


def build_execution_plan(sql: str, params: dict[str, Any] | None = None, *, limit: int = 100) -> SqlExecutionPlan:
    normalized = normalize_sql(sql)
    validation = lint_sql(normalized)
    bounded_limit = max(1, min(int(limit or 100), 100000))
    return SqlExecutionPlan(
        sql=normalized,
        params=dict(params or {}),
        validation=validation,
        dependencies=extract_dependencies(normalized),
        limit=bounded_limit,
    )
