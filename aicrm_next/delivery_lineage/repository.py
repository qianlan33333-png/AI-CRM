from __future__ import annotations

from typing import Protocol

from sqlalchemy import text

from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import raw_database_url

from .dto import DeliveryLineageDailyMetric, DeliveryLineageItem


class DeliveryLineageRepository(Protocol):
    def list_items(self, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]: ...

    def get_item(self, lineage_id: str) -> DeliveryLineageItem | None: ...

    def list_by_unionid(self, unionid: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]: ...

    def list_by_trace(self, trace_id: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]: ...

    def daily_metrics(self, *, days: int = 7) -> list[DeliveryLineageDailyMetric]: ...


class EmptyDeliveryLineageRepository:
    def list_items(self, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        return []

    def get_item(self, lineage_id: str) -> DeliveryLineageItem | None:
        return None

    def list_by_unionid(self, unionid: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        return []

    def list_by_trace(self, trace_id: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        return []

    def daily_metrics(self, *, days: int = 7) -> list[DeliveryLineageDailyMetric]:
        return []


class InMemoryDeliveryLineageRepository(EmptyDeliveryLineageRepository):
    def __init__(
        self,
        items: list[DeliveryLineageItem],
        metrics: list[DeliveryLineageDailyMetric] | None = None,
    ) -> None:
        self._items = list(items)
        self._metrics = list(metrics or [])

    def list_items(self, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        return self._items[offset : offset + limit]

    def get_item(self, lineage_id: str) -> DeliveryLineageItem | None:
        return next((item for item in self._items if item.lineage_id == lineage_id), None)

    def list_by_unionid(self, unionid: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        items = [item for item in self._items if item.unionid == unionid]
        return items[offset : offset + limit]

    def list_by_trace(self, trace_id: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        items = [item for item in self._items if item.trace_id == trace_id]
        return items[offset : offset + limit]

    def daily_metrics(self, *, days: int = 7) -> list[DeliveryLineageDailyMetric]:
        return self._metrics


class PostgresDeliveryLineageRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def list_items(self, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        return self._query("", {}, limit=limit, offset=offset)

    def get_item(self, lineage_id: str) -> DeliveryLineageItem | None:
        items = self._query("WHERE lineage_id = :lineage_id", {"lineage_id": lineage_id}, limit=1, offset=0)
        return items[0] if items else None

    def list_by_unionid(self, unionid: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        where = "WHERE unionid = :unionid"
        return self._query(where, {"unionid": unionid}, limit=limit, offset=offset)

    def list_by_trace(self, trace_id: str, *, limit: int = 50, offset: int = 0) -> list[DeliveryLineageItem]:
        where = "WHERE trace_id = :trace_id"
        return self._query(where, {"trace_id": trace_id}, limit=limit, offset=offset)

    def daily_metrics(self, *, days: int = 7) -> list[DeliveryLineageDailyMetric]:
        safe_days = max(1, min(int(days or 7), 31))
        with self._session_factory() as session:
            rows = session.execute(text(_DAILY_METRICS_SQL), {"days": safe_days}).mappings().all()
        return [DeliveryLineageDailyMetric(**dict(row)) for row in rows]

    def _query(self, where_sql: str, params: dict, *, limit: int, offset: int) -> list[DeliveryLineageItem]:
        sql = text(_LINEAGE_SQL.format(where_sql=where_sql))
        query_params = {**params, "limit": int(limit), "offset": int(offset)}
        with self._session_factory() as session:
            rows = session.execute(sql, query_params).mappings().all()
        return [DeliveryLineageItem(**dict(row)) for row in rows]


def build_delivery_lineage_repository() -> DeliveryLineageRepository:
    if not raw_database_url():
        return EmptyDeliveryLineageRepository()
    return PostgresDeliveryLineageRepository()


_LINEAGE_SQL = """
WITH lineage AS (
    SELECT
        'broadcast:' || bj.id::text AS lineage_id,
        COALESCE(bj.source_type, '') AS source_type,
        COALESCE(bj.source_id, '') AS source_id,
        COALESCE(bj.business_domain, '') AS business_domain,
        '' AS unionid,
        bj.id AS broadcast_job_id,
        COALESCE(bj.status, '') AS broadcast_job_status,
        COALESCE(be.event_count, 0)::int AS broadcast_event_count,
        bj.outbound_task_id AS outbound_task_id,
        '' AS outbound_task_status,
        NULL::bigint AS external_effect_job_id,
        '' AS external_effect_status,
        0 AS external_effect_attempt_count,
        '' AS internal_event_id,
        '' AS domain_event_id,
        COALESCE(bj.last_error, '') AS last_error,
        bj.created_at AS first_created_at,
        bj.updated_at AS last_updated_at,
        COALESCE(bj.trace_id, '') AS trace_id
    FROM broadcast_jobs bj
    LEFT JOIN (
        SELECT job_id, COUNT(*) AS event_count
        FROM broadcast_job_events
        GROUP BY job_id
    ) be ON be.job_id = bj.id
    UNION ALL
    SELECT
        'external_effect:' || ej.id::text AS lineage_id,
        COALESCE(ej.source_module, '') AS source_type,
        COALESCE(NULLIF(ej.source_command_id, ''), ej.business_id, '') AS source_id,
        COALESCE(ej.business_type, '') AS business_domain,
        CASE WHEN ej.target_type = 'unionid' THEN COALESCE(ej.target_id, '') ELSE '' END AS unionid,
        NULL::bigint AS broadcast_job_id,
        '' AS broadcast_job_status,
        0 AS broadcast_event_count,
        NULL::bigint AS outbound_task_id,
        '' AS outbound_task_status,
        ej.id AS external_effect_job_id,
        COALESCE(ej.status, '') AS external_effect_status,
        COALESCE(ej.attempt_count, 0)::int AS external_effect_attempt_count,
        '' AS internal_event_id,
        '' AS domain_event_id,
        COALESCE(NULLIF(ej.last_error_message, ''), ej.last_error_code, '') AS last_error,
        ej.created_at AS first_created_at,
        ej.updated_at AS last_updated_at,
        COALESCE(ej.trace_id, '') AS trace_id
    FROM external_effect_job ej
    UNION ALL
    SELECT
        'internal_event:' || ie.event_id AS lineage_id,
        COALESCE(ie.source_module, '') AS source_type,
        COALESCE(ie.source_command_id, '') AS source_id,
        COALESCE(ie.aggregate_type, '') AS business_domain,
        CASE WHEN ie.subject_type = 'unionid' THEN COALESCE(ie.subject_id, '') ELSE '' END AS unionid,
        NULL::bigint AS broadcast_job_id,
        '' AS broadcast_job_status,
        0 AS broadcast_event_count,
        NULL::bigint AS outbound_task_id,
        '' AS outbound_task_status,
        NULL::bigint AS external_effect_job_id,
        '' AS external_effect_status,
        0 AS external_effect_attempt_count,
        COALESCE(ie.event_id, '') AS internal_event_id,
        '' AS domain_event_id,
        '' AS last_error,
        ie.created_at AS first_created_at,
        ie.created_at AS last_updated_at,
        COALESCE(ie.trace_id, '') AS trace_id
    FROM internal_event ie
)
SELECT *
FROM lineage
{where_sql}
ORDER BY last_updated_at DESC NULLS LAST, lineage_id DESC
LIMIT :limit OFFSET :offset
"""


_DAILY_METRICS_SQL = """
WITH metric_rows AS (
    SELECT
        'failed_delivery_daily' AS metric,
        bj.updated_at::date AS day,
        COUNT(*)::int AS value
    FROM broadcast_jobs bj
    WHERE bj.updated_at >= CURRENT_DATE - (:days || ' days')::interval
      AND (bj.status = 'failed' OR COALESCE(bj.failed_count, 0) > 0)
    GROUP BY bj.updated_at::date
    UNION ALL
    SELECT
        'blocked_delivery_daily' AS metric,
        bj.updated_at::date AS day,
        COUNT(*)::int AS value
    FROM broadcast_jobs bj
    WHERE bj.updated_at >= CURRENT_DATE - (:days || ' days')::interval
      AND bj.status = 'blocked'
    GROUP BY bj.updated_at::date
    UNION ALL
    SELECT
        'blocked_delivery_daily' AS metric,
        ej.updated_at::date AS day,
        COUNT(*)::int AS value
    FROM external_effect_job ej
    WHERE ej.updated_at >= CURRENT_DATE - (:days || ' days')::interval
      AND ej.status = 'blocked'
    GROUP BY ej.updated_at::date
    UNION ALL
    SELECT
        'retryable_effect_daily' AS metric,
        ej.updated_at::date AS day,
        COUNT(*)::int AS value
    FROM external_effect_job ej
    WHERE ej.updated_at >= CURRENT_DATE - (:days || ' days')::interval
      AND ej.status = 'failed_retryable'
    GROUP BY ej.updated_at::date
)
SELECT metric, day::text AS day, SUM(value)::int AS value
FROM metric_rows
GROUP BY metric, day
ORDER BY day DESC, metric ASC
"""
