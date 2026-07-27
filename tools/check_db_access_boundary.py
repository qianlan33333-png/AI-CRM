from __future__ import annotations

import argparse
import ast
import fnmatch
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "docs" / "architecture" / "db_access_boundary.yml"

RULE_DB_ACCESS_BOUNDARY = "db_access_boundary_violation"
REQUIRED_ALLOWLIST_FIELDS = {"path", "rule", "owner", "reason", "migration_target", "matches"}
DB_EXECUTE_RECEIVERS = {"conn", "connection", "cur", "cursor", "db", "engine", "session"}
SQL_PREFIXES = ("select", "insert", "update", "delete", "with", "create", "alter", "drop", "truncate")
BROAD_MATCHES = {
    "execute",
    ".execute",
    "connect",
    ".connect",
    "create_engine",
    "create_engine(",
    "sessionmaker",
    "sessionmaker(",
    "session",
    "session(",
    "select",
    "insert",
    "update",
    "delete",
    "sqlalchemy",
    "sqlalchemy.create_engine",
    "sqlalchemy.orm.session",
    "sqlalchemy.orm.session(",
    "sqlalchemy.orm.sessionmaker",
    "sqlalchemy.orm.sessionmaker(",
    "psycopg",
    "psycopg.connect",
    "psycopg.connect(",
    "sqlite3",
    "sqlite3.connect",
    "sqlite3.connect(",
    "conn.execute",
    "conn.execute(",
    "connection.execute",
    "connection.execute(",
    "cur.execute",
    "cur.execute(",
    "cursor.execute",
    "cursor.execute(",
    "engine.execute",
    "engine.execute(",
    "session.execute",
    "session.execute(",
    "text",
    "text(",
}
ALLOWED_MIGRATION_TARGETS = (
    "aicrm_next/platform/shared/db_session.py",
    "aicrm_next/**/repo.py",
    "aicrm_next/**/repository.py",
    "aicrm_next/**/repositories.py",
)


@dataclass(frozen=True)
class BoundaryViolation:
    path: Path
    line: int
    rule: str
    detected_primitive: str
    owner: str
    reason: str
    suggestion: str

    def format(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        return (
            f"{display_path}:{self.line}: {self.rule}: detected_primitive={self.detected_primitive}: "
            f"owner={self.owner}: {self.reason} Suggestion: {self.suggestion}"
        )


def load_config(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("DB access boundary config must be a mapping")
    _validate_string_list(raw.get("allowed_paths") or [], "allowed_paths")
    _validate_string_list(raw.get("allowed_globs") or [], "allowed_globs")
    _validate_string_list(raw.get("forbidden_layers") or [], "forbidden_layers")
    _validate_temporary_allowlist(raw.get("temporary_allowlist") or [])
    return raw


def check_db_access_boundary(root: Path = ROOT, config_path: Path = DEFAULT_CONFIG) -> list[BoundaryViolation]:
    config = load_config(config_path)
    allowlist = config.get("temporary_allowlist") or []
    violations: list[BoundaryViolation] = []

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        if _is_allowed_db_boundary(rel, config):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            violations.append(
                BoundaryViolation(
                    path=path,
                    line=exc.lineno or 1,
                    rule="python_parse_error",
                    detected_primitive="python.parse",
                    owner=_owner_for_path(rel),
                    reason=str(exc),
                    suggestion="Fix syntax before running DB access boundary checks.",
                )
            )
            continue

        lines = source.splitlines()
        aliases = _collect_db_aliases(tree)
        is_forbidden_layer = _matches_any(rel, config.get("forbidden_layers") or [])
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            detected = _detected_db_primitive(node, aliases, is_forbidden_layer)
            if not detected:
                continue
            line_number = getattr(node, "lineno", 1)
            stripped_line = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""
            if _is_allowlisted_call(allowlist, rel, stripped_line):
                continue
            violations.append(
                BoundaryViolation(
                    path=path,
                    line=line_number,
                    rule=RULE_DB_ACCESS_BOUNDARY,
                    detected_primitive=detected,
                    owner=_owner_for_path(rel),
                    reason=f"{rel} uses direct DB/session primitives outside repository or shared DB session boundaries.",
                    suggestion=(
                        "Move DB access into this context's repo.py/repository.py or aicrm_next.platform.shared.db_session; "
                        "if this is historical debt, add only a precise temporary allowlist entry with path, rule, "
                        "owner, reason, migration_target, and exact match."
                    ),
                )
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AI-CRM Next DB/session access boundaries.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    violations = check_db_access_boundary(root=root, config_path=Path(args.config).resolve())
    if violations:
        print("DB access boundary check failed:")
        for violation in violations:
            print(f"- {violation.format(root)}")
        return 1
    print(f"DB access boundary check OK: {args.config}")
    return 0


def _iter_python_files(root: Path) -> Iterable[Path]:
    candidates = [root / "aicrm_next", root / "migrations", root / "scripts", root / "tests", root / "tools"]
    for base in candidates:
        if not base.exists():
            continue
        yield from (path for path in sorted(base.rglob("*.py")) if "__pycache__" not in path.parts)


def _is_allowed_db_boundary(rel: str, config: dict[str, Any]) -> bool:
    allowed_paths = set(config.get("allowed_paths") or [])
    allowed_globs = config.get("allowed_globs") or []
    return rel in allowed_paths or _matches_any(rel, allowed_globs)


def _collect_db_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                asname = alias.asname or alias.name
                if alias.name in {"sqlalchemy", "sqlalchemy.orm", "psycopg", "sqlite3"}:
                    aliases[asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                asname = alias.asname or alias.name
                if module == "sqlalchemy" and alias.name in {"create_engine", "text"}:
                    aliases[asname] = f"sqlalchemy.{alias.name}"
                elif module == "sqlalchemy.orm" and alias.name in {"Session", "sessionmaker"}:
                    aliases[asname] = f"sqlalchemy.orm.{alias.name}"
                elif module == "psycopg" and alias.name == "connect":
                    aliases[asname] = "psycopg.connect"
                elif module == "sqlite3" and alias.name == "connect":
                    aliases[asname] = "sqlite3.connect"
    return aliases


def _detected_db_primitive(node: ast.Call, aliases: dict[str, str], is_forbidden_layer: bool) -> str | None:
    qualified = _qualified_name(node.func, aliases)
    if qualified in {
        "sqlalchemy.create_engine",
        "sqlalchemy.orm.Session",
        "sqlalchemy.orm.sessionmaker",
        "psycopg.connect",
        "sqlite3.connect",
    }:
        return qualified
    if qualified == "sqlalchemy.text" and is_forbidden_layer and _call_has_sql_literal(node):
        return "sqlalchemy.text"
    if is_forbidden_layer and _is_db_execute_call(node):
        return "db.execute"
    return None


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, aliases)
        if prefix:
            return f"{prefix}.{node.attr}"
        if isinstance(node.value, ast.Name):
            base = aliases.get(node.value.id)
            if base:
                return f"{base}.{node.attr}"
    return None


def _is_db_execute_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
        return False
    if not isinstance(node.func.value, ast.Name) or node.func.value.id not in DB_EXECUTE_RECEIVERS:
        return False
    return True


def _call_has_sql_literal(node: ast.Call) -> bool:
    if not node.args:
        return False
    first_arg = node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return _looks_like_sql(first_arg.value)
    return False


def _looks_like_sql(value: str) -> bool:
    stripped = value.strip().lower()
    return stripped.startswith(SQL_PREFIXES)


def _is_allowlisted_call(allowlist: list[dict[str, Any]], path: str, stripped_line: str) -> bool:
    for entry in allowlist:
        if entry.get("path") != path:
            continue
        if entry.get("rule") != RULE_DB_ACCESS_BOUNDARY:
            continue
        if stripped_line in set(entry.get("matches") or []):
            return True
    return False


def _owner_for_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "aicrm_next":
        return parts[1]
    return "unknown"


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _validate_string_list(values: Any, field: str) -> None:
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"{field} must be a list of non-empty strings")


def _validate_temporary_allowlist(allowlist: list[dict[str, Any]]) -> None:
    if not isinstance(allowlist, list):
        raise ValueError("temporary_allowlist must be a list")
    for index, entry in enumerate(allowlist, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"temporary_allowlist entry #{index} must be a mapping")
        missing = sorted(field for field in REQUIRED_ALLOWLIST_FIELDS if _missing_required_value(entry.get(field)))
        if missing:
            raise ValueError(f"temporary_allowlist entry #{index} missing required fields: {', '.join(missing)}")
        path = str(entry["path"])
        if any(marker in path for marker in ("*", "?", "[")) or path.endswith("/") or Path(path).suffix != ".py":
            raise ValueError(f"temporary_allowlist entry #{index} path must be an exact Python file path")
        if str(entry["rule"]) != RULE_DB_ACCESS_BOUNDARY:
            raise ValueError(f"temporary_allowlist entry #{index} rule must be {RULE_DB_ACCESS_BOUNDARY}")
        migration_target = str(entry["migration_target"])
        if not _is_allowed_migration_target(migration_target):
            raise ValueError(f"temporary_allowlist entry #{index} migration_target must point to repo/repository/shared DB session boundary")
        matches = entry.get("matches")
        if not isinstance(matches, list) or not all(isinstance(match, str) and match.strip() for match in matches):
            raise ValueError(f"temporary_allowlist entry #{index} matches must be non-empty strings")
        for match in matches:
            if _is_broad_match(match):
                raise ValueError(f"temporary_allowlist entry #{index} match is too broad: {match}")


def _is_allowed_migration_target(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in ALLOWED_MIGRATION_TARGETS)


def _is_broad_match(match: str) -> bool:
    normalized = match.strip().lower()
    return normalized in BROAD_MATCHES


def _missing_required_value(value: Any) -> bool:
    return value is None or value == "" or value == []


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
