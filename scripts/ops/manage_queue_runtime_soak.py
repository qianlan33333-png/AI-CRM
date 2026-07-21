#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.execution_runtime.cutover import (  # noqa: E402
    RuntimeGenerationRepository,
)
from aicrm_next.platform_foundation.execution_runtime.repository import (  # noqa: E402
    normalize_runtime_database_url,
    open_runtime_connection,
)
from aicrm_next.platform_foundation.execution_runtime.validation import (  # noqa: E402
    REQUIRED_VALIDATION_EVIDENCE,
    collect_soak_metrics,
    configuration_hash,
    evaluate_soak_snapshot,
)
from aicrm_next.platform_foundation.repository import RuntimeReadinessRepository  # noqa: E402
from aicrm_next.shared.release import current_release_sha  # noqa: E402
from aicrm_next.shared.runtime import raw_database_url  # noqa: E402


AUTHORIZATION_ENV = "AICRM_QUEUE_SOAK_AUTHORIZED"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start, sample, and complete the durable 72-hour queue-runtime soak.",
    )
    parser.add_argument(
        "--action",
        choices=("start", "snapshot", "status", "complete", "invalidate"),
        required=True,
    )
    parser.add_argument("--expected-release-sha", default="")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--expected-policy-version", default="")
    parser.add_argument("--duration-hours", type=int, default=72)
    parser.add_argument("--actor", default="queue-soak-system")
    parser.add_argument("--reason", default="periodic queue soak evidence")
    parser.add_argument("--confirmation", default="")
    return parser.parse_args(argv)


def _authorized(expected_confirmation: str, actual_confirmation: str) -> None:
    if str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")
    if str(actual_confirmation or "").strip() != expected_confirmation:
        raise RuntimeError(f"--confirmation must equal {expected_confirmation}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _migration_revision(database_url: str) -> str:
    with RuntimeReadinessRepository(database_url) as readiness:
        revisions = readiness.migration_revisions()
    if len(revisions) != 1:
        raise RuntimeError("soak requires exactly one Alembic head")
    return revisions[0]


def _running_soak(connection: Any, *, for_update: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = connection.execute(
        """
        SELECT *
        FROM queue_runtime_soak_run
        WHERE status = 'running'
        ORDER BY started_at DESC
        LIMIT 1
        """
        + suffix
    ).fetchone()
    return dict(row) if row else None


def _evidence_types(
    connection: Any,
    *,
    release_sha: str,
    generation: int,
    policy_version: str,
) -> set[str]:
    rows = connection.execute(
        """
        SELECT evidence_type
        FROM (
            SELECT DISTINCT ON (evidence_type) evidence_type, status
            FROM queue_runtime_validation_evidence
            WHERE release_sha = %s
              AND active_generation = %s
              AND policy_version = %s
            ORDER BY evidence_type, created_at DESC, evidence_id DESC
        ) latest
        WHERE status = 'passed'
        """,
        (release_sha, generation, policy_version),
    ).fetchall()
    return {str(row.get("evidence_type") or "") for row in rows}


def _snapshot_count(connection: Any, soak_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*)::BIGINT AS count FROM queue_runtime_soak_snapshot WHERE soak_id = %s",
        (soak_id,),
    ).fetchone()
    return int((row or {}).get("count") or 0)


def _public_soak(row: dict[str, Any], *, snapshot_count: int) -> dict[str, Any]:
    return {
        "soak_id": str(row.get("soak_id") or ""),
        "release_sha": str(row.get("release_sha") or ""),
        "migration_revision": str(row.get("migration_revision") or ""),
        "active_generation": int(row.get("active_generation") or 0),
        "policy_version": str(row.get("policy_version") or ""),
        "external_claim_scope": str(row.get("external_claim_scope") or ""),
        "configuration_hash": str(row.get("configuration_hash") or ""),
        "status": str(row.get("status") or ""),
        "started_at": str(row.get("started_at") or ""),
        "required_until": str(row.get("required_until") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "invalidated_at": str(row.get("invalidated_at") or ""),
        "invalidated_reason": str(row.get("invalidated_reason") or ""),
        "snapshot_count": int(snapshot_count),
    }


def _capture(database_url: str, row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    started_at = row.get("started_at")
    if not isinstance(started_at, datetime):
        raise RuntimeError("soak start timestamp is invalid")
    metrics = collect_soak_metrics(database_url, started_at=started_at)
    release_sha = current_release_sha()
    current_config_hash = configuration_hash()
    current_migration = _migration_revision(database_url)
    baseline = dict(row.get("baseline_json") or {})
    violations = evaluate_soak_snapshot(
        metrics,
        baseline,
        release_matches=release_sha == str(row.get("release_sha") or ""),
        configuration_matches=current_config_hash == str(row.get("configuration_hash") or ""),
        migration_matches=current_migration == str(row.get("migration_revision") or ""),
    )
    return metrics, violations


def _persist_snapshot(
    database_url: str,
    *,
    row: dict[str, Any],
    metrics: dict[str, Any],
    violations: list[str],
) -> dict[str, Any]:
    context_violations = {
        "release_sha_changed",
        "canary_configuration_changed",
        "migration_revision_changed",
    }.intersection(violations)
    target_status = "invalidated" if context_violations else ("failed" if violations else "running")
    with open_runtime_connection(database_url) as connection:
        with connection.transaction():
            current = _running_soak(connection, for_update=True)
            if not current or str(current.get("soak_id") or "") != str(row.get("soak_id") or ""):
                raise RuntimeError("running soak changed before snapshot commit")
            connection.execute(
                """
                INSERT INTO queue_runtime_soak_snapshot (
                    snapshot_id, soak_id, release_sha, configuration_hash,
                    ok, metrics_json, violations_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    "qrss_" + uuid4().hex,
                    str(row["soak_id"]),
                    current_release_sha(),
                    configuration_hash(),
                    not violations,
                    _json(metrics),
                    _json(violations),
                ),
            )
            updated = connection.execute(
                """
                UPDATE queue_runtime_soak_run
                SET status = %s,
                    latest_snapshot_json = %s::jsonb,
                    invalidated_at = CASE WHEN %s = 'invalidated' THEN CURRENT_TIMESTAMP ELSE invalidated_at END,
                    invalidated_reason = CASE WHEN %s <> 'running' THEN %s ELSE invalidated_reason END,
                    completed_at = CASE WHEN %s = 'failed' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE soak_id = %s
                  AND status = 'running'
                RETURNING *
                """,
                (
                    target_status,
                    _json(metrics),
                    target_status,
                    target_status,
                    ",".join(violations),
                    target_status,
                    str(row["soak_id"]),
                ),
            ).fetchone()
            if not updated:
                raise RuntimeError("soak snapshot CAS lost")
            count = _snapshot_count(connection, str(row["soak_id"]))
    return _public_soak(dict(updated), snapshot_count=count)


def _start(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    release_sha = str(args.expected_release_sha or "").strip()
    if FULL_SHA.fullmatch(release_sha) is None or release_sha != current_release_sha():
        raise RuntimeError("expected release SHA must exactly match the active release")
    generation = int(args.generation or 0)
    policy_version = str(args.expected_policy_version or "").strip()
    duration_hours = int(args.duration_hours or 72)
    if generation <= 0 or not policy_version or duration_hours != 72:
        raise ValueError("start requires a positive generation, policy version, and exactly 72 hours")
    _authorized(f"START_QUEUE_SOAK_{release_sha}_{generation}", args.confirmation)
    state = RuntimeGenerationRepository(database_url).read_state()
    if (
        state.active_generation != generation
        or not state.claim_enabled
        or state.policy_version != policy_version
        or state.external_claim_scope != "allowlisted"
    ):
        raise RuntimeError("soak requires the exact active allowlisted generation")
    migration_revision = _migration_revision(database_url)
    metrics = collect_soak_metrics(database_url, started_at=datetime.now(timezone.utc))
    baseline_violations = evaluate_soak_snapshot(
        metrics,
        metrics,
        release_matches=True,
        configuration_matches=True,
        migration_matches=True,
    )
    if baseline_violations:
        raise RuntimeError(
            "soak baseline is unhealthy: " + ",".join(baseline_violations)
        )
    config_hash = configuration_hash()
    soak_id = "qrsoak_" + uuid4().hex
    with open_runtime_connection(database_url) as connection:
        with connection.transaction():
            if _running_soak(connection, for_update=True):
                raise RuntimeError("a queue runtime soak is already running")
            evidence = _evidence_types(
                connection,
                release_sha=release_sha,
                generation=generation,
                policy_version=policy_version,
            )
            missing = sorted(REQUIRED_VALIDATION_EVIDENCE - evidence)
            if missing:
                raise RuntimeError("required validation evidence is missing: " + ",".join(missing))
            row = connection.execute(
                """
                INSERT INTO queue_runtime_soak_run (
                    soak_id, release_sha, migration_revision, active_generation,
                    policy_version, external_claim_scope, configuration_hash,
                    status, required_until, baseline_json, latest_snapshot_json,
                    actor, reason
                ) VALUES (
                    %s, %s, %s, %s, %s, 'allowlisted', %s,
                    'running', CURRENT_TIMESTAMP + INTERVAL '72 hours',
                    %s::jsonb, %s::jsonb, %s, %s
                )
                RETURNING *
                """,
                (
                    soak_id,
                    release_sha,
                    migration_revision,
                    generation,
                    policy_version,
                    config_hash,
                    _json(metrics),
                    _json(metrics),
                    str(args.actor).strip(),
                    str(args.reason).strip(),
                ),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO queue_runtime_soak_snapshot (
                    snapshot_id, soak_id, release_sha, configuration_hash,
                    ok, metrics_json, violations_json
                ) VALUES (%s, %s, %s, %s, TRUE, %s::jsonb, '[]'::jsonb)
                """,
                ("qrss_" + uuid4().hex, soak_id, release_sha, config_hash, _json(metrics)),
            )
    return _public_soak(dict(row), snapshot_count=1)


def _snapshot(database_url: str) -> dict[str, Any]:
    with open_runtime_connection(database_url) as connection:
        row = _running_soak(connection)
    if not row:
        return {"status": "not_running", "snapshot_recorded": False}
    metrics, violations = _capture(database_url, row)
    result = _persist_snapshot(
        database_url,
        row=row,
        metrics=metrics,
        violations=violations,
    )
    return {**result, "snapshot_recorded": True, "violations": violations}


def _status(database_url: str) -> dict[str, Any]:
    with open_runtime_connection(database_url) as connection:
        row = connection.execute(
            "SELECT * FROM queue_runtime_soak_run ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"status": "not_started", "snapshot_count": 0}
        count = _snapshot_count(connection, str(row.get("soak_id") or ""))
    return _public_soak(dict(row), snapshot_count=count)


def _required_snapshot_count(duration_seconds: float) -> int:
    return max(1, math.ceil(max(0.0, duration_seconds) / 900 * 0.95))


def _complete(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    release_sha = str(args.expected_release_sha or "").strip()
    generation = int(args.generation or 0)
    _authorized(f"COMPLETE_QUEUE_SOAK_{release_sha}_{generation}", args.confirmation)
    snapshot = _snapshot(database_url)
    if snapshot.get("status") != "running":
        raise RuntimeError("soak cannot complete after a failed, invalidated, or missing snapshot")
    with open_runtime_connection(database_url) as connection:
        with connection.transaction():
            row = _running_soak(connection, for_update=True)
            if not row:
                raise RuntimeError("no running soak exists")
            if str(row.get("release_sha") or "") != release_sha or int(
                row.get("active_generation") or 0
            ) != generation:
                raise RuntimeError("soak completion identity mismatch")
            if str(row.get("policy_version") or "") != str(
                args.expected_policy_version or ""
            ).strip():
                raise RuntimeError("soak completion policy mismatch")
            now = connection.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone().get("now")
            if not isinstance(now, datetime) or now < row.get("required_until"):
                raise RuntimeError("72-hour soak window has not elapsed")
            duration_seconds = max(
                0.0,
                (row.get("required_until") - row.get("started_at")).total_seconds(),
            )
            required_snapshots = _required_snapshot_count(duration_seconds)
            snapshot_count = _snapshot_count(connection, str(row["soak_id"]))
            if snapshot_count < required_snapshots:
                raise RuntimeError(
                    f"soak snapshot coverage is insufficient: {snapshot_count}/{required_snapshots}"
                )
            updated = connection.execute(
                """
                UPDATE queue_runtime_soak_run
                SET status = 'passed', completed_at = CURRENT_TIMESTAMP,
                    row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE soak_id = %s AND status = 'running' AND row_version = %s
                RETURNING *
                """,
                (str(row["soak_id"]), int(row.get("row_version") or 0)),
            ).fetchone()
            if not updated:
                raise RuntimeError("soak completion CAS lost")
    return _public_soak(dict(updated), snapshot_count=snapshot_count)


def _invalidate(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    release_sha = str(args.expected_release_sha or "").strip()
    generation = int(args.generation or 0)
    _authorized(f"INVALIDATE_QUEUE_SOAK_{release_sha}_{generation}", args.confirmation)
    with open_runtime_connection(database_url) as connection:
        with connection.transaction():
            row = _running_soak(connection, for_update=True)
            if not row:
                raise RuntimeError("no running soak exists")
            if (
                str(row.get("release_sha") or "") != release_sha
                or int(row.get("active_generation") or 0) != generation
                or str(row.get("policy_version") or "")
                != str(args.expected_policy_version or "").strip()
            ):
                raise RuntimeError("soak invalidation identity mismatch")
            updated = connection.execute(
                """
                UPDATE queue_runtime_soak_run
                SET status = 'invalidated', invalidated_at = CURRENT_TIMESTAMP,
                    invalidated_reason = %s, row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE soak_id = %s AND status = 'running'
                RETURNING *
                """,
                (str(args.reason).strip(), str(row["soak_id"])),
            ).fetchone()
            count = _snapshot_count(connection, str(row["soak_id"]))
    return _public_soak(dict(updated), snapshot_count=count)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    database_url = normalize_runtime_database_url(raw_database_url())
    if args.action == "start":
        result = _start(args, database_url)
    elif args.action == "snapshot":
        result = _snapshot(database_url)
    elif args.action == "status":
        result = _status(database_url)
    elif args.action == "complete":
        result = _complete(args, database_url)
    else:
        result = _invalidate(args, database_url)
    print(
        _json(
            {
                "ok": True,
                "action": str(args.action),
                "result": result,
                "pii_in_output": False,
                "secrets_in_output": False,
                "real_external_call_executed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
