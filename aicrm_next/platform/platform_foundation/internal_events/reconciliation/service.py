from __future__ import annotations

from typing import Any

from aicrm_next.platform.platform_foundation.external_effects import WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH
from aicrm_next.platform.platform_foundation.external_effects.models import ExternalEffectJob
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.external_effects.view_model import external_effect_job_list_item

from ..config import (
    consumer_metadata,
    worker_allows,
)
from ..models import InternalEvent, InternalEventConsumerAttempt, InternalEventConsumerRun
from ..repository import InternalEventRepository, build_internal_event_repository

PLANNED_EFFECT_STATUSES = {"planned", "approved", "queued", "dispatching", "succeeded", "failed_retryable"}
BLOCKED_EFFECT_STATUSES = {"blocked", "failed_terminal", "cancelled", "expired"}
OPEN_CONSUMER_STATUSES = {"pending", "running", "failed_retryable", "failed_terminal", "blocked"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


class InternalEventReconciliationService:
    def __init__(
        self,
        repository: InternalEventRepository | None = None,
        external_effect_service: ExternalEffectService | None = None,
    ) -> None:
        self._repo = repository or build_internal_event_repository()
        self._external_effects = external_effect_service or ExternalEffectService()

    def build_event_reconciliation(self, event_id: str) -> dict[str, Any]:
        event = self._repo.get_event(event_id)
        if not event:
            return {}
        runs, _ = self._repo.list_consumer_runs({"event_id": event.event_id}, limit=200)
        attempts = self._repo.list_attempts(event_id=event.event_id)
        job_reuse = self._job_reuse_flags(runs, attempts)
        jobs = self._find_external_effect_jobs(event, runs, attempts)
        external_effects = [self._external_effect_item(job, event=event, reused=job_reuse.get(job.id, False)) for job in jobs]
        consumer_states = [self._consumer_state(event, run) for run in runs]
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "consumer_states": consumer_states,
            "external_effects": external_effects,
            "derived_status": self._derived_status(event, consumer_states, external_effects),
        }

    def _consumer_state(self, event: InternalEvent, run: InternalEventConsumerRun) -> dict[str, Any]:
        metadata = consumer_metadata(run.consumer_name)
        return {
            "consumer_name": run.consumer_name,
            "status": run.status,
            "metadata": metadata,
            "why_pending": self._why_pending(event, run, metadata),
        }

    def _why_pending(self, event: InternalEvent, run: InternalEventConsumerRun, metadata: dict[str, str]) -> dict[str, Any]:
        if metadata.get("type") == "placeholder":
            return {
                "category": "placeholder_not_configured",
                "message": "未配置，不计入业务待办",
                "actionable": False,
            }
        if run.status in {"skipped", "succeeded"}:
            return {
                "category": "skipped_expected",
                "message": "该 consumer 已完成或按预期跳过，不代表业务未执行",
                "actionable": False,
            }
        if run.status in OPEN_CONSUMER_STATUSES and not self._worker_allows(event.event_type, run.consumer_name):
            return {
                "category": "allowlist_blocked",
                "message": "灰度执行白名单未开放；这是保留的迁移观察记录，不计入当前业务待办，也不会自动重放",
                "actionable": False,
                "rollout_gated": True,
            }
        if run.status in {"pending", "running"}:
            return {
                "category": "normal_pending",
                "message": "等待 worker 扫描或正在执行中",
                "actionable": True,
            }
        return {
            "category": "normal_pending",
            "message": "该状态需要按队列/错误信息继续排查",
            "actionable": True,
        }

    def _worker_allows(self, event_type: str, consumer_name: str) -> bool:
        return worker_allows(event_type, consumer_name)

    def _find_external_effect_jobs(
        self,
        event: InternalEvent,
        runs: list[InternalEventConsumerRun],
        attempts: list[InternalEventConsumerAttempt],
    ) -> list[ExternalEffectJob]:
        by_id: dict[int, ExternalEffectJob] = {}
        for job_id in self._candidate_job_ids(runs, attempts):
            job = self._external_effects.get(job_id)
            if job:
                by_id[job.id] = job
        for filters in self._candidate_job_filters(event):
            jobs, _ = self._external_effects.list_jobs(filters, limit=50)
            for job in jobs:
                by_id[job.id] = job
        return sorted(by_id.values(), key=lambda job: (job.created_at or "", int(job.id or 0)), reverse=True)

    def _candidate_job_ids(self, runs: list[InternalEventConsumerRun], attempts: list[InternalEventConsumerAttempt]) -> list[int]:
        ids: list[int] = []
        for payload in [run.result_summary_json for run in runs] + [attempt.response_summary_json for attempt in attempts]:
            job_id = self._int_id(_payload_dict(payload).get("external_effect_job_id"))
            if job_id and job_id not in ids:
                ids.append(job_id)
        return ids

    def _job_reuse_flags(
        self,
        runs: list[InternalEventConsumerRun],
        attempts: list[InternalEventConsumerAttempt],
    ) -> dict[int, bool]:
        flags: dict[int, bool] = {}
        for payload in [run.result_summary_json for run in runs] + [attempt.response_summary_json for attempt in attempts]:
            data = _payload_dict(payload)
            job_id = self._int_id(data.get("external_effect_job_id"))
            if job_id:
                flags[job_id] = bool(flags.get(job_id) or _bool(data.get("external_effect_job_reused")))
        return flags

    def _candidate_job_filters(self, event: InternalEvent) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [{"source_event_id": event.event_id}]
        if _text(event.trace_id):
            filters.append({"trace_id": event.trace_id})
        if event.event_type == "questionnaire.submitted":
            business_id = self._questionnaire_business_id(event)
            if _text(event.aggregate_id) and business_id:
                filters.append(
                    {
                        "effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
                        "target_type": "questionnaire_submission",
                        "target_id": event.aggregate_id,
                        "business_type": "questionnaire",
                        "business_id": business_id,
                    }
                )
        return filters

    def _questionnaire_business_id(self, event: InternalEvent) -> str:
        payload = _payload_dict(event.payload_json)
        questionnaire = _payload_dict(payload.get("questionnaire"))
        submission = _payload_dict(payload.get("submission"))
        return _text(questionnaire.get("id") or submission.get("questionnaire_id"))

    def _external_effect_item(self, job: ExternalEffectJob, *, event: InternalEvent, reused: bool) -> dict[str, Any]:
        item = external_effect_job_list_item(job)
        item.pop("payload_json_redacted", None)
        source = self._effect_source(job, event)
        real_external_call_executed = False
        for attempt in self._external_effects.list_attempts(job.id):
            response = _payload_dict(attempt.response_summary_json)
            real_external_call_executed = bool(real_external_call_executed or _bool(response.get("real_external_call_executed")))
        return {
            **item,
            "job_id": job.id,
            "job_status": job.status,
            "reused": bool(reused),
            "created": not bool(reused),
            "source": source,
            "real_external_call_executed": real_external_call_executed,
            "blocked_reason": " ".join(part for part in (job.last_error_code, job.last_error_message) if _text(part)),
        }

    def _effect_source(self, job: ExternalEffectJob, event: InternalEvent) -> str:
        source_module = _text(job.source_module)
        if source_module == "questionnaire.external_push":
            return "submit_path"
        if source_module == "questionnaire.external_push_logs" or job.target_type == "questionnaire_external_push_log":
            return "legacy"
        if source_module.startswith("platform_foundation.internal_events") or job.source_event_id == event.event_id:
            return "consumer"
        return "legacy" if "legacy" in source_module else "submit_path"

    def _derived_status(
        self,
        event: InternalEvent,
        consumer_states: list[dict[str, Any]],
        external_effects: list[dict[str, Any]],
    ) -> str:
        if any(effect.get("reused") for effect in external_effects):
            return "effect_reused"
        statuses = {_text(effect.get("job_status")) for effect in external_effects}
        if statuses & PLANNED_EFFECT_STATUSES:
            return "effect_planned"
        if statuses & BLOCKED_EFFECT_STATUSES:
            return "effect_blocked"
        actionable = [state for state in consumer_states if (state.get("why_pending") or {}).get("actionable")]
        if not actionable and consumer_states:
            return "noop"
        if event.event_type == "questionnaire.submitted":
            return "submitted_only"
        return "noop"

    def _int_id(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
