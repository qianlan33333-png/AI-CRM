from __future__ import annotations

from typing import Any, Callable

from psycopg.types.json import Jsonb

from . import application
from .repo import _connect, json_safe, text


SyncFunc = Callable[[dict[str, Any], str, int | None], dict[str, Any]]
DEFAULT_CLAIM_LEASE_SECONDS = 600


class IdentityResolutionBackfillWorker:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] | None = None,
        sync_func: SyncFunc | None = None,
        locked_by: str = "identity-resolution-worker",
        claim_lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
    ) -> None:
        self.connection_factory = connection_factory or _connect
        self.sync_func = sync_func
        self.locked_by = text(locked_by) or "identity-resolution-worker"
        self.claim_lease_seconds = max(30, min(int(claim_lease_seconds or DEFAULT_CLAIM_LEASE_SECONDS), 3600))

    def run_due(self, *, limit: int = 100, max_attempts: int = 5, dry_run: bool = True) -> dict[str, Any]:
        conn = self.connection_factory()
        bounded_limit = max(1, min(int(limit or 100), 500))
        runtime_reserve = 0
        processed = 0
        resolved = 0
        retryable = 0
        terminal = 0
        runtime_processed = 0
        details: list[dict[str, Any]] = []
        try:
            runtime_reserve = _runtime_reserve(
                conn,
                limit=bounded_limit,
                max_attempts=max_attempts,
            )
            queue_limit = max(0, bounded_limit - runtime_reserve)
            queue_rows = (
                _claim_queue_rows(
                    conn,
                    limit=queue_limit,
                    locked_by=self.locked_by,
                    lease_seconds=self.claim_lease_seconds,
                )
                if queue_limit > 0
                else []
            )
            if dry_run:
                runtime_rows = _claim_runtime_rows(
                    conn,
                    limit=max(0, bounded_limit - len(queue_rows)),
                    max_attempts=max_attempts,
                    locked_by=self.locked_by,
                    lease_seconds=self.claim_lease_seconds,
                )
                conn.rollback()
                return {
                    "ok": True,
                    "dry_run": True,
                    "runtime_reserved_count": runtime_reserve,
                    "claimed_count": len(queue_rows),
                    "runtime_claimed_count": len(runtime_rows),
                    "resolved_count": 0,
                    "retryable_count": 0,
                    "terminal_count": 0,
                    "details": [
                        {"source": "queue", "id": row.get("id"), "external_userid": text(row.get("external_userid"))}
                        for row in queue_rows
                    ]
                    + [
                        {"source": "runtime", "id": row.get("id"), "external_userid": text(row.get("external_userid"))}
                        for row in runtime_rows
                    ],
                    "source_status": "identity_resolution_backfill_worker",
                }
            # The claim transaction must end before any adapter call. Identity
            # synchronization can enqueue the same pending source through a
            # separate connection; holding the claim row lock here would create
            # a deterministic self-deadlock.
            conn.commit()
            for row in queue_rows:
                processed += 1
                event = _event_from_queue_row(row)
                result = self._sync_event(
                    event,
                    text(row.get("corp_id")),
                    None,
                    persist_runtime_identity=True,
                )
                status = text(result.get("status"))
                if status == "success":
                    _mark_queue_resolved(conn, row, result)
                    resolved += 1
                else:
                    attempts = int(row.get("attempts") or row.get("attempt_count") or 0)
                    if attempts >= max(1, int(max_attempts)):
                        _mark_queue_failed(conn, row, result)
                        terminal += 1
                    else:
                        _mark_queue_retryable(conn, row, result)
                        retryable += 1
                details.append({"source": "queue", "id": row.get("id"), "status": status, "reason": text(result.get("reason"))})
                conn.commit()

            # Queue synchronization may have resolved matching runtime rows.
            # Claim runtime work only afterwards so those rows are not called a
            # second time from a stale pre-queue snapshot.
            runtime_rows = _claim_runtime_rows(
                conn,
                limit=max(0, bounded_limit - len(queue_rows)),
                max_attempts=max_attempts,
                locked_by=self.locked_by,
                lease_seconds=self.claim_lease_seconds,
            )
            conn.commit()
            for row in runtime_rows:
                runtime_processed += 1
                event = _event_from_runtime_row(row)
                result = self._sync_event(
                    event,
                    text(row.get("corp_id")),
                    int(row.get("event_log_id") or 0) or None,
                    persist_runtime_identity=False,
                )
                status = text(result.get("status")) or "pending"
                attempts = int(row.get("identity_attempt_count") or 0)
                terminal_after_attempt = status != "success" and attempts + 1 >= max(1, int(max_attempts))
                _mark_runtime_identity(conn, row, result, terminal=terminal_after_attempt, max_attempts=max_attempts)
                if status == "success":
                    resolved += 1
                elif terminal_after_attempt:
                    terminal += 1
                else:
                    retryable += 1
                details.append({"source": "runtime", "id": row.get("id"), "status": status, "reason": text(result.get("reason"))})
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            # A terminal row is a successfully classified business outcome,
            # not a worker-process failure. Keeping the process green lets the
            # timer continue draining the remaining queue while the count-only
            # result still exposes terminal outcomes to operators.
            "ok": True,
            "dry_run": False,
            "runtime_reserved_count": runtime_reserve,
            "processed_count": processed,
            "runtime_processed_count": runtime_processed,
            "resolved_count": resolved,
            "retryable_count": retryable,
            "terminal_count": terminal,
            "terminal_results_present": terminal > 0,
            "details": details,
            "source_status": "identity_resolution_backfill_worker",
        }

    def _sync_event(
        self,
        event: dict[str, Any],
        corp_id: str,
        event_log_id: int | None,
        *,
        persist_runtime_identity: bool,
    ) -> dict[str, Any]:
        if self.sync_func is not None:
            return self.sync_func(event, corp_id, event_log_id)
        return application._sync_identity_best_effort(
            event,
            corp_id=corp_id,
            event_log_id=event_log_id,
            persist_runtime_identity=persist_runtime_identity,
        )


def _runtime_reserve(conn: Any, *, limit: int, max_attempts: int) -> int:
    """Reserve bounded capacity when runtime identity work is already due.

    Queue rows are intentionally synchronized first so they can resolve matching
    runtime rows before those rows are claimed. Without a small reservation, a
    perpetually due queue backlog can consume the whole batch and starve runtime
    rows forever.
    """

    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM automation_channel_entry_runtime
            WHERE identity_status IN ('pending', 'pending_identity', 'failed')
              AND identity_external_effect_job_id IS NULL
              AND COALESCE(identity_attempt_count, 0) < %s
              AND (identity_next_attempt_at IS NULL OR identity_next_attempt_at <= CURRENT_TIMESTAMP)
        ) AS runtime_due
        """,
        (max(1, int(max_attempts or 5)),),
    ).fetchone()
    runtime_due = bool((row or {}).get("runtime_due"))
    if not runtime_due:
        return 0
    bounded_limit = max(1, min(int(limit or 1), 500))
    return min(bounded_limit, 5, max(1, bounded_limit // 4))


def _claim_queue_rows(conn: Any, *, limit: int, locked_by: str, lease_seconds: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH due AS (
            SELECT id
            FROM crm_user_identity_resolution_queue
            WHERE status = 'pending'
              AND external_effect_job_id IS NULL
              AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
            ORDER BY COALESCE(next_attempt_at, first_seen_at, created_at) ASC, id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE crm_user_identity_resolution_queue q
        SET attempts = COALESCE(attempts, 0) + 1,
            attempt_count = COALESCE(attempt_count, 0) + 1,
            last_seen_at = CURRENT_TIMESTAMP,
            next_attempt_at = CURRENT_TIMESTAMP + make_interval(secs => %s),
            updated_at = CURRENT_TIMESTAMP,
            payload_json = payload_json || %s
        FROM due
        WHERE q.id = due.id
        RETURNING q.*
        """,
        (
            max(1, min(int(limit or 100), 500)),
            max(30, min(int(lease_seconds or DEFAULT_CLAIM_LEASE_SECONDS), 3600)),
            Jsonb({"identity_backfill_claim": {"locked_by": locked_by, "lease_seconds": lease_seconds}}),
        ),
    ).fetchall()
    return [dict(row) for row in rows or []]


def _claim_runtime_rows(
    conn: Any,
    *,
    limit: int,
    max_attempts: int,
    locked_by: str,
    lease_seconds: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        WITH due AS (
            SELECT id
            FROM automation_channel_entry_runtime
            WHERE identity_status IN ('pending', 'pending_identity', 'failed')
              AND identity_external_effect_job_id IS NULL
              AND COALESCE(identity_attempt_count, 0) < %s
              AND (identity_next_attempt_at IS NULL OR identity_next_attempt_at <= CURRENT_TIMESTAMP)
            ORDER BY COALESCE(identity_next_attempt_at, updated_at) ASC, id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE automation_channel_entry_runtime runtime
        SET identity_next_attempt_at = CURRENT_TIMESTAMP + make_interval(secs => %s),
            payload_json = payload_json || %s,
            updated_at = CURRENT_TIMESTAMP
        FROM due
        WHERE runtime.id = due.id
        RETURNING runtime.*
        """,
        (
            max(1, int(max_attempts or 5)),
            max(1, min(int(limit or 100), 500)),
            max(30, min(int(lease_seconds or DEFAULT_CLAIM_LEASE_SECONDS), 3600)),
            Jsonb({"identity_backfill_claim": {"locked_by": locked_by, "lease_seconds": lease_seconds}}),
        ),
    ).fetchall()
    return [dict(row) for row in rows or []]


def _event_from_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload_json") or {})
    if text(payload.get("Event")):
        return payload
    source_key_parts = text(row.get("source_key")).split(":")
    follow_user_userid = text(payload.get("follow_user_userid") or payload.get("UserID"))
    if not follow_user_userid and len(source_key_parts) >= 3:
        follow_user_userid = source_key_parts[2]
    return {
        **payload,
        "Event": "change_external_contact",
        "ChangeType": text(payload.get("ChangeType")) or "edit_external_contact",
        "ExternalUserID": text(row.get("external_userid")) or text(payload.get("ExternalUserID")),
        "UserID": follow_user_userid,
    }


def _event_from_runtime_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload_json") or {})
    return {
        **payload,
        "Event": text(payload.get("Event")) or "change_external_contact",
        "ChangeType": text(payload.get("ChangeType")) or "edit_external_contact",
        "ExternalUserID": text(row.get("external_userid")) or text(payload.get("ExternalUserID")),
        "UserID": text(row.get("follow_user_userid")) or text(payload.get("UserID")),
    }


def _mark_queue_resolved(conn: Any, row: dict[str, Any], result: dict[str, Any]) -> None:
    unionid = text(result.get("unionid"))
    conn.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'resolved',
            resolved_unionid = %s,
            resolved_at = CURRENT_TIMESTAMP,
            last_error = '',
            payload_json = payload_json || %s,
            next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (unionid, Jsonb({"identity_backfill_result": json_safe(result)}), int(row.get("id") or 0)),
    )


def _mark_queue_retryable(conn: Any, row: dict[str, Any], result: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'pending',
            last_error = %s,
            payload_json = payload_json || %s,
            next_attempt_at = CURRENT_TIMESTAMP + (LEAST(GREATEST(COALESCE(attempts, 1), 1), 30) || ' minutes')::interval,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (_result_error(result), Jsonb({"identity_backfill_result": json_safe(result)}), int(row.get("id") or 0)),
    )


def _mark_queue_failed(conn: Any, row: dict[str, Any], result: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'failed',
            last_error = %s,
            payload_json = payload_json || %s,
            next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (_result_error(result), Jsonb({"identity_backfill_result": json_safe(result)}), int(row.get("id") or 0)),
    )


def _mark_runtime_identity(
    conn: Any,
    row: dict[str, Any],
    result: dict[str, Any],
    *,
    terminal: bool = False,
    max_attempts: int = 5,
) -> None:
    status = text(result.get("status")) or "pending"
    stored_status = "failed_terminal" if terminal else status
    conn.execute(
        """
        UPDATE automation_channel_entry_runtime
        SET unionid = CASE WHEN %s <> '' THEN %s ELSE unionid END,
            identity_status = %s,
            identity_attempt_count = CASE
                WHEN %s = 'success' THEN 0
                ELSE COALESCE(identity_attempt_count, 0) + 1
            END,
            identity_next_attempt_at = CASE
                WHEN %s = 'success' THEN NULL
                WHEN COALESCE(identity_attempt_count, 0) + 1 >= %s THEN NULL
                ELSE CURRENT_TIMESTAMP + (LEAST(GREATEST(COALESCE(identity_attempt_count, 0) + 1, 1), 30) || ' minutes')::interval
            END,
            identity_last_error = CASE WHEN %s = 'success' THEN '' ELSE %s END,
            payload_json = payload_json || %s,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            text(result.get("unionid")),
            text(result.get("unionid")),
            stored_status,
            status,
            status,
            max(1, int(max_attempts or 5)),
            status,
            _result_error(result),
            Jsonb({"identity_backfill_result": json_safe(result)}),
            int(row.get("id") or 0),
        ),
    )


def _result_error(result: dict[str, Any]) -> str:
    return (text(result.get("reason")) or text(result.get("message")) or text(result.get("status")) or "identity_resolution_failed")[:500]
