from __future__ import annotations

from collections import Counter

from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectJob, ExternalEffectService
from aicrm_next.platform.shared.typing import JsonDict

from .effect_enqueue import USER_OPS_BATCH_SEND_BUSINESS_TYPE


def _text(value: object) -> str:
    return str(value or "").strip()


def _mask_external_userid(value: str) -> str:
    text = _text(value)
    if not text:
        return ""
    if len(text) <= 6:
        return text[:1] + "***" + text[-1:]
    return text[:4] + "***" + text[-3:]


def _job_external_userid(job: ExternalEffectJob) -> str:
    payload = dict(job.payload_json or {})
    external_userids = [str(item or "").strip() for item in list(payload.get("external_userids") or []) if str(item or "").strip()]
    return external_userids[0] if external_userids else ""


def _job_target_unionid(job: ExternalEffectJob) -> str:
    payload = dict(job.payload_json or {})
    return _text(payload.get("target_unionid") or payload.get("unionid") or job.target_id)


def _latest_attempt(service: ExternalEffectService, job_id: int) -> JsonDict:
    attempts = service.list_attempts(job_id)
    return attempts[-1].to_dict() if attempts else {}


def derive_send_record_status(counts: Counter[str], *, total: int) -> str:
    if total <= 0:
        return "failed"
    failed_count = counts["failed"] + counts["failed_retryable"] + counts["failed_terminal"]
    terminal_or_done = counts["succeeded"] + failed_count + counts["blocked"] + counts["cancelled"]
    if counts["succeeded"] == total:
        return "succeeded"
    if counts["blocked"] == total:
        return "blocked"
    if counts["cancelled"] == total:
        return "cancelled"
    if failed_count == total:
        return "failed"
    if counts["dispatching"]:
        return "dispatching"
    if terminal_or_done:
        return "partially_succeeded"
    if counts["queued"]:
        return "queued"
    return "planned"


def build_send_record_external_effect_projection(
    record_id: str,
    *,
    service: ExternalEffectService | None = None,
    job_ids: list[int] | None = None,
) -> JsonDict:
    effect_service = service or ExternalEffectService()
    jobs_by_id: dict[int, ExternalEffectJob] = {}
    if job_ids:
        for job_id in job_ids:
            job = effect_service.get(int(job_id))
            if job is not None:
                jobs_by_id[job.id] = job
    listed_jobs, _ = effect_service.list_jobs(
        {
            "business_type": USER_OPS_BATCH_SEND_BUSINESS_TYPE,
            "business_id": record_id,
        },
        limit=200,
    )
    for job in listed_jobs:
        jobs_by_id[job.id] = job
    jobs = sorted(jobs_by_id.values(), key=lambda item: item.id)
    counts: Counter[str] = Counter(job.status for job in jobs)
    failed_count = counts["failed"] + counts["failed_retryable"] + counts["failed_terminal"]
    task_results: list[JsonDict] = []
    for job in jobs:
        latest = _latest_attempt(effect_service, job.id)
        external_userid = _job_external_userid(job)
        task_results.append(
            {
                "external_effect_job_id": job.id,
                "status": job.status,
                "target_id": job.target_id,
                "target_unionid": _job_target_unionid(job),
                "external_userid_masked": _mask_external_userid(external_userid),
                "attempt_count": int(job.attempt_count or 0),
                "last_error_code": job.last_error_code or latest.get("error_code") or "",
                "last_error_message": _text(job.last_error_message or latest.get("error_message"))[:200],
                "latest_attempt_id": latest.get("attempt_id") or "",
            }
        )
    status = derive_send_record_status(counts, total=len(jobs))
    return {
        "record_id": record_id,
        "status": status,
        "planned_count": counts["planned"],
        "queued_count": counts["queued"],
        "dispatching_count": counts["dispatching"],
        "succeeded_count": counts["succeeded"],
        "failed_count": failed_count,
        "blocked_count": counts["blocked"],
        "cancelled_count": counts["cancelled"],
        "total_count": len(jobs),
        "external_effect_job_ids": [job.id for job in jobs],
        "by_status": {key: value for key, value in sorted(counts.items())},
        "task_results": task_results,
    }
