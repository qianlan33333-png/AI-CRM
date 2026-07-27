from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from aicrm_next.platform.admin_auth.guards import admin_page_auth_redirect
from aicrm_next.admin_shell_contract import shell_context


router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_AUTOMATION_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "automation_engine" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _AUTOMATION_TEMPLATES_DIR])

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}


@router.get("/admin/automation-agents", name="api.admin_automation_agents_page")
def admin_automation_agents_page(request: Request):
    if redirect := admin_page_auth_redirect(request):
        return redirect
    context = shell_context(
        request=request,
        page_title="自动化话术",
        page_summary="",
        active_endpoint="api.admin_automation_agents_page",
    )
    context.update(
        {
            "show_page_header": False,
            "automation_agents_api_url": "/api/admin/automation-agents",
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/automation_agent_list.html",
        context,
        headers=_HEADERS,
    )


@router.get("/admin/automation-agents/{agent_id}/edit", name="api.admin_automation_agent_edit_page")
def admin_automation_agent_edit_page(request: Request, agent_id: int):
    if redirect := admin_page_auth_redirect(request):
        return redirect
    context = shell_context(
        request=request,
        page_title="编辑自动化话术",
        page_summary="",
        active_endpoint="api.admin_automation_agents_page",
    )
    context.update(
        {
            "show_page_header": False,
            "agent_id": agent_id,
            "automation_agent_api_url": f"/api/admin/automation-agents/{agent_id}",
            "automation_agent_fixed_content_api_url": f"/api/admin/automation-agents/{agent_id}/fixed-content",
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/automation_agent_edit.html",
        context,
        headers=_HEADERS,
    )
