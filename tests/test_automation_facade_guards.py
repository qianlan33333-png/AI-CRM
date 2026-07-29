from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_automation_scripts_do_not_import_legacy_runtime() -> None:
    for rel_path in (
        "scripts/run_automation_ops_scheduler.py",
        "scripts/run_broadcast_queue_worker.py",
    ):
        source = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "wecom_ability" + "_service" not in source
        assert "create_app" not in source
        assert "app_context" not in source


def test_next_background_job_modules_are_the_active_owners() -> None:
    for rel_path in (
        "aicrm_next/automation/background_jobs/automation_ops_scheduler.py",
        "aicrm_next/automation/background_jobs/broadcast_queue_worker.py",
    ):
        source = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "def run_" in source
        assert "wecom_ability" + "_service" not in source
        assert "aicrm_next.automation.automation_engine" not in source
