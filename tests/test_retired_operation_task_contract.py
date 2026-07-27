from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_retired_operation_task_contract_module_is_removed() -> None:
    assert importlib.util.find_spec("aicrm_next.automation.automation_engine.operation_task_contract") is None


def test_retired_operation_task_is_not_a_broadcast_source_option() -> None:
    from aicrm_next.platform.admin_jobs.domain import BROADCAST_SOURCE_TYPE_LABELS, BROADCAST_SOURCE_TYPES
    from aicrm_next.platform.admin_jobs.repository import clean_broadcast_filters

    assert "operation_task" not in BROADCAST_SOURCE_TYPES
    assert "operation_task" not in BROADCAST_SOURCE_TYPE_LABELS

    _, source_types = clean_broadcast_filters([], ["operation_task", "manual"])

    assert source_types == ["manual"]


def test_retired_operation_task_label_is_not_special_cased_in_admin_jobs() -> None:
    source = (PROJECT_ROOT / "aicrm_next" / "platform" / "admin_jobs" / "application.py").read_text(encoding="utf-8")

    assert 'source_type == "operation_task"' not in source
    assert '"运营任务"' not in source


def test_retired_admin_jobs_deferred_runner_is_removed() -> None:
    repository_source = (PROJECT_ROOT / "aicrm_next" / "platform" / "admin_jobs" / "repository.py").read_text(encoding="utf-8")
    admin_jobs_template = PROJECT_ROOT / "aicrm_next" / "platform" / "admin_jobs" / "templates" / "admin_console" / "jobs.html"
    frontend_jobs_template = PROJECT_ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "jobs.html"

    assert "def run_due_deferred_jobs" not in repository_source
    assert "UPDATE user_ops_deferred_jobs" not in repository_source
    assert not admin_jobs_template.exists()
    assert not frontend_jobs_template.exists()


def test_retired_admin_jobs_webhook_retry_runner_is_removed() -> None:
    routes_source = (PROJECT_ROOT / "aicrm_next" / "platform" / "admin_jobs" / "routes.py").read_text(encoding="utf-8")

    assert '"/api/admin/jobs/webhook-deliveries/run"' not in routes_source
    assert '"/api/admin/jobs/webhook-deliveries/{delivery_id}/retry"' not in routes_source
