from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.platform.admin_auth.guards import admin_api_auth_error
from aicrm_next.platform.shared.admin_read_fallback import admin_read_unavailable_payload

from .application import AutomationAgentAdminService

router = APIRouter()

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def _request_base_url(request: Request) -> str:
    proto = str(request.headers.get("X-Forwarded-Proto") or request.url.scheme or "https").split(",", 1)[0].strip()
    host = str(request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.url.netloc or "").split(",", 1)[0].strip()
    return f"{proto}://{host}" if host else ""


def _response(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    if not payload.get("ok", True):
        error = payload.get("error")
        if error == "agent_not_found":
            status_code = 404
        elif status_code == 200:
            status_code = 400
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


@router.get("/api/admin/automation-agents", name="api.admin_automation_agents")
def list_automation_agents(request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    try:
        payload = AutomationAgentAdminService().list_agents()
    except Exception as exc:
        payload = admin_read_unavailable_payload(
            capability_owner="aicrm_next/extensions/ai/automation_agents",
            page_error="自动化话术读模型暂不可用，请稍后重试。",
            exc=exc,
            items_keys=("items",),
            count_keys=("total",),
        )
    return _response(payload)


@router.post("/api/admin/automation-agents", name="api.admin_automation_agent_create")
def create_automation_agent(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().create_agent(payload, request_base_url=_request_base_url(request)))


@router.get("/api/admin/automation-agents/{agent_id}", name="api.admin_automation_agent_detail")
def get_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().get_agent(agent_id, request_base_url=_request_base_url(request)))


@router.patch("/api/admin/automation-agents/{agent_id}", name="api.admin_automation_agent_update")
def update_automation_agent(agent_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().update_agent(agent_id, payload, request_base_url=_request_base_url(request)))


@router.post("/api/admin/automation-agents/{agent_id}/copy", name="api.admin_automation_agent_copy")
def copy_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().copy_agent(agent_id, request_base_url=_request_base_url(request)))


@router.post("/api/admin/automation-agents/{agent_id}/publish", name="api.admin_automation_agent_publish")
def publish_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().publish_agent(agent_id, request_base_url=_request_base_url(request)))


@router.post("/api/admin/automation-agents/{agent_id}/pause", name="api.admin_automation_agent_pause")
def pause_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().set_status(agent_id, "paused", request_base_url=_request_base_url(request)))


@router.post("/api/admin/automation-agents/{agent_id}/activate", name="api.admin_automation_agent_activate")
def activate_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().set_status(agent_id, "active", request_base_url=_request_base_url(request)))


@router.delete("/api/admin/automation-agents/{agent_id}", name="api.admin_automation_agent_delete")
def archive_automation_agent(agent_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AutomationAgentAdminService().set_status(agent_id, "archived", request_base_url=_request_base_url(request)))


@router.put("/api/admin/automation-agents/{agent_id}/fixed-content", name="api.admin_automation_agent_fixed_content")
def save_automation_agent_fixed_content(agent_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    content_package = payload.get("content_package") if isinstance(payload, dict) else {}
    return _response(
        AutomationAgentAdminService().save_fixed_content(
            agent_id,
            content_package if isinstance(content_package, dict) else {},
            request_base_url=_request_base_url(request),
        )
    )
