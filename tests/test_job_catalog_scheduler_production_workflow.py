from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "job-catalog-scheduler-production-diagnostics.yml"


def test_scheduler_diagnostics_is_exact_release_read_only_and_redacted() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "expected_release_sha:" in source
    assert "DIAGNOSE AI-CRM JOB CATALOG SCHEDULER READ ONLY" in source
    assert "https://www.youcangogogo.com/health" in source
    assert "test \"$(git rev-parse HEAD)\" = \"$EXPECTED_RELEASE_SHA\"" in source
    assert "run_job_catalog_scheduler.py \\" in source
    assert "--dry-run" in source
    assert "--execute" not in source
    assert 'assert report["real_external_call_executed"] is False' in source
    assert 'assert report["executed_count"] == 0' in source
    assert "secretref:" in source
    assert "systemctl is-enabled --quiet aicrm-job-catalog-scheduler.timer" in source
    assert "systemctl is-active --quiet aicrm-job-catalog-scheduler.timer" in source
    assert 'scheduler_mode="$(python3 - <<\'PY\'' in source
    assert 'scheduler_runtime_contract=absent_for_release residue=%s' in source
    assert 'test "$scheduler_residue" = "0"' in source


def test_scheduler_diagnostics_reports_rollback_residue_before_running_release_code() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    unit_state_index = source.index('load_state="$(systemctl show "$unit"')
    mode_index = source.index('scheduler_mode="$(python3')
    scheduler_run_index = source.index("python3 scripts/run_job_catalog_scheduler.py")

    assert unit_state_index < mode_index < scheduler_run_index
    assert "aicrm-job-catalog-scheduler.service" in source
    assert 'scheduler_timer_load' in source
    assert 'scheduler_service_load' in source
    assert 'scheduler_timer_enabled' in source
    assert 'scheduler_timer_active' in source
    assert 'if [ "$scheduler_mode" = "absent" ]; then' in source
    assert 'elif [ "$scheduler_mode" = "observe" ]; then' in source
    assert 'elif [ "$scheduler_mode" = "enforce" ]; then' in source
    assert "aicrm_next/platform/platform_foundation/background_jobs/scheduler_runtime.py" in source


def test_scheduler_diagnostics_includes_only_aggregate_release_refresh_state() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "customer_read_model_refresh_intent" in source
    assert "customer_read_model.refresh.requested" in source
    assert "customer_read_model_refresh_intent_consumer" in source
    assert "GROUP BY status, last_error_code" in source
    assert '"target_values_redacted": True' in source
    assert '"real_external_call_executed": False' in source
    assert "payload_json" not in source
    assert "target_id" not in source


def test_scheduler_diagnostics_reports_all_three_predecessor_timers() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "aicrm-next-broadcast-delegation.timer" in source
    assert "aicrm-next-group-ops-planning.timer" in source
    assert "aicrm-ai-audience-daily-intent.timer" in source
    assert "aicrm-next-broadcast-delegation.service" in source
    assert "aicrm-next-group-ops-planning.service" in source
    assert "aicrm-ai-audience-daily-intent.service" in source


def test_scheduler_diagnostics_proves_single_owner_and_keeps_payment_observe_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "AICRM_JOB_CATALOG_SCHEDULER_EXECUTE=1" in source
    assert "EXECUTE_SAFE_JOB_CATALOG_SCHEDULER" in source
    assert "! grep -Fq 'AICRM_GROUP_OPS_MATERIAL_UPLOAD_MODE=real'" in source
    assert "scheduler_owner_last_exit_epoch" in source
    assert "openclaw-wechat-pay-order-reconciliation-worker.timer" in source
    assert 'test "$load_state" = "not-found"' in source
    assert 'test ! -e "/etc/systemd/system/$unit"' in source
