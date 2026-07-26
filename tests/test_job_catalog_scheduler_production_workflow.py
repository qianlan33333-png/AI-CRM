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


def test_scheduler_diagnostics_reports_all_three_predecessor_timers() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "aicrm-next-broadcast-delegation.timer" in source
    assert "aicrm-next-group-ops-planning.timer" in source
    assert "aicrm-ai-audience-daily-intent.timer" in source
