from __future__ import annotations

from dataclasses import replace

import pytest

from aicrm_next.deployment_profile import default_deployment_profile
from aicrm_next.platform_foundation.background_jobs.catalog import JOB_SPECS, JobCatalog, validate_job_catalog
from tools.check_job_catalog import (
    validate_internal_worker_consolidation_manifest,
    validate_runtime_role_catalog,
)


def test_job_catalog_has_one_owner_and_external_worker_boundary() -> None:
    assert validate_job_catalog() == []
    assert len(JOB_SPECS) == len({spec.job_type for spec in JOB_SPECS})
    external = [spec for spec in JOB_SPECS if spec.runtime_role == "external_worker"]
    assert [spec.job_type for spec in external] == ["external_effect.dispatch"]
    assert validate_runtime_role_catalog() == []


def test_internal_worker_owner_contract_is_enforced_and_retires_predecessors() -> None:
    assert validate_internal_worker_consolidation_manifest() == []


def test_scheduler_execution_policy_keeps_provider_reconciliation_observe_only() -> None:
    scheduler_jobs = {
        spec.job_type: spec.scheduler_execution
        for spec in JOB_SPECS
        if spec.runtime_role == "scheduler"
    }

    assert scheduler_jobs == {
        "external_effect.reconcile": "safe_command",
        "campaign.plan": "safe_command",
        "group_ops.plan": "safe_command",
        "ai_audience.refresh": "safe_command",
        "payment.reconcile": "observe_only",
    }
    assert next(spec for spec in JOB_SPECS if spec.job_type == "campaign.plan").handler_ref == (
        "background_jobs.broadcast_queue_worker:delegate_external_effects"
    )


def test_enforce_profile_removes_disabled_extension_jobs() -> None:
    profile = replace(default_deployment_profile(), activation_mode="enforce")
    active = JobCatalog(profile=profile).active_specs()

    assert active
    assert all(not spec.capability_id.startswith("extension.") for spec in active)
    assert "external_effect.dispatch" in {spec.job_type for spec in active}


def test_job_dispatch_is_role_bound_and_fails_closed_without_handler() -> None:
    catalog = JobCatalog()
    with pytest.raises(PermissionError, match="not active for runtime role"):
        catalog.dispatch("internal_event.dispatch", {}, runtime_role="scheduler")
    with pytest.raises(RuntimeError, match="handler is not registered"):
        catalog.dispatch("internal_event.dispatch", {}, runtime_role="internal_worker")

    catalog.register_handler("internal_event.dispatch", lambda payload: {"ok": True, "payload": payload})
    result = catalog.dispatch("internal_event.dispatch", {"event_id": "event:1"}, runtime_role="internal_worker")
    assert result == {
        "ok": True,
        "payload": {"event_id": "event:1"},
        "job_type": "internal_event.dispatch",
        "runtime_role": "internal_worker",
        "real_external_call_executed": False,
    }
