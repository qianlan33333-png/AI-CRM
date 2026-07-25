from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _group_ops_page_context(
    request: Request,
    *,
    page_title: str,
    page_summary: str,
    page_mode: str,
    plan_id: int | None = None,
) -> dict:
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_group_ops_ui",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "群运营计划", "href": request.url_for("api.admin_group_ops_ui")},
            ],
            "group_ops_page_mode": page_mode,
            "group_ops_plan_id": int(plan_id or 0),
            "page_actions": [],
        }
    )
    if page_mode != "list":
        context["breadcrumbs"].append({"label": page_title})
    return context


@router.get("/admin/automation-conversion/group-ops/ui", name="api.admin_group_ops_ui")
def admin_group_ops_ui(request: Request) -> Response:
    context = _group_ops_page_context(
        request,
        page_title="群运营计划",
        page_summary="按计划管理客户群运营内容。",
        page_mode="list",
    )
    return templates.TemplateResponse(request, "admin_console/group_ops.html", context)


@router.get("/admin/automation-conversion/group-ops/plans/{plan_id:int}", name="api.admin_group_ops_plan_detail")
def admin_group_ops_plan_detail(request: Request, plan_id: int) -> Response:
    context = _group_ops_page_context(
        request,
        page_title="群运营计划",
        page_summary="按基础配置、绑定群和计划类型维护群运营计划。",
        page_mode="detail",
        plan_id=int(plan_id),
    )
    return templates.TemplateResponse(request, "admin_console/group_ops.html", context)


@router.get("/admin/automation-conversion/group-ops/groups/ui", name="api.admin_group_ops_groups_ui")
def admin_group_ops_groups_ui(request: Request) -> Response:
    context = _group_ops_page_context(
        request,
        page_title="查看所有群",
        page_summary="按群名、群主、所属计划和状态查看客户群。",
        page_mode="groups",
    )
    return templates.TemplateResponse(request, "admin_console/group_ops.html", context)
