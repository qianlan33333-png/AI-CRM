from __future__ import annotations

import importlib.util

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine.repo import build_automation_repository
from aicrm_next.main import create_app


def test_retired_task_workflow_profile_modules_are_removed() -> None:
    retired_modules = {
        "aicrm_next.automation.automation_engine.domain",
        "aicrm_next.automation.automation_engine.profile_segments",
        "aicrm_next.automation.automation_engine.state_machine",
        "aicrm_next.automation.automation_engine.task_groups",
        "aicrm_next.automation.automation_engine.tasks",
        "aicrm_next.automation.automation_engine.workflow",
        "aicrm_next.automation.automation_engine.workflow_nodes",
        "aicrm_next.automation.automation_engine.workflows",
    }

    for module_name in retired_modules:
        assert importlib.util.find_spec(module_name) is None


def test_fixture_automation_repository_is_agent_only() -> None:
    repo = build_automation_repository()

    for name in (
        "list_pools",
        "list_members",
        "get_member",
        "find_member",
        "list_profile_segment_templates",
        "create_profile_segment_template",
        "list_task_groups",
        "create_task_group",
        "list_workflows",
        "create_workflow",
        "list_workflow_nodes",
        "create_workflow_node",
        "list_tasks",
        "create_task",
    ):
        assert not hasattr(repo, name)

    assert hasattr(repo, "list_agents")
    assert hasattr(repo, "list_agent_outputs")
    assert hasattr(repo, "list_agent_runs")


def test_agent_admin_api_is_not_scoped_by_retired_program_or_task(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "retired-agent-program-scope-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    agents = client.get(
        "/api/admin/automation-conversion/agents",
        params={"program_id": 999, "workflow_id": 999, "node_id": 999, "task_id": 999},
    )
    assert agents.status_code == 200
    agent_payload = agents.json()
    assert agent_payload["ok"] is True
    assert agent_payload["total"] > 0
    for retired in ("program_id", "workflow_id", "node_id", "task_id"):
        assert retired not in agent_payload["filters"]
        assert all(retired not in item for item in agent_payload["items"])

    created = client.post(
        "/api/admin/automation-conversion/agents",
        json={
            "agent_name": "Standalone Agent",
            "agent_code": "standalone_agent",
            "program_id": 123,
            "workflow_id": 456,
            "node_id": 789,
            "task_id": 101,
            "idempotency_key": "standalone-agent-create",
        },
    )
    assert created.status_code == 201
    created_agent = created.json()["agent"]
    for retired in ("program_id", "workflow_id", "node_id", "task_id"):
        assert retired not in created_agent


def test_agent_run_api_is_not_filtered_or_projected_by_retired_workflow_task(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "retired-agent-run-scope-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/admin/automation-conversion/agent-runs",
        params={"workflow_id": 999, "task_id": 999},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["total"] > 0
    for retired in ("workflow_id", "task_id"):
        assert retired not in payload["filters"]
        assert all(retired not in item for item in payload["items"])


def test_agent_run_fixture_source_has_no_retired_workflow_task_columns() -> None:
    repo = build_automation_repository()
    runs, total, _filters = repo.list_agent_runs({"visibility": "console"})

    assert total > 0
    for run in runs:
        assert "workflow_id" not in run
        assert "task_id" not in run

    raw_runs = getattr(repo, "_agent_runs", {})
    assert raw_runs
    for run in raw_runs.values():
        assert "workflow_id" not in run
        assert "task_id" not in run
