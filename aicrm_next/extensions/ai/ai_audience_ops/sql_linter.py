from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .sql_catalog import ALLOWED_VIEWS


REQUIRED_COLUMNS = ("identity_type", "identity_value", "event_source_key", "payload_json")
FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "copy",
    "call",
    "truncate",
    "grant",
    "revoke",
    "vacuum",
)
DANGEROUS_FUNCTIONS = (
    "pg_sleep",
    "pg_read_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "postgres_fdw_handler",
)
TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w$]*(?:\.[a-zA-Z_][\w$]*)?)", re.IGNORECASE)
PARAM_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")


@dataclass(frozen=True)
class SqlValidationResult:
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


def normalize_sql(sql: str) -> str:
    value = str(sql or "").strip()
    while value.endswith(";"):
        value = value[:-1].strip()
    return value


def lint_sql(sql: str) -> SqlValidationResult:
    normalized = normalize_sql(sql)
    errors: list[str] = []
    if not normalized:
        return SqlValidationResult(ok=False, errors=["sql_empty"])
    if ";" in normalized:
        errors.append("multiple_statements_forbidden")

    stripped = _strip_comments(normalized).strip()
    lowered = stripped.lower()
    if not (re.match(r"^(select|with)\s", lowered)):
        errors.append("only_select_allowed")

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            errors.append(f"keyword_forbidden:{keyword}")
    for function_name in DANGEROUS_FUNCTIONS:
        if re.search(rf"\b{re.escape(function_name)}\s*\(", lowered):
            errors.append(f"function_forbidden:{function_name}")

    if re.search(r"\bselect\s+\*", lowered) or re.search(r",\s*\*", lowered):
        errors.append("select_star_forbidden")

    dependencies = extract_dependencies(normalized)
    if not dependencies:
        errors.append("audience_read_dependency_required")
    for dependency in dependencies:
        if dependency not in ALLOWED_VIEWS:
            errors.append(f"dependency_not_allowed:{dependency}")

    for column in REQUIRED_COLUMNS:
        if not re.search(rf"\b{re.escape(column)}\b", lowered):
            errors.append(f"required_column_missing:{column}")

    return SqlValidationResult(
        ok=not errors,
        errors=sorted(set(errors)),
        dependencies=dependencies,
        params=extract_params(normalized),
    )


def validate_sql(sql: str) -> None:
    result = lint_sql(sql)
    if not result.ok:
        raise ValueError(";".join(result.errors))


def extract_dependencies(sql: str) -> list[str]:
    dependencies: set[str] = set()
    stripped = _strip_comments(normalize_sql(sql))
    cte_names = _extract_cte_names(stripped)
    for match in TABLE_REF_RE.finditer(stripped):
        ref = match.group(1).strip().strip('"').lower()
        if ref.startswith("("):
            continue
        if ref in cte_names:
            continue
        dependencies.add(ref)
    return sorted(dependencies)


def extract_params(sql: str) -> list[str]:
    return sorted(set(PARAM_RE.findall(normalize_sql(sql))))


def _strip_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", without_block, flags=re.MULTILINE)


def _extract_cte_names(sql: str) -> set[str]:
    lowered = sql.lower()
    if not re.match(r"^\s*with\s", lowered):
        return set()
    return {
        match.group(1).lower()
        for match in re.finditer(r"(?:\bwith|,)\s+([a-zA-Z_][\w$]*)\s+as\s*\(", lowered)
    }
