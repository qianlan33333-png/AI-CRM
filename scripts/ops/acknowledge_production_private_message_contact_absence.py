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
from aicrm_next.platform.shared.queue_provenance import (  # noqa: E402
    private_message_contact_relationship_absent_terminal_sql,
)


AUTHORIZATION_ENV = "AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED"
AUTHORIZATION_BASE_SHA = "e240613e20a479ed2083c1d7fe5bd9c59aabef71"
ACKNOWLEDGEMENT_TYPE = "production_private_message_contact_absence_20260728_no_replay"
ACKNOWLEDGED_STATUS = "acknowledged_history"
EXPECTED_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM PRIVATE MESSAGE EXTERNAL_CONTACT_RELATIONSHIP_ABSENT "
    "TERMINALS 2026-07-28 16:27:28+08:00 TO 17:02:58+08:00 "
    "AS NO-REPLAY HISTORY ON PRODUCTION"
)
EXPECTED_COUNT = 3
ERROR_CODE = "external_contact_relationship_absent"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _full_sha(value: object, *, name: str) -> str:
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
    authorization = json.loads(path.read_text(encoding="utf-8"))
    recorded_at = _timestamp(
        authorization.get("authorization_recorded_at_utc"),
        name="authorization_recorded_at_utc",
    )
    expected = {
        "schema_version": 1,
        "authorization_base_sha": AUTHORIZATION_BASE_SHA,
        "authorization_recorded_at_utc": "2026-07-28T10:13:30Z",
        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
        "confirmation": EXPECTED_CONFIRMATION,
        "confirmation_sha256": _sha256(EXPECTED_CONFIRMATION),
        "expected_job_count": EXPECTED_COUNT,
        "effect_type": "wecom.message.private.send",
        "adapter_name": "wecom_private_message",
        "operation": "send_private_message",
        "target_type": "external_contact",
        "business_type": "broadcast_job",
        "source_module": "background_jobs.broadcast_effect_delegate",
        "source_route": "broadcast_effect_delegate",
        "error_code": ERROR_CODE,
        "expected_lane": "wecom_bulk",
        "expected_policy_version": "queue-v2-production-all-g1",
        "expected_worker_generation": 1,
        "window_start": "2026-07-28T16:27:28+08:00",
        "window_end_exclusive": "2026-07-28T17:02:59+08:00",
        "replay_prohibited": True,
        "provider_success_claimed": False,
    }
    mismatches = {
        key: {"expected": value, "actual": authorization.get(key)}
        for key, value in expected.items()
        if authorization.get(key) != value
    }
    if mismatches:
        raise ValueError(f"private-message acknowledgement scope mismatch: {sorted(mismatches)}")
    authorization["window_start"] = _timestamp(
        authorization.get("window_start"), name="window_start"
    )
    authorization["window_end_exclusive"] = _timestamp(
        authorization.get("window_end_exclusive"), name="window_end_exclusive"
    )
    if authorization["window_start"] >= authorization["window_end_exclusive"]:
        raise ValueError("authorization window is invalid")
    if recorded_at.astimezone(timezone.utc) <= authorization["window_end_exclusive"].astimezone(
        timezone.utc
    ):
        raise ValueError("authorization must be recorded after the terminal window")
    return authorization


_ROW_SELECT = """
    job.id AS job_id,
    COALESCE(job.execution_id, '') AS execution_id,
    COALESCE(job.business_id, '') AS business_id,
    job.target_type,
    job.target_id,
    job.created_at AS job_created_at,
    job.updated_at AS job_updated_at,
    job.completed_at AS job_completed_at,
    job.last_attempt_id,
    attempt.attempt_id,
    attempt.started_at AS attempt_started_at,
    attempt.completed_at AS attempt_completed_at,
    (job.provider_call_started_at IS NOT NULL
        AND attempt.provider_call_started_at IS NOT NULL) AS provider_boundary_recorded
"""


def _candidate_rows(session, authorization: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    strict_predicate = private_message_contact_relationship_absent_terminal_sql(job_alias="job")
    rows = list(
        session.execute(
            text(
                f"""
                SELECT {_ROW_SELECT}
                FROM external_effect_job job
                JOIN external_effect_attempt attempt
                  ON attempt.job_id = job.id
                 AND attempt.attempt_id = job.last_attempt_id
                WHERE ({strict_predicate})
                  AND job.last_error_code = :error_code
                  AND job.updated_at >= :window_start
                  AND job.updated_at < :window_end_exclusive
                ORDER BY job.id
                FOR UPDATE OF job
                """
            ),
            {
                "error_code": ERROR_CODE,
                "window_start": authorization["window_start"],
                "window_end_exclusive": authorization["window_end_exclusive"],
            },
        ).mappings()
    )
    if rows and len(rows) != EXPECTED_COUNT:
        raise RuntimeError(
            f"expected zero or exactly {EXPECTED_COUNT} authorized production private-message "
            f"terminals; found {len(rows)}"
        )
    return rows


def _fingerprint(row: Mapping[str, Any]) -> str:
    stable = {
        "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
        "attempt_completed_at": row["attempt_completed_at"].isoformat(),
        "attempt_id": str(row["attempt_id"]),
        "attempt_started_at": row["attempt_started_at"].isoformat(),
        "business_id": str(row["business_id"]),
        "execution_id": str(row["execution_id"]),
        "job_completed_at": row["job_completed_at"].isoformat(),
        "job_created_at": row["job_created_at"].isoformat(),
        "job_id": int(row["job_id"]),
        "job_updated_at": row["job_updated_at"].isoformat(),
        "last_attempt_id": str(row["last_attempt_id"]),
        "provider_error_class": "external_contact_relationship_absent",
        "target_hash_sha256": _sha256(f"{row['target_type']}\0{row['target_id']}"),
    }
    return _sha256(json.dumps(stable, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _existing_rows(session) -> list[Mapping[str, Any]]:
    total_count = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM queue_terminal_acknowledgement
                WHERE acknowledgement_type = :acknowledgement_type
                """
            ),
            {"acknowledgement_type": ACKNOWLEDGEMENT_TYPE},
        ).scalar_one()
    )
    if total_count == 0:
        return []
    if total_count != EXPECTED_COUNT:
        raise RuntimeError(
            f"expected exactly {EXPECTED_COUNT} existing {ACKNOWLEDGEMENT_TYPE} "
            f"acknowledgements; found {total_count}"
        )
    rows = list(
        session.execute(
            text(
                f"""
                SELECT {_ROW_SELECT},
                       acknowledgement.acknowledgement_id,
                       acknowledgement.authorization_base_sha,
                       acknowledgement.authorization_confirmation_sha256,
                       acknowledgement.job_fingerprint_sha256,
                       acknowledgement.release_sha,
                       acknowledgement.evidence_json,
                       acknowledgement.actor,
                       acknowledgement.reason
                FROM queue_terminal_acknowledgement acknowledgement
                JOIN external_effect_job job ON job.id = acknowledgement.job_id
                JOIN external_effect_attempt attempt
                  ON attempt.job_id = job.id
                 AND attempt.attempt_id = acknowledgement.attempt_id
                WHERE acknowledgement.acknowledgement_type = :acknowledgement_type
                  AND acknowledgement.job_execution_id = COALESCE(job.execution_id, '')
                  AND acknowledgement.attempt_id = COALESCE(job.last_attempt_id, '')
                  AND acknowledgement.graph_id IS NULL
                  AND acknowledgement.authorization_base_sha = :authorization_base_sha
                  AND acknowledgement.authorization_confirmation_sha256 = :confirmation_hash
                  AND acknowledgement.status = :acknowledged_status
                  AND acknowledgement.job_status = 'failed_terminal'
                  AND acknowledgement.error_code = :error_code
                  AND acknowledgement.replay_prohibited IS TRUE
                  AND acknowledgement.provider_success_claimed IS FALSE
                  AND job.effect_type = 'wecom.message.private.send'
                  AND job.adapter_name = 'wecom_private_message'
                  AND job.operation = 'send_private_message'
                  AND job.target_type = 'external_contact'
                  AND job.business_type = 'broadcast_job'
                  AND job.source_module = 'background_jobs.broadcast_effect_delegate'
                  AND job.source_route = 'broadcast_effect_delegate'
                  AND job.status = 'failed_terminal'
                  AND job.last_error_code = :error_code
                  AND job.execution_mode = 'execute'
                  AND job.lane = 'wecom_bulk'
                  AND job.attempt_count = 1
                  AND job.max_attempts = 5
                  AND job.side_effect_executed IS TRUE
                  AND job.provider_result_received IS TRUE
                  AND job.provider_call_started_at IS NOT NULL
                  AND job.completed_at IS NOT NULL
                  AND job.reconciliation_required IS FALSE
                  AND job.worker_generation = 1
                  AND job.policy_version = 'queue-v2-production-all-g1'
                  AND job.lease_token = ''
                  AND job.lease_expires_at IS NULL
                  AND job.locked_by = ''
                  AND job.locked_at IS NULL
                  AND job.hold_reason = ''
                  AND job.cancel_requested_at IS NULL
                  AND attempt.adapter_name = 'wecom_private_message'
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = 'send_private_message'
                  AND attempt.status = 'failed_terminal'
                  AND attempt.error_code = :error_code
                  AND attempt.provider_call_started_at IS NOT NULL
                  AND attempt.completed_at IS NOT NULL
                  AND attempt.worker_generation = 1
                  AND COALESCE((attempt.response_summary_json->>'errcode')::INTEGER, 0) = 84061
                  AND COALESCE(
                        (attempt.response_summary_json->>'real_external_call_executed')::BOOLEAN,
                        FALSE
                      )
                  AND 1 = (
                      SELECT COUNT(*) FROM external_effect_attempt counted_attempt
                      WHERE counted_attempt.job_id = job.id
                  )
                ORDER BY job.id
                FOR UPDATE OF acknowledgement, job
                """
            ),
            {
                "acknowledgement_type": ACKNOWLEDGEMENT_TYPE,
                "authorization_base_sha": AUTHORIZATION_BASE_SHA,
                "confirmation_hash": _sha256(EXPECTED_CONFIRMATION),
                "acknowledged_status": ACKNOWLEDGED_STATUS,
                "error_code": ERROR_CODE,
            },
        ).mappings()
    )
    if len(rows) != EXPECTED_COUNT:
        raise RuntimeError(
            f"existing {ACKNOWLEDGEMENT_TYPE} acknowledgements failed durable linkage validation"
        )
    return rows


def _expected_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_count": 1,
        "durable_provider_attempt_count": 1,
        "provider_boundary_crossed": bool(row["provider_boundary_recorded"]),
        "provider_error_class": "external_contact_relationship_absent",
        "provider_errcode": 84061,
        "provider_result_received": True,
        "provider_success_claimed": False,
        "real_external_call_executed": True,
        "real_external_call_executed_by_acknowledgement": False,
        "replay_prohibited": True,
        "target_hash_sha256": _sha256(f"{row['target_type']}\0{row['target_id']}"),
        "target_values_redacted": True,
    }


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
            existing_rows = _existing_rows(session)
            rows = existing_rows or _candidate_rows(session, authorization)
            if not rows:
                session.rollback()
                return {
                    "ok": True,
                    "applied": False,
                    "acknowledged_count": 0,
                    "created_count": 0,
                    "no_op_reason": "authorized_historical_terminals_absent",
                    "replay_prohibited": True,
                    "provider_success_claimed": False,
                    "real_external_call_executed": False,
                    "target_values_redacted": True,
                }
            if len(rows) != EXPECTED_COUNT:
                raise RuntimeError(
                    f"expected exactly {EXPECTED_COUNT} authorized production private-message "
                    f"terminals; found {len(rows)}"
                )
            created_count = 0
            for row in rows:
                fingerprint = _fingerprint(row)
                acknowledgement_id = f"qta_{fingerprint[:32]}"
                expected_evidence = _expected_evidence(row)
                if existing_rows:
                    _full_sha(row["release_sha"], name="existing release_sha")
                    if row["acknowledgement_id"] != acknowledgement_id:
                        raise RuntimeError("existing acknowledgement id does not match fingerprint")
                    if row["job_fingerprint_sha256"] != fingerprint:
                        raise RuntimeError("existing acknowledgement fingerprint does not match job")
                    if row["evidence_json"] != expected_evidence:
                        raise RuntimeError("existing acknowledgement evidence does not match authorization")
                    if not str(row["actor"] or "").strip() or not str(row["reason"] or "").strip():
                        raise RuntimeError("existing acknowledgement actor or reason is missing")
                    continue
                duplicate = session.execute(
                    text(
                        """
                        SELECT acknowledgement_id
                        FROM queue_terminal_acknowledgement
                        WHERE job_id = :job_id OR job_fingerprint_sha256 = :fingerprint
                        """
                    ),
                    {"job_id": int(row["job_id"]), "fingerprint": fingerprint},
                ).first()
                if duplicate:
                    raise RuntimeError("terminal already has a conflicting acknowledgement")
                if not apply:
                    continue
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
                            :job_execution_id, :attempt_id, NULL,
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
                        "release_sha": release_sha,
                        "authorization_base_sha": authorization_base_sha,
                        "confirmation_hash": _sha256(confirmation),
                        "fingerprint": fingerprint,
                        "status": ACKNOWLEDGED_STATUS,
                        "error_code": ERROR_CODE,
                        "evidence_json": json.dumps(expected_evidence, sort_keys=True),
                        "actor": actor,
                        "reason": reason,
                    },
                )
                created_count += 1
            if apply:
                session.commit()
            else:
                session.rollback()
            return {
                "ok": True,
                "applied": apply,
                "acknowledged_count": EXPECTED_COUNT,
                "created_count": created_count,
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
        description=(
            "Acknowledge zero or the exact three 2026-07-28 private-message "
            "contact-absence terminals as immutable no-replay history."
        )
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
