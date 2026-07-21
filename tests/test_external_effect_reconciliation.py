from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from aicrm_next.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
)
from aicrm_next.platform_foundation.external_effects.reconciliation import (
    DELIVERY_EVIDENCE_CUTOVER_AT,
    ExternalEffectDispatchReconciliationService,
)
from aicrm_next.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.push_center.status_mapper import standard_attempt_status, standard_push_status

ROOT = Path(__file__).resolve().parents[1]


def _plan(repo: InMemoryExternalEffectRepository, key: str) -> dict:
    return ExternalEffectService(repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="test_adapter",
        operation="post",
        target_type="test_target",
        target_id=key,
        payload={"secret": "must-not-appear", "external_userid": "wm_sensitive"},
        idempotency_key=key,
        status="queued",
        execution_mode="execute",
    )


def test_reconciliation_is_count_only_and_reports_unknown_without_pii() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, "r07-reconcile-unknown")
    claimed = repo.acquire_job(job["id"], locked_by="crashed-worker")
    assert claimed is not None
    updated = repo.mark_dispatch_unknown(
        job=claimed,
        error_code="timeout",
        error_message="provider outcome unknown",
        side_effect_executed=True,
    )
    assert updated is not None

    result = ExternalEffectDispatchReconciliationService(repo).diagnose()

    assert result["ok"] is True
    assert result["mode"] == "count_only"
    assert result["repair_supported"] is False
    assert result["database_mutation_performed"] is False
    assert result["real_external_call_executed"] is False
    assert result["pii_in_output"] is False
    assert result["has_anomalies"] is True
    assert result["counts"]["unknown_after_dispatch_count"] == 1
    assert result["counts"]["reconciliation_required_count"] == 1
    assert "wm_sensitive" not in str(result)
    assert repo.get_job(job["id"]) == updated


def test_reconciliation_reports_clean_queue_without_mutation() -> None:
    repo = InMemoryExternalEffectRepository()

    result = ExternalEffectDispatchReconciliationService(repo).diagnose()

    assert result["has_anomalies"] is False
    assert all(value == 0 for value in result["counts"].values())


def test_internal_automation_side_effect_is_valid_delivery_evidence() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, "r07-internal-side-effect")
    claimed = repo.acquire_job(job["id"], locked_by="internal-adapter")
    assert claimed is not None
    begun = repo.begin_provider_attempt(
        job=claimed,
        request_summary={"provider_boundary": "internal_automation"},
    )
    assert begun is not None
    claimed, _attempt = begun
    completed = repo.complete_dispatch(
        job=claimed,
        result=ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            response_summary={"internal_side_effect_executed": True, "http_status": 200},
            real_external_call_executed=False,
            provider_result_received=True,
        ),
    )
    assert completed is not None

    result = ExternalEffectDispatchReconciliationService(repo).diagnose()

    assert result["has_anomalies"] is False
    assert result["counts"]["succeeded_without_evidence_count"] == 0


class _HistoricalEvidenceMetricsRepository:
    @staticmethod
    def queue_metrics(filters):
        metrics = {
            "stale_dispatching_count": 0,
            "unknown_after_dispatch_count": 0,
            "reconciliation_required_count": 0,
            "succeeded_without_evidence_count": 22,
            "simulated_recorded_as_succeeded_count": 0,
            "dispatching_without_active_lease_count": 0,
            "lease_on_non_dispatching_count": 0,
        }
        if filters.get("completed_from"):
            metrics["succeeded_without_evidence_count"] = 0
        return metrics


def test_pre_cutover_missing_evidence_stays_auditable_without_failing_current_queue() -> None:
    result = ExternalEffectDispatchReconciliationService(_HistoricalEvidenceMetricsRepository()).diagnose()

    assert result["has_anomalies"] is False
    assert result["counts"]["succeeded_without_evidence_count"] == 0
    assert result["historical_counts"]["succeeded_without_evidence_count"] == 22
    assert result["evidence_cutover_at"] == DELIVERY_EVIDENCE_CUTOVER_AT


def test_push_center_preserves_simulated_and_unknown_delivery_truth() -> None:
    assert standard_push_status("simulated") == "simulated"
    assert standard_attempt_status("simulated") == "simulated"
    assert standard_push_status("unknown_after_dispatch") == "unknown_after_dispatch"
    assert standard_attempt_status("unknown_after_dispatch") == "unknown_after_dispatch"


def test_count_only_script_runs_from_outside_repository(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("AICRM_TEST_DATABASE_URL", None)

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ops/reconcile_external_effect_dispatch.py")],
        cwd=tmp_path,
        env=env,
        text=True,
        check=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["mode"] == "count_only"
    assert payload["database_mutation_performed"] is False
    assert payload["real_external_call_executed"] is False
