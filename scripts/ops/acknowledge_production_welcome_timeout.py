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
ACKNOWLEDGEMENT_TYPE = "production_welcome_41050_job_2157_no_replay"
ACKNOWLEDGED_STATUS = "acknowledged_history"
AUTHORIZATION_BASE_SHA = "1ccf3a056f21d3d023fcf77ac809d0569cc09820"
EXPECTED_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM PRODUCTION WELCOME 41050 JOB 2157 AS NO-REPLAY HISTORY"
)
EXPECTED_JOB_ID = 2157
EXPECTED_GRAPH_ID = 3
EXPECTED_EXECUTION_ID = "exe_channel_welcome_send_4795cc3d70964dc2b8a0fd5c6b875d33"
EXPECTED_BUSINESS_ID = "exe_channel_welcome_root_1ae456f3e96b4a97a478a5d4630e8907"
EXPECTED_ATTEMPT_ID = "eea_be52aed9558e43e89c189dd6d472e6fc"
EXPECTED_CREATED_AT = "2026-07-24T11:30:38.262825+08:00"
EXPECTED_PROVIDER_CALL_STARTED_AT = "2026-07-24T11:31:32.223640+08:00"
EXPECTED_COMPLETED_AT = "2026-07-24T11:31:32.739066+08:00"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _full_sha(value: str, *, name: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) != 40 or any(character not in "0123456789abcdef" for character in cleaned):
        raise ValueError(f"{name} must be one full lowercase SHA")
    return cleaned


def _timestamp(value: object, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include timezone")
    return parsed


def _load_authorization(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported acknowledgement manifest schema")
    if _full_sha(payload.get("authorization_base_sha"), name="authorization_base_sha") != AUTHORIZATION_BASE_SHA:
        raise ValueError("manifest authorization base SHA is not the incident release SHA")
    recorded_at = _timestamp(
        payload.get("authorization_recorded_at_utc"),
        name="authorization_recorded_at_utc",
    )
    authorization = dict(payload.get("acknowledgement") or {})
    expected = {
        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
        "confirmation": EXPECTED_CONFIRMATION,
        "confirmation_sha256": _sha256(EXPECTED_CONFIRMATION),
        "maximum_job_count": 1,
        "job_id": EXPECTED_JOB_ID,
        "graph_id": EXPECTED_GRAPH_ID,
        "execution_id": EXPECTED_EXECUTION_ID,
        "business_id": EXPECTED_BUSINESS_ID,
        "attempt_id": EXPECTED_ATTEMPT_ID,
        "effect_type": "wecom.welcome_message.send",
        "adapter_name": "wecom_welcome_message",
        "operation": "send",
        "business_type": "channel_welcome_effect_graph",
        "source_module": "channel_entry.application",
        "source_route": "channel_entry.process_channel_entry",
        "error_code": "wecom_error_41050",
        "expected_lane": "wecom_interactive",
        "expected_policy_version": "queue-v2-production-all-g1",
        "expected_worker_generation": 1,
        "job_created_at": EXPECTED_CREATED_AT,
        "provider_call_started_at": EXPECTED_PROVIDER_CALL_STARTED_AT,
        "job_completed_at": EXPECTED_COMPLETED_AT,
        "replay_prohibited": True,
        "provider_success_claimed": False,
    }
    mismatches = {
        key: {"expected": value, "actual": authorization.get(key)}
        for key, value in expected.items()
        if authorization.get(key) != value
    }
    if mismatches:
        raise ValueError(f"production welcome acknowledgement scope mismatch: {sorted(mismatches)}")
    authorization["authorization_recorded_at_utc"] = recorded_at.astimezone(timezone.utc)
    authorization["job_created_at"] = _timestamp(
        authorization["job_created_at"], name="job_created_at"
    )
    authorization["provider_call_started_at"] = _timestamp(
        authorization["provider_call_started_at"], name="provider_call_started_at"
    )
    authorization["job_completed_at"] = _timestamp(
        authorization["job_completed_at"], name="job_completed_at"
    )
    if authorization["authorization_recorded_at_utc"] <= authorization["job_completed_at"]:
        raise ValueError("authorization must be recorded after the terminal incident")
    return authorization


def _candidate_rows(session, authorization: Mapping[str, Any]) -> list[Mapping[str, Any]]:
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
                    attempt.completed_at AS attempt_completed_at,
                    (job.provider_call_started_at IS NOT NULL
                        AND attempt.provider_call_started_at IS NOT NULL)
                        AS provider_boundary_recorded
                FROM external_effect_job job
                JOIN channel_welcome_effect_graph graph
                  ON graph.id = :graph_id
                 AND graph.final_effect_job_id = job.id
                 AND graph.execution_id = job.business_id
                JOIN external_effect_attempt attempt
                  ON attempt.job_id = job.id
                 AND attempt.attempt_id = job.last_attempt_id
                WHERE job.id = :job_id
                  AND job.execution_id = :execution_id
                  AND job.business_id = :business_id
                  AND job.last_attempt_id = :attempt_id
                  AND job.effect_type = :effect_type
                  AND job.adapter_name = :adapter_name
                  AND job.operation = :operation
                  AND job.business_type = :business_type
                  AND job.source_module = :source_module
                  AND job.source_route = :source_route
                  AND job.status = 'failed_terminal'
                  AND job.last_error_code = :error_code
                  AND job.execution_mode = 'execute'
                  AND job.lane = :lane
                  AND job.attempt_count = 1
                  AND job.max_attempts = 5
                  AND job.side_effect_executed IS TRUE
                  AND job.provider_result_received IS TRUE
                  AND job.provider_call_started_at = :provider_call_started_at
                  AND job.reconciliation_required IS FALSE
                  AND job.worker_generation = :worker_generation
                  AND job.policy_version = :policy_version
                  AND job.created_at = :job_created_at
                  AND job.updated_at = :job_completed_at
                  AND job.completed_at = :job_completed_at
                  AND graph.status = 'terminal'
                  AND attempt.attempt_id = :attempt_id
                  AND attempt.adapter_name = :adapter_name
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = :operation
                  AND attempt.status = 'failed_terminal'
                  AND attempt.error_code = :error_code
                  AND attempt.provider_call_started_at = :provider_call_started_at
                  AND attempt.worker_generation = :worker_generation
                  AND attempt.started_at = :provider_call_started_at
                  AND attempt.completed_at = :job_completed_at
                  AND (
                      SELECT COUNT(*) FROM external_effect_attempt counted_attempt
                      WHERE counted_attempt.job_id = job.id
                  ) = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM external_effect_job success
                      WHERE success.id <> job.id
                        AND success.status = 'succeeded'
                        AND success.effect_type = job.effect_type
                        AND success.business_type = job.business_type
                        AND success.business_id = job.business_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM external_effect_job later_success
                      WHERE later_success.id <> job.id
                        AND later_success.status = 'succeeded'
                        AND later_success.effect_type = job.effect_type
                        AND later_success.target_type = job.target_type
                        AND later_success.target_id = job.target_id
                        AND later_success.updated_at > job.updated_at
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM channel_welcome_effect_dependency dependency
                      JOIN external_effect_job sibling
                        ON sibling.id IN (
                            dependency.prerequisite_effect_job_id,
                            dependency.dependent_effect_job_id
                        )
                      WHERE dependency.graph_id = graph.id
                        AND sibling.id <> job.id
                        AND sibling.status IN (
                            'planned', 'approved', 'queued', 'dispatching', 'failed_retryable'
                        )
                  )
                FOR UPDATE OF job, graph
                """
            ),
            {
                "job_id": int(authorization["job_id"]),
                "graph_id": int(authorization["graph_id"]),
                "execution_id": authorization["execution_id"],
                "business_id": authorization["business_id"],
                "attempt_id": authorization["attempt_id"],
                "effect_type": authorization["effect_type"],
                "adapter_name": authorization["adapter_name"],
                "operation": authorization["operation"],
                "business_type": authorization["business_type"],
                "source_module": authorization["source_module"],
                "source_route": authorization["source_route"],
                "error_code": authorization["error_code"],
                "lane": authorization["expected_lane"],
                "worker_generation": authorization["expected_worker_generation"],
                "policy_version": authorization["expected_policy_version"],
                "job_created_at": authorization["job_created_at"],
                "provider_call_started_at": authorization["provider_call_started_at"],
                "job_completed_at": authorization["job_completed_at"],
            },
        ).mappings()
    )


def _fingerprint(row: Mapping[str, Any]) -> str:
    stable = {
        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
        "attempt_completed_at": row["attempt_completed_at"].isoformat(),
        "attempt_id": str(row["attempt_id"]),
        "attempt_started_at": row["attempt_started_at"].isoformat(),
        "business_id": str(row["business_id"]),
        "execution_id": str(row["execution_id"]),
        "graph_id": int(row["graph_id"]),
        "job_completed_at": row["job_completed_at"].isoformat(),
        "job_created_at": row["job_created_at"].isoformat(),
        "job_id": int(row["job_id"]),
        "last_attempt_id": str(row["last_attempt_id"]),
        "provider_error_class": "welcome_code_expired_after_queue_delay",
        "target_hash_sha256": _sha256(f"{row['target_type']}\0{row['target_id']}"),
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
    authorization_base_sha = _full_sha(
        authorization_base_sha, name="authorization_base_sha"
    )
    if authorization_base_sha != AUTHORIZATION_BASE_SHA:
        raise ValueError("authorization base SHA does not match the incident manifest")
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
            rows = _candidate_rows(session, authorization)
            if len(rows) != 1:
                raise RuntimeError(
                    f"expected exactly one authorized production welcome terminal; found {len(rows)}"
                )
            row = rows[0]
            fingerprint = _fingerprint(row)
            acknowledgement_id = f"qta_{fingerprint[:32]}"
            existing = session.execute(
                text(
                    """
                    SELECT * FROM queue_terminal_acknowledgement
                    WHERE job_id = :job_id AND job_fingerprint_sha256 = :fingerprint
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
                "error_code": authorization["error_code"],
                "replay_prohibited": True,
                "provider_success_claimed": False,
            }
            if existing:
                _full_sha(str(existing.get("release_sha") or ""), name="existing release_sha")
                if any(existing.get(key) != value for key, value in expected_existing.items()):
                    raise RuntimeError(
                        "existing production welcome acknowledgement does not match authorization"
                    )

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
                    "graph_status": str(row["graph_status"]),
                    "provider_boundary_crossed": bool(row["provider_boundary_recorded"]),
                    "provider_error_class": "welcome_code_expired_after_queue_delay",
                    "provider_result_received": True,
                    "provider_success_claimed": False,
                    "real_external_call_executed_by_acknowledgement": False,
                    "replay_prohibited": True,
                    "target_hash_sha256": _sha256(f"{row['target_type']}\0{row['target_id']}"),
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
                        "error_code": authorization["error_code"],
                        "evidence_json": json.dumps(evidence, sort_keys=True),
                        "actor": actor,
                        "reason": reason,
                    },
                )
            session.commit()
            return {
                "ok": True,
                "applied": True,
                "acknowledged_count": 1,
                "created_count": 0 if existing else 1,
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
        description="Acknowledge production welcome job 2157 as immutable no-replay history.",
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
