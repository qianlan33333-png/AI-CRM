from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aicrm_next.shared.db_session import connect_raw_postgres
from aicrm_next.shared.runtime import raw_database_url


_R10_PRODUCTION_CUTOVER_SQL = "TIMESTAMPTZ '2026-07-13 05:42:30+00'"


_ANOMALY_QUERIES = {
    "stale_dispatching": f"""
        SELECT job.id
        FROM broadcast_jobs job
        WHERE job.status = 'dispatching'
          AND job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND COALESCE(job.dispatch_started_at, job.updated_at)
              < CURRENT_TIMESTAMP - INTERVAL '15 minutes'
    """,
    "unknown_after_dispatch": f"""
        SELECT job.id
        FROM broadcast_jobs job
        WHERE job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND (
              job.status = 'unknown_after_dispatch'
              OR job.reconciliation_required = TRUE
          )
    """,
    "job_recipient_projection_mismatch": f"""
        SELECT job.id
        FROM broadcast_jobs job
        JOIN cloud_broadcast_plan_recipients recipient
          ON recipient.broadcast_job_id = job.id
        WHERE job.source_type = 'cloud_plan'
          AND job.source_table = 'cloud_broadcast_plan_recipients'
          AND job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND job.status IN (
              'dispatching', 'sent', 'simulated', 'failed_retryable',
              'failed_terminal', 'blocked', 'unknown_after_dispatch'
          )
          AND recipient.send_status IS DISTINCT FROM job.status
    """,
    "job_message_projection_mismatch": f"""
        SELECT job.id
        FROM broadcast_jobs job
        JOIN cloud_broadcast_plan_recipients recipient
          ON recipient.broadcast_job_id = job.id
        WHERE job.source_type = 'cloud_plan'
          AND job.source_table = 'cloud_broadcast_plan_recipients'
          AND job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND job.status IN (
              'dispatching', 'sent', 'simulated', 'failed_retryable',
              'failed_terminal', 'blocked', 'unknown_after_dispatch'
          )
          AND EXISTS (
              SELECT 1
              FROM cloud_broadcast_plan_recipient_messages message
              WHERE message.recipient_id = recipient.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM cloud_broadcast_plan_recipient_messages message
              WHERE message.recipient_id = recipient.id
                AND message.status = job.status
          )
    """,
    "sent_missing_delivery_evidence": f"""
        SELECT job.id
        FROM broadcast_jobs job
        WHERE job.status = 'sent'
          AND job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND (
              job.side_effect_executed IS NOT TRUE
              OR job.provider_result_received IS NOT TRUE
              OR COALESCE(job.result_summary_json, '{{}}'::jsonb) = '{{}}'::jsonb
          )
    """,
    "sent_missing_outbound_task": f"""
        SELECT job.id
        FROM broadcast_jobs job
        WHERE job.status = 'sent'
          AND job.created_at >= {_R10_PRODUCTION_CUTOVER_SQL}
          AND (
              job.outbound_task_id IS NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM outbound_tasks task
                  WHERE task.id = job.outbound_task_id
                    AND task.broadcast_job_id = job.id
              )
          )
    """,
    "duplicate_idempotency_key": """
        SELECT MIN(job.id) AS id
        FROM broadcast_jobs job
        WHERE COALESCE(job.idempotency_key, '') <> ''
        GROUP BY job.idempotency_key
        HAVING COUNT(*) > 1
    """,
}

_P1_TABLES = (
    "group_ops_workspace_allowlist_snapshots",
    "group_ops_workspace_draft_audit_logs",
    "group_ops_workspace_draft_items",
    "group_ops_workspace_drafts",
    "group_ops_workspace_governance_review_steps",
    "group_ops_workspace_governance_reviews",
    "group_ops_workspace_gray_window_approvals",
)

_P1_RUNTIME_PATHS = (
    "aicrm_next/app/admin_console/templates/admin_shell/p1_group_ops_workspace.html",
    "aicrm_next/automation_engine/group_ops/draft_api.py",
    "aicrm_next/automation_engine/group_ops/draft_repository.py",
    "aicrm_next/automation_engine/group_ops/draft_service.py",
    "aicrm_next/automation_engine/group_ops/governance_api.py",
    "aicrm_next/automation_engine/group_ops/governance_repository.py",
    "aicrm_next/automation_engine/group_ops/governance_service.py",
    "aicrm_next/app/admin_console/static/admin_console/p1/p1_group_ops_workspace",
    "scripts/diagnose_p1_group_ops_workspace_bridge_acceptance.py",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


class GroupOpsBroadcastReconciliationService:
    """Count broadcast/P1 convergence gaps without exposing rows or executing work."""

    def __init__(self, *, database_url: str = "", repo_root: Path | None = None) -> None:
        self._database_url = _text(database_url) or raw_database_url()
        self._repo_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()

    def diagnose(self) -> dict[str, Any]:
        if not self._database_url:
            return self._error("database_url_required")
        from psycopg.rows import dict_row

        counts: dict[str, int] = {}
        with connect_raw_postgres(self._database_url) as conn:
            conn.row_factory = dict_row
            for name, query in _ANOMALY_QUERIES.items():
                row = conn.execute(f"WITH anomalies AS ({query}) SELECT COUNT(*)::integer AS anomaly_count FROM anomalies").fetchone()
                counts[name] = int((row or {}).get("anomaly_count") or 0)
        counts.update(self._static_p1_counts())
        return {
            "ok": True,
            "mode": "count_only",
            "repair_supported": False,
            "has_anomalies": any(counts.values()),
            "counts": counts,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "provider_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }

    def _static_p1_counts(self) -> dict[str, int]:
        runtime_artifacts = 0
        for relative in _P1_RUNTIME_PATHS:
            path = self._repo_root / relative
            if path.is_file() or (path.is_dir() and any(item.is_file() for item in path.rglob("*"))):
                runtime_artifacts += 1
        lifecycle_path = self._repo_root / "docs/architecture/data_table_lifecycle_manifest.yml"
        lifecycle = yaml.safe_load(lifecycle_path.read_text(encoding="utf-8")) if lifecycle_path.exists() else {}
        tables = lifecycle.get("tables", {}) if isinstance(lifecycle, dict) else {}
        active_ownership = 0
        for table_name in _P1_TABLES:
            entry = tables.get(table_name, {}) if isinstance(tables, dict) else {}
            if not isinstance(entry, dict):
                active_ownership += 1
                continue
            write_owners = entry.get("write_owners") if isinstance(entry.get("write_owners"), list) else []
            runtime_entrypoints = entry.get("runtime_entrypoints") if isinstance(entry.get("runtime_entrypoints"), list) else []
            if (
                _text(entry.get("lifecycle")) != "retired"
                or _text(entry.get("write_owner"))
                or any(_text(item) for item in write_owners)
                or any(_text(item) for item in runtime_entrypoints)
            ):
                active_ownership += 1
        return {
            "p1_runtime_artifact": runtime_artifacts,
            "p1_active_ownership_declaration": active_ownership,
        }

    @staticmethod
    def _error(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "mode": "count_only",
            "repair_supported": False,
            "error": error,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "provider_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }
