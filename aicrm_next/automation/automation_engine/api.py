from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.errors import ContractError, NotFoundError

from .application import (
    CreateAgentCommand,
    GetAgentOutputDetailQuery,
    GetAgentRunDetailQuery,
    ListAgentsQuery,
    ListAgentOutputsQuery,
    ListAgentRunsQuery,
)
from .dto import (
    AgentCreateRequest,
    AgentListRequest,
    AgentOutputDetailRequest,
    AgentOutputListRequest,
    AgentRunDetailRequest,
    AgentRunListRequest,
)
from .group_ops.api import router as group_ops_router

router = APIRouter()
router.include_router(group_ops_router)

_CUSTOMER_WEBHOOK_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-Outbound-Webhook-Executed": "false",
    "X-AICRM-Automation-Runtime-Executed": "false",
    "X-AICRM-WeCom-Send-Executed": "false",
}
_AUTOMATION_READ_MODEL_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
}


def _retired_customer_automation_response() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": "legacy_customer_automation_retired",
            "message": "Legacy customer automation webhook routes are retired; use AI Audience, group_ops, or external_effect_job current paths.",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "outbound_webhook_executed": False,
            "automation_runtime_executed": False,
            "wecom_send_executed": False,
        },
        status_code=410,
        headers=_CUSTOMER_WEBHOOK_HEADERS,
    )


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ContractError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _json_result(payload: dict) -> JSONResponse:
    status_code = int(payload.get("status_code") or 200)
    return JSONResponse(payload, status_code=status_code)


@router.options("/api/customers/automation/activation-webhook")
def api_customer_automation_activation_webhook_options() -> JSONResponse:
    return _retired_customer_automation_response()


@router.post("/api/customers/automation/activation-webhook")
async def api_customer_automation_activation_webhook() -> JSONResponse:
    return _retired_customer_automation_response()


@router.options("/api/customers/automation/webhook-deliveries/{delivery_id:int}/retry")
def api_customer_automation_webhook_delivery_retry_options(delivery_id: int) -> JSONResponse:
    return _retired_customer_automation_response()


@router.post("/api/customers/automation/webhook-deliveries/{delivery_id:int}/retry")
async def api_plan_customer_automation_webhook_delivery_retry(delivery_id: int) -> JSONResponse:
    return _retired_customer_automation_response()


@router.options("/api/customers/automation/webhook-deliveries/retry-due")
def api_customer_automation_webhook_delivery_retry_due_options() -> JSONResponse:
    return _retired_customer_automation_response()


@router.post("/api/customers/automation/webhook-deliveries/retry-due")
async def api_plan_customer_automation_webhook_delivery_retry_due() -> JSONResponse:
    return _retired_customer_automation_response()


@router.get("/api/admin/automation-conversion/agents")
def list_agents(
    agent_type: str = "",
    status: str = "",
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    request = AgentListRequest(
        agent_type=agent_type,
        status=status,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return _json_result(ListAgentsQuery()(request))


@router.get("/api/admin/automation-conversion/agents/options")
def agent_options(
    agent_type: str = "",
    status: str = "",
    include_archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> JSONResponse:
    request = AgentListRequest(
        agent_type=agent_type,
        status=status,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return _json_result(ListAgentsQuery()(request))


@router.post("/api/admin/automation-conversion/agents")
def create_agent(payload: AgentCreateRequest) -> JSONResponse:
    try:
        return _json_result(CreateAgentCommand()(payload))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/automation-conversion/agent-outputs")
def list_agent_outputs(
    page: int = 1,
    page_size: int = 50,
    request_id: str = "",
    unionid: str = "",
    userid: str = "",
    agent_code: str = "",
    output_type: str = "",
    applied_status: str = "",
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    has_error: bool | None = None,
    visibility: str = "masked",
) -> JSONResponse:
    request = AgentOutputListRequest(
        page=page,
        page_size=page_size,
        request_id=request_id,
        unionid=unionid,
        userid=userid,
        agent_code=agent_code,
        output_type=output_type,
        applied_status=applied_status,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        has_error=has_error,
        visibility=visibility,
    )
    try:
        return _json_result(ListAgentOutputsQuery()(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/automation-conversion/agent-outputs/{output_id}")
def get_agent_output_detail(output_id: str, visibility: str = "masked") -> JSONResponse:
    request = AgentOutputDetailRequest(output_id=output_id, visibility=visibility)
    try:
        return _json_result(GetAgentOutputDetailQuery()(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/automation-conversion/agent-runs")
def list_agent_runs(
    page: int = 1,
    page_size: int = 50,
    request_id: str = "",
    run_id: str = "",
    agent_code: str = "",
    run_status: str = "",
    trigger_source: str = "",
    unionid: str = "",
    userid: str = "",
    started_after: str = "",
    started_before: str = "",
    has_error: bool | None = None,
    visibility: str = "masked",
) -> JSONResponse:
    request = AgentRunListRequest(
        page=page,
        page_size=page_size,
        request_id=request_id,
        run_id=run_id,
        agent_code=agent_code,
        run_status=run_status,
        trigger_source=trigger_source,
        unionid=unionid,
        userid=userid,
        started_after=started_after,
        started_before=started_before,
        has_error=has_error,
        visibility=visibility,
    )
    try:
        return _json_result(ListAgentRunsQuery()(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/automation-conversion/agent-runs/{run_id}")
def get_agent_run_detail(run_id: str, visibility: str = "masked") -> JSONResponse:
    request = AgentRunDetailRequest(run_id=run_id, visibility=visibility)
    try:
        return _json_result(GetAgentRunDetailQuery()(request))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/customer-automation/activation-webhook")
def activation_webhook() -> JSONResponse:
    return _retired_customer_automation_response()


@router.get("/api/customers/automation/signup-conversion/batches")
def signup_conversion_batches(limit: int = 20, cursor: str = "") -> JSONResponse:
    return _retired_customer_automation_response()


@router.get("/api/customers/automation/signup-conversion/batches/{batch_id}")
def signup_conversion_batch(batch_id: int) -> JSONResponse:
    return _retired_customer_automation_response()


@router.get("/api/customers/automation/webhook-deliveries")
def customer_automation_webhook_deliveries(
    event_type: str = "",
    status: str = "",
    limit: int = 50,
) -> JSONResponse:
    return _retired_customer_automation_response()
