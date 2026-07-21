#!/usr/bin/env python3
"""Read-only snapshot of the 0125 queue-history freeze classification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.shared.runtime import raw_database_url  # noqa: E402
from aicrm_next.shared.sensitive_data import redact_sensitive_data  # noqa: E402


FREEZE_REVISION = "0125_execution_runtime_correctness"
QUEUE_TABLES = {
    "external_effect": "external_effect_job",
    "internal_event_consumer": "internal_event_consumer_run",
    "internal_event_outbox": "internal_event_outbox",
    "webhook_inbox": "webhook_inbox",
    "broadcast_job": "broadcast_jobs",
}


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        _psycopg_url(database_url),
        autocommit=True,
        connect_timeout=5,
        row_factory=dict_row,
    )


def snapshot(
    database_url: str,
    *,
    sample_limit: int = 20,
    connection_factory: Callable[[str], Any] = _connect,
) -> dict[str, Any]:
    if not str(database_url or "").startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise ValueError("a PostgreSQL database URL is required")
    limit = max(0, min(int(sample_limit or 0), 100))
    with connection_factory(database_url) as conn:
        counts = conn.execute(
            """
            SELECT queue_kind, classification, COUNT(*)::BIGINT AS count
            FROM queue_history_classification
            WHERE freeze_revision = %(freeze_revision)s
            GROUP BY queue_kind, classification
            ORDER BY queue_kind, classification
            """,
            {"freeze_revision": FREEZE_REVISION},
        ).fetchall()
        samples = conn.execute(
            """
            SELECT queue_kind, queue_row_id, source_status, classification,
                   hold_reason, classified_at
            FROM queue_history_classification
            WHERE freeze_revision = %(freeze_revision)s
            ORDER BY queue_kind, queue_row_id
            LIMIT %(limit)s
            """,
            {"freeze_revision": FREEZE_REVISION, "limit": limit},
        ).fetchall()
        live_holds: dict[str, int] = {}
        for queue_kind, table_name in QUEUE_TABLES.items():
            row = conn.execute(f"SELECT COUNT(*)::BIGINT AS count FROM {table_name} WHERE hold_reason <> ''").fetchone()
            live_holds[queue_kind] = int((row or {}).get("count") or 0)

    by_queue: dict[str, dict[str, int]] = {queue_kind: {} for queue_kind in QUEUE_TABLES}
    for row in counts:
        by_queue[str(row.get("queue_kind") or "")][str(row.get("classification") or "")] = int(row.get("count") or 0)
    classified_total = sum(sum(values.values()) for values in by_queue.values())
    held_classification_total = sum(count for values in by_queue.values() for classification, count in values.items() if classification != "terminal_readonly")
    return {
        "ok": True,
        "read_only": True,
        "freeze_revision": FREEZE_REVISION,
        "classified_total": classified_total,
        "held_classification_total": held_classification_total,
        "live_hold_total": sum(live_holds.values()),
        "live_holds_by_queue": live_holds,
        "classifications_by_queue": by_queue,
        "samples": [
            {
                "queue_kind": str(row.get("queue_kind") or ""),
                "queue_row_id": int(row.get("queue_row_id") or 0),
                "source_status": str(row.get("source_status") or ""),
                "classification": str(row.get("classification") or ""),
                "hold_reason": str(row.get("hold_reason") or ""),
                "classified_at": row.get("classified_at").isoformat() if row.get("classified_at") else "",
            }
            for row in samples
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="", help="PostgreSQL URL; defaults to DATABASE_URL")
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        payload = snapshot(
            str(args.database_url or raw_database_url()).strip(),
            sample_limit=args.sample_limit,
        )
    except Exception as exc:  # command must fail closed without echoing credentials
        payload = {"ok": False, "read_only": True, "error_class": exc.__class__.__name__}
        print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
