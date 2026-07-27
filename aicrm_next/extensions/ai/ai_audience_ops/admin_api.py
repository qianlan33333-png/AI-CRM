from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.admin_auth.guards import admin_api_auth_error
from aicrm_next.shared.admin_read_fallback import admin_read_unavailable_payload

from .service import AudiencePackageService

router = APIRouter()

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


@router.get("/api/admin/ai-audience/packages", name="api.admin_ai_audience_packages")
def admin_ai_audience_packages(request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    try:
        payload = AudiencePackageService().list_admin_package_summaries(limit=200)
        status_code = 200
    except Exception as exc:
        payload = admin_read_unavailable_payload(
            capability_owner="aicrm_next/extensions/ai/ai_audience_ops",
            page_error="人群包读模型暂不可用，请稍后重试。",
            exc=exc,
            items_keys=("items",),
            count_keys=("total",),
        )
        status_code = 200
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


@router.post("/api/admin/ai-audience/packages", name="api.admin_ai_audience_package_create")
def admin_ai_audience_package_create(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().create_admin_package(payload))


def _request_base_url(request: Request) -> str:
    proto = str(request.headers.get("X-Forwarded-Proto") or request.url.scheme or "https").split(",", 1)[0].strip()
    host = str(request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.url.netloc or "").split(",", 1)[0].strip()
    return f"{proto}://{host}" if host else ""


def _response(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    if not payload.get("ok", True) and payload.get("error") == "package_not_found":
        status_code = 404
    elif not payload.get("ok", True) and status_code == 200:
        status_code = 400
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


@router.get("/api/admin/ai-audience/packages/{package_id}", name="api.admin_ai_audience_package_detail")
def admin_ai_audience_package_detail(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().get_admin_package_detail(package_id))


@router.patch("/api/admin/ai-audience/packages/{package_id}", name="api.admin_ai_audience_package_update")
def admin_ai_audience_package_update(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().update_admin_package(package_id, payload))


@router.post("/api/admin/ai-audience/packages/{package_id}/versions", name="api.admin_ai_audience_package_version_create")
def admin_ai_audience_package_version_create(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().create_admin_version(package_id, payload))


@router.post("/api/admin/ai-audience/packages/{package_id}/preview", name="api.admin_ai_audience_package_preview")
def admin_ai_audience_package_preview(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().preview_admin_package(package_id, payload))


@router.post("/api/admin/ai-audience/packages/{package_id}/publish", name="api.admin_ai_audience_package_publish")
def admin_ai_audience_package_publish(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().publish_admin_package(package_id, payload))


@router.post("/api/admin/ai-audience/packages/{package_id}/copy", name="api.admin_ai_audience_package_copy")
def admin_ai_audience_package_copy(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().copy_admin_package(package_id))


@router.post("/api/admin/ai-audience/packages/{package_id}/pause", name="api.admin_ai_audience_package_pause")
def admin_ai_audience_package_pause(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().pause_admin_package(package_id))


@router.post("/api/admin/ai-audience/packages/{package_id}/activate", name="api.admin_ai_audience_package_activate")
def admin_ai_audience_package_activate(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().activate_admin_package(package_id))


@router.delete("/api/admin/ai-audience/packages/{package_id}", name="api.admin_ai_audience_package_delete")
def admin_ai_audience_package_delete(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().archive_admin_package(package_id))


@router.get("/api/admin/ai-audience/packages/{package_id}/members", name="api.admin_ai_audience_package_members")
def admin_ai_audience_package_members(package_id: int, request: Request, limit: int = 50, offset: int = 0) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().list_admin_members(package_id, limit=limit, offset=offset))


@router.get("/api/admin/ai-audience/packages/{package_id}/webhooks", name="api.admin_ai_audience_package_webhooks")
def admin_ai_audience_package_webhooks(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().get_admin_webhook(package_id, request_base_url=_request_base_url(request)))


@router.patch("/api/admin/ai-audience/packages/{package_id}/webhooks", name="api.admin_ai_audience_package_webhooks_update")
def admin_ai_audience_package_webhooks_update(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().update_admin_webhook(package_id, payload))


@router.get("/api/admin/ai-audience/packages/{package_id}/senders", name="api.admin_ai_audience_package_senders")
def admin_ai_audience_package_senders(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().list_admin_senders(package_id))


@router.put("/api/admin/ai-audience/packages/{package_id}/senders", name="api.admin_ai_audience_package_senders_replace")
def admin_ai_audience_package_senders_replace(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().replace_admin_senders(package_id, payload))
