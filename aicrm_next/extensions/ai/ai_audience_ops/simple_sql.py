from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .repository import _text
from .sql_catalog import ALLOWED_VIEWS
from .sql_executor import build_execution_plan
from .sql_linter import (
    DANGEROUS_FUNCTIONS,
    FORBIDDEN_KEYWORDS,
    extract_dependencies,
    extract_params,
    normalize_sql,
)


SYSTEM_PARAMS = frozenset(
    {
        "package_key",
        "package_id",
        "refresh_started_at",
        "last_watermark_at",
        "lookback_seconds",
    }
)

SIMPLE_REFRESH_MODE_TO_ADMIN = {
    "every_3m": "incremental_3m",
    "daily_0200": "daily_0200",
    "manual": "manual",
}


@dataclass(frozen=True)
class SimpleSqlValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "dependencies": list(self.dependencies),
            "params": list(self.params),
        }


def compile_simple_sql(sql: str) -> str:
    user_sql = normalize_sql(sql)
    return f"""
WITH simple_audience AS (
{user_sql}
)
SELECT
  'external_userid' AS identity_type,
  external_userid AS identity_value,
  'simple:' || :package_key || ':' || external_userid AS event_source_key,
  '{{}}'::jsonb AS payload_json,
  external_userid,
  CAST(:refresh_started_at AS timestamptz) AS event_at
FROM simple_audience
WHERE external_userid IS NOT NULL
""".strip()


def validate_simple_sql(sql: str, parameters: dict[str, Any] | None = None) -> SimpleSqlValidation:
    normalized = normalize_sql(sql)
    declared_params = set((parameters or {}).keys())
    errors: list[str] = []
    if not normalized:
        return SimpleSqlValidation(ok=False, errors=["sql_empty"])
    if ";" in normalized:
        errors.append("multiple_statements_forbidden")

    stripped = _strip_comments(normalized).strip()
    lowered = stripped.lower()
    if not re.match(r"^(select|with)\s", lowered):
        errors.append("only_select_allowed")
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            errors.append(f"keyword_forbidden:{keyword}")
    for function_name in DANGEROUS_FUNCTIONS:
        if re.search(rf"\b{re.escape(function_name)}\s*\(", lowered):
            errors.append(f"function_forbidden:{function_name}")
    if re.search(r"\bselect\s+(?:distinct\s+)?\*", lowered) or re.search(r",\s*\*", lowered):
        errors.append("select_star_forbidden")
    if not _selects_external_userid(lowered):
        errors.append("required_column_missing:external_userid")

    dependencies = extract_dependencies(normalized)
    if not dependencies:
        errors.append("audience_read_dependency_required")
    for dependency in dependencies:
        if not dependency.startswith("audience_read."):
            errors.append(f"dependency_not_allowed:{dependency}")
        elif dependency not in ALLOWED_VIEWS:
            errors.append(f"dependency_not_allowed:{dependency}")

    params = extract_params(normalized)
    missing_params = sorted(set(params) - SYSTEM_PARAMS - declared_params)
    errors.extend(f"parameter_not_declared:{param}" for param in missing_params)

    compiled = compile_simple_sql(normalized)
    plan = build_execution_plan(compiled, {"package_key": "preview", **dict(parameters or {})}, limit=1)
    errors.extend(f"compiled:{error}" for error in plan.validation.errors)

    return SimpleSqlValidation(
        ok=not errors,
        errors=sorted(set(errors)),
        dependencies=dependencies,
        params=params,
    )


def simple_refresh_mode_config(refresh_mode: str) -> str | None:
    return SIMPLE_REFRESH_MODE_TO_ADMIN.get(_text(refresh_mode))


def _selects_external_userid(lowered_sql: str) -> bool:
    if re.search(r"\bselect\s+(?:distinct\s+)?(?:[a-zA-Z_][\w$]*\.)?external_userid\b", lowered_sql):
        return True
    return bool(re.search(r"\bexternal_userid\s+as\s+external_userid\b", lowered_sql))


def _strip_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", without_block, flags=re.MULTILINE)
