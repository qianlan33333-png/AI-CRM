from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime import raw_database_url
from aicrm_next.shared.sensitive_data import redact_sensitive_data

from .repository import (
    _default_connect,
    _psycopg_url,
    external_canary_authorization_predicate,
    external_claim_scope_predicate,
)


PROVENANCE_PATH = Path("/home/ubuntu/.aicrm-releases/id-validation.json")
TIMELINE_MAX_DEPTH = 12
TIMELINE_MAX_EXECUTION_NODES = 256
TIMELINE_MAX_ITEMS = 1024


def queue_policy_base_eligible_predicate(
    row_alias: str = "rows",
    control_alias: str = "control",
    policy_alias: str = "policy",
) -> str:
    """Canonical eligibility gate excluding external execution scope."""

    return f"""
        {row_alias}.hold_reason = ''
        AND {row_alias}.ready_state
        AND {row_alias}.available_at <= CURRENT_TIMESTAMP
        AND {row_alias}.attempt_count < {row_alias}.max_attempts
        AND {row_alias}.worker_generation IN (0, {control_alias}.active_generation)
        AND {row_alias}.policy_version = {control_alias}.policy_version
        AND {control_alias}.active_generation > 0
        AND {control_alias}.claim_enabled
        AND {control_alias}.rollout_mode IN ('canary', 'execute')
        AND {policy_alias}.enabled
        AND {policy_alias}.rollout_mode IN ('canary', 'execute')
        AND ({policy_alias}.blocked_until IS NULL OR {policy_alias}.blocked_until <= CURRENT_TIMESTAMP)
        AND {policy_alias}.policy_version = {control_alias}.policy_version
        AND NOT {row_alias}.in_flight
        AND NOT {row_alias}.unknown_state
        AND NOT {row_alias}.dlq_state
        AND NOT {row_alias}.rate_limited
        AND NOT {row_alias}.ordering_blocked
    """


def queue_policy_external_scope_predicate(
    row_alias: str = "rows",
    control_alias: str = "control",
) -> str:
    """Apply the durable DB external claim scope to external queue rows."""

    external_scope = external_claim_scope_predicate(
        row_alias=row_alias,
        scope_expression=f"{control_alias}.external_claim_scope",
        execution_scope_expression=f"{row_alias}.execution_scope",
        canary_authorized_expression=f"{row_alias}.canary_authorized",
    )
    return f"({row_alias}.queue_kind <> 'external_effect' OR {external_scope})"


def queue_policy_eligible_predicate(
    row_alias: str = "rows",
    control_alias: str = "control",
    policy_alias: str = "policy",
) -> str:
    """Canonical policy gate shared by claims, runtime and system health."""

    base = queue_policy_base_eligible_predicate(row_alias, control_alias, policy_alias)
    scope = queue_policy_external_scope_predicate(row_alias, control_alias)
    return f"({base}) AND ({scope})"


def _public_datetime(value: Any) -> str:
    if not isinstance(value, datetime):
        return str(value or "")
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_hash(value: Any) -> str:
    normalized = str(value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def release_provenance(path: Path = PROVENANCE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"available": False}
    if not isinstance(payload, dict):
        return {"available": False}
    allowed = {
        "repository",
        "release_sha",
        "base_sha",
        "bundle_sha256",
        "source_ci_run_id",
        "deploy_run_id",
        "deploy_run_attempt",
        "environment",
        "public_health_url",
        "deployed_at",
    }
    return {"available": True, **{key: payload.get(key) for key in allowed if key in payload}}


class ExecutionRuntimeReadModel:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[[str], Any] = _default_connect,
        provenance_path: Path = PROVENANCE_PATH,
    ) -> None:
        self._database_url = _psycopg_url(database_url or raw_database_url())
        if not self._database_url.startswith("postgresql://"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required for execution runtime reads")
        self._connect = connect
        self._provenance_path = provenance_path

    def runtime_snapshot(self) -> dict[str, Any]:
        release_sha = current_release_sha()
        with self._connect(self._database_url) as connection:
            control = connection.execute(
                """
                SELECT active_generation, claim_enabled, rollout_mode,
                       global_max_in_flight, policy_version,
                       external_claim_scope, updated_by,
                       updated_reason, updated_at
                FROM queue_runtime_control
                WHERE singleton = TRUE
                """
            ).fetchone()
            lanes = connection.execute(self._lane_metrics_sql()).fetchall()
            workers = connection.execute(
                """
                SELECT service_name, worker_id, queue_kind, generation,
                       release_sha, rollout_mode, listener_connected,
                       last_notification_at, last_drain_at, heartbeat_at,
                       heartbeat_at > CURRENT_TIMESTAMP - INTERVAL '30 seconds' AS fresh,
                       release_sha = %s AS release_matches
                FROM queue_worker_heartbeat
                ORDER BY service_name, worker_id
                """,
                (release_sha,),
            ).fetchall()
            cooldowns = connection.execute(
                """
                SELECT rate_scope_key, provider, corp_id, app_id, operation,
                       blocked_until, reason, source_attempt_id
                FROM queue_rate_scope_cooldown
                WHERE blocked_until > CURRENT_TIMESTAMP
                ORDER BY blocked_until DESC, rate_scope_key
                LIMIT 100
                """
            ).fetchall()
            policy = connection.execute(
                """
                SELECT policy_version, policy_json, created_by, created_reason, created_at
                FROM queue_policy_snapshot
                WHERE policy_version = %s
                """,
                (str((control or {}).get("policy_version") or ""),),
            ).fetchone()
        lane_items = [self._lane_payload(row) for row in lanes]
        worker_items = [self._worker_payload(row) for row in workers]
        fresh_worker_items = [item for item in worker_items if item["fresh"]]
        control_payload = self._control_payload(control or {})
        durable_external_scope = str(control_payload.get("external_claim_scope") or "blocked")
        public_external_scope = "test_loopback_only" if durable_external_scope == "test_loopback" else durable_external_scope
        external_scope_modes = {
            lane["lane"]: public_external_scope
            for lane in lane_items
            if lane["lane"] in {"wecom_interactive", "wecom_bulk", "wecom_media", "outbound_webhook"}
        }
        policy_payload = self._policy_payload(policy or {})
        policy_payload["external_claim_scope"] = durable_external_scope
        policy_payload["external_execution_scope_mode"] = external_scope_modes
        return {
            "ok": bool(control),
            "control": control_payload,
            "lanes": lane_items,
            "workers": worker_items,
            "active_rate_limits": [self._cooldown_payload(row) for row in cooldowns],
            "policy_snapshot": policy_payload,
            "release": {
                "web_release_sha": release_sha,
                "fresh_worker_count": len(fresh_worker_items),
                "stale_worker_count": len(worker_items) - len(fresh_worker_items),
                "all_fresh_workers_match_release": bool(fresh_worker_items)
                and all(item["release_matches"] for item in fresh_worker_items),
                "provenance": release_provenance(self._provenance_path),
            },
            "pii_in_output": False,
            "secrets_in_output": False,
        }

    def lane_summary(self, lane_names: set[str] | frozenset[str]) -> dict[str, Any]:
        snapshot = self.runtime_snapshot()
        selected = [lane for lane in snapshot.get("lanes", []) if lane.get("lane") in lane_names]
        count_keys = (
            "raw_open",
            "held",
            "eligible",
            "policy_gated",
            "scheduled",
            "retry_wait",
            "rate_limited",
            "in_flight",
            "unknown",
            "dlq",
        )
        return {
            "policy_version": str((snapshot.get("control") or {}).get("policy_version") or ""),
            "active_generation": int((snapshot.get("control") or {}).get("active_generation") or 0),
            "claim_enabled": bool((snapshot.get("control") or {}).get("claim_enabled")),
            "rollout_mode": str((snapshot.get("control") or {}).get("rollout_mode") or "blocked"),
            "lanes": selected,
            **{key: sum(int(lane.get(key) or 0) for lane in selected) for key in count_keys},
        }

    def execution_timeline(self, execution_id: str) -> dict[str, Any] | None:
        normalized = str(execution_id or "").strip()
        if not normalized:
            return None
        with self._connect(self._database_url) as connection:
            graph = self._discover_execution_graph(connection, normalized)
            execution_ids = sorted(graph["execution_ids"])
            rows = connection.execute(
                self._timeline_sql(),
                (*([execution_ids] * 7), TIMELINE_MAX_ITEMS + 1),
            ).fetchall()
        if not rows:
            return None
        items_truncated = len(rows) > TIMELINE_MAX_ITEMS
        rows = rows[:TIMELINE_MAX_ITEMS]
        items = [self._timeline_payload(row) for row in rows]
        parent_ids = sorted({parent_id for _, parent_id in graph["edges"]})
        child_ids = sorted(set(execution_ids) - {normalized})
        return {
            "execution_id": normalized,
            "parent_execution_ids": parent_ids,
            "child_execution_ids": child_ids,
            "items": items,
            "graph": {
                "execution_node_count": len(execution_ids),
                "edge_count": len(graph["edges"]),
                "max_depth_reached": int(graph["max_depth_reached"]),
                "max_depth": TIMELINE_MAX_DEPTH,
                "max_execution_nodes": TIMELINE_MAX_EXECUTION_NODES,
                "max_items": TIMELINE_MAX_ITEMS,
                "truncated": bool(graph["truncated"] or items_truncated),
            },
            "pii_in_output": False,
            "secrets_in_output": False,
        }

    @classmethod
    def _discover_execution_graph(cls, connection: Any, root_execution_id: str) -> dict[str, Any]:
        """Discover the bounded connected execution graph around one public root.

        Parent links are traversed in both directions so the same endpoint works
        when an operator opens a root, an intermediate event, or a leaf run.  A
        global visited set prevents cycles and caps database work independently
        of malformed historical links.
        """

        execution_ids = {root_execution_id}
        frontier = {root_execution_id}
        edges: set[tuple[str, str]] = set()
        max_depth_reached = 0
        truncated = False

        for depth in range(1, TIMELINE_MAX_DEPTH + 1):
            if not frontier:
                break
            frontier_ids = sorted(frontier)
            per_lookup_limit = TIMELINE_MAX_EXECUTION_NODES + 1
            rows = connection.execute(
                cls._timeline_links_sql(),
                (
                    *(
                        value
                        for _ in range(10)
                        for value in (frontier_ids, per_lookup_limit)
                    ),
                    per_lookup_limit,
                ),
            ).fetchall()
            discovered: set[str] = set()
            for row in rows:
                child_execution_id = str(row.get("execution_id") or "").strip()
                parent_execution_id = str(row.get("parent_execution_id") or "").strip()
                if child_execution_id and parent_execution_id:
                    edges.add((child_execution_id, parent_execution_id))
                for candidate in (child_execution_id, parent_execution_id):
                    if candidate and candidate not in execution_ids:
                        discovered.add(candidate)

            remaining = TIMELINE_MAX_EXECUTION_NODES - len(execution_ids)
            if len(discovered) > remaining:
                truncated = True
                discovered = set(sorted(discovered)[:remaining])
            execution_ids.update(discovered)
            frontier = discovered
            if discovered:
                max_depth_reached = depth
            if len(execution_ids) >= TIMELINE_MAX_EXECUTION_NODES:
                truncated = truncated or bool(frontier)
                break
        else:
            truncated = truncated or bool(frontier)

        bounded_edges = {
            edge
            for edge in edges
            if edge[0] in execution_ids and edge[1] in execution_ids
        }
        return {
            "execution_ids": execution_ids,
            "edges": bounded_edges,
            "max_depth_reached": max_depth_reached,
            "truncated": truncated,
        }

    @staticmethod
    def _lane_metrics_sql() -> str:
        base_eligible = queue_policy_base_eligible_predicate()
        external_scope = external_claim_scope_predicate(
            row_alias="rows",
            scope_expression="control.external_claim_scope",
            execution_scope_expression="rows.execution_scope",
            canary_authorized_expression="rows.canary_authorized",
        )
        external_canary_authorized = external_canary_authorization_predicate(row_alias="job")
        eligible = queue_policy_eligible_predicate()
        return f"""
            WITH queue_rows AS (
                SELECT 'external_effect'::TEXT AS queue_kind,
                       COALESCE(job.payload_json->>'execution_scope', '') AS execution_scope,
                       ({external_canary_authorized}) AS canary_authorized,
                       job.lane, job.status, job.hold_reason, job.available_at,
                       job.lease_expires_at, job.attempt_count, job.max_attempts,
                       job.worker_generation, job.policy_version,
                       job.status IN ('queued', 'failed_retryable') AS ready_state,
                       CASE WHEN status = 'dispatching' THEN TRUE ELSE FALSE END AS in_flight,
                       CASE WHEN status = 'unknown_after_dispatch' THEN TRUE ELSE FALSE END AS unknown_state,
                       CASE WHEN status IN ('failed_terminal', 'blocked') THEN TRUE ELSE FALSE END AS dlq_state,
                       EXISTS (
                           SELECT 1 FROM queue_rate_scope_cooldown cooldown
                           WHERE cooldown.rate_scope_key = job.rate_scope_key
                             AND cooldown.blocked_until > CURRENT_TIMESTAMP
                       ) AS rate_limited,
                       EXISTS (
                           SELECT 1
                           FROM external_effect_job active
                           WHERE active.id <> job.id
                             AND active.lane = job.lane
                             AND active.ordering_key = job.ordering_key
                             AND active.ordering_key <> ''
                             AND active.status = 'dispatching'
                             AND active.lease_expires_at > CURRENT_TIMESTAMP
                       ) AS ordering_blocked
                FROM external_effect_job job
                WHERE job.status IN (
                    'planned', 'approved', 'queued', 'dispatching',
                    'failed_retryable', 'unknown_after_dispatch',
                    'failed_terminal', 'blocked'
                )
                UNION ALL
                SELECT 'internal_event', '', TRUE, lane, status, hold_reason, available_at, lease_expires_at,
                       attempt_count, max_attempts, worker_generation, policy_version,
                       status IN ('pending', 'failed_retryable'),
                       status = 'running', FALSE,
                       status IN ('failed_terminal', 'blocked'), FALSE,
                       EXISTS (
                           SELECT 1
                           FROM internal_event_consumer_run active
                           WHERE active.id <> run.id
                             AND active.lane = run.lane
                             AND active.ordering_key = run.ordering_key
                             AND active.ordering_key <> ''
                             AND active.status = 'running'
                             AND active.lease_expires_at > CURRENT_TIMESTAMP
                       )
                FROM internal_event_consumer_run run
                WHERE run.status IN (
                    'pending', 'running', 'failed_retryable',
                    'failed_terminal', 'blocked'
                )
                UNION ALL
                SELECT 'internal_outbox', '', TRUE, lane, status, hold_reason, available_at, lease_expires_at,
                       attempt_count, max_attempts, worker_generation, policy_version,
                       status IN ('pending', 'failed_retryable'),
                       status = 'running', FALSE,
                       status = 'failed_terminal', FALSE,
                       EXISTS (
                           SELECT 1
                           FROM internal_event_outbox active
                           WHERE active.id <> outbox.id
                             AND active.lane = outbox.lane
                             AND active.ordering_key = outbox.ordering_key
                             AND active.ordering_key <> ''
                             AND active.status = 'running'
                             AND active.lease_expires_at > CURRENT_TIMESTAMP
                       )
                FROM internal_event_outbox outbox
                WHERE outbox.status IN (
                    'pending', 'running', 'failed_retryable', 'failed_terminal'
                )
                UNION ALL
                SELECT 'webhook_inbox', '', TRUE, lane, status, hold_reason, available_at, lease_expires_at,
                       attempt_count, max_attempts, worker_generation, policy_version,
                       status IN ('received', 'failed_retryable'),
                       status = 'processing', FALSE,
                       status IN ('failed_terminal', 'dead_letter'), FALSE,
                       EXISTS (
                           SELECT 1
                           FROM webhook_inbox active
                           WHERE active.id <> inbox.id
                             AND active.lane = inbox.lane
                             AND active.ordering_key = inbox.ordering_key
                             AND active.ordering_key <> ''
                             AND active.status = 'processing'
                             AND active.lease_expires_at > CURRENT_TIMESTAMP
                       )
                FROM webhook_inbox inbox
                WHERE inbox.status IN (
                    'received', 'processing', 'failed_retryable',
                    'failed_terminal', 'dead_letter'
                )
            )
            SELECT policy.lane, policy.max_in_flight, policy.enabled,
                   policy.rollout_mode, policy.blocked_until, policy.policy_version,
                   COUNT(rows.*)::BIGINT AS raw_open,
                   COUNT(rows.*) FILTER (WHERE rows.hold_reason <> '')::BIGINT AS held,
                   COUNT(rows.*) FILTER (
                       WHERE {eligible}
                   )::BIGINT AS eligible,
                   COUNT(rows.*) FILTER (
                       WHERE rows.queue_kind = 'external_effect'
                         AND {base_eligible}
                         AND NOT ({external_scope})
                   )::BIGINT AS policy_gated,
                   COUNT(rows.*) FILTER (
                       WHERE rows.hold_reason = ''
                         AND rows.ready_state
                         AND rows.available_at > CURRENT_TIMESTAMP
                         AND rows.status NOT IN ('failed_retryable')
                   )::BIGINT AS scheduled,
                   COUNT(rows.*) FILTER (
                       WHERE rows.hold_reason = ''
                         AND rows.ready_state
                         AND rows.available_at > CURRENT_TIMESTAMP
                         AND rows.status = 'failed_retryable'
                   )::BIGINT AS retry_wait,
                   COUNT(rows.*) FILTER (
                       WHERE rows.rate_limited
                         AND rows.ready_state
                         AND rows.hold_reason = ''
                         AND rows.attempt_count < rows.max_attempts
                   )::BIGINT AS rate_limited,
                   COUNT(rows.*) FILTER (
                       WHERE rows.in_flight
                         AND rows.lease_expires_at > CURRENT_TIMESTAMP
                   )::BIGINT AS in_flight,
                   COUNT(rows.*) FILTER (WHERE rows.unknown_state)::BIGINT AS unknown,
                   COUNT(rows.*) FILTER (WHERE rows.dlq_state)::BIGINT AS dlq,
                   COALESCE(
                       EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(rows.available_at) FILTER (
                           WHERE {eligible}
                       )),
                       0
                   )::BIGINT AS oldest_eligible_age_seconds
            FROM queue_lane_policy policy
            CROSS JOIN queue_runtime_control control
            LEFT JOIN queue_rows rows ON rows.lane = policy.lane
            GROUP BY policy.lane, policy.max_in_flight, policy.enabled,
                     policy.rollout_mode, policy.blocked_until, policy.policy_version,
                     control.active_generation, control.claim_enabled,
                     control.rollout_mode, control.policy_version,
                     control.external_claim_scope
            ORDER BY policy.lane
        """

    @staticmethod
    def _timeline_links_sql() -> str:
        return """
            SELECT DISTINCT execution_id, parent_execution_id
            FROM (
                (
                SELECT event.execution_id, event.parent_execution_id
                FROM internal_event event
                WHERE event.execution_id <> ''
                  AND event.execution_id = ANY(%s::text[])
                ORDER BY event.execution_id, event.parent_execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT event.execution_id, event.parent_execution_id
                FROM internal_event event
                WHERE event.parent_execution_id <> ''
                  AND event.parent_execution_id = ANY(%s::text[])
                ORDER BY event.parent_execution_id, event.execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT outbox.execution_id, outbox.parent_execution_id
                FROM internal_event_outbox outbox
                WHERE outbox.execution_id <> ''
                  AND outbox.execution_id = ANY(%s::text[])
                ORDER BY outbox.execution_id, outbox.parent_execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT outbox.execution_id, outbox.parent_execution_id
                FROM internal_event_outbox outbox
                WHERE outbox.parent_execution_id <> ''
                  AND outbox.parent_execution_id = ANY(%s::text[])
                ORDER BY outbox.parent_execution_id, outbox.execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT run.execution_id, run.parent_execution_id
                FROM internal_event_consumer_run run
                WHERE run.execution_id <> ''
                  AND run.execution_id = ANY(%s::text[])
                ORDER BY run.execution_id, run.parent_execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT run.execution_id, run.parent_execution_id
                FROM internal_event_consumer_run run
                WHERE run.parent_execution_id <> ''
                  AND run.parent_execution_id = ANY(%s::text[])
                ORDER BY run.parent_execution_id, run.execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT job.execution_id, job.parent_execution_id
                FROM external_effect_job job
                WHERE job.execution_id <> ''
                  AND job.execution_id = ANY(%s::text[])
                ORDER BY job.execution_id, job.parent_execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT job.execution_id, job.parent_execution_id
                FROM external_effect_job job
                WHERE job.parent_execution_id <> ''
                  AND job.parent_execution_id = ANY(%s::text[])
                ORDER BY job.parent_execution_id, job.execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT inbox.execution_id, inbox.parent_execution_id
                FROM webhook_inbox inbox
                WHERE inbox.execution_id <> ''
                  AND inbox.execution_id = ANY(%s::text[])
                ORDER BY inbox.execution_id, inbox.parent_execution_id
                LIMIT %s
                )
                UNION ALL
                (
                SELECT inbox.execution_id, inbox.parent_execution_id
                FROM webhook_inbox inbox
                WHERE inbox.parent_execution_id <> ''
                  AND inbox.parent_execution_id = ANY(%s::text[])
                ORDER BY inbox.parent_execution_id, inbox.execution_id
                LIMIT %s
                )
            ) links
            WHERE execution_id <> '' OR parent_execution_id <> ''
            ORDER BY execution_id ASC, parent_execution_id ASC
            LIMIT %s
        """

    @staticmethod
    def _timeline_sql() -> str:
        return """
            SELECT 'internal_event' AS item_kind, event.id::TEXT AS item_id,
                   event.execution_id, event.parent_execution_id,
                   event.event_type AS item_type, 'recorded' AS status,
                   '' AS lane, event.occurred_at AS available_at,
                   event.created_at, event.created_at AS updated_at,
                   event.payload_summary_json AS summary_json
            FROM internal_event event
            WHERE event.execution_id <> ''
              AND event.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'internal_outbox', outbox.id::TEXT, outbox.execution_id,
                   outbox.parent_execution_id, outbox.event_type, outbox.status,
                   outbox.lane, outbox.available_at, outbox.created_at, outbox.updated_at,
                   outbox.payload_summary_json
            FROM internal_event_outbox outbox
            WHERE outbox.execution_id <> ''
              AND outbox.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'internal_consumer_run', run.id::TEXT, run.execution_id,
                   run.parent_execution_id, run.consumer_name, run.status,
                   run.lane, run.available_at, run.created_at, run.updated_at,
                   run.result_summary_json
            FROM internal_event_consumer_run run
            WHERE run.execution_id <> ''
              AND run.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'internal_consumer_attempt', attempt.attempt_id, run.execution_id,
                   run.parent_execution_id, attempt.consumer_name, attempt.status,
                   run.lane, attempt.started_at, attempt.started_at,
                   COALESCE(attempt.completed_at, attempt.started_at),
                   attempt.request_summary_json || attempt.response_summary_json
            FROM internal_event_consumer_attempt attempt
            JOIN internal_event_consumer_run run ON run.id = attempt.consumer_run_id
            WHERE run.execution_id <> ''
              AND run.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'external_effect', job.id::TEXT, job.execution_id,
                   job.parent_execution_id, job.effect_type, job.status,
                   job.lane, job.available_at, job.created_at, job.updated_at,
                   job.payload_summary_json || job.result_summary_json
            FROM external_effect_job job
            WHERE job.execution_id <> ''
              AND job.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'external_effect_attempt', attempt.attempt_id, job.execution_id,
                   job.parent_execution_id, attempt.operation, attempt.status,
                   job.lane, attempt.started_at, attempt.started_at,
                   COALESCE(attempt.completed_at, attempt.started_at),
                   attempt.request_summary_json || attempt.response_summary_json
            FROM external_effect_attempt attempt
            JOIN external_effect_job job ON job.id = attempt.job_id
            WHERE job.execution_id <> ''
              AND job.execution_id = ANY(%s::text[])
            UNION ALL
            SELECT 'webhook_inbox', inbox.id::TEXT, inbox.execution_id,
                   inbox.parent_execution_id, inbox.event_family, inbox.status,
                   inbox.lane, inbox.available_at, inbox.created_at, inbox.updated_at,
                   inbox.payload_summary_json || inbox.processing_summary_json
            FROM webhook_inbox inbox
            WHERE inbox.execution_id <> ''
              AND inbox.execution_id = ANY(%s::text[])
            ORDER BY created_at ASC, item_kind ASC, item_id ASC
            LIMIT %s
        """

    @staticmethod
    def _control_payload(row: Any) -> dict[str, Any]:
        return {
            "active_generation": int(row.get("active_generation") or 0),
            "claim_enabled": bool(row.get("claim_enabled")),
            "rollout_mode": str(row.get("rollout_mode") or "blocked"),
            "global_max_in_flight": int(row.get("global_max_in_flight") or 0),
            "policy_version": str(row.get("policy_version") or ""),
            "external_claim_scope": str(row.get("external_claim_scope") or "blocked"),
            "updated_by": str(row.get("updated_by") or ""),
            "updated_reason": str(row.get("updated_reason") or ""),
            "updated_at": _public_datetime(row.get("updated_at")),
        }

    @staticmethod
    def _lane_payload(row: Any) -> dict[str, Any]:
        return {
            "lane": str(row.get("lane") or ""),
            "max_in_flight": int(row.get("max_in_flight") or 0),
            "enabled": bool(row.get("enabled")),
            "rollout_mode": str(row.get("rollout_mode") or "blocked"),
            "blocked_until": _public_datetime(row.get("blocked_until")),
            "policy_version": str(row.get("policy_version") or ""),
            "raw_open": int(row.get("raw_open") or 0),
            "held": int(row.get("held") or 0),
            "eligible": int(row.get("eligible") or 0),
            "policy_gated": int(row.get("policy_gated") or 0),
            "scheduled": int(row.get("scheduled") or 0),
            "retry_wait": int(row.get("retry_wait") or 0),
            "rate_limited": int(row.get("rate_limited") or 0),
            "in_flight": int(row.get("in_flight") or 0),
            "unknown": int(row.get("unknown") or 0),
            "dlq": int(row.get("dlq") or 0),
            "oldest_eligible_age_seconds": int(row.get("oldest_eligible_age_seconds") or 0),
        }

    @staticmethod
    def _worker_payload(row: Any) -> dict[str, Any]:
        return {
            "service_name": str(row.get("service_name") or ""),
            "worker_id": str(row.get("worker_id") or ""),
            "queue_kind": str(row.get("queue_kind") or ""),
            "generation": int(row.get("generation") or 0),
            "release_sha": str(row.get("release_sha") or ""),
            "rollout_mode": str(row.get("rollout_mode") or ""),
            "listener_connected": bool(row.get("listener_connected")),
            "last_notification_at": _public_datetime(row.get("last_notification_at")),
            "last_drain_at": _public_datetime(row.get("last_drain_at")),
            "heartbeat_at": _public_datetime(row.get("heartbeat_at")),
            "fresh": bool(row.get("fresh")),
            "release_matches": bool(row.get("release_matches")),
        }

    @staticmethod
    def _cooldown_payload(row: Any) -> dict[str, Any]:
        return {
            "rate_scope_hash": _public_hash(row.get("rate_scope_key")),
            "provider": str(row.get("provider") or ""),
            "corp_id_present": bool(row.get("corp_id")),
            "app_id_present": bool(row.get("app_id")),
            "operation": str(row.get("operation") or ""),
            "blocked_until": _public_datetime(row.get("blocked_until")),
            "reason": str(row.get("reason") or ""),
            "source_attempt_id": str(row.get("source_attempt_id") or ""),
        }

    @staticmethod
    def _policy_payload(row: Any) -> dict[str, Any]:
        return {
            "policy_version": str(row.get("policy_version") or ""),
            "policy": dict(row.get("policy_json") or {}),
            "created_by": str(row.get("created_by") or ""),
            "created_reason": str(row.get("created_reason") or ""),
            "created_at": _public_datetime(row.get("created_at")),
        }

    @staticmethod
    def _timeline_payload(row: Any) -> dict[str, Any]:
        return {
            "item_kind": str(row.get("item_kind") or ""),
            "item_id": str(row.get("item_id") or ""),
            "execution_id": str(row.get("execution_id") or ""),
            "parent_execution_id": str(row.get("parent_execution_id") or ""),
            "item_type": str(row.get("item_type") or ""),
            "status": str(row.get("status") or ""),
            "lane": str(row.get("lane") or ""),
            "available_at": _public_datetime(row.get("available_at")),
            "created_at": _public_datetime(row.get("created_at")),
            "updated_at": _public_datetime(row.get("updated_at")),
            "summary": redact_sensitive_data(dict(row.get("summary_json") or {})),
        }


__all__ = [
    "ExecutionRuntimeReadModel",
    "TIMELINE_MAX_DEPTH",
    "TIMELINE_MAX_EXECUTION_NODES",
    "TIMELINE_MAX_ITEMS",
    "queue_policy_base_eligible_predicate",
    "queue_policy_eligible_predicate",
    "queue_policy_external_scope_predicate",
    "release_provenance",
]
