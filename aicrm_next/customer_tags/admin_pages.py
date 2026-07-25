from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/admin/wecom-tags", name="api.admin_wecom_tags_page")
def admin_wecom_tags(request: Request) -> Response:
    context = shell_context(
        request=request,
        page_title="企微标签管理",
        page_summary="集中管理企业客户标签：同步、搜索、新增、编辑、删除和复制 tag_id。",
        active_endpoint="api.admin_wecom_tags_page",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "企微标签管理"},
    ]
    return templates.TemplateResponse(request, "admin_console/config_wecom_tags.html", context)
