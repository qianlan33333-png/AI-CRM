from __future__ import annotations

from typing import Any

from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError, blocked_production_payload
from aicrm_next.platform.shared.runtime import production_data_ready, production_environment

from .agent_outputs import agent_output_run_projection, agent_output_side_effect_safety
from .agent_runs import agent_run_side_effect_safety
from .agents import agent_side_effect_safety
from .dto import (
    AgentCreateRequest,
    AgentListRequest,
    AgentOutputDetailRequest,
    AgentOutputListRequest,
    AgentRunDetailRequest,
    AgentRunListRequest,
)
from .repo import AutomationRepository, agent_postgres_enabled, build_automation_repository


def _automation_side_effect_safety(**overrides: bool) -> dict[str, bool]:
    safety = {
        "real_automation_write_executed": False,
        "real_activation_webhook_executed": False,
        "real_external_call_executed": False,
        "real_wecom_call_executed": False,
        "real_openclaw_call_executed": False,
        "real_mcp_call_executed": False,
        "real_timer_executed": False,
        "real_outbound_send_executed": False,
        "real_customer_pool_state_changed": False,
        "real_openclaw_push_executed": False,
        "real_workflow_runtime_executed": False,
        "real_agent_runtime_executed": False,
        "real_external_webhook_executed": False,
    }
    safety.update({key: bool(value) for key, value in overrides.items() if key in safety})
    return safety


def _agent_production_unavailable_payload(detail: str | None = None) -> dict[str, Any]:
    payload = blocked_production_payload(
        capability_owner="aicrm_next.automation.automation_engine",
        detail=detail
        or "agent production repository is not enabled; production repository handoff remains blocked until explicitly enabled.",
    )
    payload.update(
        {
            "status_code": 503,
            "error_code": "production_repository_not_enabled",
            "route_owner": "ai_crm_next",
            "side_effect_safety": agent_side_effect_safety(),
        }
    )
    return payload


def _agent_output_production_unavailable_payload(detail: str | None = None) -> dict[str, Any]:
    payload = blocked_production_payload(
        capability_owner="aicrm_next.automation.automation_engine",
        detail=detail
        or "agent output production repository is not enabled; production repository handoff remains blocked until explicitly enabled.",
    )
    payload.update(
        {
            "status_code": 503,
            "error_code": "production_repository_not_enabled",
            "route_owner": "ai_crm_next",
            "side_effect_safety": agent_output_side_effect_safety(),
        }
    )
    return payload


def _agent_run_production_unavailable_payload(detail: str | None = None) -> dict[str, Any]:
    payload = blocked_production_payload(
        capability_owner="aicrm_next.automation.automation_engine",
        detail=detail
        or "agent run production repository is not enabled; production repository handoff remains blocked until explicitly enabled.",
    )
    payload.update(
        {
            "status_code": 503,
            "error_code": "production_repository_not_enabled",
            "route_owner": "ai_crm_next",
            "side_effect_safety": agent_run_side_effect_safety(),
        }
    )
    return payload


def _agent_response(payload: dict[str, Any], *, status_code: int = 200) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": "fixture_local_contract",
        "route_owner": "ai_crm_next",
        "status_code": status_code,
        "side_effect_safety": agent_side_effect_safety(),
        **payload,
    }


def _agent_output_response(payload: dict[str, Any], *, status_code: int = 200) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": "fixture_local_contract",
        "route_owner": "ai_crm_next",
        "status_code": status_code,
        "side_effect_safety": agent_output_side_effect_safety(),
        **payload,
    }


def _agent_run_response(payload: dict[str, Any], *, status_code: int = 200) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": "fixture_local_contract",
        "route_owner": "ai_crm_next",
        "status_code": status_code,
        "side_effect_safety": agent_run_side_effect_safety(),
        **payload,
    }


def _request_dump(request: Any, *, exclude_unset: bool = False) -> dict[str, Any]:
    dump = getattr(request, "model_dump", None)
    if callable(dump):
        return dump(exclude_unset=exclude_unset)
    return request.dict(exclude_unset=exclude_unset)


class _AgentRepositoryOwner:
    def __init__(self, repo: AutomationRepository | None = None) -> None:
        self._repo = repo

    def _repo_or_none(self) -> AutomationRepository | None:
        if (production_environment() or production_data_ready()) and self._repo is None:
            if not agent_postgres_enabled():
                return None
            self._repo = build_automation_repository(agent_backend="postgres")
        if self._repo is None:
            self._repo = build_automation_repository()
        return self._repo

    def _blocked_payload(self, exc: Exception | None = None) -> dict[str, Any]:
        detail = str(exc) if exc else None
        return _agent_production_unavailable_payload(detail)


class _AgentOutputRepositoryOwner:
    def __init__(self, repo: AutomationRepository | None = None) -> None:
        self._repo = repo

    def _repo_or_none(self) -> AutomationRepository | None:
        if (production_environment() or production_data_ready()) and self._repo is None:
            return None
        if self._repo is None:
            self._repo = build_automation_repository()
        return self._repo

    def _blocked_payload(self, exc: Exception | None = None) -> dict[str, Any]:
        detail = str(exc) if exc else None
        return _agent_output_production_unavailable_payload(detail)


class _AgentRunRepositoryOwner:
    def __init__(self, repo: AutomationRepository | None = None) -> None:
        self._repo = repo

    def _repo_or_none(self) -> AutomationRepository | None:
        if (production_environment() or production_data_ready()) and self._repo is None:
            return None
        if self._repo is None:
            self._repo = build_automation_repository()
        return self._repo

    def _blocked_payload(self, exc: Exception | None = None) -> dict[str, Any]:
        detail = str(exc) if exc else None
        return _agent_run_production_unavailable_payload(detail)


class ListAgentsQuery(_AgentRepositoryOwner):
    def execute(self, request: AgentListRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_production_unavailable_payload()
        try:
            rows, total = repo.list_agents(_request_dump(request))
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        options = [
            {
                **item,
                "value": item.get("agent_code") or item.get("code") or "",
                "label": item.get("agent_name") or item.get("name") or item.get("agent_code") or "",
            }
            for item in rows
        ]
        return _agent_response(
            {
                "items": rows,
                "agents": rows,
                "options": options,
                "total": total,
                "count": len(rows),
                "limit": request.limit,
                "offset": request.offset,
                "filters": {
                    "agent_type": request.agent_type,
                    "status": request.status,
                    "include_archived": request.include_archived,
                },
            }
        )

    __call__ = execute

class CreateAgentCommand(_AgentRepositoryOwner):
    def execute(self, request: AgentCreateRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_production_unavailable_payload()
        payload = _request_dump(request)
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ContractError("idempotency_key is required")
        try:
            result = repo.create_agent(
                payload,
                idempotency_key=idempotency_key,
                operator=str(payload.get("operator") or "system"),
            )
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        return _agent_response(result, status_code=201)

    __call__ = execute


class ListAgentOutputsQuery(_AgentOutputRepositoryOwner):
    def execute(self, request: AgentOutputListRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_output_production_unavailable_payload()
        try:
            rows, total, filters = repo.list_agent_outputs(_request_dump(request))
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        return _agent_output_response(
            {
                "items": rows,
                "rows": rows,
                "outputs": rows,
                "total": total,
                "count": len(rows),
                "page": filters["page"],
                "page_size": filters["page_size"],
                "filters": {
                    "request_id": filters["request_id"],
                    "unionid": filters["unionid"],
                    "userid": filters["userid"],
                    "agent_code": filters["agent_code"],
                    "output_type": filters["output_type"],
                    "applied_status": filters["applied_status"],
                    "min_confidence": filters["min_confidence"],
                    "max_confidence": filters["max_confidence"],
                    "has_error": filters["has_error"],
                    "visibility": filters["visibility"],
                },
            }
        )

    __call__ = execute


class GetAgentOutputDetailQuery(_AgentOutputRepositoryOwner):
    def execute(self, request: AgentOutputDetailRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_output_production_unavailable_payload()
        try:
            output = repo.get_agent_output(request.output_id, _request_dump(request))
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        if not output:
            raise NotFoundError("agent output not found")
        return _agent_output_response(
            {
                "output": output,
                "run": agent_output_run_projection(output, visibility=output.get("visibility") or "masked"),
            }
        )

    __call__ = execute


class ListAgentRunsQuery(_AgentRunRepositoryOwner):
    def execute(self, request: AgentRunListRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_run_production_unavailable_payload()
        try:
            rows, total, filters = repo.list_agent_runs(_request_dump(request))
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        return _agent_run_response(
            {
                "items": rows,
                "rows": rows,
                "runs": rows,
                "total": total,
                "count": len(rows),
                "page": filters["page"],
                "page_size": filters["page_size"],
                "filters": {
                    "request_id": filters["request_id"],
                    "run_id": filters["run_id"],
                    "agent_code": filters["agent_code"],
                    "run_status": filters["run_status"],
                    "trigger_source": filters["trigger_source"],
                    "unionid": filters["unionid"],
                    "userid": filters["userid"],
                    "started_after": filters["started_after"],
                    "started_before": filters["started_before"],
                    "has_error": filters["has_error"],
                    "visibility": filters["visibility"],
                },
            }
        )

    __call__ = execute


class GetAgentRunDetailQuery(_AgentRunRepositoryOwner):
    def execute(self, request: AgentRunDetailRequest) -> dict[str, Any]:
        repo = self._repo_or_none()
        if repo is None:
            return _agent_run_production_unavailable_payload()
        try:
            run = repo.get_agent_run(request.run_id, _request_dump(request))
        except RepositoryProviderError as exc:
            return self._blocked_payload(exc)
        if not run:
            raise NotFoundError("agent run not found")
        return _agent_run_response({"run": run})

    __call__ = execute
