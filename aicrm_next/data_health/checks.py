from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Callable

from sqlalchemy import text

from aicrm_next.shared.release_cutovers import (
    QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT,
    QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL,
)
from aicrm_next.shared.db_session import get_session_factory
from tools.check_data_table_lifecycle import check_data_table_lifecycle

from .dto import DataHealthCheckResult
from .external_effect_provenance import callback_welcome_failure_sql, direct_canary_job_sql
from .schema_drift import (
    database_schema_available,
    evaluate_schema_drift,
    load_table_lifecycle_manifest,
    public_schema_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]
PROJECTION_FRESHNESS_MAX_MINUTES = int(os.getenv("AICRM_DATA_HEALTH_PROJECTION_FRESHNESS_MAX_MINUTES", "60") or 60)
BROADCAST_BLOCKED_MAX_COUNT = int(os.getenv("AICRM_DATA_HEALTH_BROADCAST_BLOCKED_MAX_COUNT", "0") or 0)
BROADCAST_RETRYABLE_DUE_MAX_COUNT = int(os.getenv("AICRM_DATA_HEALTH_BROADCAST_RETRYABLE_DUE_MAX_COUNT", "100") or 100)
BROADCAST_TERMINAL_LOOKBACK_HOURS = max(
    1,
    int(os.getenv("AICRM_DATA_HEALTH_BROADCAST_TERMINAL_LOOKBACK_HOURS", "24") or 24),
)
EXTERNAL_EFFECT_RETRYABLE_DUE_MAX_COUNT = int(os.getenv("AICRM_DATA_HEALTH_EXTERNAL_EFFECT_RETRYABLE_DUE_MAX_COUNT", "100") or 100)
EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS = max(
    1,
    int(os.getenv("AICRM_DATA_HEALTH_EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS", "24") or 24),
)
QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL = QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL
COMMERCE_CONTINUATION_CUTOVER_SQL = "TIMESTAMPTZ '2026-07-13 09:46:09+00'"


def run_all_checks() -> list[DataHealthCheckResult]:
    return [check() for check in _CHECKS]


def run_check(check_id: str) -> DataHealthCheckResult | None:
    for check in _CHECKS:
        result = check()
        if result.check_id == check_id:
            return result
    return None


def _table_lifecycle_manifest_guard() -> DataHealthCheckResult:
    violations = list(_lifecycle_violations())
    return _static_guard_result(
        check_id="table_lifecycle_manifest_guard",
        title="Lifecycle manifest guard",
        violations=violations,
        ok_summary="Lifecycle manifest and table registrations are valid.",
        remediation="Run tools/check_data_table_lifecycle.py and register or fix the reported table entries.",
    )


def _retired_table_runtime_reference_guard() -> DataHealthCheckResult:
    violations = [violation for violation in _lifecycle_violations() if "references retired table" in violation]
    return _static_guard_result(
        check_id="retired_table_runtime_reference_guard",
        title="Retired table runtime reference guard",
        violations=violations,
        ok_summary="No Next runtime SQL references retired lifecycle tables.",
        remediation="Remove the runtime SQL reference or move the table out of retired lifecycle with an approved owner.",
    )


def _schema_drift_guard() -> DataHealthCheckResult:
    if not database_schema_available():
        return DataHealthCheckResult(
            check_id="schema_drift_guard",
            title="Schema drift guard",
            status="not_applicable",
            severity="gray",
            summary="DATABASE_URL is not configured, so live information_schema drift cannot be checked.",
            evidence={"runtime_probe": "database_url_not_configured"},
            remediation="Run this check in an environment with a migrated read-only database connection.",
        )
    try:
        violations = evaluate_schema_drift(
            manifest=load_table_lifecycle_manifest(),
            actual_schema=public_schema_snapshot(),
        )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return DataHealthCheckResult(
            check_id="schema_drift_guard",
            title="Schema drift guard",
            status="fail",
            severity="red",
            summary="Schema drift check could not read the live schema.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify DATABASE_URL, migration state, and information_schema access.",
        )
    return _static_guard_result(
        check_id="schema_drift_guard",
        title="Schema drift guard",
        violations=violations,
        ok_summary="Live public schema is aligned with the lifecycle manifest.",
        remediation="Register missing tables, remove retired physical tables, or add required ownership/PII/queue metadata.",
    )


@lru_cache(maxsize=1)
def _lifecycle_violations() -> tuple[str, ...]:
    return tuple(check_data_table_lifecycle(root=ROOT))


def _identity_legacy_column_guard() -> DataHealthCheckResult:
    guard_path = ROOT / "tests" / "test_unionid_final_schema_guard.py"
    source = guard_path.read_text(encoding="utf-8") if guard_path.exists() else ""
    required_tokens = (
        "LEGACY_IDENTITY_COLUMN_NAMES",
        "ALLOWED_FINAL_LEGACY_IDENTITY_COLUMNS",
        "BOUNDARY_PREFIXES",
    )
    missing = [token for token in required_tokens if token not in source]
    return _static_guard_result(
        check_id="identity_legacy_column_guard",
        title="Legacy identity column guard",
        violations=[f"{guard_path.relative_to(ROOT)} missing {token}" for token in missing],
        ok_summary="Final schema guard restricts legacy identity columns to approved identity boundaries.",
        remediation="Restore tests/test_unionid_final_schema_guard.py allowed-boundary assertions.",
    )


def _static_guard_result(
    *,
    check_id: str,
    title: str,
    violations: list[str],
    ok_summary: str,
    remediation: str,
) -> DataHealthCheckResult:
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary=f"{len(violations)} violation(s) found.",
            evidence={"violations": violations[:50], "violation_count": len(violations)},
            remediation=remediation,
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary=ok_summary,
        evidence={"violation_count": 0},
        remediation="",
    )


def _db_backed_placeholder(check_id: str, title: str, source_tables: list[str]) -> DataHealthCheckResult:
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="not_applicable",
        severity="gray",
        summary="Runtime data check is registered but no production database probe is attached in this PR.",
        evidence={"source_tables": source_tables, "runtime_probe": "not_configured"},
        remediation="Attach a production-safe read repository before turning this into a red/yellow operational check.",
    )


def _db_unavailable_placeholder(check_id: str, title: str, source_tables: list[str]) -> DataHealthCheckResult:
    return _db_backed_placeholder(check_id, title, source_tables)


def _database_probe_failure(
    check_id: str,
    title: str,
    exc: Exception,
    source_tables: list[str],
) -> DataHealthCheckResult:
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="fail",
        severity="red",
        summary="The production-safe database probe could not be completed.",
        evidence={"error": type(exc).__name__, "source_tables": source_tables},
        remediation="Verify the migrated schema and read access, then rerun the check.",
    )


def _unionid_orphan_fact_guard() -> DataHealthCheckResult:
    check_id = "unionid_orphan_fact_guard"
    title = "Unionid orphan fact guard"
    source_tables = [
        "questionnaire_submissions",
        "wechat_pay_orders",
        "alipay_pay_orders",
        "wechat_shop_orders",
        "broadcast_jobs",
        "crm_user_identity",
    ]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT
                            (
                                SELECT COUNT(*) FROM questionnaire_submissions fact
                                WHERE fact.submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND NULLIF(BTRIM(fact.unionid), '') IS NOT NULL
                                  AND NOT EXISTS (
                                      SELECT 1 FROM crm_user_identity identity
                                      WHERE identity.unionid = fact.unionid
                                  )
                            ) AS questionnaire_orphan_count,
                            (
                                SELECT COUNT(*) FROM wechat_pay_orders fact
                                WHERE COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (fact.status = 'paid' OR fact.trade_state = 'SUCCESS')
                                  AND NULLIF(BTRIM(fact.unionid), '') IS NOT NULL
                                  AND NOT EXISTS (
                                      SELECT 1 FROM crm_user_identity identity
                                      WHERE identity.unionid = fact.unionid
                                  )
                            ) AS wechat_pay_orphan_count,
                            (
                                SELECT COUNT(*) FROM alipay_pay_orders fact
                                WHERE COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (fact.status = 'paid' OR fact.trade_status IN ('TRADE_SUCCESS', 'TRADE_FINISHED'))
                                  AND NULLIF(BTRIM(fact.unionid), '') IS NOT NULL
                                  AND NOT EXISTS (
                                      SELECT 1 FROM crm_user_identity identity
                                      WHERE identity.unionid = fact.unionid
                                  )
                            ) AS alipay_pay_orphan_count,
                            (
                                SELECT COUNT(*) FROM wechat_shop_orders fact
                                WHERE COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND fact.paid_at IS NOT NULL
                                  AND NULLIF(BTRIM(fact.unionid), '') IS NOT NULL
                                  AND NOT EXISTS (
                                      SELECT 1 FROM crm_user_identity identity
                                      WHERE identity.unionid = fact.unionid
                                  )
                            ) AS wechat_shop_orphan_count,
                            (
                                SELECT COUNT(DISTINCT job.id)
                                FROM broadcast_jobs job
                                WHERE job.created_at >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND EXISTS (
                                      SELECT 1
                                      FROM jsonb_array_elements_text(
                                          CASE
                                              WHEN jsonb_typeof(job.target_unionids_json) = 'array'
                                              THEN job.target_unionids_json
                                              ELSE '[]'::jsonb
                                          END
                                      ) target(unionid)
                                      WHERE NULLIF(BTRIM(target.unionid), '') IS NOT NULL
                                        AND NOT EXISTS (
                                            SELECT 1 FROM crm_user_identity identity
                                            WHERE identity.unionid = target.unionid
                                        )
                                  )
                            ) AS broadcast_orphan_count
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, source_tables)
    counts = {
        key: int(row.get(key) or 0)
        for key in (
            "questionnaire_orphan_count",
            "wechat_pay_orphan_count",
            "alipay_pay_orphan_count",
            "wechat_shop_orphan_count",
            "broadcast_orphan_count",
        )
    }
    violations = [f"{key}={value}" for key, value in counts.items() if value]
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Post-cutover business facts reference unionids missing from the canonical identity table.",
            evidence={**counts, "violations": violations},
            remediation="Repair canonical identity before replaying the affected continuation; do not invent or overwrite unionids.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="Post-cutover questionnaire, paid-order, shop-order, and broadcast facts all resolve to canonical identities.",
        evidence=counts,
        remediation="",
    )


def _identity_resolution_queue_backlog() -> DataHealthCheckResult:
    if not database_schema_available():
        return _db_backed_placeholder(
            "identity_resolution_queue_backlog",
            "Identity resolution queue backlog",
            ["crm_user_identity_resolution_queue"],
        )
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
                            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(first_seen_at) FILTER (WHERE status = 'pending'))) / 3600
                                AS oldest_pending_hours
                        FROM crm_user_identity_resolution_queue
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return DataHealthCheckResult(
            check_id="identity_resolution_queue_backlog",
            title="Identity resolution queue backlog",
            status="fail",
            severity="red",
            summary="Identity resolution queue backlog check could not read the live queue.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify crm_user_identity_resolution_queue exists and DATABASE_URL has read access.",
        )
    pending_count = int(row.get("pending_count") or 0)
    oldest_pending_hours = float(row.get("oldest_pending_hours") or 0)
    violations = []
    if pending_count > 100:
        violations.append(f"pending_count={pending_count} exceeds 100")
    if oldest_pending_hours > 24:
        violations.append(f"oldest_pending_hours={oldest_pending_hours:.1f} exceeds 24")
    if violations:
        return DataHealthCheckResult(
            check_id="identity_resolution_queue_backlog",
            title="Identity resolution queue backlog",
            status="fail",
            severity="red",
            summary="Identity resolution queue backlog exceeded the production threshold.",
            evidence={"pending_count": pending_count, "oldest_pending_hours": oldest_pending_hours, "violations": violations},
            remediation="Run the identity resolution backfill worker and inspect terminal failures.",
        )
    return DataHealthCheckResult(
        check_id="identity_resolution_queue_backlog",
        title="Identity resolution queue backlog",
        status="ok",
        severity="green",
        summary="Identity resolution queue backlog is within threshold.",
        evidence={"pending_count": pending_count, "oldest_pending_hours": oldest_pending_hours},
        remediation="",
    )


def _projection_freshness_customer_read_model() -> DataHealthCheckResult:
    check_id = "projection_freshness_customer_read_model"
    title = "Customer read model projection freshness"
    source_tables = [
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_timeline_event_next",
    ]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM customer_list_index_next) AS list_count,
                            (SELECT COUNT(*) FROM customer_detail_snapshot_next) AS detail_count,
                            EXISTS (
                                SELECT 1 FROM customer_read_model_refresh_state WHERE singleton_id = 1
                            ) AS refresh_state_present,
                            (
                                SELECT source_count FROM customer_read_model_refresh_state WHERE singleton_id = 1
                            ) AS refresh_source_count,
                            (
                                SELECT target_count FROM customer_read_model_refresh_state WHERE singleton_id = 1
                            ) AS refresh_target_count,
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
                                CURRENT_TIMESTAMP - (
                                    SELECT last_succeeded_at
                                    FROM customer_read_model_refresh_state
                                    WHERE singleton_id = 1
                                )
                            )) / 60 AS refresh_age_minutes
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


def _broadcast_job_blocked_backlog() -> DataHealthCheckResult:
    check_id = "broadcast_job_blocked_backlog"
    title = "Legacy broadcast ledger backlog"
    source_tables = ["broadcast_jobs", "broadcast_job_events"]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        WITH legacy_broadcast AS (
                            SELECT *
                            FROM broadcast_jobs
                            WHERE COALESCE(NULLIF(execution_owner, ''), 'legacy_frozen') = 'legacy_frozen'
                        )
                        SELECT
                            COUNT(*) FILTER (
                                WHERE status IN (
                                    'waiting_approval', 'queued', 'claimed', 'dispatching',
                                    'failed', 'failed_retryable', 'failed_terminal', 'blocked',
                                    'unknown_after_dispatch'
                                )
                            ) AS raw_open_count,
                            COUNT(*) FILTER (
                                WHERE hold_reason <> ''
                                  AND status IN (
                                      'waiting_approval', 'queued', 'claimed', 'dispatching',
                                      'failed', 'failed_retryable', 'failed_terminal', 'blocked',
                                      'unknown_after_dispatch'
                                  )
                            ) AS held_count,
                            COUNT(*) FILTER (
                                WHERE status IN ('failed', 'failed_terminal', 'blocked')
                            ) AS dlq_count,
                            COUNT(*) FILTER (
                                WHERE status = 'unknown_after_dispatch'
                            ) AS unknown_count,
                            0::BIGINT AS eligible_count,
                            COUNT(*) FILTER (
                                WHERE status = 'blocked'
                                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {BROADCAST_TERMINAL_LOOKBACK_HOURS})
                            ) AS recent_blocked_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed_terminal'
                                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {BROADCAST_TERMINAL_LOOKBACK_HOURS})
                            ) AS recent_failed_terminal_count,
                            COUNT(*) FILTER (WHERE status = 'blocked') AS historical_blocked_count,
                            COUNT(*) FILTER (WHERE status = 'failed_terminal') AS historical_failed_terminal_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed_retryable'
                                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                            ) AS due_retryable_count,
                            EXTRACT(EPOCH FROM (
                                CURRENT_TIMESTAMP - MIN(updated_at) FILTER (
                                    WHERE status IN ('blocked', 'failed_terminal')
                                )
                            )) / 3600 AS oldest_terminal_hours
                        FROM legacy_broadcast
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
            summary="Legacy broadcast ledger check could not read the live ledger.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify broadcast_jobs migrations and DATABASE_URL read access.",
        )

    raw_open_count = int(row.get("raw_open_count") or 0)
    held_count = int(row.get("held_count") or 0)
    dlq_count = int(row.get("dlq_count") or 0)
    unknown_count = int(row.get("unknown_count") or 0)
    eligible_count = 0
    blocked_count = int(row.get("recent_blocked_count", row.get("blocked_count")) or 0)
    failed_terminal_count = int(row.get("recent_failed_terminal_count", row.get("failed_terminal_count")) or 0)
    historical_blocked_count = int(row.get("historical_blocked_count") or blocked_count)
    historical_failed_terminal_count = int(row.get("historical_failed_terminal_count") or failed_terminal_count)
    due_retryable_count = int(row.get("due_retryable_count") or 0)
    oldest_terminal_hours = float(row.get("oldest_terminal_hours") or 0)
    violations = []
    if blocked_count > BROADCAST_BLOCKED_MAX_COUNT:
        violations.append(f"blocked_count={blocked_count} exceeds {BROADCAST_BLOCKED_MAX_COUNT}")
    if failed_terminal_count > 0:
        violations.append(f"failed_terminal_count={failed_terminal_count} exceeds 0")
    if due_retryable_count > BROADCAST_RETRYABLE_DUE_MAX_COUNT:
        violations.append(f"due_retryable_count={due_retryable_count} exceeds {BROADCAST_RETRYABLE_DUE_MAX_COUNT}")
    evidence = {
        "execution_owner": "legacy_frozen",
        "execution_semantics": "readonly",
        "raw_open_count": raw_open_count,
        "held_count": held_count,
        "eligible_count": eligible_count,
        "dlq_count": dlq_count,
        "unknown_count": unknown_count,
        "blocked_count": blocked_count,
        "failed_terminal_count": failed_terminal_count,
        "historical_blocked_count": historical_blocked_count,
        "historical_failed_terminal_count": historical_failed_terminal_count,
        "terminal_lookback_hours": BROADCAST_TERMINAL_LOOKBACK_HOURS,
        "due_retryable_count": due_retryable_count,
        "oldest_terminal_hours": oldest_terminal_hours,
        "due_retryable_threshold": BROADCAST_RETRYABLE_DUE_MAX_COUNT,
    }
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Legacy broadcast ledger has recent DLQ entries or excessive frozen retryable history; eligible remains zero.",
            evidence={**evidence, "violations": violations},
            remediation="Inspect and classify broadcast history manually; provider work must stay owned by external_effect_job and the legacy worker must remain retired.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="Legacy broadcast history is visible as a read-only ledger and has no executable rows.",
        evidence=evidence,
        remediation="",
    )


def _external_effect_failed_retryable_backlog() -> DataHealthCheckResult:
    check_id = "external_effect_failed_retryable_backlog"
    title = "External effect failed retryable backlog"
    source_tables = ["external_effect_job", "external_effect_attempt"]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        WITH classified_jobs AS (
                            SELECT job.*,
                                   (
                                       ({direct_canary_job_sql("job")})
                                       OR ({callback_welcome_failure_sql("job")})
                                   ) AS id_validation_canary,
                                   ({callback_welcome_failure_sql("job")})
                                       AS id_validation_callback_welcome
                            FROM external_effect_job job
                        )
                        SELECT
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary AND status = 'failed_retryable'
                            ) AS failed_retryable_count,
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary
                                  AND status = 'failed_terminal'
                                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS})
                            ) AS recent_failed_terminal_count,
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary
                                  AND status = 'blocked'
                                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS})
                            ) AS recent_blocked_count,
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary AND status = 'failed_terminal'
                            ) AS historical_failed_terminal_count,
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary AND status = 'blocked'
                            ) AS historical_blocked_count,
                            COUNT(*) FILTER (
                                WHERE NOT id_validation_canary
                                  AND status = 'failed_retryable'
                                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                            ) AS due_retryable_count,
                            EXTRACT(EPOCH FROM (
                                CURRENT_TIMESTAMP - MIN(COALESCE(next_retry_at, updated_at)) FILTER (
                                    WHERE NOT id_validation_canary AND status = 'failed_retryable'
                                )
                            )) AS oldest_failed_retryable_age_seconds,
                            COUNT(*) FILTER (
                                WHERE id_validation_canary AND status = 'failed_retryable'
                            ) AS canary_failed_retryable_count,
                            COUNT(*) FILTER (
                                WHERE id_validation_canary AND status = 'failed_terminal'
                            ) AS canary_failed_terminal_count,
                            COUNT(*) FILTER (
                                WHERE id_validation_canary AND status = 'blocked'
                            ) AS canary_blocked_count,
                            COUNT(*) FILTER (
                                WHERE id_validation_callback_welcome
                                  AND status = 'failed_terminal'
                            ) AS callback_welcome_failed_terminal_count
                        FROM classified_jobs
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
            summary="External effect backlog check could not read the live queue.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify external_effect_job migrations and DATABASE_URL read access.",
        )

    failed_retryable_count = int(row.get("failed_retryable_count") or 0)
    failed_terminal_count = int(row.get("recent_failed_terminal_count", row.get("failed_terminal_count")) or 0)
    blocked_count = int(row.get("recent_blocked_count", row.get("blocked_count")) or 0)
    historical_failed_terminal_count = int(row.get("historical_failed_terminal_count") or failed_terminal_count)
    historical_blocked_count = int(row.get("historical_blocked_count") or blocked_count)
    due_retryable_count = int(row.get("due_retryable_count") or 0)
    oldest_failed_retryable_age_seconds = int(float(row.get("oldest_failed_retryable_age_seconds") or 0))
    canary_failed_retryable_count = int(row.get("canary_failed_retryable_count") or 0)
    canary_failed_terminal_count = int(row.get("canary_failed_terminal_count") or 0)
    canary_blocked_count = int(row.get("canary_blocked_count") or 0)
    callback_welcome_failed_terminal_count = int(row.get("callback_welcome_failed_terminal_count") or 0)
    violations = []
    if failed_terminal_count > 0:
        violations.append(f"failed_terminal_count={failed_terminal_count} exceeds 0")
    if blocked_count > 0:
        violations.append(f"blocked_count={blocked_count} exceeds 0")
    if due_retryable_count > EXTERNAL_EFFECT_RETRYABLE_DUE_MAX_COUNT:
        violations.append(f"due_retryable_count={due_retryable_count} exceeds {EXTERNAL_EFFECT_RETRYABLE_DUE_MAX_COUNT}")
    evidence = {
        "failed_retryable_count": failed_retryable_count,
        "failed_terminal_count": failed_terminal_count,
        "blocked_count": blocked_count,
        "historical_failed_terminal_count": historical_failed_terminal_count,
        "historical_blocked_count": historical_blocked_count,
        "terminal_lookback_hours": EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS,
        "due_retryable_count": due_retryable_count,
        "oldest_failed_retryable_age_seconds": oldest_failed_retryable_age_seconds,
        "due_retryable_threshold": EXTERNAL_EFFECT_RETRYABLE_DUE_MAX_COUNT,
        "id_validation_canary": {
            "failed_retryable_count": canary_failed_retryable_count,
            "failed_terminal_count": canary_failed_terminal_count,
            "blocked_count": canary_blocked_count,
            "callback_welcome_failed_terminal_count": callback_welcome_failed_terminal_count,
            "excluded_from_business_health": True,
            "strict_provenance_required": True,
        },
    }
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="External effect queue has blocked, terminal, or excessive due retryable jobs.",
            evidence={**evidence, "violations": violations},
            remediation="Inspect external_effect_attempt, repair adapter/runtime failures, and requeue explicitly.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="External effect retryable backlog is within threshold.",
        evidence=evidence,
        remediation="",
    )


def _deprecated_execution_settings_present() -> DataHealthCheckResult:
    deprecated = [
        key
        for key in (
            "AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE",
            "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE",
            "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES",
            "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS",
        )
        if str(os.getenv(key) or "").strip()
    ]
    if deprecated:
        return DataHealthCheckResult(
            check_id="deprecated_execution_settings_present",
            title="Deprecated execution settings present",
            status="warn",
            severity="yellow",
            summary="Deprecated execution config is still present in the runtime environment.",
            evidence={"deprecated_settings_present": deprecated},
            remediation="Move WeCom execution to AICRM_WECOM_EXECUTION_MODE and AICRM_WECOM_ENABLED_EFFECT_TYPES; keep questionnaire external push fixed to queue.",
        )
    return DataHealthCheckResult(
        check_id="deprecated_execution_settings_present",
        title="Deprecated execution settings present",
        status="ok",
        severity="green",
        summary="No deprecated execution env settings are present in this process.",
        evidence={"deprecated_settings_present": []},
        remediation="",
    )


def _fake_stub_route_exposed() -> DataHealthCheckResult:
    api_path = ROOT / "aicrm_next" / "customer_tags" / "api.py"
    source = api_path.read_text(encoding="utf-8") if api_path.exists() else ""
    fake_stub_routes = source.count("/api/admin/wecom/tags/fake-stub")
    violations = []
    if fake_stub_routes:
        violations.append("fake-stub routes must not be registered in runtime routers")
    return _static_guard_result(
        check_id="fake_stub_route_exposed",
        title="Fake-stub route exposure guard",
        violations=violations,
        ok_summary="WeCom tag fake-stub runtime routes are not registered.",
        remediation="Keep fake-stub fixtures under tests; runtime routers must use the real read/write models.",
    )


def _external_effect_approved_not_queued() -> DataHealthCheckResult:
    check_id = "external_effect_approved_not_queued"
    title = "External effect approved-not-queued guard"
    source_tables = ["external_effect_job"]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) FILTER (
                                WHERE approved_at IS NOT NULL
                                  AND status IN (
                                      'pending_approval', 'awaiting_approval',
                                      'planned', 'approved', 'blocked'
                                  )
                            ) AS approved_not_runnable_count,
                            COUNT(*) FILTER (WHERE approved_at IS NOT NULL) AS approved_job_count
                        FROM external_effect_job
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, source_tables)
    blocked = int(row.get("approved_not_runnable_count") or 0)
    evidence = {
        "approved_not_runnable_count": blocked,
        "approved_job_count": int(row.get("approved_job_count") or 0),
    }
    if blocked:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Approved external-effect jobs remain in a non-runnable approval state.",
            evidence=evidence,
            remediation="Inspect approval projection and explicitly queue or cancel each approved job.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="No approved external-effect job is stranded before the runnable queue.",
        evidence=evidence,
        remediation="",
    )


def _questionnaire_submission_without_user_guard() -> DataHealthCheckResult:
    check_id = "questionnaire_submission_without_user_guard"
    title = "Questionnaire submissions without identity"
    source_tables = [
        "questionnaire_submissions",
        "crm_user_identity",
        "internal_event_outbox",
        "internal_event",
        "external_effect_job",
    ]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        WITH classified_submissions AS (
                            SELECT
                                submission.id,
                                submission.submitted_at,
                                NULLIF(BTRIM(submission.unionid), '') AS submission_unionid,
                                identity.unionid AS identity_unionid,
                                (
                                    EXISTS (
                                        SELECT 1
                                        FROM internal_event_outbox outbox
                                        WHERE outbox.tenant_id = 'aicrm'
                                          AND outbox.event_type = 'questionnaire.submitted'
                                          AND (
                                              outbox.idempotency_key = 'questionnaire.submitted:' || submission.id::text
                                              OR (
                                                  outbox.aggregate_type = 'questionnaire_submission'
                                                  AND outbox.aggregate_id = submission.id::text
                                              )
                                          )
                                    )
                                    OR EXISTS (
                                        SELECT 1
                                        FROM internal_event event
                                        WHERE event.tenant_id = 'aicrm'
                                          AND event.event_type = 'questionnaire.submitted'
                                          AND (
                                              event.idempotency_key = 'questionnaire.submitted:' || submission.id::text
                                              OR (
                                                  event.aggregate_type = 'questionnaire_submission'
                                                  AND event.aggregate_id = submission.id::text
                                              )
                                          )
                                    )
                                ) AS continuation_guard_present,
                                EXISTS (
                                    SELECT 1
                                    FROM external_effect_job job
                                    WHERE job.effect_type IN (
                                        'webhook.questionnaire_submission.push',
                                        'wecom.contact.tag.mark'
                                    )
                                      AND (
                                          (
                                              job.target_type = 'questionnaire_submission'
                                              AND job.target_id = submission.id::text
                                          )
                                          OR (
                                              job.business_type = 'questionnaire_submission'
                                              AND job.business_id = submission.id::text
                                          )
                                      )
                                ) AS identity_dependent_effect_present
                            FROM questionnaire_submissions submission
                            LEFT JOIN crm_user_identity identity ON identity.unionid = submission.unionid
                        )
                        SELECT
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NULL
                            ) AS missing_unionid_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NULL
                                  AND continuation_guard_present
                                  AND NOT identity_dependent_effect_present
                            ) AS guarded_missing_unionid_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NULL
                                  AND (
                                      NOT continuation_guard_present
                                      OR identity_dependent_effect_present
                                  )
                            ) AS unguarded_missing_unionid_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NULL
                                  AND NOT continuation_guard_present
                            ) AS missing_continuation_guard_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NULL
                                  AND identity_dependent_effect_present
                            ) AS identity_dependent_effect_without_unionid_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at >= {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND submission_unionid IS NOT NULL
                                  AND identity_unionid IS NULL
                            ) AS missing_identity_count,
                            COUNT(*) FILTER (
                                WHERE submitted_at < {QUESTIONNAIRE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      submission_unionid IS NULL
                                      OR identity_unionid IS NULL
                                  )
                            ) AS historical_pre_cutover_count
                        FROM classified_submissions
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, source_tables)
    evidence = {
        "missing_unionid_count": int(row.get("missing_unionid_count") or 0),
        "guarded_missing_unionid_count": int(row.get("guarded_missing_unionid_count") or 0),
        "unguarded_missing_unionid_count": int(row.get("unguarded_missing_unionid_count") or 0),
        "missing_continuation_guard_count": int(row.get("missing_continuation_guard_count") or 0),
        "identity_dependent_effect_without_unionid_count": int(row.get("identity_dependent_effect_without_unionid_count") or 0),
        "missing_identity_count": int(row.get("missing_identity_count") or 0),
        "historical_pre_cutover_count": int(row.get("historical_pre_cutover_count") or 0),
        "cutover_at": QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT,
    }
    actionable = evidence["unguarded_missing_unionid_count"] + evidence["missing_identity_count"]
    if actionable:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Post-cutover questionnaire submissions are missing canonical identity without a safe continuation guard.",
            evidence=evidence,
            remediation=(
                "Restore the durable questionnaire continuation guard or remove identity-dependent effects; "
                "resolve canonical identity before replaying quarantined continuations."
            ),
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary=(
            "Post-cutover questionnaire submissions either resolve canonical identity or remain quarantined "
            "behind the durable continuation guard without identity-dependent effects."
        ),
        evidence=evidence,
        remediation="",
    )


def _payment_order_without_user_guard() -> DataHealthCheckResult:
    check_id = "payment_order_without_user_guard"
    title = "Paid orders without identity"
    source_tables = [
        "wechat_pay_orders",
        "alipay_pay_orders",
        "wechat_shop_orders",
        "crm_user_identity",
    ]
    if not database_schema_available():
        return _db_unavailable_placeholder(check_id, title, source_tables)
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT
                            (
                                SELECT COUNT(*) FROM wechat_pay_orders fact
                                WHERE (fact.status = 'paid' OR fact.trade_state = 'SUCCESS')
                                  AND COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS wechat_pay_missing_user_count,
                            (
                                SELECT COUNT(*) FROM alipay_pay_orders fact
                                WHERE (fact.status = 'paid' OR fact.trade_status IN ('TRADE_SUCCESS', 'TRADE_FINISHED'))
                                  AND COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS alipay_pay_missing_user_count,
                            (
                                SELECT COUNT(*) FROM wechat_shop_orders fact
                                WHERE fact.paid_at IS NOT NULL
                                  AND COALESCE(fact.paid_at, fact.created_at) >= {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS wechat_shop_missing_user_count,
                            (
                                SELECT COUNT(*) FROM wechat_pay_orders fact
                                WHERE (fact.status = 'paid' OR fact.trade_state = 'SUCCESS')
                                  AND COALESCE(fact.paid_at, fact.created_at) < {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS historical_wechat_pay_count,
                            (
                                SELECT COUNT(*) FROM alipay_pay_orders fact
                                WHERE (fact.status = 'paid' OR fact.trade_status IN ('TRADE_SUCCESS', 'TRADE_FINISHED'))
                                  AND COALESCE(fact.paid_at, fact.created_at) < {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS historical_alipay_pay_count,
                            (
                                SELECT COUNT(*) FROM wechat_shop_orders fact
                                WHERE fact.paid_at IS NOT NULL
                                  AND COALESCE(fact.paid_at, fact.created_at) < {COMMERCE_CONTINUATION_CUTOVER_SQL}
                                  AND (
                                      NULLIF(BTRIM(fact.unionid), '') IS NULL
                                      OR NOT EXISTS (
                                          SELECT 1 FROM crm_user_identity identity
                                          WHERE identity.unionid = fact.unionid
                                      )
                                  )
                            ) AS historical_wechat_shop_count
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, source_tables)
    evidence = {
        key: int(row.get(key) or 0)
        for key in (
            "wechat_pay_missing_user_count",
            "alipay_pay_missing_user_count",
            "wechat_shop_missing_user_count",
            "historical_wechat_pay_count",
            "historical_alipay_pay_count",
            "historical_wechat_shop_count",
        )
    }
    evidence["cutover_at"] = "2026-07-13T09:46:09Z"
    actionable = sum(
        evidence[key]
        for key in (
            "wechat_pay_missing_user_count",
            "alipay_pay_missing_user_count",
            "wechat_shop_missing_user_count",
        )
    )
    if actionable:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Post-cutover paid orders are missing canonical user identity.",
            evidence=evidence,
            remediation="Resolve the payer identity before granting or replaying user authorization.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="Every post-cutover paid order is linked to a canonical user identity.",
        evidence=evidence,
        remediation="",
    )


def _customer_360_freshness_guard() -> DataHealthCheckResult:
    check_id = "customer_360_freshness_guard"
    title = "Customer 360 freshness guard"
    freshness_probes = [
        "latest_identity_update",
        "latest_order",
        "latest_questionnaire",
        "latest_message",
        "latest_projection_refresh",
    ]
    source_tables = [
        "crm_user_identity",
        "wechat_pay_orders",
        "alipay_pay_orders",
        "wechat_shop_orders",
        "questionnaire_submissions",
        "archived_messages",
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_read_model_refresh_state",
    ]
    if not database_schema_available():
        result = _db_unavailable_placeholder(check_id, title, source_tables)
        return DataHealthCheckResult(
            check_id=result.check_id,
            title=result.title,
            status=result.status,
            severity=result.severity,
            summary=result.summary,
            evidence={
                **result.evidence,
                "freshness_probes": freshness_probes,
            },
            remediation=result.remediation,
        )
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        """
                        WITH refresh AS (
                            SELECT last_succeeded_at
                            FROM customer_read_model_refresh_state
                            WHERE singleton_id = 1
                        )
                        SELECT
                            EXISTS (SELECT 1 FROM refresh) AS refresh_state_present,
                            EXTRACT(EPOCH FROM (
                                CURRENT_TIMESTAMP - (SELECT last_succeeded_at FROM refresh)
                            )) / 60 AS refresh_age_minutes,
                            EXTRACT(EPOCH FROM (
                                (SELECT MAX(updated_at) FROM crm_user_identity)
                                - (SELECT last_succeeded_at FROM refresh)
                            )) / 60 AS identity_lag_minutes,
                            EXTRACT(EPOCH FROM (
                                GREATEST(
                                    (SELECT MAX(COALESCE(paid_at, updated_at, created_at)) FROM wechat_pay_orders),
                                    (SELECT MAX(COALESCE(paid_at, updated_at, created_at)) FROM alipay_pay_orders),
                                    (SELECT MAX(COALESCE(paid_at, updated_at, created_at)) FROM wechat_shop_orders)
                                ) - (SELECT last_succeeded_at FROM refresh)
                            )) / 60 AS order_lag_minutes,
                            EXTRACT(EPOCH FROM (
                                (SELECT MAX(submitted_at) FROM questionnaire_submissions)
                                - (SELECT last_succeeded_at FROM refresh)
                            )) / 60 AS questionnaire_lag_minutes,
                            EXTRACT(EPOCH FROM (
                                (SELECT MAX(created_at) FROM archived_messages)
                                - (SELECT last_succeeded_at FROM refresh)
                            )) / 60 AS message_lag_minutes
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, source_tables)
    evidence = {
        "refresh_state_present": bool(row.get("refresh_state_present")),
        "refresh_age_minutes": float(row.get("refresh_age_minutes") or 0),
        "identity_lag_minutes": float(row.get("identity_lag_minutes") or 0),
        "order_lag_minutes": float(row.get("order_lag_minutes") or 0),
        "questionnaire_lag_minutes": float(row.get("questionnaire_lag_minutes") or 0),
        "message_lag_minutes": float(row.get("message_lag_minutes") or 0),
        "max_lag_minutes": PROJECTION_FRESHNESS_MAX_MINUTES,
        "freshness_probes": freshness_probes,
    }
    violations = []
    if not evidence["refresh_state_present"]:
        violations.append("customer read model has no successful managed refresh")
    for key in (
        "identity_lag_minutes",
        "order_lag_minutes",
        "questionnaire_lag_minutes",
        "message_lag_minutes",
    ):
        if evidence[key] > PROJECTION_FRESHNESS_MAX_MINUTES:
            violations.append(f"{key}={evidence[key]:.1f} exceeds {PROJECTION_FRESHNESS_MAX_MINUTES}")
    if violations:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="fail",
            severity="red",
            summary="Customer 360 projection lags one or more canonical sources.",
            evidence={**evidence, "violations": violations},
            remediation="Run the managed customer read model refresh and inspect its timer/service state.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="Customer 360 projection is within the freshness threshold for identity, order, questionnaire, and message sources.",
        evidence=evidence,
        remediation="",
    )


def _wecom_media_lease_health() -> DataHealthCheckResult:
    check_id = "wecom_media_lease_health"
    title = "WeCom temporary media lease health"
    if not database_schema_available():
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="not_applicable",
            severity="gray",
            summary="DATABASE_URL is not configured, so WeCom media leases cannot be checked.",
            evidence={"runtime_probe": "database_url_not_configured"},
            remediation="Run this check against the migrated production database.",
        )
    try:
        with get_session_factory()() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        WITH classified_leases AS (
                            SELECT lease.*,
                                   EXISTS (
                                       SELECT 1
                                       FROM external_effect_job canary_job
                                       WHERE canary_job.target_type = 'media_library_material'
                                         AND canary_job.target_id = CONCAT(
                                             lease.material_kind, ':', lease.material_id, ':', lease.upload_kind
                                         )
                                         AND ({direct_canary_job_sql("canary_job")})
                                   ) AS id_validation_canary_referenced,
                                   EXISTS (
                                       SELECT 1
                                       FROM external_effect_job ordinary_job
                                       WHERE ordinary_job.target_type = 'media_library_material'
                                         AND ordinary_job.target_id = CONCAT(
                                             lease.material_kind, ':', lease.material_id, ':', lease.upload_kind
                                         )
                                         AND NOT ({direct_canary_job_sql("ordinary_job")})
                                   ) AS ordinary_job_referenced
                            FROM wecom_media_leases lease
                            WHERE lease.tenant_id = 'aicrm'
                        )
                        SELECT
                            COUNT(*) AS total_count,
                            COUNT(*) FILTER (WHERE status = 'ready' AND provider_expires_at > CURRENT_TIMESTAMP) AS ready_count,
                            COUNT(*) FILTER (WHERE status = 'ready' AND refresh_after <= CURRENT_TIMESTAMP) AS refresh_due_count,
                            COUNT(*) FILTER (WHERE status = 'refreshing') AS refreshing_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed'
                                  AND NOT (id_validation_canary_referenced AND NOT ordinary_job_referenced)
                            ) AS failed_count,
                            COUNT(*) FILTER (
                                WHERE status = 'invalid_source'
                                  AND NOT (id_validation_canary_referenced AND NOT ordinary_job_referenced)
                            ) AS invalid_source_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed'
                                  AND id_validation_canary_referenced AND NOT ordinary_job_referenced
                            ) AS canary_failed_count,
                            COUNT(*) FILTER (
                                WHERE status = 'invalid_source'
                                  AND id_validation_canary_referenced AND NOT ordinary_job_referenced
                            ) AS canary_invalid_source_count,
                            COUNT(*) FILTER (WHERE status = 'ready' AND provider_expires_at <= CURRENT_TIMESTAMP) AS expired_count,
                            (
                                SELECT COUNT(*) FROM (
                                    SELECT id FROM image_library
                                    WHERE enabled IS TRUE AND COALESCE(data_base64, '') = ''
                                    UNION ALL
                                    SELECT id FROM attachment_library
                                    WHERE enabled IS TRUE AND COALESCE(data_base64, '') = ''
                                    UNION ALL
                                    SELECT id FROM miniprogram_library
                                    WHERE enabled IS TRUE AND thumb_image_id IS NULL
                                      AND COALESCE(thumb_image_base64, '') = ''
                                ) source_gaps
                            ) AS source_gap_count
                        FROM classified_leases
                        """
                    )
                )
                .mappings()
                .one()
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return _database_probe_failure(check_id, title, exc, ["wecom_media_leases"])
    evidence = {key: int(value or 0) for key, value in dict(row).items()}
    unhealthy = evidence["failed_count"] + evidence["invalid_source_count"] + evidence["expired_count"]
    if unhealthy:
        return DataHealthCheckResult(
            check_id=check_id,
            title=title,
            status="warn",
            severity="yellow",
            summary="One or more WeCom temporary media leases require repair.",
            evidence=evidence,
            remediation="Run the media lease backfill/refresh worker and repair any material with an invalid durable source.",
        )
    return DataHealthCheckResult(
        check_id=check_id,
        title=title,
        status="ok",
        severity="green",
        summary="WeCom temporary media leases have no failed, invalid, or expired rows.",
        evidence=evidence,
        remediation="",
    )


_CHECKS: tuple[Callable[[], DataHealthCheckResult], ...] = (
    _identity_legacy_column_guard,
    _table_lifecycle_manifest_guard,
    _retired_table_runtime_reference_guard,
    _schema_drift_guard,
    _unionid_orphan_fact_guard,
    _identity_resolution_queue_backlog,
    _projection_freshness_customer_read_model,
    _broadcast_job_blocked_backlog,
    _external_effect_failed_retryable_backlog,
    _deprecated_execution_settings_present,
    _fake_stub_route_exposed,
    _external_effect_approved_not_queued,
    _questionnaire_submission_without_user_guard,
    _payment_order_without_user_guard,
    _customer_360_freshness_guard,
    _wecom_media_lease_health,
)
