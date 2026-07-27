from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime_settings import runtime_settings_request_scope

from .api import callback_router
from .callback_ingress import CALLBACK_ACK_BOUNDARY, CALLBACK_MAX_BODY_BYTES


def _include_callback_routes(app: FastAPI) -> None:
    for route in callback_router.routes:
        if not isinstance(route, APIRoute):
            app.router.routes.append(route)
            continue
        app.add_api_route(
            route.path,
            route.endpoint,
            methods=route.methods,
            name=route.name,
            include_in_schema=route.include_in_schema,
        )


def create_wecom_callback_ingress_app() -> FastAPI:
    app = FastAPI(title="AI-CRM WeCom Callback Ingress", version="0.1.0")

    @app.middleware("http")
    async def write_ingress_headers(request, call_next):
        with runtime_settings_request_scope():
            response = await call_next(request)
        response.headers.setdefault("X-AICRM-Route-Owner", "ai_crm_next")
        response.headers.setdefault("X-AICRM-Fallback-Used", "false")
        response.headers.setdefault("X-AICRM-App", "ai_crm_wecom_ingress")
        response.headers.setdefault("X-AICRM-Release-SHA", current_release_sha())
        return response

    @app.get("/health", name="wecom_callback_ingress_health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "runtime": "ai_crm_wecom_ingress",
                "route_owner": "ai_crm_next",
                "release_sha": current_release_sha(),
                "ack_boundary": CALLBACK_ACK_BOUNDARY,
                "durable_inbox_only": True,
                "max_body_bytes": CALLBACK_MAX_BODY_BYTES,
            }
        )

    _include_callback_routes(app)
    return app


app = create_wecom_callback_ingress_app()
