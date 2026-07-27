from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


STATIC_ROOT = Path(__file__).resolve().parents[1] / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_retired_automation_overview_static_bundle_is_removed() -> None:
    retired_files = [
        "automation_overview.js",
        "automation_overview_actions.js",
        "automation_overview_core.js",
        "automation_overview_renderers.js",
    ]

    for filename in retired_files:
        assert not (STATIC_ROOT / filename).exists()


def test_retired_customer_profile_automation_actions_static_file_is_removed() -> None:
    assert not (STATIC_ROOT / "customer_profile_automation.js").exists()


def test_retired_automation_overview_api_is_removed() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/automation-conversion/overview")

    assert response.status_code == 404


def test_retired_runtime_v2_package_is_removed() -> None:
    assert not (PROJECT_ROOT / "aicrm_next" / "automation_runtime_v2").exists()


def test_retired_operation_task_publish_contract_is_removed() -> None:
    assert not (PROJECT_ROOT / "aicrm_next" / "automation" / "automation_engine" / "operation_task_contract.py").exists()


def test_retired_runtime_v2_routes_are_not_registered() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path in [
        "/api/automation-runtime/v2/events",
        "/api/automation-runtime/v2/replay",
        "/api/automation-runtime/v2/runtime-check",
    ]:
        response = client.get(path)
        assert response.status_code in {404, 410}


def test_retired_member_action_and_timer_routes_are_not_registered() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path in [
        "/api/admin/automation-conversion/member/put-in-pool",
        "/api/admin/automation-conversion/member/remove-from-pool",
        "/api/admin/automation-conversion/member/set-focus",
        "/api/admin/automation-conversion/member/set-normal",
        "/api/admin/automation-conversion/member/mark-won",
        "/api/admin/automation-conversion/member/unmark-won",
        "/api/admin/automation-conversion/member/push-openclaw",
        "/api/admin/automation-conversion/reply-monitor/run-due",
        "/api/admin/automation-conversion/reply-monitor/capture",
        "/api/admin/automation-conversion/jobs/run-due",
        "/api/admin/automation-conversion/tasks/run-due",
        "/api/admin/automation-conversion/execution-items/1/send-via-bazhuayu",
    ]:
        response = client.post(path, json={})
        assert response.status_code in {404, 410}, path


def test_retired_runtime_v2_realtest_guard_is_removed_from_broadcast_worker() -> None:
    source = (PROJECT_ROOT / "aicrm_next" / "automation" / "background_jobs" / "broadcast_queue_worker.py").read_text(encoding="utf-8")

    assert "RuntimeV2真实链路测试" not in source
    assert "runtime_v2_realtest_" not in source
    assert "realtest_sender_not_allowed" not in source
    assert "realtest_target_not_allowed" not in source


def test_internal_event_audit_no_longer_treats_runtime_v2_event_store_as_queue() -> None:
    source = (PROJECT_ROOT / "scripts" / "audit_internal_event_coverage.py").read_text(encoding="utf-8")

    queue_patterns_block = source.split("QUEUE_PATTERNS =", 1)[1].split(")", 1)[0]
    assert "automation_event_v2" not in queue_patterns_block


def test_internal_event_audit_artifacts_do_not_reference_removed_runtime_modules() -> None:
    removed_markers = [
        "aicrm_next/automation_runtime_v2/",
        "aicrm_next/automation/automation_engine/automation_program_admission.py",
        "aicrm_next/automation/automation_engine/timers.py",
        "aicrm_next/automation/automation_engine/workspace_runtime.py",
        "aicrm_next/automation/automation_engine/member_actions.py",
    ]
    audit_artifact = (PROJECT_ROOT / "docs" / "reports" / "evidence" / "internal_event_coverage_audit.json").read_text(encoding="utf-8")
    audit_docs = (PROJECT_ROOT / "docs" / "queue" / "internal-event-coverage-audit.md").read_text(encoding="utf-8")

    for marker in removed_markers:
        assert marker not in audit_artifact
        assert marker not in audit_docs
