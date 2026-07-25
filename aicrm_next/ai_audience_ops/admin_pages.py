from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_auth.guards import admin_page_auth_redirect
from aicrm_next.admin_shell_contract import shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_AUTOMATION_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "automation_engine" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _AUTOMATION_TEMPLATES_DIR])

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}


@router.get("/admin/automation-conversion", name="api.admin_automation_conversion")
def admin_ai_audience_automation(request: Request):
    if redirect := admin_page_auth_redirect(request):
        return redirect
    context = shell_context(
        request=request,
        page_title="AI 自动化运营",
        page_summary="",
        active_endpoint="api.admin_automation_conversion",
    )
    context.update(
        {
            "ai_audience_packages_api_url": "/api/admin/ai-audience/packages",
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/ai_audience_package_list.html",
        context,
        headers=_HEADERS,
    )


@router.get(
    "/admin/automation-conversion/packages/{package_id}",
    name="api.admin_automation_conversion_package_detail_page",
)
def admin_ai_audience_package_detail_page(request: Request, package_id: int):
    if redirect := admin_page_auth_redirect(request):
        return redirect
    context = shell_context(
        request=request,
        page_title="AI 自动化运营",
        page_summary="",
        active_endpoint="api.admin_automation_conversion",
    )
    context.update(
        {
            "package_id": package_id,
            "ai_audience_package_api_url": f"/api/admin/ai-audience/packages/{package_id}",
            "ai_audience_package_members_api_url": f"/api/admin/ai-audience/packages/{package_id}/members",
            "ai_audience_package_webhooks_api_url": f"/api/admin/ai-audience/packages/{package_id}/webhooks",
            "ai_audience_package_senders_api_url": f"/api/admin/ai-audience/packages/{package_id}/senders",
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/ai_audience_package_detail.html",
        context,
        headers=_HEADERS,
    )


@router.get("/admin/automation-conversion/programs/{retired_path:path}")
def retired_automation_program_page(request: Request, retired_path: str):
    del retired_path
    if redirect := admin_page_auth_redirect(request):
        return redirect
    return PlainTextResponse(
        "旧自动化运营方案页面已下架，请使用 AI 自动化运营人群包",
        status_code=410,
        headers=_HEADERS,
    )
