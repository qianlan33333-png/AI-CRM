from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .ai_audience_e2e_composition import build_ai_audience_e2e_runner_factory
from . import fixture_reset_registry
from .admin_auth.route_policy import route_policy_required_response
from .admin_auth.action_token import build_admin_action_token_bundle, validate_action_token_for_request
from .admin_config.pii_audit_repository import AdminConfigPiiAuditRepository
from .automation_engine.repo import reset_automation_fixture_state
from .automation_engine.channel_completion import ChannelQrReadService
from .channel_entry_composition import build_wecom_callback_inbox_worker_factory
from .commerce.repo import reset_commerce_fixture_state
from .external_effect_composition import (
    build_external_effect_adapter_registry,
    build_external_effect_continuation_registry,
)
from .internal_event_composition import build_internal_event_consumer_registry
from .integration_gateway.channel_completion_client import configure_channel_completion_provider
from .media_library.repo import reset_media_library_fixture_state
from .mcp_composition import build_mcp_jsonrpc_application
from .ops_enrollment.application import reset_user_ops_fixture_state
from .platform_foundation.internal_events import internal_event_consumer_registry_scope
from .questionnaire.repo import reset_questionnaire_fixture_state
from .read_model_composition import build_sidebar_contact_binding_status_query, get_customer_detail
from .radar_links.repo import reset_radar_links_fixture_state
from .service_period_composition import build_service_period_member_grid_access_service
from .router_registry import register_routers
from .shared.errors import ApplicationError
from .shared.repository_provider import RepositoryProviderError
from .shared.release import current_release_sha
from .shared.pii_audit import PiiAuditRepository, apply_pii_audit, pii_audit_enabled
from .shared.route_policy import RoutePolicyIndex
from .shared.runtime import assert_required_runtime_secrets, fixture_mode, public_https_environment, require_signing_secret
from .shared.runtime_settings import runtime_settings_request_scope
from .shared.safe_logging import safe_log_exception

__all__ = [
    "app",
    "create_app",
    "reset_automation_fixture_state",
    "reset_commerce_fixture_state",
    "reset_media_library_fixture_state",
    "reset_questionnaire_fixture_state",
    "reset_radar_links_fixture_state",
    "reset_user_ops_fixture_state",
]

_FRONTEND_COMPAT_DIR = Path(__file__).resolve().parent / "frontend_compat"
_OPERATION_CYCLES_DIR = Path(__file__).resolve().parent / "operation_cycles"
_GROUP_OPS_DIR = Path(__file__).resolve().parent / "automation_engine" / "group_ops"
_AUTOMATION_ENGINE_DIR = Path(__file__).resolve().parent / "automation_engine"
_CUSTOMER_TAGS_DIR = Path(__file__).resolve().parent / "customer_tags"
_QUESTIONNAIRE_DIR = Path(__file__).resolve().parent / "questionnaire"
_NAVIGATION_TARGET_DIR = Path(__file__).resolve().parent / "navigation_target"
_SERVICE_PERIOD_DIR = Path(__file__).resolve().parent / "service_period"
logger = logging.getLogger(__name__)


def create_app(*, pii_audit_repository: PiiAuditRepository | None = None) -> FastAPI:
    assert_required_runtime_secrets()
    configure_channel_completion_provider(ChannelQrReadService())
    app = FastAPI(title="AI-CRM Next", version="0.1.0")
    app.state.admin_action_token_bundle_builder = build_admin_action_token_bundle
    app.state.admin_action_token_validator = validate_action_token_for_request
    app.state.mcp_jsonrpc_application = build_mcp_jsonrpc_application()
    app.state.external_effect_adapter_registry = build_external_effect_adapter_registry()
    app.state.wecom_callback_inbox_worker_factory = build_wecom_callback_inbox_worker_factory(
        external_effect_adapter_registry=app.state.external_effect_adapter_registry,
    )
    app.state.external_effect_continuation_registry = build_external_effect_continuation_registry()
    app.state.ai_audience_e2e_runner_factory = build_ai_audience_e2e_runner_factory(
        external_effect_adapter_registry=app.state.external_effect_adapter_registry,
    )
    app.state.internal_event_consumer_registry = build_internal_event_consumer_registry()
    app.state.sidebar_contact_binding_status_query_factory = build_sidebar_contact_binding_status_query
    app.state.external_customer_detail_query = get_customer_detail
    app.state.service_period_member_grid_access_service_factory = build_service_period_member_grid_access_service

    if fixture_mode():
        fixture_reset_registry.reset_fixture_state()
    route_policy_index = RoutePolicyIndex.from_manifest()
    audit_repository = pii_audit_repository or AdminConfigPiiAuditRepository()
    pii_fingerprint_secret = require_signing_secret("SECRET_KEY", local_fallback="aicrm-next-local-secret")

    @app.exception_handler(RepositoryProviderError)
    async def repository_provider_error_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "degraded": True,
                "source_status": "production_unavailable",
                "error_code": "fixture_repository_blocked_in_production",
                "detail": str(exc),
            },
        )

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request, exc):
        return JSONResponse(
            status_code=int(getattr(exc, "status_code", 400) or 400),
            content={
                "ok": False,
                "error_code": _application_error_code(exc),
                "detail": str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request, exc):
        safe_log_exception(logger, "unhandled ai-crm next exception", exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "internal_server_error",
                "detail": "internal server error",
            },
        )

    @app.middleware("http")
    async def write_route_owner_headers(request, call_next):
        with runtime_settings_request_scope():
            with internal_event_consumer_registry_scope(app.state.internal_event_consumer_registry):
                auth_response = await route_policy_required_response(request, app=app, index=route_policy_index)
                if auth_response is not None:
                    response = auth_response
                else:
                    response = await call_next(request)
            if pii_audit_enabled():
                response = apply_pii_audit(
                    request=request,
                    response=response,
                    repository=audit_repository,
                    fingerprint_secret=pii_fingerprint_secret,
                )
            response.headers.setdefault("X-AICRM-Route-Owner", "ai_crm_next")
            response.headers.setdefault("X-AICRM-Fallback-Used", "false")
            response.headers.setdefault("X-AICRM-App", "ai_crm_next")
            response.headers.setdefault("X-AICRM-Release-SHA", current_release_sha())
            if public_https_environment():
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            if request.url.path == "/sidebar/bind-mobile" or request.url.path.startswith("/api/sidebar/"):
                response.headers.setdefault("Cache-Control", "no-store, max-age=0")
                response.headers.setdefault("Pragma", "no-cache")
                response.headers.setdefault("Expires", "0")
            return response

    app.mount(
        "/static/group-ops",
        StaticFiles(directory=_GROUP_OPS_DIR / "static"),
        name="group_ops_static",
    )
    app.mount(
        "/static/automation-engine",
        StaticFiles(directory=_AUTOMATION_ENGINE_DIR / "static"),
        name="automation_engine_static",
    )
    app.mount(
        "/static/customer-tags",
        StaticFiles(directory=_CUSTOMER_TAGS_DIR / "static"),
        name="customer_tags_static",
    )
    app.mount(
        "/static/questionnaire",
        StaticFiles(directory=_QUESTIONNAIRE_DIR / "static"),
        name="questionnaire_static",
    )
    app.mount(
        "/static/navigation-target",
        StaticFiles(directory=_NAVIGATION_TARGET_DIR / "static"),
        name="navigation_target_static",
    )
    app.mount(
        "/static/operation-cycles",
        StaticFiles(directory=_OPERATION_CYCLES_DIR / "static"),
        name="operation_cycles_static",
    )
    app.mount(
        "/static/service-period",
        StaticFiles(directory=_SERVICE_PERIOD_DIR / "static"),
        name="service_period_static",
    )
    app.mount(
        "/static",
        StaticFiles(directory=_FRONTEND_COMPAT_DIR / "static"),
        name="static",
    )
    register_routers(app)
    return app


def _application_error_code(exc: ApplicationError) -> str:
    name = type(exc).__name__
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    error_code = "".join(chars)
    if error_code.endswith("_error"):
        error_code = error_code[: -len("_error")]
    return error_code or "application_error"


app = create_app()
