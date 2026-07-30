#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import text

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.platform.shared.db_session import get_session_factory  # noqa: E402


AUTHORIZATION_ENV = "AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED"
ACKNOWLEDGEMENT_TYPE = "pre_cutover_welcome_41050_no_replay"
ACKNOWLEDGED_STATUS = "acknowledged_history"
WELCOME_EFFECT_TYPE = "wecom.welcome_message.send"
WELCOME_ADAPTER = "wecom_welcome_message"
WELCOME_OPERATION = "send"
WELCOME_BUSINESS_TYPE = "channel_welcome_effect_graph"
WELCOME_SOURCE_MODULE = "channel_entry.application"
WELCOME_SOURCE_ROUTE = "channel_entry.process_channel_entry"
WELCOME_ERROR_CODE = "wecom_error_41050"
PRE_CUTOVER_POLICY = "queue-v2-test-loopback"
EXPECTED_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM PRE-CUTOVER WELCOME 41050 TERMINAL "
    "AS NO-REPLAY HISTORY ON PRODUCTION"
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _full_sha(value: str, *, name: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) != 40 or any(character not in "0123456789abcdef" for character in cleaned):
        raise ValueError(f"{name} must be one full lowercase SHA")
    return cleaned


def _load_authorization(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    authorization = dict(payload.get("pre_cutover_welcome_terminal_acknowledgement") or {})
    if authorization.get("confirmation") != EXPECTED_CONFIRMATION:
        raise ValueError("manifest acknowledgement confirmation does not match the authorized phrase")
    if authorization.get("confirmation_sha256") != _sha256(EXPECTED_CONFIRMATION):
        raise ValueError("manifest acknowledgement confirmation hash is invalid")
    if int(authorization.get("maximum_job_count") or 0) != 1:
        raise ValueError("manifest must authorize at most one historical job")
    expected = {
        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
        "effect_type": WELCOME_EFFECT_TYPE,
        "adapter_name": WELCOME_ADAPTER,
        "operation": WELCOME_OPERATION,
        "business_type": WELCOME_BUSINESS_TYPE,
        "source_module": WELCOME_SOURCE_MODULE,
        "source_route": WELCOME_SOURCE_ROUTE,
        "error_code": WELCOME_ERROR_CODE,
        "expected_policy_version": PRE_CUTOVER_POLICY,
        "expected_worker_generation": 0,
        "replay_prohibited": True,
        "provider_success_claimed": False,
    }
    mismatches = {
        key: {"expected": value, "actual": authorization.get(key)}
        for key, value in expected.items()
        if authorization.get(key) != value
    }
    if mismatches:
        raise ValueError(f"manifest acknowledgement scope mismatch: {sorted(mismatches)}")
    recorded_at = str(authorization.get("authorization_recorded_at_utc") or "").strip()
    try:
        parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("manifest authorization_recorded_at_utc is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("manifest authorization_recorded_at_utc must include timezone")
    authorization["authorization_recorded_at_utc"] = parsed.astimezone(timezone.utc)
    return authorization


def _candidate_rows(session, *, cutoff: datetime) -> list[Mapping[str, Any]]:
    return list(
        session.execute(
            text(
                """
                SELECT
                    job.id AS job_id,
                    job.execution_id,
                    job.business_id,
                    job.target_type,
                    job.target_id,
                    job.created_at AS job_created_at,
                    job.updated_at AS job_updated_at,
                    job.completed_at AS job_completed_at,
                    job.last_attempt_id,
                    graph.id AS graph_id,
                    graph.status AS graph_status,
                    attempt.attempt_id,
                    attempt.started_at AS attempt_started_at,
                    attempt.completed_at AS attempt_completed_at
                FROM external_effect_job job
                JOIN channel_welcome_effect_graph graph
                  ON graph.final_effect_job_id = job.id
                 AND graph.execution_id = job.business_id
                JOIN external_effect_attempt attempt
                  ON attempt.job_id = job.id
                 AND attempt.attempt_id = job.last_attempt_id
                WHERE job.effect_type = :effect_type
                  AND job.adapter_name = :adapter_name
                  AND job.operation = :operation
                  AND job.business_type = :business_type
                  AND job.source_module = :source_module
                  AND job.source_route = :source_route
                  AND job.status = 'failed_terminal'
                  AND job.last_error_code = :error_code
                  AND job.execution_mode = 'execute'
                  AND job.attempt_count = 1
                  AND job.max_attempts = 5
                  AND job.side_effect_executed IS TRUE
                  AND job.provider_result_received IS TRUE
                  AND job.provider_call_started_at IS NOT NULL
                  AND job.reconciliation_required IS FALSE
                  AND job.worker_generation = 0
                  AND job.policy_version = :policy_version
                  AND job.created_at <= :cutoff
                  AND graph.status IN ('ready', 'terminal')
                  AND attempt.adapter_name = :adapter_name
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = :operation
                  AND attempt.status = 'failed_terminal'
                  AND attempt.error_code = :error_code
                  AND attempt.provider_call_started_at IS NOT NULL
                  AND attempt.worker_generation = 0
                  AND attempt.completed_at IS NOT NULL
                  AND (
                      SELECT COUNT(*)
                      FROM external_effect_attempt counted_attempt
                      WHERE counted_attempt.job_id = job.id
                  ) = 1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM external_effect_job success
                      WHERE success.id <> job.id
                        AND success.status = 'succeeded'
                        AND success.effect_type = job.effect_type
                        AND job.business_id <> ''
                        AND success.business_type = job.business_type
                        AND success.business_id = job.business_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM external_effect_job later_success
                      WHERE later_success.id <> job.id
                        AND later_success.status = 'succeeded'
                        AND later_success.effect_type = job.effect_type
                        AND later_success.target_type = job.target_type
                        AND later_success.target_id = job.target_id
                        AND later_success.updated_at > job.updated_at
                  )
                ORDER BY job.id
                FOR UPDATE OF job, graph
                """
            ),
            {
                "effect_type": WELCOME_EFFECT_TYPE,
                "adapter_name": WELCOME_ADAPTER,
                "operation": WELCOME_OPERATION,
                "business_type": WELCOME_BUSINESS_TYPE,
                "source_module": WELCOME_SOURCE_MODULE,
                "source_route": WELCOME_SOURCE_ROUTE,
                "error_code": WELCOME_ERROR_CODE,
                "policy_version": PRE_CUTOVER_POLICY,
                "cutoff": cutoff,
            },
        ).mappings()
    )


def _fingerprint(row: Mapping[str, Any]) -> str:
    target_hash = _sha256(f"{row['target_type']}\0{row['target_id']}")
    stable = {
        "attempt_completed_at": row["attempt_completed_at"].isoformat(),
        "attempt_id": str(row["attempt_id"]),
        "attempt_started_at": row["attempt_started_at"].isoformat(),
        "business_id": str(row["business_id"]),
        "execution_id": str(row["execution_id"]),
        "graph_id": int(row["graph_id"]),
        "job_completed_at": row["job_completed_at"].isoformat() if row["job_completed_at"] else "",
        "job_created_at": row["job_created_at"].isoformat(),
        "job_id": int(row["job_id"]),
        "last_attempt_id": str(row["last_attempt_id"]),
        "target_hash_sha256": target_hash,
    }
    return _sha256(json.dumps(stable, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def acknowledge(
    *,
    manifest_path: Path,
    release_sha: str,
    authorization_base_sha: str,
    confirmation: str,
    actor: str,
    reason: str,
    apply: bool,
) -> dict[str, Any]:
    authorization = _load_authorization(manifest_path)
    release_sha = _full_sha(release_sha, name="release_sha")
    authorization_base_sha = _full_sha(authorization_base_sha, name="authorization_base_sha")
    if authorization_base_sha != str(authorization.get("authorization_base_sha") or ""):
        raise ValueError("authorization base SHA does not match the manifest acknowledgement")
    if confirmation != EXPECTED_CONFIRMATION:
        raise ValueError("confirmation does not match the exact production authorization")
    actor = str(actor or "").strip()
    reason = str(reason or "").strip()
    if not actor or not reason:
        raise ValueError("actor and reason are required")
    if apply and str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")

    with get_session_factory()() as session:
        try:
            rows = _candidate_rows(
                session,
                cutoff=authorization["authorization_recorded_at_utc"],
            )
            if len(rows) > 1:
                raise RuntimeError(
                    f"expected at most one authorized historical welcome terminal; found {len(rows)}"
                )
            if not rows:
                session.rollback()
                return {
                    "ok": True,
                    "applied": False,
                    "candidate_count": 0,
                    "acknowledged_count": 0,
                    "created_count": 0,
                    "no_op_reason": "authorized_historical_terminal_absent",
                    "replay_prohibited": True,
                    "provider_success_claimed": False,
                    "real_external_call_executed": False,
                    "target_values_redacted": True,
                }
            row = rows[0]
            fingerprint = _fingerprint(row)
            acknowledgement_id = f"qta_{fingerprint[:32]}"
            existing = session.execute(
                text(
                    """
                    SELECT *
                    FROM queue_terminal_acknowledgement
                    WHERE job_id = :job_id
                      AND job_fingerprint_sha256 = :fingerprint
                    """
                ),
                {"job_id": int(row["job_id"]), "fingerprint": fingerprint},
            ).mappings().first()
            confirmation_hash = _sha256(confirmation)
            expected_existing = {
                "acknowledgement_id": acknowledgement_id,
                "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
                "job_execution_id": str(row["execution_id"]),
                "attempt_id": str(row["attempt_id"]),
                "graph_id": int(row["graph_id"]),
                "authorization_base_sha": authorization_base_sha,
                "authorization_confirmation_sha256": confirmation_hash,
                "job_fingerprint_sha256": fingerprint,
                "status": ACKNOWLEDGED_STATUS,
                "job_status": "failed_terminal",
                "error_code": WELCOME_ERROR_CODE,
                "replay_prohibited": True,
                "provider_success_claimed": False,
            }
            if existing:
                _full_sha(str(existing.get("release_sha") or ""), name="existing release_sha")
                mismatches = {
                    key: {"expected": value, "actual": existing.get(key)}
                    for key, value in expected_existing.items()
                    if existing.get(key) != value
                }
                if mismatches:
                    raise RuntimeError(
                        "existing terminal acknowledgement does not match the immutable authorization"
                    )

            active_sibling_count = int(
                session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT dependency_job_id)
                        FROM (
                            SELECT dependency.prerequisite_effect_job_id AS dependency_job_id
                            FROM channel_welcome_effect_dependency dependency
                            WHERE dependency.graph_id = :graph_id
                            UNION
                            SELECT dependency.dependent_effect_job_id AS dependency_job_id
                            FROM channel_welcome_effect_dependency dependency
                            WHERE dependency.graph_id = :graph_id
                        ) linked
                        JOIN external_effect_job linked_job ON linked_job.id = linked.dependency_job_id
                        WHERE linked_job.id <> :job_id
                          AND linked_job.status IN (
                              'planned', 'approved', 'queued', 'dispatching', 'failed_retryable'
                          )
                        """
                    ),
                    {"graph_id": int(row["graph_id"]), "job_id": int(row["job_id"])},
                ).scalar_one()
            )
            if active_sibling_count:
                raise RuntimeError("welcome graph still has active sibling provider work")

            if not apply:
                session.rollback()
                return {
                    "ok": True,
                    "applied": False,
                    "candidate_count": 1,
                    "replay_prohibited": True,
                    "provider_success_claimed": False,
                    "real_external_call_executed": False,
                    "target_values_redacted": True,
                }

            if not existing:
                evidence = {
                    "active_sibling_count": 0,
                    "attempt_count": 1,
                    "durable_provider_attempt_count": 1,
                    "graph_status_before": str(row["graph_status"]),
                    "provider_boundary_crossed": True,
                    "provider_result_received": True,
                    "provider_success_claimed": False,
                    "real_external_call_executed_by_acknowledgement": False,
                    "replay_prohibited": True,
                    "target_values_redacted": True,
                }
                session.execute(
                    text(
                        """
                        INSERT INTO queue_terminal_acknowledgement (
                            acknowledgement_id, acknowledgement_type, job_id,
                            job_execution_id, attempt_id, graph_id,
                            release_sha, authorization_base_sha,
                            authorization_confirmation_sha256, job_fingerprint_sha256,
                            status, job_status, error_code, replay_prohibited,
                            provider_success_claimed, evidence_json, actor, reason
                        ) VALUES (
                            :acknowledgement_id, :acknowledgement_type, :job_id,
                            :job_execution_id, :attempt_id, :graph_id,
                            :release_sha, :authorization_base_sha,
                            :confirmation_hash, :fingerprint,
                            :status, 'failed_terminal', :error_code, TRUE,
                            FALSE, CAST(:evidence_json AS JSONB), :actor, :reason
                        )
                        """
                    ),
                    {
                        "acknowledgement_id": acknowledgement_id,
                        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
                        "job_id": int(row["job_id"]),
                        "job_execution_id": str(row["execution_id"]),
                        "attempt_id": str(row["attempt_id"]),
                        "graph_id": int(row["graph_id"]),
                        "release_sha": release_sha,
                        "authorization_base_sha": authorization_base_sha,
                        "confirmation_hash": confirmation_hash,
                        "fingerprint": fingerprint,
                        "status": ACKNOWLEDGED_STATUS,
                        "error_code": WELCOME_ERROR_CODE,
                        "evidence_json": json.dumps(evidence, sort_keys=True),
                        "actor": actor,
                        "reason": reason,
                    },
                )
            graph_terminalized_count = int(
                session.execute(
                    text(
                        """
                        UPDATE channel_welcome_effect_graph
                        SET status = 'terminal', updated_at = CURRENT_TIMESTAMP
                        WHERE id = :graph_id
                          AND final_effect_job_id = :job_id
                          AND status = 'ready'
                        """
                    ),
                    {"graph_id": int(row["graph_id"]), "job_id": int(row["job_id"])},
                ).rowcount
                or 0
            )
            final_graph_status = session.execute(
                text(
                    """
                    SELECT status
                    FROM channel_welcome_effect_graph
                    WHERE id = :graph_id AND final_effect_job_id = :job_id
                    """
                ),
                {"graph_id": int(row["graph_id"]), "job_id": int(row["job_id"])},
            ).scalar_one()
            if final_graph_status != "terminal":
                raise RuntimeError("welcome graph did not settle to terminal history")
            session.commit()
            return {
                "ok": True,
                "applied": True,
                "acknowledged_count": 1,
                "created_count": 0 if existing else 1,
                "graph_terminalized_count": graph_terminalized_count,
                "replay_prohibited": True,
                "provider_success_claimed": False,
                "real_external_call_executed": False,
                "target_values_redacted": True,
            }
        except Exception:
            session.rollback()
            raise


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acknowledge at most one pre-cutover welcome 41050 as no-replay history.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--authorization-base-sha", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = acknowledge(
        manifest_path=args.manifest,
        release_sha=args.release_sha,
        authorization_base_sha=args.authorization_base_sha,
        confirmation=args.confirmation,
        actor=args.actor,
        reason=args.reason,
        apply=bool(args.apply),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
