from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/admin/radar-links", name="api.admin_radar_links")
def admin_radar_links(request: Request) -> Response:
    context = shell_context(
        request=request,
        page_title="内容雷达",
        page_summary="管理可追踪的链接、图片和 PDF。配置和查看记录进入二级页面。",
        active_endpoint="api.admin_radar_links",
    )
    context["page_actions"] = [{"label": "新建内容雷达", "href": "/admin/radar-links/new", "variant": "primary"}]
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "内容雷达"},
    ]
    return templates.TemplateResponse(request, "admin_console/radar_links.html", context)


@router.get("/admin/radar-links/new", name="api.admin_radar_link_new")
def admin_radar_link_new(request: Request) -> Response:
    context = shell_context(
        request=request,
        page_title="新建内容雷达",
        page_summary="选择链接、图片或 PDF。素材可以从素材库选择，也可以上传。",
        active_endpoint="api.admin_radar_links",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "内容雷达", "href": "/admin/radar-links"},
        {"label": "新建内容雷达"},
    ]
    context["radar_form_mode"] = "new"
    context["radar_link_id"] = 0
    return templates.TemplateResponse(request, "admin_console/radar_link_form.html", context)


@router.get("/admin/radar-links/{link_id:int}/edit", name="api.admin_radar_link_edit")
def admin_radar_link_edit(request: Request, link_id: int) -> Response:
    context = shell_context(
        request=request,
        page_title="编辑内容雷达",
        page_summary="维护内容雷达的基础配置、素材来源与启用状态。",
        active_endpoint="api.admin_radar_links",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "内容雷达", "href": "/admin/radar-links"},
        {"label": f"编辑 #{link_id}"},
    ]
    context["radar_form_mode"] = "edit"
    context["radar_link_id"] = int(link_id)
    return templates.TemplateResponse(request, "admin_console/radar_link_form.html", context)


@router.get("/admin/radar-links/{link_id:int}/detail", name="api.admin_radar_link_detail")
def admin_radar_link_detail(request: Request, link_id: int) -> Response:
    context = shell_context(
        request=request,
        page_title="点击记录",
        page_summary="只展示已授权并取得 unionid 的访问记录，外部联系人 ID 由统一身份补齐。编辑请返回列表点击“编辑”。",
        active_endpoint="api.admin_radar_links",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "内容雷达", "href": "/admin/radar-links"},
        {"label": f"点击记录 #{link_id}"},
    ]
    context["radar_link_id"] = int(link_id)
    return templates.TemplateResponse(request, "admin_console/radar_link_detail.html", context)
