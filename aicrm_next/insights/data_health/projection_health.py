from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from .dto import DataHealthCheckResult


SOURCE_TABLES = [
    "customer_list_index_next",
    "customer_detail_snapshot_next",
    "customer_list_index_next_shadow",
    "customer_detail_snapshot_next_shadow",
    "customer_timeline_event_next",
    "customer_read_model_refresh_state",
]


def projection_freshness_customer_read_model(
    *,
    database_schema_available: Callable[[], bool],
    get_session_factory: Callable[[], Any],
) -> DataHealthCheckResult:
    check_id = "projection_freshness_customer_read_model"
    title = "Customer read model projection freshness"
    if not database_schema_available():
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="not_applicable",
            severity="gray",
            summary="Runtime data check is registered but no production database probe is attached in this PR.",
            evidence={"source_tables": SOURCE_TABLES, "runtime_probe": "not_configured"},
            remediation="Attach a production-safe read repository before turning this into a red/yellow operational check.",
        )
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        """
                        WITH refresh_state AS (
                            SELECT
                                singleton_id,
                                active_slot,
                                source_count,
                                target_count,
                                last_succeeded_at
                            FROM customer_read_model_refresh_state
                            WHERE singleton_id = 1
                        )
                        SELECT
                            CASE
                                WHEN COALESCE(refresh_state.active_slot, 'primary') = 'shadow'
                                THEN (SELECT COUNT(*) FROM customer_list_index_next_shadow)
                                ELSE (SELECT COUNT(*) FROM customer_list_index_next)
                            END AS list_count,
                            CASE
                                WHEN COALESCE(refresh_state.active_slot, 'primary') = 'shadow'
                                THEN (SELECT COUNT(*) FROM customer_detail_snapshot_next_shadow)
                                ELSE (SELECT COUNT(*) FROM customer_detail_snapshot_next)
                            END AS detail_count,
                            COALESCE(refresh_state.active_slot, 'primary') AS active_slot,
                            refresh_state.singleton_id IS NOT NULL AS refresh_state_present,
                            refresh_state.source_count AS refresh_source_count,
                            refresh_state.target_count AS refresh_target_count,
                            (SELECT COUNT(*) FROM customer_timeline_event_next) AS timeline_event_count,
                            (
                                SELECT COUNT(*)
                                FROM (
                                    SELECT event_id
                                    FROM customer_timeline_event_next
                                    GROUP BY event_id
                                    HAVING COUNT(*) > 1
                                ) duplicate_events
                            ) AS timeline_duplicate_event_id_count,
                            EXTRACT(EPOCH FROM (
                                CURRENT_TIMESTAMP - refresh_state.last_succeeded_at
                            )) / 60 AS refresh_age_minutes
                        FROM (SELECT 1) singleton
                        LEFT JOIN refresh_state ON TRUE
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Customer read model freshness check could not read the live projection tables.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify customer read model migrations and DATABASE_URL read access.",
        )

    list_count = int(row.get("list_count") or 0)
    detail_count = int(row.get("detail_count") or 0)
    active_slot = "shadow" if str(row.get("active_slot") or "").strip() == "shadow" else "primary"
    refresh_state_present = bool(row.get("refresh_state_present"))
    refresh_source_count = int(row.get("refresh_source_count") or 0)
    refresh_target_count = int(row.get("refresh_target_count") or 0)
    timeline_event_count = int(row.get("timeline_event_count") or 0)
    timeline_duplicate_event_id_count = int(row.get("timeline_duplicate_event_id_count") or 0)
    refresh_age_minutes = float(row.get("refresh_age_minutes") or 0)
    violations = []
    if list_count <= 0:
        violations.append("customer_list_index_next is empty")
    if detail_count <= 0:
        violations.append("customer_detail_snapshot_next is empty")
    if list_count != detail_count:
        violations.append(f"projection_count_mismatch={list_count}:{detail_count}")
    if not refresh_state_present:
        violations.append("customer read model has no successful managed refresh")
    if refresh_state_present and refresh_target_count != list_count:
        violations.append(f"refresh_target_count={refresh_target_count} does not match list_count={list_count}")
    if refresh_state_present and refresh_source_count != refresh_target_count:
        violations.append(f"refresh_count_mismatch={refresh_source_count}:{refresh_target_count}")
    if timeline_duplicate_event_id_count > 0:
        violations.append(f"timeline_duplicate_event_id_count={timeline_duplicate_event_id_count}")
    evidence = {
        "list_count": list_count,
        "detail_count": detail_count,
        "active_slot": active_slot,
        "refresh_state_present": refresh_state_present,
        "refresh_source_count": refresh_source_count,
        "refresh_target_count": refresh_target_count,
        "timeline_event_count": timeline_event_count,
        "timeline_duplicate_event_id_count": timeline_duplicate_event_id_count,
        "refresh_age_minutes": refresh_age_minutes,
        "freshness_policy": "source_change_lag",
        "wall_clock_age_is_diagnostic": True,
    }
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Customer read model projections are empty or inconsistent.",
            evidence={**evidence, "violations": violations},
            remediation="Inspect the event-driven refresh intent and projection consumer; source-change lag is enforced by customer_360_freshness_guard.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="Customer read model projections are populated and internally consistent.",
        evidence=evidence,
        remediation="",
    )
