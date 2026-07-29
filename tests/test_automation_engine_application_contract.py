from __future__ import annotations

import importlib.util

import aicrm_next.automation.automation_engine.application as automation_application
from aicrm_next.automation.automation_engine.repo import build_automation_repository


def test_automation_application_surface_is_next_native() -> None:
    assert hasattr(automation_application, "CreateAgentCommand")


def test_retired_task_workflow_member_action_surface_is_not_exported() -> None:
    retired_names = {
        "ListTasksQuery",
        "CreateTaskCommand",
        "GetTaskDetailQuery",
        "UpdateTaskCommand",
        "ListWorkflowsQuery",
        "CreateWorkflowCommand",
        "ListWorkflowNodesQuery",
        "CreateWorkflowNodeCommand",
        "ListAutomationMembersQuery",
        "GetAutomationMemberDetailQuery",
        "OverrideFollowupTypeCommand",
        "ConfirmConversionCommand",
        "EnterSilentPoolCommand",
        "ExitMarketingCommand",
        "PushMemberContextToOpenClawCommand",
        "RunDueWorkflowsCommand",
        "GenerateAgentOutputCommand",
        "ReviewAgentOutputCommand",
        "ApplyQuestionnaireResultCommand",
        "ApplyActivationFactCommand",
        "ApplyActivationWebhookCommand",
        "ApplyCustomerActivationWebhookCommand",
        "PlanCustomerWebhookDeliveryRetryCommand",
        "PlanCustomerWebhookDeliveryRetryDueCommand",
        "execute_customer_webhook_command",
    }

    for name in retired_names:
        assert not hasattr(automation_application, name)


def test_retired_task_workflow_sql_repository_modules_are_removed() -> None:
    retired_modules = {
        "aicrm_next.automation.automation_engine.postgres_repo",
        "aicrm_next.automation.automation_engine.task_sqlalchemy_repository",
        "aicrm_next.automation.automation_engine.task_group_sqlalchemy_repository",
        "aicrm_next.automation.automation_engine.workflow_sqlalchemy_repository",
        "aicrm_next.automation.automation_engine.workflow_node_sqlalchemy_repository",
        "aicrm_next.automation.automation_engine.customer_webhooks",
    }

    for module_name in retired_modules:
        assert importlib.util.find_spec(module_name) is None


def test_retired_task_workflow_repository_backends_are_not_accepted() -> None:
    retired_kwargs = (
        {"task_backend": "postgres"},
        {"task_group_backend": "sqlalchemy"},
        {"workflow_backend": "sqlalchemy"},
        {"workflow_node_backend": "sqlalchemy"},
    )

    for kwargs in retired_kwargs:
        try:
            build_automation_repository(**kwargs)
        except TypeError:
            continue
        raise AssertionError(f"retired repository backend was still accepted: {kwargs}")

def test_retired_automation_member_write_dtos_and_repo_methods_are_not_exported() -> None:
    import aicrm_next.automation.automation_engine.dto as automation_dto
    import aicrm_next.automation.automation_engine.repo as automation_repo

    for name in (
        "ApplyQuestionnaireResultRequest",
        "ApplyTrialOpenedFactRequest",
        "ApplyActivationFactRequest",
        "OverrideFollowupTypeRequest",
        "AutomationActionRequest",
        "PushOpenClawContextRequest",
        "TaskGroupListRequest",
        "TaskGroupCreateRequest",
        "WorkflowListRequest",
        "WorkflowCreateRequest",
        "WorkflowNodeListRequest",
        "WorkflowNodeCreateRequest",
        "WorkflowNodeUpdateRequest",
        "TaskListRequest",
        "TaskCreateRequest",
        "TaskUpdateRequest",
        "SendStrategyUpdateRequest",
        "UnifiedSendContentUpdateRequest",
        "ProfileSegmentSendContentUpdateRequest",
        "BehaviorSegmentSendContentUpdateRequest",
        "AgentMaterialsUpdateRequest",
    ):
        assert not hasattr(automation_dto, name)

    source = automation_repo.__loader__.get_source(automation_repo.__name__) or ""
    for marker in (
        "def save_member(",
        "def create_member_from_questionnaire(",
        "def create_execution_record(",
    ):
        assert marker not in source


def test_retired_customer_webhook_command_module_is_not_application_surface() -> None:
    assert not hasattr(automation_application, "customer_webhooks")


def test_agent_run_side_effect_safety_is_not_workflow_task_scoped() -> None:
    from aicrm_next.automation.automation_engine.agent_runs import agent_run_side_effect_safety

    safety = agent_run_side_effect_safety()

    assert "real_workflow_executed" not in safety
    assert "real_task_executed" not in safety
    assert safety["real_agent_output_generated"] is False
    assert safety["real_llm_call_executed"] is False
