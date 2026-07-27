from __future__ import annotations

from datetime import datetime
from typing import Any


def lost_lease_count(connection: Any, *, started_at: datetime) -> int:
    """Count durable recoveries plus active expired leases in a time window."""

    row = connection.execute(
        """
        SELECT (
            SELECT COUNT(*)::BIGINT
            FROM queue_runtime_lease_recovery_event
            WHERE detected_at >= %s
        ) + (
            SELECT COUNT(*)::BIGINT
            FROM (
                SELECT id FROM external_effect_job
                WHERE status = 'dispatching' AND lease_expires_at >= %s
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT id FROM internal_event_consumer_run
                WHERE status = 'running' AND lease_expires_at >= %s
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT id FROM internal_event_outbox
                WHERE status = 'running' AND lease_expires_at >= %s
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT id FROM webhook_inbox
                WHERE status = 'processing' AND lease_expires_at >= %s
                  AND lease_expires_at <= CURRENT_TIMESTAMP
            ) active_expirations
        ) AS count
        """,
        (started_at,) * 5,
    ).fetchone()
    return int((row or {}).get("count") or 0)
