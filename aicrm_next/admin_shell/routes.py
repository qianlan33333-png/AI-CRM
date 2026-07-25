from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context
router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/admin", name="api.admin_console_dashboard")
def admin_dashboard(request: Request):
    context = shell_context(
        request=request,
        page_title="快捷入口",
        page_summary="进入需要直接操作的业务模块。",
        active_endpoint="api.admin_automation_conversion",
    )
    context.update(
        {
            "quick_links": [
                {
                    "label": "客户激活 / 客户列表",
                    "description": "查看客户列表和激活状态。",
                    "href": admin_path_for("api.admin_console_customers"),
                },
                {
                    "label": "AI 助手",
                    "description": "进入 AI 助手兼容入口。",
                    "href": admin_path_for("api.admin_cloud_orchestrator_workspace"),
                },
            ],
        }
    )
    return templates.TemplateResponse(request, "admin_shell/dashboard.html", context)


@router.get("/admin/logout", name="api.admin_logout_compat")
def admin_logout_compat() -> RedirectResponse:
    return RedirectResponse(admin_path_for("api.admin_logout"), status_code=302)
