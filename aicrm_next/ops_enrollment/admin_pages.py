from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/admin/user-ops/ui", name="api.admin_user_ops_ui")
def admin_user_ops_ui(_request: Request) -> RedirectResponse:
    return RedirectResponse("/admin/user-ops", status_code=302)


@router.get("/admin/user-ops", name="api.admin_user_ops")
def admin_user_ops_page(request: Request):
    context = shell_context(
        request=request,
        page_title="客户激活 / 客户列表",
        page_summary="User Ops 读模型与预览能力由 Next-native API 提供。",
        active_endpoint="api.admin_console_customers",
    )
    context.update({"admin_action_token": "", "action_result": {}})
    return templates.TemplateResponse(request, "admin_console/user_ops.html", context)
