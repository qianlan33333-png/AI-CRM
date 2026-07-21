from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

try:  # FastAPI 0.139 keeps included router routes behind include contexts.
    from fastapi.routing import _iter_routes_with_context
except ImportError:  # FastAPI <=0.136 materializes included routes eagerly.
    def _iter_routes_with_context(routes):
        return ()

from .ai_assist.api import router as ai_assist_router
from .ai_audience_ops.admin_api import router as ai_audience_admin_api_router
from .ai_audience_ops.admin_pages import router as ai_audience_admin_pages_router
from .ai_audience_ops.api import router as ai_audience_ops_router
from .ai_audience_ops.external_api import router as ai_audience_external_api_router
from .admin_auth.api import router as admin_auth_router
from .admin_config.api import router as admin_config_router
from .admin_jobs.routes import router as admin_jobs_router
from .admin_shell.routes import router as admin_shell_router
from .automation_agents.admin_api import router as automation_agents_admin_router
from .automation_agents.admin_pages import router as automation_agents_admin_pages_router
from .automation_agents.api import router as automation_agents_router
from .automation_engine.api import router as automation_router
from .automation_engine.channel_admin_pages import router as channel_admin_pages_router
from .automation_engine.channels_api import router as automation_channels_router
from .automation_engine.group_ops.admin_pages import router as group_ops_admin_pages_router
from .auth_wecom.api import router as auth_wecom_router
from .channel_entry.api import router as channel_entry_router
from .class_user_management.api import router as class_user_management_router
from .cloud_orchestrator.api import router as cloud_orchestrator_router
from .commerce.api import router as commerce_router
from .commerce.coupons.admin_api import router as coupons_admin_api_router
from .commerce.coupons.admin_pages import router as coupons_admin_pages_router
from .commerce.coupons.public_api import router as coupons_public_router
from .commerce.coupons.sidebar_api import router as coupons_sidebar_router
from .common_operation_members import router as common_operation_members_router
from .customer_read_model.admin_pages import router as customer_admin_pages_router
from .customer_read_model.api import router as customer_router
from .customer_tags.admin_pages import router as customer_tags_admin_pages_router
from .customer_tags.api import read_router as customer_tags_read_router
from .customer_tags.api import router as customer_tags_router
from .customer_tags.api import write_router as customer_tags_write_router
from .data_health.api import router as data_health_router
from .delivery_lineage.api import router as delivery_lineage_router
from .growth_orchestration.api import router as growth_orchestration_router
from .hxc_dashboard.api import router as hxc_dashboard_router
from .identity_contact.admin_pages import router as identity_admin_pages_router
from .identity_contact.api import router as identity_router
from .identity_contact.sidebar_jssdk import router as sidebar_jssdk_router
from .integration_gateway.api import router as mcp_router
from .media_library.admin_pages import router as media_library_admin_pages_router
from .media_library.api import router as media_library_router
from .message_archive.api import router as message_archive_router
from .ops_enrollment.admin_pages import router as user_ops_admin_pages_router
from .ops_enrollment.api import router as user_ops_router
from .operation_cycles.admin_pages import router as operation_cycles_admin_pages_router
from .operation_cycles.api import router as operation_cycles_router
from .owner_migration.api import router as owner_migration_router
from .platform_foundation.api import router as platform_router
from .platform_foundation.auth_platform.api import router as auth_platform_router
from .platform_foundation.external_effects.api import router as external_effects_router
from .platform_foundation.execution_runtime.api import router as execution_runtime_router
from .platform_foundation.internal_events.api import router as internal_events_router
from .platform_foundation.push_center.api import router as push_center_router
from .platform_foundation.verification_files import router as verification_files_router
from .platform_foundation.webhook_inbox.api import router as webhook_inbox_router
from .public_product.api import router as public_product_router
from .questionnaire.admin_pages import router as questionnaire_admin_pages_router
from .questionnaire.api import router as questionnaire_router
from .radar_links.admin_pages import router as radar_links_admin_pages_router
from .radar_links.api import router as radar_links_router
from .send_content.api import router as send_content_router
from .service_period.api import router as service_period_router
from .sidebar_write.api import router as sidebar_write_router


@dataclass(frozen=True)
class RouterSpec:
    capability_owner: str
    route_group: str
    router: APIRouter
    notes: str = ""


ROUTER_SPECS: tuple[RouterSpec, ...] = (
    RouterSpec("platform_foundation", "platform", platform_router, "foundation health and shell contracts"),
    RouterSpec("platform_foundation", "auth_platform", auth_platform_router, "unified OAuth 2.0 and OIDC platform"),
    RouterSpec("platform_foundation", "external_effects", external_effects_router, "external effects job/admin APIs"),
    RouterSpec(
        "platform_foundation",
        "execution_runtime",
        execution_runtime_router,
        "lane runtime and unified execution timeline read APIs",
    ),
    RouterSpec("platform_foundation", "internal_events", internal_events_router, "internal event center APIs"),
    RouterSpec("platform_foundation", "push_center", push_center_router, "push center APIs"),
    RouterSpec("platform_foundation", "webhook_inbox", webhook_inbox_router, "webhook inbox metrics and operations APIs"),
    RouterSpec("admin_auth", "admin_auth", admin_auth_router, "admin auth APIs"),
    RouterSpec("admin_shell", "admin_shell", admin_shell_router, "admin shell pages"),
    RouterSpec("data_health", "data_health", data_health_router, "data health check APIs"),
    RouterSpec("delivery_lineage", "delivery_lineage", delivery_lineage_router, "delivery lineage read APIs"),
    RouterSpec("growth_orchestration", "growth_orchestration", growth_orchestration_router, "growth orchestration read APIs"),
    RouterSpec("operation_cycles", "operation_cycles_admin_pages", operation_cycles_admin_pages_router, "operation cycle read-only admin pages"),
    RouterSpec("operation_cycles", "operation_cycles", operation_cycles_router, "operation cycle report and admin read APIs"),
    RouterSpec("admin_config", "admin_config", admin_config_router, "admin config pages and APIs"),
    RouterSpec("class_user_management", "class_user_management", class_user_management_router),
    RouterSpec("platform_foundation", "common_operation_members", common_operation_members_router),
    RouterSpec("channel_entry", "channel_entry", channel_entry_router),
    RouterSpec("automation_engine", "automation_channels", automation_channels_router),
    RouterSpec("automation_agents", "automation_agents_admin_pages", automation_agents_admin_pages_router, "Automation agent admin config pages"),
    RouterSpec("automation_agents", "automation_agents_admin_api", automation_agents_admin_router, "Automation agent admin config APIs"),
    RouterSpec("automation_agents", "automation_agents_api", automation_agents_router, "Automation agent audience webhook APIs"),
    RouterSpec("hxc_dashboard", "hxc_dashboard", hxc_dashboard_router),
    RouterSpec("public_product", "public_product", public_product_router),
    RouterSpec("service_period", "service_period", service_period_router, "service period products and entitlements"),
    RouterSpec("sidebar_write", "sidebar_write", sidebar_write_router),
    RouterSpec("identity_contact", "sidebar_jssdk", sidebar_jssdk_router),
    RouterSpec("customer_tags", "customer_tags_read", customer_tags_read_router),
    RouterSpec("customer_tags", "customer_tags_write", customer_tags_write_router),
    RouterSpec("cloud_orchestrator", "cloud_orchestrator", cloud_orchestrator_router),
    RouterSpec("customer_read_model", "customer_read_model", customer_router),
    RouterSpec("customer_read_model", "customer_admin_pages", customer_admin_pages_router),
    RouterSpec("customer_tags", "customer_tags", customer_tags_router),
    RouterSpec("ops_enrollment", "user_ops", user_ops_router),
    RouterSpec("ops_enrollment", "user_ops_admin_pages", user_ops_admin_pages_router),
    RouterSpec("integration_gateway", "mcp", mcp_router),
    RouterSpec("identity_contact", "identity", identity_router),
    RouterSpec("identity_contact", "identity_admin_pages", identity_admin_pages_router),
    RouterSpec("message_archive", "message_archive", message_archive_router),
    RouterSpec("questionnaire", "questionnaire_admin_pages", questionnaire_admin_pages_router),
    RouterSpec("questionnaire", "questionnaire", questionnaire_router),
    RouterSpec("radar_links", "radar_links_admin_pages", radar_links_admin_pages_router),
    RouterSpec("radar_links", "radar_links", radar_links_router),
    RouterSpec("auth_wecom", "auth_wecom", auth_wecom_router),
    RouterSpec("automation_engine", "group_ops_admin_pages", group_ops_admin_pages_router),
    RouterSpec("ai_audience_ops", "ai_audience_admin_pages", ai_audience_admin_pages_router, "AI audience admin package list page"),
    RouterSpec("ai_audience_ops", "ai_audience_admin_api", ai_audience_admin_api_router, "AI audience admin read APIs"),
    RouterSpec("ai_audience_ops", "ai_audience_external_api", ai_audience_external_api_router, "AI audience external package spec APIs"),
    RouterSpec("automation_engine", "channel_admin_pages", channel_admin_pages_router),
    RouterSpec("customer_tags", "customer_tags_admin_pages", customer_tags_admin_pages_router),
    RouterSpec("automation_engine", "automation", automation_router),
    RouterSpec("commerce", "commerce", commerce_router),
    RouterSpec("commerce", "coupons_admin_pages", coupons_admin_pages_router, "fixed-amount coupon admin pages"),
    RouterSpec("commerce", "coupons_admin_api", coupons_admin_api_router, "fixed-amount coupon admin APIs"),
    RouterSpec("commerce", "coupons_public", coupons_public_router, "coupon claim and availability APIs"),
    RouterSpec("commerce", "coupons_sidebar", coupons_sidebar_router, "signed sidebar coupon read API"),
    RouterSpec("media_library", "media_library", media_library_router),
    RouterSpec("media_library", "media_library_admin_pages", media_library_admin_pages_router),
    RouterSpec("ai_assist", "ai_assist", ai_assist_router),
    RouterSpec("ai_audience_ops", "ai_audience_ops", ai_audience_ops_router, "AI audience package SQL refresh APIs"),
    RouterSpec("send_content", "send_content", send_content_router),
    RouterSpec("admin_jobs", "admin_jobs", admin_jobs_router),
    RouterSpec("owner_migration", "owner_migration", owner_migration_router),
    RouterSpec("platform_foundation", "verification_files", verification_files_router, "WeChat and WeCom root verification files"),
)


def register_routers(app: FastAPI, specs: tuple[RouterSpec, ...] = ROUTER_SPECS) -> None:
    for spec in specs:
        app.include_router(spec.router)
    _materialize_included_router_routes(app)


def router_registry_summary(specs: tuple[RouterSpec, ...] = ROUTER_SPECS) -> list[dict[str, Any]]:
    return [
        {
            "capability_owner": spec.capability_owner,
            "route_group": spec.route_group,
            "route_count": len(getattr(spec.router, "routes", ()) or ()),
            "notes": spec.notes,
        }
        for spec in specs
    ]


def _materialize_included_router_routes(app: FastAPI) -> None:
    """Keep app.routes concrete for route-owner probes under FastAPI 0.139."""
    routes = list(app.router.routes)
    app.router.routes = [
        route
        for route in routes
        if not (getattr(route, "original_router", None) is not None or getattr(route, "include_context", None) is not None)
    ]
    for route, context in _iter_routes_with_context(routes):
        if context is None:
            continue
        if isinstance(route, APIRoute):
            app.router.add_api_route(
                context.path,
                route.endpoint,
                response_model=context.response_model,
                status_code=context.status_code,
                tags=context.tags,
                dependencies=context.dependencies,
                summary=context.summary,
                description=context.description,
                response_description=context.response_description,
                responses=context.responses,
                deprecated=context.deprecated,
                methods=context.methods,
                operation_id=context.operation_id,
                response_model_include=context.response_model_include,
                response_model_exclude=context.response_model_exclude,
                response_model_by_alias=context.response_model_by_alias,
                response_model_exclude_unset=context.response_model_exclude_unset,
                response_model_exclude_defaults=context.response_model_exclude_defaults,
                response_model_exclude_none=context.response_model_exclude_none,
                include_in_schema=context.include_in_schema,
                response_class=context.response_class,
                name=context.name,
                callbacks=context.callbacks,
                openapi_extra=context.openapi_extra,
                generate_unique_id_function=context.generate_unique_id_function,
                strict_content_type=context.strict_content_type,
            )
            continue
        app.router.routes.append(route)
