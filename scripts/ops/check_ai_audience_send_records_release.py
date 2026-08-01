#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Any


EXPECTED_MIGRATION_HEAD = "0164_ai_audience_send_record_read_index"
MANUAL_INDEX = "idx_user_ops_send_records_ai_audience"
AUTOMATION_INDEX = "idx_cloud_broadcast_plans_ai_audience_send_records"
REQUIRED_TABLES = (
    "alembic_version",
    "cloud_broadcast_plans",
    "cloud_broadcast_plan_recipients",
    "user_ops_send_records_next",
)


def _database_url() -> str:
    url = str(os.getenv("DATABASE_URL") or "").strip()
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL must be PostgreSQL")
    return url


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index] if row else None


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(_row_value(row, "count") or 0)


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{table}",)).fetchone()
    return bool(_row_value(row, "present"))


def _ownership_columns_ready(conn: Any) -> bool:
    return _scalar(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_ops_send_records_next'
          AND column_name IN ('target_source', 'target_source_id')
        """,
    ) == 2


def _migration_heads(conn: Any) -> list[str]:
    rows = conn.execute("SELECT version_num FROM alembic_version ORDER BY version_num").fetchall()
    return [str(_row_value(row, "version_num") or "") for row in rows]


def _index_state(conn: Any) -> dict[str, dict[str, bool]]:
    rows = conn.execute(
        """
        SELECT relation.relname AS index_name,
               state.indisvalid AS is_valid,
               state.indisready AS is_ready
        FROM pg_index state
        JOIN pg_class relation ON relation.oid = state.indexrelid
        WHERE relation.relname = ANY(%s)
        ORDER BY relation.relname
        """,
        ([MANUAL_INDEX, AUTOMATION_INDEX],),
    ).fetchall()
    return {
        str(_row_value(row, "index_name") or ""): {
            "valid": bool(_row_value(row, "is_valid", 1)),
            "ready": bool(_row_value(row, "is_ready", 2)),
        }
        for row in rows
    }


def _plan_summary(conn: Any, sql: str) -> dict[str, list[str]]:
    row = conn.execute(f"EXPLAIN (FORMAT JSON, COSTS OFF) {sql}").fetchone()
    payload = _row_value(row, "QUERY PLAN")
    if isinstance(payload, str):
        payload = json.loads(payload)
    root = dict(payload[0]) if isinstance(payload, list) and payload else {}
    stack = [dict(root.get("Plan") or {})]
    node_types: list[str] = []
    index_names: list[str] = []
    while stack:
        node = stack.pop()
        node_type = str(node.get("Node Type") or "")
        index_name = str(node.get("Index Name") or "")
        if node_type and node_type not in node_types:
            node_types.append(node_type)
        if index_name and index_name not in index_names:
            index_names.append(index_name)
        stack.extend(dict(child) for child in node.get("Plans") or [])
    return {"node_types": node_types, "index_names": index_names}


def _query_plans(conn: Any) -> dict[str, dict[str, list[str]]]:
    conn.execute("SET LOCAL enable_seqscan = off")
    return {
        "manual": _plan_summary(
            conn,
            """
            SELECT id
            FROM user_ops_send_records_next
            WHERE target_source = 'ai_audience_package'
              AND target_source_id = 0
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
        ),
        "automation": _plan_summary(
            conn,
            """
            SELECT id
            FROM cloud_broadcast_plans
            WHERE content_strategy = 'agent_generated_single'
              AND selection_json ->> 'source' = 'automation_agent'
              AND selection_json ->> 'package_key' = 'release-diagnostic-no-match'
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
        ),
    }


def collect(conn: Any, *, phase: str, release_sha: str = "") -> dict[str, Any]:
    table_state = {table: _table_exists(conn, table) for table in REQUIRED_TABLES}
    if not all(table_state.values()):
        return {
            "ok": False,
            "phase": phase,
            "release_sha": release_sha,
            "tables_ready": table_state,
            "database_write_executed": False,
            "real_external_call_executed": False,
            "pii_included": False,
        }

    columns_ready = _ownership_columns_ready(conn)
    manual_total = _scalar(conn, "SELECT COUNT(*) AS count FROM user_ops_send_records_next")
    legacy_manual_count = manual_total
    traceable_manual_count = 0
    if columns_ready:
        legacy_manual_count = _scalar(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM user_ops_send_records_next
            WHERE COALESCE(target_source, '') = ''
              AND target_source_id IS NULL
            """,
        )
        traceable_manual_count = _scalar(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM user_ops_send_records_next
            WHERE target_source = 'ai_audience_package'
              AND target_source_id IS NOT NULL
            """,
        )

    counts = {
        "automation_traceable_business_record_count": _scalar(
            conn,
            """
            SELECT COUNT(DISTINCT recipient.broadcast_job_id) AS count
            FROM cloud_broadcast_plans plan
            JOIN cloud_broadcast_plan_recipients recipient
              ON recipient.plan_id = plan.plan_id
             AND recipient.broadcast_job_id IS NOT NULL
            WHERE plan.content_strategy = 'agent_generated_single'
              AND plan.selection_json ->> 'source' = 'automation_agent'
              AND COALESCE(plan.selection_json ->> 'package_key', '') <> ''
            """,
        ),
        "legacy_manual_record_without_package_ownership_count": legacy_manual_count,
        "traceable_manual_business_record_count": traceable_manual_count,
    }
    heads = _migration_heads(conn)
    indexes = _index_state(conn)
    plans: dict[str, dict[str, list[str]]] = {}
    if phase == "post-migration" and columns_ready:
        plans = _query_plans(conn)

    post_migration_ready = (
        heads == [EXPECTED_MIGRATION_HEAD]
        and columns_ready
        and indexes.get(MANUAL_INDEX) == {"valid": True, "ready": True}
        and indexes.get(AUTOMATION_INDEX) == {"valid": True, "ready": True}
        and MANUAL_INDEX in plans.get("manual", {}).get("index_names", [])
        and AUTOMATION_INDEX in plans.get("automation", {}).get("index_names", [])
    )
    return {
        "ok": True if phase == "preflight" else post_migration_ready,
        "phase": phase,
        "release_sha": release_sha,
        "migration_heads": heads,
        "ownership_columns_ready": columns_ready,
        "counts": counts,
        "indexes": indexes,
        "query_plans": plans,
        "tables_ready": table_state,
        "database_write_executed": False,
        "real_external_call_executed": False,
        "pii_included": False,
    }


def redact_report(payload: dict[str, Any]) -> str:
    safe = {
        "ok": bool(payload.get("ok")),
        "phase": str(payload.get("phase") or ""),
        "release_sha": str(payload.get("release_sha") or ""),
        "error_type": str(payload.get("error_type") or ""),
        "migration_heads": [str(value) for value in payload.get("migration_heads") or []],
        "ownership_columns_ready": bool(payload.get("ownership_columns_ready")),
        "counts": {
            str(key): int(value)
            for key, value in dict(payload.get("counts") or {}).items()
            if str(key).endswith("_count") and isinstance(value, int)
        },
        "indexes": dict(payload.get("indexes") or {}),
        "query_plans": dict(payload.get("query_plans") or {}),
        "tables_ready": dict(payload.get("tables_ready") or {}),
        "database_write_executed": False,
        "real_external_call_executed": False,
        "pii_included": False,
    }
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only AI Audience send-record release check")
    parser.add_argument("--phase", choices=("preflight", "post-migration"), required=True)
    parser.add_argument("--expected-release-sha", default="")
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            payload = collect(
                conn,
                phase=str(args.phase),
                release_sha=str(args.expected_release_sha or "").strip(),
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "phase": str(args.phase),
            "release_sha": str(args.expected_release_sha or "").strip(),
            "error_type": type(exc).__name__,
            "database_write_executed": False,
            "real_external_call_executed": False,
            "pii_included": False,
        }
    print(redact_report(payload))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
