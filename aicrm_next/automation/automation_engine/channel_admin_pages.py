from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context
from aicrm_next.automation.automation_engine.channels_api import default_channel_form_payload, get_channel_resource

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _channel_form_payload(request: Request, *, channel: dict | None) -> dict:
    del request
    is_edit = bool(channel)
    channel_id = int((channel or {}).get("id") or 0)
    return {
        "channel": channel or default_channel_form_payload(),
        "is_edit": is_edit,
        "api_urls": {
            "channels": "/api/admin/channels",
            "detail": f"/api/admin/channels/{channel_id}" if is_edit else "",
            "qrcode_download": f"/api/admin/channels/{channel_id}/qrcode/download" if is_edit else "",
            "share_link": f"/api/admin/channels/{channel_id}/share-link" if is_edit else "",
            "welcome_materials": "/api/admin/channel-welcome-materials",
            "wecom_tags": "/api/admin/wecom/tags",
        },
    }


@router.get("/admin/channels", name="api.admin_channels_page")
async def admin_channels_page(request: Request) -> Response:
    context = shell_context(
        request=request,
        page_title="渠道码中心",
        page_summary="独立管理普通二维码和企微获客助手链接；渠道进入事实会进入 AI 人群包查询层。",
        active_endpoint="api.admin_channels_page",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "渠道码中心"},
            ],
            "state_title": "渠道码中心",
            "state_body": "渠道码中心是一级后台能力，用于查看、新建和维护独立获客渠道。",
            "state_items": [
                "普通二维码支持下载二维码",
                "企微获客助手链接支持复制链接和分享链接",
                "渠道进入记录可供 AI 人群包增量刷新使用",
            ],
            "actions": [
                {"label": "新建渠道", "href": admin_path_for("api.admin_channel_new_page"), "variant": "primary"},
                {"label": "自动化运营", "href": admin_path_for("api.admin_automation_conversion"), "variant": "secondary"},
            ],
            "channel_center_payload": {
                "api_urls": {
                    "channels": "/api/admin/channels?limit=300",
                    "contacts_base": "/api/admin/channels/0/contacts",
                }
            },
        }
    )
    return templates.TemplateResponse(request, "admin_console/channel_code_center.html", context)


@router.get("/admin/channels/new", name="api.admin_channel_new_page")
async def admin_channel_new_page(request: Request) -> Response:
    context = shell_context(
        request=request,
        page_title="新建渠道",
        page_summary="创建渠道资产本身；普通二维码和企微获客助手链接按载体类型显示不同操作。",
        active_endpoint="api.admin_channels_page",
    )
    context.update(
        {
            "show_page_header": False,
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "渠道码中心", "href": admin_path_for("api.admin_channels_page")},
                {"label": "新建渠道"},
            ],
            "channel_form_payload": _channel_form_payload(request, channel=None),
            "admin_action_token": "",
        }
    )
    return templates.TemplateResponse(request, "admin_console/channel_code_form.html", context)


@router.get("/admin/channels/{channel_id:int}/edit", name="api.admin_channel_edit_page")
async def admin_channel_edit_page(request: Request, channel_id: int) -> Response:
    channel = get_channel_resource(int(channel_id))
    if not channel:
        context = shell_context(
            request=request,
            page_title="渠道不存在",
            page_summary="当前没有找到这个渠道。",
            active_endpoint="api.admin_channels_page",
        )
        context.update(
            {
                "breadcrumbs": [
                    {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                    {"label": "渠道码中心", "href": admin_path_for("api.admin_channels_page")},
                    {"label": f"编辑渠道 {channel_id}"},
                ],
                "state_title": "渠道不存在",
                "state_body": "请确认渠道编号是否正确，或回到渠道码中心重新选择。",
                "state_items": ["渠道可能已被删除", "当前环境也可能还没有初始化渠道数据"],
                "actions": [
                    {
                        "label": "返回渠道码中心",
                        "href": admin_path_for("api.admin_channels_page"),
                        "variant": "secondary",
                    }
                ],
            }
        )
        return templates.TemplateResponse(request, "admin_console/placeholder.html", context, status_code=404)
    context = shell_context(
        request=request,
        page_title="编辑渠道",
        page_summary="维护渠道本体、欢迎语、素材配置、标签和客服分配。",
        active_endpoint="api.admin_channels_page",
    )
    context.update(
        {
            "show_page_header": False,
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "渠道码中心", "href": admin_path_for("api.admin_channels_page")},
                {"label": f"编辑渠道 {channel_id}"},
            ],
            "channel_form_payload": _channel_form_payload(request, channel=channel),
            "admin_action_token": "",
        }
    )
    return templates.TemplateResponse(request, "admin_console/channel_code_form.html", context)
