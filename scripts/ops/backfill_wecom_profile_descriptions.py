#!/usr/bin/env python3
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from sqlalchemy import text

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.channels.channel_entry.profile_description_backfill import (  # noqa: E402
    PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
    PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
    PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
)
from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext  # noqa: E402
from aicrm_next.platform.platform_foundation.external_effects import (  # noqa: E402
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    ExternalEffectService,
)
from aicrm_next.platform.platform_foundation.external_effects.realtime import wake_external_effect_job  # noqa: E402
from aicrm_next.platform.shared.db_session import get_session_factory  # noqa: E402
from aicrm_next.platform.shared.wecom_runtime import load_wecom_execution_config  # noqa: E402


AUTHORIZATION_ENV = "AICRM_PROFILE_DESCRIPTION_BACKFILL_AUTHORIZED"
CONFIRMATION = "EXECUTE_WECOM_PROFILE_DESCRIPTION_EMPTY_BACKFILL_V1"
MAX_CANDIDATE_CONTACTS = 25_000
DEFAULT_BATCH_SIZE = 500


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview, enqueue, or inspect the exact empty WeCom profile-description backfill."
    )
    parser.add_argument("--action", choices=("preview", "enqueue", "status"), required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--limit", type=int, default=MAX_CANDIDATE_CONTACTS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--operator", default="github-actions-profile-description-backfill")
    return parser.parse_args(argv)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _target_digest(*values: str) -> str:
    material = "\0".join(_text(value) for value in values)
    return sha256(material.encode("utf-8")).hexdigest()[:32]


def _assert_execute_authorized(args: argparse.Namespace) -> None:
    if args.action != "enqueue":
        return
    if _text(args.confirmation) != CONFIRMATION:
        raise RuntimeError(f"--confirmation must equal {CONFIRMATION}")
    if _text(os.getenv(AUTHORIZATION_ENV)) != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")


def _assert_provider_config() -> dict[str, Any]:
    config = load_wecom_execution_config()
    required = {WECOM_EXTERNAL_CONTACT_DETAIL_FETCH, "wecom.profile.update"}
    missing = sorted(required.difference(config.enabled_effect_types))
    if config.conflict or config.execution_mode != "execute" or not config.real_calls_enabled or missing:
        raise RuntimeError("typed WeCom detail/profile execution configuration is not ready")
    return {
        "execution_mode": config.execution_mode,
        "real_calls_enabled": config.real_calls_enabled,
        "required_effect_types_enabled": True,
        "configuration_conflict": config.conflict,
    }


def _assert_queue_control(session: Any) -> dict[str, Any]:
    row = session.execute(
        text(
            """
            SELECT active_generation, claim_enabled, rollout_mode, external_claim_scope
            FROM queue_runtime_control
            WHERE singleton = TRUE
            """
        )
    ).mappings().one()
    result = dict(row)
    if not (
        int(result.get("active_generation") or 0) > 0
        and result.get("claim_enabled") is True
        and _text(result.get("rollout_mode")) == "execute"
        and _text(result.get("external_claim_scope")) == "all"
    ):
        raise RuntimeError("canonical all-scope queue runtime is not ready")
    return result


def _population(session: Any) -> dict[str, int]:
    row = session.execute(
        text(
            """
            SELECT COUNT(*) AS active_relation_count,
                   COUNT(*) FILTER (
                       WHERE BTRIM(COALESCE(description, '')) = ''
                   ) AS active_empty_relation_count,
                   COUNT(DISTINCT external_userid) FILTER (
                       WHERE BTRIM(COALESCE(description, '')) = ''
                   ) AS active_empty_contact_count,
                   COUNT(*) FILTER (
                       WHERE BTRIM(COALESCE(description, '')) = ''
                         AND BTRIM(COALESCE(external_userid, '')) = ''
                   ) AS missing_external_userid_count,
                   COUNT(*) FILTER (
                       WHERE BTRIM(COALESCE(description, '')) = ''
                         AND BTRIM(COALESCE(user_id, '')) = ''
                   ) AS missing_owner_userid_count
            FROM wecom_external_contact_follow_users
            WHERE relation_status = 'active'
            """
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in dict(row).items()}


def _candidate_rows(session: Any, *, limit: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT follow.corp_id,
                   follow.external_userid,
                   ARRAY_AGG(DISTINCT follow.user_id ORDER BY follow.user_id) AS owner_userids,
                   COUNT(*) AS relation_count
            FROM wecom_external_contact_follow_users follow
            WHERE follow.relation_status = 'active'
              AND BTRIM(COALESCE(follow.external_userid, '')) <> ''
              AND BTRIM(COALESCE(follow.user_id, '')) <> ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM external_effect_job job
                  WHERE job.business_type = :business_type
                    AND job.business_id = :run_id
                    AND job.target_type = 'external_user'
                    AND job.target_id = follow.external_userid
                    AND COALESCE(job.payload_json->>'corp_id', '') = COALESCE(follow.corp_id, '')
              )
            GROUP BY follow.corp_id, follow.external_userid
            ORDER BY MIN(follow.id)
            LIMIT :limit
            """
        ),
        {
            "business_type": PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
            "run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
            "limit": limit,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _job_status(session: Any) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT business_type, status, COUNT(*) AS count
            FROM external_effect_job
            WHERE business_id = :run_id
              AND business_type IN (:detail_type, :update_type)
            GROUP BY business_type, status
            ORDER BY business_type, status
            """
        ),
        {
            "run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
            "detail_type": PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
            "update_type": PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
        },
    ).mappings()
    grouped: dict[str, dict[str, int]] = {
        "detail": {},
        "update": {},
    }
    for row in rows:
        bucket = "detail" if row["business_type"] == PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE else "update"
        grouped[bucket][_text(row["status"])] = int(row["count"] or 0)
    return grouped


def _remaining_breakdown(session: Any) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            SELECT CASE
                       WHEN detail.id IS NULL THEN 'not_enqueued'
                       WHEN detail.status <> 'succeeded' THEN 'detail_' || detail.status
                       WHEN profile.id IS NULL THEN 'detail_succeeded_without_profile_update'
                       ELSE 'profile_' || profile.status
                   END AS state,
                   COUNT(*) AS count
            FROM wecom_external_contact_follow_users follow
            LEFT JOIN external_effect_job detail
              ON detail.business_type = :detail_type
             AND detail.business_id = :run_id
             AND detail.target_type = 'external_user'
             AND detail.target_id = follow.external_userid
             AND COALESCE(detail.payload_json->>'corp_id', '') = COALESCE(follow.corp_id, '')
            LEFT JOIN external_effect_job profile
              ON profile.business_type = :update_type
             AND profile.business_id = :run_id
             AND profile.payload_json->>'external_userid' = follow.external_userid
             AND profile.payload_json->>'follow_user_userid' = follow.user_id
            WHERE follow.relation_status = 'active'
              AND BTRIM(COALESCE(follow.description, '')) = ''
            GROUP BY state
            ORDER BY state
            """
        ),
        {
            "run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
            "detail_type": PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
            "update_type": PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
        },
    ).mappings()
    return {_text(row["state"]): int(row["count"] or 0) for row in rows}


def _candidate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hasher = sha256()
    relation_count = 0
    for row in rows:
        owners = sorted(_text(value) for value in list(row.get("owner_userids") or []) if _text(value))
        relation_count += len(owners)
        hasher.update(_target_digest(_text(row.get("corp_id")), _text(row.get("external_userid")), *owners).encode())
    return {
        "candidate_contact_count": len(rows),
        "candidate_relation_count": relation_count,
        "target_set_digest": hasher.hexdigest(),
        "contains_raw_target_identifiers": False,
    }


def _enqueue(rows: list[dict[str, Any]], *, batch_size: int, operator: str) -> dict[str, int]:
    service = ExternalEffectService()
    created_count = 0
    existing_count = 0
    last_job_id = 0
    factory = get_session_factory()
    for offset in range(0, len(rows), batch_size):
        with factory() as session:
            _assert_queue_control(session)
            for row in rows[offset : offset + batch_size]:
                external = _text(row.get("external_userid"))
                corp_id = _text(row.get("corp_id"))
                owners = list(
                    dict.fromkeys(
                        _text(value)
                        for value in list(row.get("owner_userids") or [])
                        if _text(value)
                    )
                )
                digest = _target_digest(corp_id, external)
                planned = service.plan_effect(
                    effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
                    adapter_name="wecom_external_contact_detail",
                    operation="get_external_contact_detail",
                    target_type="external_user",
                    target_id=external,
                    payload={
                        "corp_id": corp_id,
                        "external_userid": external,
                        "owner_userids": owners,
                        "only_if_live_description_empty": True,
                        "backfill_run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
                    },
                    payload_summary={
                        "external_userid_present": True,
                        "candidate_owner_count": len(owners),
                        "only_if_live_description_empty": True,
                        "target_digest": digest,
                        "backfill_run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
                        "real_external_call_executed": False,
                    },
                    context=CommandContext(
                        actor_id=operator,
                        actor_type="system",
                        request_id=PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
                        trace_id=f"profile-backfill-{digest}",
                        source_route="scripts/ops/backfill_wecom_profile_descriptions.py",
                    ),
                    business_type=PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
                    business_id=PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
                    source_module="scripts.ops.backfill_wecom_profile_descriptions",
                    risk_level="medium",
                    execution_mode="execute",
                    status="queued",
                    priority=300,
                    max_attempts=5,
                    idempotency_key=f"wecom-profile-description-detail:{digest}:v1",
                    execution_id=f"exe_profile_description_detail_{uuid4().hex}",
                    lane="wecom_interactive",
                    ordering_key=f"external_user:{external}",
                    fairness_key="wecom-profile-description-backfill",
                    connection=session,
                )
                last_job_id = int(planned.get("id") or 0)
                if planned.get("created_on_plan"):
                    created_count += 1
                else:
                    existing_count += 1
            session.commit()
        if last_job_id:
            wake_external_effect_job(
                last_job_id,
                reason="wecom_profile_description_backfill",
                effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
            )
    return {
        "created_detail_job_count": created_count,
        "existing_detail_job_count": existing_count,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    limit = max(1, min(int(args.limit or MAX_CANDIDATE_CONTACTS), MAX_CANDIDATE_CONTACTS))
    batch_size = max(1, min(int(args.batch_size or DEFAULT_BATCH_SIZE), DEFAULT_BATCH_SIZE))
    _assert_execute_authorized(args)
    provider_config = _assert_provider_config()
    with get_session_factory()() as session:
        queue_control = _assert_queue_control(session)
        population = _population(session)
        candidates = _candidate_rows(session, limit=limit) if args.action != "status" else []
        before_status = _job_status(session)
        remaining_before = _remaining_breakdown(session)
    summary = _candidate_summary(candidates)
    if args.action == "enqueue" and len(candidates) >= MAX_CANDIDATE_CONTACTS:
        raise RuntimeError("candidate contact count reached the reviewed safety ceiling")

    enqueue_result = {
        "created_detail_job_count": 0,
        "existing_detail_job_count": 0,
    }
    if args.action == "enqueue":
        enqueue_result = _enqueue(
            candidates,
            batch_size=batch_size,
            operator=_text(args.operator) or "github-actions-profile-description-backfill",
        )
    with get_session_factory()() as session:
        after_status = _job_status(session)
        after_population = _population(session)
        remaining_after = _remaining_breakdown(session)
    return {
        "ok": True,
        "action": args.action,
        "run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
        "provider_config": provider_config,
        "queue_control": queue_control,
        "population": population,
        "candidate_summary": summary,
        "enqueue": enqueue_result,
        "job_status_before": before_status,
        "job_status_after": after_status,
        "remaining_breakdown_before": remaining_before,
        "remaining_breakdown_after": remaining_after,
        "population_after": after_population,
        "database_write_executed": args.action == "enqueue" and enqueue_result["created_detail_job_count"] > 0,
        "real_external_call_executed": False,
        "contains_raw_target_identifiers": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = run(args)
    except Exception as exc:
        payload = {
            "ok": False,
            "action": args.action,
            "run_id": PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
            "error": exc.__class__.__name__,
            "real_external_call_executed": False,
            "contains_raw_target_identifiers": False,
        }
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
