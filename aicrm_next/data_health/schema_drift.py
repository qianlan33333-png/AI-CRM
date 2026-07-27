from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml
from sqlalchemy import text

from aicrm_next.platform.shared.runtime import raw_database_url

from aicrm_next.platform.shared.db_session import get_session_factory


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"

PHYSICAL_LIFECYCLES = {"canonical", "read_model", "event", "queue", "config", "audit"}
PII_COLUMN_HINTS = (
    "mobile",
    "phone",
    "email",
    "openid",
    "external_userid",
    "unionid",
    "userid",
    "id_card",
)
QUEUE_STATUS_COLUMNS = {"status", "state", "trade_state", "trade_status", "processing_status"}


def load_table_lifecycle_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tables"), dict):
        raise ValueError("data table lifecycle manifest must include a tables mapping")
    return raw


def public_schema_snapshot(session_factory: Any | None = None) -> dict[str, set[str]]:
    factory = session_factory or get_session_factory()
    with factory() as session:
        rows = session.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
        ).mappings().all()
    snapshot: dict[str, set[str]] = {}
    for row in rows:
        table_name = str(row.get("table_name") or "").strip()
        column_name = str(row.get("column_name") or "").strip()
        if table_name and column_name:
            snapshot.setdefault(table_name, set()).add(column_name)
    return snapshot


def database_schema_available() -> bool:
    return bool(raw_database_url())


def evaluate_schema_drift(
    *,
    manifest: Mapping[str, Any],
    actual_schema: Mapping[str, set[str]],
) -> list[str]:
    tables = manifest.get("tables") or {}
    if not isinstance(tables, Mapping):
        return ["manifest tables must be a mapping"]

    violations: list[str] = []
    manifest_table_names = {str(table_name) for table_name in tables}
    actual_table_names = {str(table_name) for table_name in actual_schema}

    for table_name, entry in sorted(tables.items()):
        if not isinstance(entry, Mapping):
            violations.append(f"{table_name}: manifest entry must be a mapping")
            continue
        lifecycle = str(entry.get("lifecycle") or "").strip()
        columns = set(actual_schema.get(str(table_name), set()))

        if lifecycle in PHYSICAL_LIFECYCLES and table_name not in actual_table_names:
            violations.append(f"{table_name}: manifest declares physical lifecycle={lifecycle} but table is missing")
        if lifecycle == "retired" and table_name in actual_table_names:
            violations.append(f"{table_name}: retired table still exists in public schema")
        if lifecycle == "canonical" and not str(entry.get("write_owner") or "").strip():
            violations.append(f"{table_name}: canonical table must declare write_owner")
        if columns and _has_pii_column(columns) and not str(entry.get("pii_level") or "").strip():
            violations.append(f"{table_name}: table has PII-like columns but missing pii_level")
        if lifecycle == "queue" and columns and _has_queue_status_column(columns) and not entry.get("status_enum"):
            violations.append(f"{table_name}: queue table has status/state column but missing status_enum")

    for table_name in sorted(actual_table_names - manifest_table_names):
        violations.append(f"{table_name}: table exists but is not registered in lifecycle manifest")

    return violations


def _has_pii_column(columns: set[str]) -> bool:
    return any(any(hint in column for hint in PII_COLUMN_HINTS) for column in columns)


def _has_queue_status_column(columns: set[str]) -> bool:
    return bool(columns & QUEUE_STATUS_COLUMNS)
