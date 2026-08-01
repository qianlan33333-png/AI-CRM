from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.platform.admin_auth.guards import admin_api_auth_error, current_auth_context
from aicrm_next.platform.shared.admin_read_fallback import admin_read_unavailable_payload
from aicrm_next.extensions.ai.ai_audience_ops.automation_binding import AudienceAutomationBindingService

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
def admin_ai_audience_packages(
    request: Request,
    group_id: str = "",
    limit: int = 20,
    offset: int = 0,
) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    try:
        ungrouped = group_id == "ungrouped"
        parsed_group_id = None
        if group_id and not ungrouped:
            try:
                parsed_group_id = int(group_id)
            except (TypeError, ValueError):
                return _response({"ok": False, "error": "invalid_group_id"})
        payload = AudiencePackageService().list_admin_package_summaries(
            limit=limit,
            offset=offset,
            group_id=parsed_group_id,
            ungrouped=ungrouped,
        )
        if not payload.get("ok", True):
            return _response(payload)
    except Exception as exc:
        payload = admin_read_unavailable_payload(
            capability_owner="aicrm_next/extensions/ai/ai_audience_ops",
            page_error="人群包读模型暂不可用，请稍后重试。",
            exc=exc,
            items_keys=("items",),
            count_keys=("total",),
        )
    return JSONResponse(jsonable_encoder(payload), status_code=200, headers=_HEADERS)


@router.post("/api/admin/ai-audience/packages", name="api.admin_ai_audience_package_create")
def admin_ai_audience_package_create(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().create_admin_package(payload))


def _operator(request: Request) -> str:
    context = current_auth_context(request)
    return str((context.principal_id if context else "admin") or "admin")


def _response(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    if not payload.get("ok", True):
        error = str(payload.get("error") or "")
        if error in {"package_not_found", "group_not_found", "automation_not_found"}:
            status_code = 404
        elif error in {"group_not_empty", "group_name_exists", "automation_already_bound", "automation_not_active", "automation_binding_exists", "automation_binding_state_invalid"}:
            status_code = 409
        elif error == "webhook_configuration_retired":
            status_code = 410
        elif status_code == 200:
            status_code = 400
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


@router.get("/api/admin/ai-audience/package-groups", name="api.admin_ai_audience_package_groups")
def admin_ai_audience_package_groups(request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().list_admin_package_groups())


@router.post("/api/admin/ai-audience/package-groups", name="api.admin_ai_audience_package_group_create")
def admin_ai_audience_package_group_create(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().create_admin_package_group(payload, operator=_operator(request)))


@router.patch("/api/admin/ai-audience/package-groups/{group_id}", name="api.admin_ai_audience_package_group_update")
def admin_ai_audience_package_group_update(group_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().update_admin_package_group(group_id, payload, operator=_operator(request)))


@router.delete("/api/admin/ai-audience/package-groups/{group_id}", name="api.admin_ai_audience_package_group_delete")
def admin_ai_audience_package_group_delete(group_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudiencePackageService().delete_admin_package_group(group_id, operator=_operator(request)))


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
    del package_id
    return _response({"ok": False, "error": "webhook_configuration_retired"})


@router.patch("/api/admin/ai-audience/packages/{package_id}/webhooks", name="api.admin_ai_audience_package_webhooks_update")
def admin_ai_audience_package_webhooks_update(package_id: int, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    del package_id, payload
    return _response({"ok": False, "error": "webhook_configuration_retired"})


@router.get(
    "/api/admin/ai-audience/packages/{package_id}/automation-binding",
    name="api.admin_ai_audience_package_automation_binding",
)
def admin_ai_audience_package_automation_binding(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudienceAutomationBindingService().get(package_id))


@router.put(
    "/api/admin/ai-audience/packages/{package_id}/automation-binding",
    name="api.admin_ai_audience_package_automation_binding_put",
)
def admin_ai_audience_package_automation_binding_put(
    package_id: int,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    try:
        automation_id = int(payload.get("automation_id") or 0)
    except (TypeError, ValueError):
        automation_id = 0
    return _response(
        AudienceAutomationBindingService().put(
            package_id,
            automation_id,
            operator=_operator(request),
        )
    )


@router.delete(
    "/api/admin/ai-audience/packages/{package_id}/automation-binding",
    name="api.admin_ai_audience_package_automation_binding_delete",
)
def admin_ai_audience_package_automation_binding_delete(package_id: int, request: Request) -> JSONResponse:
    if auth := admin_api_auth_error(request):
        return auth
    return _response(AudienceAutomationBindingService().delete(package_id, operator=_operator(request)))


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
