#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
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
ACKNOWLEDGED_STATUS = "acknowledged_history"
PRIVATE_KEY = "private_message_84061"
REFUND_KEY = "wechat_refund_not_enough"
PRIVATE_TYPE = "production_private_message_84061_no_replay"
REFUND_TYPE = "production_wechat_refund_not_enough_no_replay"
PRIVATE_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM PRIVATE MESSAGE 84061 TERMINAL "
    "AS NO-REPLAY HISTORY ON PRODUCTION"
)
REFUND_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM WECHAT REFUND NOT_ENOUGH TERMINALS "
    "AS NO-REPLAY HISTORY ON PRODUCTION"
)


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


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported acknowledgement manifest schema")
    payload["authorization_base_sha"] = _full_sha(
        payload.get("authorization_base_sha"), name="manifest authorization_base_sha"
    )
    _timestamp(payload.get("authorization_recorded_at_utc"), name="authorization_recorded_at_utc")
    expected = {
        PRIVATE_KEY: {
            "acknowledgement_type": PRIVATE_TYPE,
            "confirmation": PRIVATE_CONFIRMATION,
            "maximum_job_count": 1,
            "effect_type": "wecom.message.private.send",
            "adapter_name": "wecom_private_message",
            "operation": "send_private_message",
            "business_type": "broadcast_job",
            "source_module": "background_jobs.broadcast_effect_delegate",
            "source_route": "broadcast_effect_delegate",
            "error_code": "external_call_failed_known",
            "expected_policy_version": "queue-v2-production-all-g1",
            "expected_worker_generation": 1,
            "replay_prohibited": True,
            "provider_success_claimed": False,
        },
        REFUND_KEY: {
            "acknowledgement_type": REFUND_TYPE,
            "confirmation": REFUND_CONFIRMATION,
            "maximum_job_count": 3,
            "effect_type": "payment.wechat.refund.request",
            "adapter_name": "wechat_payment",
            "operation": "refund_request",
            "business_type": "commerce_order",
            "source_module": "commerce.admin_transactions",
            "source_route": "commerce.admin_transactions.create_wechat_refund_request",
            "error_code": "http_403",
            "provider_error_code": "NOT_ENOUGH",
            "expected_policy_version": "queue-v2-production-all-g1",
            "expected_worker_generation": 1,
            "replay_prohibited": True,
            "provider_success_claimed": False,
        },
    }
    for key, exact in expected.items():
        authorization = dict(payload.get(key) or {})
        mismatches = {
            field: {"expected": value, "actual": authorization.get(field)}
            for field, value in exact.items()
            if authorization.get(field) != value
        }
        if mismatches:
            raise ValueError(f"{key} acknowledgement scope mismatch: {sorted(mismatches)}")
        if authorization.get("confirmation_sha256") != _sha256(exact["confirmation"]):
            raise ValueError(f"{key} confirmation hash is invalid")
        authorization["window_start"] = _timestamp(
            authorization.get("window_start"), name=f"{key}.window_start"
        )
        authorization["window_end"] = _timestamp(
            authorization.get("window_end"), name=f"{key}.window_end"
        )
        if authorization["window_start"] >= authorization["window_end"]:
            raise ValueError(f"{key} authorization window is invalid")
        payload[key] = authorization
    return payload


_COMMON_SELECT = """
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
    (job.provider_call_started_at IS NOT NULL OR attempt.provider_call_started_at IS NOT NULL)
        AS provider_boundary_recorded
"""


def _private_candidates(session, authorization: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(
        session.execute(
            text(
                f"""
                SELECT {_COMMON_SELECT}, 'external_contact_relationship_absent' AS provider_error_class
                FROM external_effect_job job
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
                  AND job.provider_result_received IS FALSE
                  AND job.worker_generation = :worker_generation
                  AND job.policy_version = :policy_version
                  AND job.updated_at >= :window_start
                  AND job.updated_at < :window_end
                  AND (
                    LOWER(job.last_error_message) LIKE '%not external contact%'
                    OR LOWER(job.last_error_message) LIKE '%not a friend%'
                    OR LOWER(job.last_error_message) LIKE '%external contact%not found%'
                  )
                  AND attempt.adapter_name = :adapter_name
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = :operation
                  AND attempt.status = 'failed_terminal'
                  AND attempt.completed_at IS NOT NULL
                  AND (
                    SELECT COUNT(*) FROM external_effect_attempt counted
                    WHERE counted.job_id = job.id
                  ) = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM external_effect_job success
                    WHERE success.id <> job.id
                      AND success.status = 'succeeded'
                      AND success.effect_type = job.effect_type
                      AND COALESCE(job.business_id, '') <> ''
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
                ORDER BY job.id
                FOR UPDATE OF job
                """
            ),
            {
                "effect_type": authorization["effect_type"],
                "adapter_name": authorization["adapter_name"],
                "operation": authorization["operation"],
                "business_type": authorization["business_type"],
                "source_module": authorization["source_module"],
                "source_route": authorization["source_route"],
                "error_code": authorization["error_code"],
                "worker_generation": authorization["expected_worker_generation"],
                "policy_version": authorization["expected_policy_version"],
                "window_start": authorization["window_start"],
                "window_end": authorization["window_end"],
            },
        ).mappings()
    )


def _refund_candidates(session, authorization: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(
        session.execute(
            text(
                f"""
                SELECT {_COMMON_SELECT}, 'merchant_balance_insufficient' AS provider_error_class
                FROM external_effect_job job
                JOIN external_effect_attempt attempt
                  ON attempt.job_id = job.id
                 AND attempt.attempt_id = job.last_attempt_id
                JOIN wechat_pay_refunds refund ON refund.out_refund_no = job.target_id
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
                  AND job.worker_generation = :worker_generation
                  AND job.policy_version = :policy_version
                  AND job.updated_at >= :window_start
                  AND job.updated_at < :window_end
                  AND UPPER(COALESCE(jsonb_extract_path_text(
                    refund.response_payload_json, 'provider_payload', 'code'
                  ), '')) = :provider_error_code
                  AND attempt.adapter_name = :adapter_name
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = :operation
                  AND attempt.status = 'failed_terminal'
                  AND attempt.provider_call_started_at IS NOT NULL
                  AND attempt.completed_at IS NOT NULL
                  AND (
                    SELECT COUNT(*) FROM external_effect_attempt counted
                    WHERE counted.job_id = job.id
                  ) = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM external_effect_job success
                    WHERE success.id <> job.id
                      AND success.status = 'succeeded'
                      AND success.effect_type = job.effect_type
                      AND COALESCE(job.business_id, '') <> ''
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
                ORDER BY job.id
                FOR UPDATE OF job
                """
            ),
            {
                "effect_type": authorization["effect_type"],
                "adapter_name": authorization["adapter_name"],
                "operation": authorization["operation"],
                "business_type": authorization["business_type"],
                "source_module": authorization["source_module"],
                "source_route": authorization["source_route"],
                "error_code": authorization["error_code"],
                "provider_error_code": authorization["provider_error_code"],
                "worker_generation": authorization["expected_worker_generation"],
                "policy_version": authorization["expected_policy_version"],
                "window_start": authorization["window_start"],
                "window_end": authorization["window_end"],
            },
        ).mappings()
    )


def _fingerprint(row: Mapping[str, Any], *, acknowledgement_type: str) -> str:
    stable = {
        "acknowledgement_type": acknowledgement_type,
        "attempt_completed_at": row["attempt_completed_at"].isoformat(),
        "attempt_id": str(row["attempt_id"]),
        "attempt_started_at": row["attempt_started_at"].isoformat(),
        "business_id": str(row["business_id"]),
        "execution_id": str(row["execution_id"]),
        "job_completed_at": row["job_completed_at"].isoformat() if row["job_completed_at"] else "",
        "job_created_at": row["job_created_at"].isoformat(),
        "job_id": int(row["job_id"]),
        "last_attempt_id": str(row["last_attempt_id"]),
        "provider_error_class": str(row["provider_error_class"]),
        "target_hash_sha256": _sha256(f"{row['target_type']}\0{row['target_id']}"),
    }
    return _sha256(json.dumps(stable, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _existing_acknowledged_rows(
    session,
    *,
    authorization: Mapping[str, Any],
    authorization_base_sha: str,
    provider_error_class: str,
) -> list[Mapping[str, Any]]:
    """Return previously acknowledged rows after revalidating durable linkage.

    An acknowledgement is an append-only historical fact.  A later business
    outcome may make the original candidate query false (for example because a
    same-business success now exists), but that must not make every deployment
    re-authorize the old terminal.  Existing acknowledgements therefore use
    their immutable fingerprint and exact job/attempt links as the idempotency
    authority.  Partial, altered, or orphaned acknowledgement sets fail closed.
    """

    acknowledgement_type = str(authorization["acknowledgement_type"])
    expected_count = int(authorization["maximum_job_count"])
    total_count = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM queue_terminal_acknowledgement
                WHERE acknowledgement_type = :acknowledgement_type
                """
            ),
            {"acknowledgement_type": acknowledgement_type},
        ).scalar_one()
    )
    if total_count == 0:
        return []
    if total_count != expected_count:
        raise RuntimeError(
            f"expected exactly {expected_count} existing {acknowledgement_type} "
            f"acknowledgements; found {total_count}"
        )

    rows = list(
        session.execute(
            text(
                f"""
                SELECT {_COMMON_SELECT}, :provider_error_class AS provider_error_class
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
                  AND acknowledgement.release_sha ~ '^[0-9a-f]{{40}}$'
                  AND acknowledgement.job_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'
                  AND LENGTH(BTRIM(acknowledgement.actor)) > 0
                  AND LENGTH(BTRIM(acknowledgement.reason)) > 0
                  AND job.effect_type = :effect_type
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
                  AND job.provider_result_received = :provider_result_received
                  AND job.worker_generation = :worker_generation
                  AND job.policy_version = :policy_version
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM external_effect_attempt counted_attempt
                      WHERE counted_attempt.job_id = job.id
                  )
                  AND attempt.adapter_name = :adapter_name
                  AND attempt.adapter_mode = 'execute'
                  AND attempt.operation = :operation
                  AND attempt.status = 'failed_terminal'
                  AND attempt.completed_at IS NOT NULL
                ORDER BY job.id
                FOR UPDATE OF acknowledgement, job
                """
            ),
            {
                "acknowledgement_type": acknowledgement_type,
                "authorization_base_sha": authorization_base_sha,
                "confirmation_hash": _sha256(str(authorization["confirmation"])),
                "acknowledged_status": ACKNOWLEDGED_STATUS,
                "error_code": authorization["error_code"],
                "provider_error_class": provider_error_class,
                "effect_type": authorization["effect_type"],
                "adapter_name": authorization["adapter_name"],
                "operation": authorization["operation"],
                "business_type": authorization["business_type"],
                "source_module": authorization["source_module"],
                "source_route": authorization["source_route"],
                "provider_result_received": acknowledgement_type == REFUND_TYPE,
                "worker_generation": authorization["expected_worker_generation"],
                "policy_version": authorization["expected_policy_version"],
            },
        ).mappings()
    )
    if len(rows) != expected_count:
        raise RuntimeError(
            f"existing {acknowledgement_type} acknowledgements failed durable linkage validation"
        )
    return rows


def _acknowledge_rows(
    session,
    *,
    rows: list[Mapping[str, Any]],
    authorization: Mapping[str, Any],
    release_sha: str,
    authorization_base_sha: str,
    actor: str,
    reason: str,
    apply: bool,
) -> tuple[int, int]:
    expected_count = int(authorization["maximum_job_count"])
    if len(rows) != expected_count:
        raise RuntimeError(
            f"expected exactly {expected_count} {authorization['acknowledgement_type']} jobs; found {len(rows)}"
        )
    created_count = 0
    confirmation_hash = _sha256(str(authorization["confirmation"]))
    for row in rows:
        fingerprint = _fingerprint(row, acknowledgement_type=str(authorization["acknowledgement_type"]))
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
        expected_existing = {
            "acknowledgement_id": acknowledgement_id,
            "acknowledgement_type": authorization["acknowledgement_type"],
            "job_execution_id": str(row["execution_id"]),
            "attempt_id": str(row["attempt_id"]),
            "graph_id": None,
            "authorization_base_sha": authorization_base_sha,
            "authorization_confirmation_sha256": confirmation_hash,
            "job_fingerprint_sha256": fingerprint,
            "status": ACKNOWLEDGED_STATUS,
            "job_status": "failed_terminal",
            "error_code": authorization["error_code"],
            "replay_prohibited": True,
            "provider_success_claimed": False,
            "evidence_json": {
                "attempt_count": 1,
                "durable_provider_attempt_count": 1,
                "provider_boundary_recorded": bool(row["provider_boundary_recorded"]),
                "provider_error_class": str(row["provider_error_class"]),
                "provider_success_claimed": False,
                "real_external_call_executed_by_acknowledgement": False,
                "replay_prohibited": True,
                "target_values_redacted": True,
            },
        }
        if existing:
            _full_sha(str(existing.get("release_sha") or ""), name="existing release_sha")
            if any(existing.get(key) != value for key, value in expected_existing.items()):
                raise RuntimeError("existing terminal acknowledgement does not match authorization")
            continue
        if not apply:
            continue
        evidence = dict(expected_existing["evidence_json"])
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
                "acknowledgement_type": authorization["acknowledgement_type"],
                "job_id": int(row["job_id"]),
                "job_execution_id": str(row["execution_id"]),
                "attempt_id": str(row["attempt_id"]),
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
        created_count += 1
    return expected_count, created_count


def acknowledge(
    *,
    manifest_path: Path,
    release_sha: str,
    authorization_base_sha: str,
    private_confirmation: str,
    refund_confirmation: str,
    actor: str,
    reason: str,
    apply: bool,
    acknowledge_refund_histories: bool = True,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    release_sha = _full_sha(release_sha, name="release_sha")
    authorization_base_sha = _full_sha(authorization_base_sha, name="authorization_base_sha")
    if authorization_base_sha != manifest["authorization_base_sha"]:
        raise ValueError("authorization base SHA does not match manifest")
    if private_confirmation != PRIVATE_CONFIRMATION or refund_confirmation != REFUND_CONFIRMATION:
        raise ValueError("confirmation does not match exact production authorization")
    actor = str(actor or "").strip()
    reason = str(reason or "").strip()
    if not actor or not reason:
        raise ValueError("actor and reason are required")
    if apply and str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")

    with get_session_factory()() as session:
        try:
            private_rows = _existing_acknowledged_rows(
                session,
                authorization=manifest[PRIVATE_KEY],
                authorization_base_sha=authorization_base_sha,
                provider_error_class="external_contact_relationship_absent",
            ) or _private_candidates(session, manifest[PRIVATE_KEY])
            if acknowledge_refund_histories:
                refund_rows = _existing_acknowledged_rows(
                    session,
                    authorization=manifest[REFUND_KEY],
                    authorization_base_sha=authorization_base_sha,
                    provider_error_class="merchant_balance_insufficient",
                ) or _refund_candidates(session, manifest[REFUND_KEY])
            else:
                refund_rows = []
            private_count, private_created = _acknowledge_rows(
                session,
                rows=private_rows,
                authorization=manifest[PRIVATE_KEY],
                release_sha=release_sha,
                authorization_base_sha=authorization_base_sha,
                actor=actor,
                reason=reason,
                apply=apply,
            )
            if acknowledge_refund_histories:
                refund_count, refund_created = _acknowledge_rows(
                    session,
                    rows=refund_rows,
                    authorization=manifest[REFUND_KEY],
                    release_sha=release_sha,
                    authorization_base_sha=authorization_base_sha,
                    actor=actor,
                    reason=reason,
                    apply=apply,
                )
            else:
                refund_count, refund_created = 0, 0
            if apply:
                session.commit()
            else:
                session.rollback()
            return {
                "ok": True,
                "applied": apply,
                "private_message_acknowledged_count": private_count,
                "private_message_created_count": private_created,
                "refund_acknowledged_count": refund_count,
                "refund_created_count": refund_created,
                "refund_acknowledgement_required": bool(acknowledge_refund_histories),
                "refund_business_outcome_classified": not acknowledge_refund_histories,
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
        description="Acknowledge one private-message 84061 and three refund NOT_ENOUGH terminals.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--authorization-base-sha", required=True)
    parser.add_argument("--private-confirmation", required=True)
    parser.add_argument("--refund-confirmation", required=True)
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
        private_confirmation=args.private_confirmation,
        refund_confirmation=args.refund_confirmation,
        actor=args.actor,
        reason=args.reason,
        apply=bool(args.apply),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
