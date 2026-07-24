from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell import shell_context

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/admin/image-library", name="api.admin_image_library_workspace")
def admin_image_library(request: Request):
    context = shell_context(
        request=request,
        page_title="图片素材库",
        page_summary="集中维护可被群发 / 卡片 / 自动化欢迎语等场景引用的图片，支持上传和外链。",
        active_endpoint="api.admin_image_library_workspace",
    )
    return templates.TemplateResponse(request, "admin_console/image_library.html", context)


@router.get("/admin/miniprogram-library", name="api.admin_miniprogram_library_workspace")
def admin_miniprogram_library(request: Request):
    context = shell_context(
        request=request,
        page_title="小程序素材库",
        page_summary="维护群发和自动化可复用的小程序卡片。",
        active_endpoint="api.admin_miniprogram_library_workspace",
    )
    return templates.TemplateResponse(request, "admin_console/miniprogram_library.html", context)


@router.get("/admin/attachment-library", name="api.admin_attachment_library_workspace")
def admin_attachment_library(request: Request):
    context = shell_context(
        request=request,
        page_title="附件素材库",
        page_summary="维护 PDF、附件和课程资料等可复用素材。",
        active_endpoint="api.admin_attachment_library_workspace",
    )
    return templates.TemplateResponse(request, "admin_console/attachment_library.html", context)
