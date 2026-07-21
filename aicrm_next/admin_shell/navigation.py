from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import Request

from aicrm_next.shared.admin_action_runtime import admin_action_token_bundle


@dataclass(frozen=True)
class AdminRoute:
    endpoint: str
    path: str


ADMIN_ROUTE_REGISTRY: dict[str, AdminRoute] = {
    "api.admin_console_dashboard": AdminRoute("api.admin_console_dashboard", "/admin"),
    "api.admin_console_customers": AdminRoute("api.admin_console_customers", "/admin/customers"),
    "api.admin_owner_migration_page": AdminRoute("api.admin_owner_migration_page", "/admin/owner-migration"),
    "api.admin_owner_migration_action": AdminRoute("api.admin_owner_migration_action", "/admin/owner-migration"),
    "api.admin_user_ops_ui": AdminRoute("api.admin_user_ops_ui", "/admin/user-ops/ui"),
    "api.admin_hxc_dashboard_workspace": AdminRoute("api.admin_hxc_dashboard_workspace", "/admin/hxc-dashboard"),
    "api.admin_hxc_send_config_page": AdminRoute("api.admin_hxc_send_config_page", "/admin/hxc-send-config"),
    "api.admin_cloud_orchestrator_workspace": AdminRoute(
        "api.admin_cloud_orchestrator_workspace",
        "/admin/cloud-orchestrator/plans",
    ),
    "api.admin_cloud_orchestrator_plans_workspace": AdminRoute(
        "api.admin_cloud_orchestrator_plans_workspace",
        "/admin/cloud-orchestrator/plans",
    ),
    "api.admin_cloud_orchestrator_campaigns_workspace": AdminRoute(
        "api.admin_cloud_orchestrator_campaigns_workspace",
        "/admin/cloud-orchestrator/campaigns",
    ),
    "api.admin_cloud_orchestrator_observability": AdminRoute(
        "api.admin_cloud_orchestrator_observability",
        "/admin/cloud-orchestrator/observability",
    ),
    "api.admin_wecom_tags_page": AdminRoute("api.admin_wecom_tags_page", "/admin/wecom-tags"),
    "api.admin_channels_page": AdminRoute("api.admin_channels_page", "/admin/channels"),
    "api.admin_channel_new_page": AdminRoute("api.admin_channel_new_page", "/admin/channels/new"),
    "api.admin_questionnaires": AdminRoute("api.admin_questionnaires", "/admin/questionnaires"),
    "api.admin_console_questionnaires": AdminRoute("api.admin_console_questionnaires", "/admin/questionnaires"),
    "api.admin_console_questionnaire_new": AdminRoute(
        "api.admin_console_questionnaire_new",
        "/admin/questionnaires/new",
    ),
    "api.admin_radar_links": AdminRoute("api.admin_radar_links", "/admin/radar-links"),
    "api.admin_radar_link_new": AdminRoute("api.admin_radar_link_new", "/admin/radar-links/new"),
    "api.admin_automation_conversion": AdminRoute("api.admin_automation_conversion", "/admin/automation-conversion"),
    "api.admin_automation_agents_page": AdminRoute("api.admin_automation_agents_page", "/admin/automation-agents"),
    "api.admin_group_ops_ui": AdminRoute("api.admin_group_ops_ui", "/admin/automation-conversion/group-ops/ui"),
    "api.admin_group_ops_groups_ui": AdminRoute(
        "api.admin_group_ops_groups_ui",
        "/admin/automation-conversion/group-ops/groups/ui",
    ),
    "api.admin_jobs": AdminRoute("api.admin_jobs", "/admin/jobs"),
    "api.admin_push_center_page": AdminRoute("api.admin_push_center_page", "/admin/push-center"),
    "api.admin_internal_events_page": AdminRoute("api.admin_internal_events_page", "/admin/internal-events"),
    "api.admin_webhook_inbox_page": AdminRoute("api.admin_webhook_inbox_page", "/admin/webhook-inbox"),
    "api.admin_broadcast_jobs": AdminRoute("api.admin_broadcast_jobs", "/admin/broadcast-jobs"),
    "api.admin_console_jobs_action": AdminRoute("api.admin_console_jobs_action", "/admin/jobs/actions"),
    "api.admin_wechat_pay_transactions_page": AdminRoute(
        "api.admin_wechat_pay_transactions_page",
        "/admin/wechat-pay/transactions",
    ),
    "api.admin_orders_page": AdminRoute("api.admin_orders_page", "/admin/orders"),
    "api.admin_wechat_pay_products_page": AdminRoute(
        "api.admin_wechat_pay_products_page",
        "/admin/wechat-pay/products",
    ),
    "api.admin_service_period_products_page": AdminRoute(
        "api.admin_service_period_products_page",
        "/admin/service-period-products",
    ),
    "api.admin_coupons_page": AdminRoute("api.admin_coupons_page", "/admin/coupons"),
    "api.admin_alipay_transactions_page": AdminRoute("api.admin_alipay_transactions_page", "/admin/alipay/transactions"),
    "api.admin_image_library_workspace": AdminRoute("api.admin_image_library_workspace", "/admin/image-library"),
    "api.admin_miniprogram_library_workspace": AdminRoute(
        "api.admin_miniprogram_library_workspace",
        "/admin/miniprogram-library",
    ),
    "api.admin_attachment_library_workspace": AdminRoute(
        "api.admin_attachment_library_workspace",
        "/admin/attachment-library",
    ),
    "api.admin_group_invite_library_workspace": AdminRoute(
        "api.admin_group_invite_library_workspace",
        "/admin/group-invite-library",
    ),
    "api.admin_config": AdminRoute("api.admin_config", "/admin/config"),
    "api.admin_config_app_settings": AdminRoute("api.admin_config_app_settings", "/admin/config/app-settings"),
    "api.admin_api_docs": AdminRoute("api.admin_api_docs", "/admin/api-docs"),
    "api.admin_console_api_docs": AdminRoute("api.admin_console_api_docs", "/admin/api-docs"),
    "api.admin_console_jobs": AdminRoute("api.admin_console_jobs", "/admin/jobs"),
    "api.admin_data_health_page": AdminRoute("api.admin_data_health_page", "/admin/data-health"),
    "api.admin_data_quality_page": AdminRoute("api.admin_data_quality_page", "/admin/data-quality"),
    "api.admin_delivery_lineage_page": AdminRoute("api.admin_delivery_lineage_page", "/admin/delivery-lineage"),
    "api.admin_growth_orchestration_page": AdminRoute(
        "api.admin_growth_orchestration_page",
        "/admin/growth-orchestration",
    ),
    "api.admin_operation_cycles_page": AdminRoute(
        "api.admin_operation_cycles_page",
        "/admin/operation-cycles",
    ),
    "api.admin_dashboard_shell_context": AdminRoute(
        "api.admin_dashboard_shell_context",
        "/api/admin/dashboard/shell-context",
    ),
    "api.admin_logout": AdminRoute("api.admin_logout", "/logout"),
}


def admin_path_for(name: str, **path_params: object) -> str:
    if name == "static":
        return "/static/" + str(path_params.get("filename", "")).lstrip("/")
    if name == "api.admin_console_customer_detail":
        external_userid = str(path_params.get("external_userid", ""))
        return f"/admin/customers/{quote(external_userid, safe='')}"
    if name == "api.admin_cloud_orchestrator_plan_detail":
        plan_id = quote(str(path_params.get("plan_id", "")).strip(), safe="")
        return f"/admin/cloud-orchestrator/plans/{plan_id}"
    if name == "api.admin_channel_edit_page":
        return "/admin/channels/" + str(path_params.get("channel_id", "")).strip() + "/edit"
    if name in {"api.admin_radar_link_edit", "api.admin_radar_link_detail"}:
        suffix = "edit" if name == "api.admin_radar_link_edit" else "detail"
        return "/admin/radar-links/" + str(path_params.get("link_id", "")).strip() + f"/{suffix}"
    if name == "api.admin_group_ops_plan_detail":
        return "/admin/automation-conversion/group-ops/plans/" + str(path_params.get("plan_id", "")).strip()
    if name == "api.admin_wechat_pay_transaction_detail_page":
        return "/admin/wechat-pay/transactions/" + str(path_params.get("order_id", "")).strip()
    if name == "api.admin_wechat_shop_transaction_detail_page":
        return "/admin/wechat-shop/transactions/" + str(path_params.get("order_id", "")).strip()
    if name == "api.admin_console_questionnaire_detail":
        return "/admin/questionnaires/" + str(path_params.get("questionnaire_id", "")).strip()
    if name == "api.admin_growth_orchestration_detail_page":
        program_key = quote(str(path_params.get("program_key", "")).strip(), safe="")
        return f"/admin/growth-orchestration/{program_key}"
    if name == "api.admin_operation_cycle_strategy_page":
        strategy_key = quote(str(path_params.get("strategy_key", "")).strip(), safe="")
        return f"/admin/operation-cycles/{strategy_key}"
    if name == "api.admin_operation_cycle_run_page":
        strategy_key = quote(str(path_params.get("strategy_key", "")).strip(), safe="")
        run_key = quote(str(path_params.get("run_key", "")).strip(), safe="")
        return f"/admin/operation-cycles/{strategy_key}/runs/{run_key}"
    if name == "api.admin_push_center_job_page":
        job_id = quote(str(path_params.get("job_id", "")).strip(), safe=":")
        return f"/admin/push-center/jobs/{job_id}"
    if name == "api.admin_internal_event_page":
        event_id = quote(str(path_params.get("event_id", "")).strip(), safe="")
        return f"/admin/internal-events/{event_id}"
    if name == "api.admin_broadcast_job_page":
        job_id = quote(str(path_params.get("job_id", "")).strip(), safe="")
        return f"/admin/broadcast-jobs/{job_id}"

    route = ADMIN_ROUTE_REGISTRY.get(name)
    base = route.path if route else "#"
    query = {key: value for key, value in path_params.items() if value not in (None, "")}
    return base + (f"?{urlencode(query)}" if query else "")


ADMIN_NAV_GROUPS: list[dict[str, Any]] = [
    {
        "title": "运营",
        "items": [
            {"key": "automation_conversion", "label": "自动化运营", "endpoint": "api.admin_automation_conversion"},
            {"key": "operation_cycles", "label": "运营闭环", "endpoint": "api.admin_operation_cycles_page"},
            {"key": "group_ops", "label": "群运营计划", "endpoint": "api.admin_group_ops_ui"},
            {"key": "channels", "label": "渠道码中心", "endpoint": "api.admin_channels_page"},
            {"key": "cloud_orchestrator", "label": "AI 助手", "endpoint": "api.admin_cloud_orchestrator_workspace"},
            {"key": "customers", "label": "客户激活 / 客户列表", "endpoint": "api.admin_console_customers"},
            {"key": "user_ops_funnel", "label": "漏斗 / 数据看板", "endpoint": "api.admin_hxc_dashboard_workspace"},
            {"key": "questionnaires", "label": "问卷", "endpoint": "api.admin_questionnaires"},
            {"key": "radar_links", "label": "内容雷达", "endpoint": "api.admin_radar_links"},
            {"key": "wecom_tags", "label": "企微标签管理", "endpoint": "api.admin_wecom_tags_page"},
        ],
    },
    {
        "title": "交易",
        "items": [
            {"key": "wechat_pay_transactions", "label": "交易管理", "endpoint": "api.admin_orders_page"},
            {"key": "wechat_pay_products", "label": "商品管理", "endpoint": "api.admin_wechat_pay_products_page"},
            {"key": "service_period_products", "label": "周期商品管理", "endpoint": "api.admin_service_period_products_page"},
            {"key": "coupons", "label": "优惠券", "endpoint": "api.admin_coupons_page"},
        ],
    },
    {
        "title": "素材",
        "items": [
            {"key": "image_library", "label": "图片素材库", "endpoint": "api.admin_image_library_workspace"},
            {"key": "miniprogram_library", "label": "小程序素材库", "endpoint": "api.admin_miniprogram_library_workspace"},
            {"key": "attachment_library", "label": "附件素材库", "endpoint": "api.admin_attachment_library_workspace"},
        ],
    },
    {
        "title": "配置及后台",
        "items": [
            {"key": "group_invite_library", "label": "群邀请托管", "endpoint": "api.admin_group_invite_library_workspace"},
            {"key": "jobs", "label": "同步任务配置 / 同步任务", "endpoint": "api.admin_jobs"},
            {"key": "push_center", "label": "推送中心", "endpoint": "api.admin_push_center_page"},
            {"key": "internal_events", "label": "事件中心", "endpoint": "api.admin_internal_events_page"},
            {"key": "webhook_inbox", "label": "Webhook Inbox", "endpoint": "api.admin_webhook_inbox_page"},
            {"key": "data_health", "label": "数据健康", "endpoint": "api.admin_data_health_page"},
            {"key": "data_quality", "label": "数据质量规则", "endpoint": "api.admin_data_quality_page"},
            {"key": "delivery_lineage", "label": "投递排障", "endpoint": "api.admin_delivery_lineage_page"},
            {"key": "growth_orchestration", "label": "增长运营", "endpoint": "api.admin_growth_orchestration_page"},
            {"key": "automation_agents", "label": "自动化话术", "endpoint": "api.admin_automation_agents_page"},
            {"key": "owner_migration", "label": "负责人迁移", "endpoint": "api.admin_owner_migration_page"},
            {"key": "config", "label": "配置", "endpoint": "api.admin_config"},
            {"key": "api_docs", "label": "API 文档", "endpoint": "api.admin_api_docs"},
        ],
    },
]


def nav_items(active_endpoint: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for group in ADMIN_NAV_GROUPS:
        items = [
            {
                **item,
                "active": item["endpoint"] == active_endpoint,
                "href": admin_path_for(str(item["endpoint"])),
            }
            for item in group["items"]
        ]
        groups.append({**group, "items": items, "active": any(item["active"] for item in items)})
    return groups


def shell_context(
    *,
    request: Request,
    page_title: str,
    page_summary: str,
    active_endpoint: str,
) -> dict[str, Any]:
    return {
        "request": request,
        "page_title": page_title,
        "page_summary": page_summary,
        "breadcrumbs": [{"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")}],
        "nav_items": nav_items(active_endpoint),
        "current_admin_user": None,
        "show_shell_meta": False,
        "shell_status": {
            "environment": {"tone": "prod", "label": "AI-CRM Next"},
            "health": {"state": "ok", "label": "OK", "detail": "postgres"},
        },
        "page_notice": "",
        "page_error": "",
        "admin_path_for": admin_path_for,
        "url_for": admin_path_for,
        "admin_action_tokens": admin_action_token_bundle(request),
    }
