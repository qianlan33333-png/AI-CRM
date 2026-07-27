#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.admin_config.application import AdminConfigWriteCommand  # noqa: E402
from aicrm_next.admin_config.repository import AdminConfigRepository  # noqa: E402
from aicrm_next.shared.db_session import get_engine  # noqa: E402
from aicrm_next.shared.queue_provenance import (  # noqa: E402
    POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
    POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
    external_contact_relationship_absent_terminal_sql,
    post_cutover_identity_recovery_predicate_sql,
)
from aicrm_next.shared.sensitive_data import redact_sensitive_data  # noqa: E402
from aicrm_next.shared.wecom_runtime import (  # noqa: E402
    WECOM_ENABLED_EFFECT_TYPES_KEY,
    load_wecom_execution_config,
)


AUTHORIZATION_ENV = "AICRM_QUEUE_ALL_SCOPE_RECOVERY_AUTHORIZED"
CONTACT_DETAIL_EFFECT_TYPE = "wecom.external_contact.detail.fetch"
TARGET_GENERATION = 1
TARGET_SCOPE = "all"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the exact WeCom contact-detail type and recover only the known "
            "bounded pre-provider all-scope gate blocks."
        )
    )
    parser.add_argument("--action", choices=("prepare", "recover"), required=True)
    parser.add_argument("--authorization-base-sha", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--maximum-candidate-count", type=int, default=6)
    parser.add_argument("--wait-timeout-seconds", type=int, default=180)
    parser.add_argument("--apply", action="store_true", default=False)
    return parser.parse_args(argv)


def _validate_authorization(args: argparse.Namespace) -> None:
    base_sha = str(args.authorization_base_sha or "").strip()
    if len(base_sha) != 40 or any(character not in "0123456789abcdef" for character in base_sha):
        raise RuntimeError("--authorization-base-sha must be one full lowercase SHA")
    expected = f"AUTHORIZE AI-CRM QUEUE ALL-SCOPE CUTOVER {base_sha} ON PRODUCTION"
    if str(args.confirmation or "").strip() != expected:
        raise RuntimeError(f"--confirmation must equal {expected}")
    if args.apply and str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")


def _public_config_snapshot() -> dict[str, Any]:
    config = load_wecom_execution_config()
    return {
        "execution_mode": config.execution_mode,
        "real_calls_enabled": config.real_calls_enabled,
        "enabled_effect_type_count": len(config.enabled_effect_types),
        "contact_detail_enabled": CONTACT_DETAIL_EFFECT_TYPE in config.enabled_effect_types,
        "conflict": config.conflict,
        "blocking_reasons": list(config.blocking_reasons),
    }


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    before = load_wecom_execution_config()
    if before.conflict or before.execution_mode != "execute" or not before.real_calls_enabled:
        raise RuntimeError("typed WeCom provider configuration is not ready for exact contact-detail enablement")
    existing_types = list(dict.fromkeys(before.enabled_effect_types))
    changed = CONTACT_DETAIL_EFFECT_TYPE not in existing_types
    if changed:
        existing_types.append(CONTACT_DETAIL_EFFECT_TYPE)
    if args.apply and changed:
        AdminConfigWriteCommand().execute(
            {WECOM_ENABLED_EFFECT_TYPES_KEY: ",".join(existing_types)},
            operator=str(args.actor),
        )
    after = _public_config_snapshot()
    if args.apply and not after["contact_detail_enabled"]:
        raise RuntimeError("contact-detail typed effect did not become enabled")
    return {
        "ok": True,
        "action": "prepare",
        "applied": bool(args.apply and changed),
        "configuration_changed": bool(changed),
        "before_enabled_effect_type_count": len(before.enabled_effect_types),
        "after": after,
        "exact_effect_type": CONTACT_DETAIL_EFFECT_TYPE,
        "real_external_call_executed": False,
    }


def _assert_control(connection: Any) -> dict[str, Any]:
    control = connection.execute(
        text(
            """
            SELECT active_generation, claim_enabled, rollout_mode,
                   policy_version, external_claim_scope
            FROM queue_runtime_control
            WHERE singleton = TRUE
            FOR SHARE
            """
        )
    ).mappings().one()
    expected = {
        "active_generation": TARGET_GENERATION,
        "claim_enabled": True,
        "rollout_mode": "execute",
        "policy_version": POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
        "external_claim_scope": TARGET_SCOPE,
    }
    actual = {key: control.get(key) for key in expected}
    if actual != expected:
        raise RuntimeError("queue runtime is not the authorized generation-1 all-scope owner")
    return actual


def _candidate_rows(
    connection: Any,
    *,
    policy_version: str = POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                f"""
                SELECT job.id, queue.id AS queue_id,
                       job.attempt_count AS pre_provider_attempt_count
                FROM external_effect_job job
                JOIN crm_user_identity_resolution_queue queue
                  ON queue.external_effect_job_id = job.id
                 AND queue.id::TEXT = job.business_id
                WHERE {post_cutover_identity_recovery_predicate_sql(job_alias="job", queue_alias="queue", policy_version=policy_version)}
                ORDER BY job.id
                FOR UPDATE OF job, queue
                """
            )
        ).mappings()
    ]


def _completed_rows(connection: Any) -> list[int]:
    rows = connection.execute(
        text(
            """
            SELECT job.id
            FROM external_effect_job job
            JOIN crm_user_identity_resolution_queue queue
              ON queue.external_effect_job_id = job.id
             AND queue.id::TEXT = job.business_id
            WHERE job.effect_type = :effect_type
              AND job.adapter_name = 'wecom_external_contact_detail'
              AND job.operation = 'get_external_contact_detail'
              AND job.target_type = 'external_user'
              AND job.business_type = 'identity_resolution_queue'
              AND job.source_module = 'aicrm_next.crm.identity_contact.resolution_effects'
              AND job.source_route IN (
                  'channel_entry.identity_resolution.enqueue',
                  'message_archive.identity_resolution.enqueue'
              )
              AND job.status = 'succeeded'
              AND job.attempt_count BETWEEN 2 AND 3
              AND job.worker_generation = :generation
              AND job.policy_version = :policy_version
              AND job.side_effect_executed = TRUE
              AND job.provider_result_received = TRUE
              AND job.provider_call_started_at IS NOT NULL
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt attempt
                  WHERE attempt.job_id = job.id
              ) = job.attempt_count
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt attempt
                  WHERE attempt.job_id = job.id
                    AND attempt.status = 'blocked'
                    AND attempt.error_code = 'effect_type_not_allowed'
                    AND attempt.adapter_name = 'wecom_external_contact_detail'
                    AND attempt.operation = 'get_external_contact_detail'
                    AND attempt.adapter_mode = 'disabled'
                    AND attempt.provider_call_started_at IS NULL
                    AND COALESCE(attempt.provider_result_json, '{}'::jsonb) = '{}'::jsonb
                    AND attempt.worker_generation IN (0, 1)
              ) = job.attempt_count - 1
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt attempt
                  WHERE attempt.job_id = job.id
                    AND attempt.status = 'succeeded'
                    AND attempt.adapter_name = 'wecom_external_contact_detail'
                    AND attempt.operation = 'get_external_contact_detail'
                    AND attempt.adapter_mode = 'execute'
                    AND attempt.provider_call_started_at IS NOT NULL
                    AND attempt.worker_generation = :generation
                    AND COALESCE((attempt.response_summary_json->>'errcode')::INTEGER, -1) = 0
                    AND COALESCE((attempt.response_summary_json->>'real_external_call_executed')::BOOLEAN, FALSE)
                    AND attempt.provider_result_consumed_at IS NOT NULL
              ) = 1
              AND queue.status IN ('resolved', 'conflict')
              AND EXISTS (
                  SELECT 1
                  FROM identity_resolution_completion_receipt receipt
                  WHERE receipt.external_effect_job_id = job.id
                    AND receipt.queue_id = queue.id
                    AND receipt.result_status IN ('resolved', 'conflict')
              )
            ORDER BY job.id
            """
        ),
        {
            "effect_type": CONTACT_DETAIL_EFFECT_TYPE,
            "generation": TARGET_GENERATION,
            "policy_version": POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
        },
    ).scalars().all()
    return [int(item) for item in rows]


def _business_negative_rows(connection: Any) -> list[int]:
    rows = connection.execute(
        text(
            f"""
            SELECT job.id
            FROM external_effect_job job
            WHERE {external_contact_relationship_absent_terminal_sql(job_alias="job")}
            ORDER BY job.id
            """
        )
    ).scalars().all()
    return [int(item) for item in rows]


def _reopen_candidates(
    connection: Any,
    rows: list[dict[str, Any]],
    *,
    policy_version: str = POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
) -> dict[str, int]:
    job_ids = [int(row["id"]) for row in rows]
    queue_ids = [int(row["queue_id"]) for row in rows]
    if not job_ids:
        return {"job_count": 0, "queue_count": 0, "runtime_count": 0}
    jobs = connection.execute(
        text(
            """
            UPDATE external_effect_job
            SET status = 'queued',
                available_at = CURRENT_TIMESTAMP,
                scheduled_at = LEAST(scheduled_at, CURRENT_TIMESTAMP),
                next_retry_at = NULL,
                lease_token = '',
                lease_expires_at = NULL,
                locked_by = '',
                locked_at = NULL,
                dispatch_started_at = NULL,
                heartbeat_at = NULL,
                completed_at = NULL,
                last_error_code = '',
                last_error_message = '',
                result_summary_json = jsonb_build_object(
                    'post_cutover_pre_provider_recovery', TRUE,
                    'predicate_version', CAST(:predicate_version AS TEXT)
                ),
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(:job_ids)
              AND status = 'blocked'
              AND attempt_count BETWEEN 1 AND 2
              AND worker_generation = :generation
              AND policy_version = :policy_version
              AND provider_call_started_at IS NULL
              AND side_effect_executed = FALSE
              AND provider_result_received = FALSE
            RETURNING id
            """
        ),
        {
            "job_ids": job_ids,
            "generation": TARGET_GENERATION,
            "policy_version": policy_version,
            "predicate_version": POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
        },
    ).scalars().all()
    queues = connection.execute(
        text(
            """
            UPDATE crm_user_identity_resolution_queue
            SET status = 'pending',
                hold_reason = '',
                held_at = NULL,
                next_attempt_at = CURRENT_TIMESTAMP,
                last_error = '',
                completed_at = NULL,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(:queue_ids)
              AND external_effect_job_id = ANY(:job_ids)
              AND status = 'held'
              AND hold_reason = 'effect_type_not_allowed'
              AND last_error = 'effect_type_not_allowed'
            RETURNING id
            """
        ),
        {"queue_ids": queue_ids, "job_ids": job_ids},
    ).scalars().all()
    runtime_count = connection.execute(
        text(
            """
            UPDATE automation_channel_entry_runtime
            SET identity_status = 'pending',
                identity_hold_reason = '',
                identity_held_at = NULL,
                identity_next_attempt_at = CURRENT_TIMESTAMP,
                identity_last_error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE identity_external_effect_job_id = ANY(:job_ids)
              AND identity_status = 'held'
              AND identity_hold_reason = 'effect_type_not_allowed'
            """
        ),
        {"job_ids": job_ids},
    ).rowcount
    if len(jobs) != len(job_ids) or len(queues) != len(queue_ids):
        raise RuntimeError("post-cutover contact-detail recovery CAS lost")
    return {
        "job_count": len(jobs),
        "queue_count": len(queues),
        "runtime_count": int(runtime_count or 0),
    }


def _progress(job_ids: list[int]) -> dict[str, int]:
    if not job_ids:
        return {
            "job_count": 0,
            "succeeded_count": 0,
            "business_negative_count": 0,
            "active_count": 0,
            "unsafe_terminal_count": 0,
            "provider_boundary_attempt_count": 0,
            "duplicate_provider_attempt_job_count": 0,
            "completion_receipt_count": 0,
            "resolved_count": 0,
            "conflict_count": 0,
        }
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS job_count,
                    COUNT(*) FILTER (WHERE job.status = 'succeeded') AS succeeded_count,
                    COUNT(*) FILTER (
                        WHERE ({external_contact_relationship_absent_terminal_sql(job_alias="job")})
                    ) AS business_negative_count,
                    COUNT(*) FILTER (
                        WHERE job.status IN ('queued', 'dispatching', 'failed_retryable')
                    ) AS active_count,
                    COUNT(*) FILTER (
                        WHERE job.status IN ('blocked', 'failed_terminal', 'unknown_after_dispatch')
                          AND NOT ({external_contact_relationship_absent_terminal_sql(job_alias="job")})
                    ) AS unsafe_terminal_count,
                    COALESCE(SUM((
                        SELECT COUNT(*)
                        FROM external_effect_attempt attempt
                        WHERE attempt.job_id = job.id
                          AND attempt.provider_call_started_at IS NOT NULL
                    )), 0) AS provider_boundary_attempt_count,
                    COUNT(*) FILTER (WHERE (
                        SELECT COUNT(*)
                        FROM external_effect_attempt attempt
                        WHERE attempt.job_id = job.id
                          AND attempt.provider_call_started_at IS NOT NULL
                    ) > 1) AS duplicate_provider_attempt_job_count,
                    COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1
                        FROM identity_resolution_completion_receipt receipt
                        WHERE receipt.external_effect_job_id = job.id
                    )) AS completion_receipt_count,
                    COUNT(*) FILTER (WHERE queue.status = 'resolved') AS resolved_count,
                    COUNT(*) FILTER (WHERE queue.status = 'conflict') AS conflict_count
                FROM external_effect_job job
                JOIN crm_user_identity_resolution_queue queue
                  ON queue.external_effect_job_id = job.id
                 AND queue.id::TEXT = job.business_id
                WHERE job.id = ANY(:job_ids)
                """
            ),
            {"job_ids": job_ids},
        ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _wait_for_completion(job_ids: list[int], timeout_seconds: int) -> dict[str, int]:
    deadline = time.monotonic() + max(1, int(timeout_seconds or 180))
    while True:
        progress = _progress(job_ids)
        if progress["unsafe_terminal_count"]:
            raise RuntimeError("contact-detail recovery reached an unsafe terminal state")
        complete = (
            progress["job_count"] == len(job_ids)
            and progress["succeeded_count"] + progress["business_negative_count"] == len(job_ids)
            and progress["provider_boundary_attempt_count"] == len(job_ids)
            and progress["duplicate_provider_attempt_job_count"] == 0
            and progress["completion_receipt_count"] == progress["succeeded_count"]
            and progress["resolved_count"] + progress["conflict_count"] == progress["succeeded_count"]
        )
        if complete:
            with get_engine().connect() as connection:
                verified = set(_completed_rows(connection)) | set(_business_negative_rows(connection))
            if set(job_ids).issubset(verified):
                return progress
            raise RuntimeError("contact-detail recovery completion proof did not match strict provenance")
        if time.monotonic() >= deadline:
            raise RuntimeError("contact-detail recovery did not settle before the bounded timeout")
        time.sleep(1)


def _recover(args: argparse.Namespace) -> dict[str, Any]:
    config = _public_config_snapshot()
    if not config["contact_detail_enabled"] or config["execution_mode"] != "execute" or not config["real_calls_enabled"]:
        raise RuntimeError("exact contact-detail typed execution is not ready")
    maximum = max(0, int(args.maximum_candidate_count))
    with get_engine().begin() as connection:
        control = _assert_control(connection)
        rows = _candidate_rows(connection)
        if len(rows) > maximum:
            raise RuntimeError("strict contact-detail recovery candidate count exceeds reviewed maximum")
        candidate_ids = [int(row["id"]) for row in rows]
        candidate_attempt_histogram = {
            str(attempt_count): sum(
                1 for row in rows
                if int(row["pre_provider_attempt_count"]) == attempt_count
            )
            for attempt_count in (1, 2)
        }
        completed_before = _completed_rows(connection)
        business_negative_before = _business_negative_rows(connection)
        if not args.apply:
            return {
                "ok": True,
                "action": "recover",
                "applied": False,
                "candidate_count": len(candidate_ids),
                "candidate_attempt_histogram": candidate_attempt_histogram,
                "previously_completed_count": len(completed_before),
                "business_negative_count": len(business_negative_before),
                "maximum_candidate_count": maximum,
                "predicate_version": POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
                "control": control,
                "contains_raw_target_or_job_ids": False,
                "real_external_call_executed": False,
            }
        reopened = _reopen_candidates(connection, rows)
    if candidate_ids:
        AdminConfigRepository().insert_audit_log(
            operator=str(args.actor),
            action_type="recover_exact_pre_provider_block",
            target_type="queue_all_scope_contact_detail",
            target_id=CONTACT_DETAIL_EFFECT_TYPE,
            before={
                "candidate_count": len(candidate_ids),
                "candidate_attempt_histogram": candidate_attempt_histogram,
                "predicate_version": POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
                "provider_boundary_attempt_count": 0,
            },
            after={**reopened, "reason": str(args.reason)},
        )
        progress = _wait_for_completion(candidate_ids, int(args.wait_timeout_seconds))
        real_external_call_executed = True
    else:
        progress = _progress([])
        real_external_call_executed = False
    with get_engine().connect() as connection:
        completed_after = _completed_rows(connection)
        business_negative_after = _business_negative_rows(connection)
    if len(completed_after) + len(business_negative_after) < len(completed_before) + len(business_negative_before) + len(candidate_ids):
        raise RuntimeError("strict settled contact-detail recovery count regressed")
    return {
        "ok": True,
        "action": "recover",
        "applied": bool(candidate_ids),
        "candidate_count": len(candidate_ids),
        "candidate_attempt_histogram": candidate_attempt_histogram,
        "previously_completed_count": len(completed_before),
        "completed_count": len(completed_after),
        "business_negative_count": len(business_negative_after),
        "maximum_candidate_count": maximum,
        "predicate_version": POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
        "control": control,
        "reopened": reopened,
        "progress": progress,
        "contains_raw_target_or_job_ids": False,
        "real_external_call_executed": real_external_call_executed,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_authorization(args)
    payload = _prepare(args) if args.action == "prepare" else _recover(args)
    print(
        json.dumps(
            redact_sensitive_data(payload),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
