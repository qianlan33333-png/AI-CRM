from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aicrm_next.platform.admin_auth.capabilities import context_can
from aicrm_next.platform.admin_auth.guards import current_auth_context
from aicrm_next.platform.platform_foundation.auth_platform.api import auth_client_service
from aicrm_next.platform.shared.admin_action_runtime import validate_admin_action_token
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.public_url import canonical_public_base_url

from .direct_api_key import DirectExternalApiKeyService


router = APIRouter()
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _service(request: Request) -> DirectExternalApiKeyService:
    return DirectExternalApiKeyService(auth_client_service(request))


def _base_url(request: Request) -> str:
    try:
        return canonical_public_base_url(request)
    except ContractError:
        issuer = str(auth_client_service(request).config.issuer or "").strip()
        parsed = urlsplit(issuer)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            path = parsed.path.rstrip("/")
            if path.endswith("/oauth"):
                path = path[: -len("/oauth")]
            return f"{parsed.scheme}://{parsed.netloc}{path}"
        return ""


def _operator(request: Request) -> str:
    return str(getattr(current_auth_context(request), "principal_id", "") or "").strip() or "crm_console"


def _corp_id(request: Request) -> str:
    return str(getattr(current_auth_context(request), "corp_id", "") or "").strip()


def _permission_error(request: Request) -> JSONResponse | None:
    if context_can(current_auth_context(request), "manage_api_clients"):
        return None
    return JSONResponse({"ok": False, "error": "manage_api_clients_required"}, status_code=403)


def _strict_payload(payload: Any, allowed_fields: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    unknown = sorted(set(payload) - allowed_fields)
    if unknown:
        raise ValueError(f"unknown_fields:{','.join(unknown)}")
    return payload


def _token_error(request: Request, payload: dict[str, Any]) -> str:
    token = str(request.headers.get("X-Admin-Action-Token") or payload.get("admin_action_token") or "").strip()
    return validate_admin_action_token(token, request=request)


def _error(exc: Exception) -> JSONResponse:
    error = str(exc) or "direct_api_key_operation_failed"
    if isinstance(exc, KeyError):
        return JSONResponse({"ok": False, "error": str(exc.args[0])}, status_code=404)
    if isinstance(exc, PermissionError):
        return JSONResponse({"ok": False, "error": error}, status_code=409)
    return JSONResponse(
        {"ok": False, "error": error},
        status_code=409 if error == "direct_api_key_already_configured" else 400,
    )


async def _confirmed_payload(request: Request, allowed_fields: set[str]) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        payload = _strict_payload(await request.json(), allowed_fields | {"confirm", "admin_action_token"})
    except (TypeError, ValueError) as exc:
        return None, _error(exc)
    token_error = _token_error(request, payload)
    if token_error:
        return None, JSONResponse({"ok": False, "error": token_error}, status_code=401, headers=NO_STORE_HEADERS)
    if payload.get("confirm") is not True:
        return None, JSONResponse(
            {"ok": False, "error": "operation_confirmation_required"},
            status_code=400,
            headers=NO_STORE_HEADERS,
        )
    return payload, None


@router.get("/api/admin/config/api-key", name="api.admin_config_api_key_resource")
def api_admin_config_api_key_resource(request: Request):
    try:
        status = _service(request).status(base_url=_base_url(request))
    except (KeyError, PermissionError, TypeError, ValueError) as exc:
        return _error(exc)
    return {
        "ok": True,
        "api_key_status": status,
        "source_status": "auth_platform_read_model",
        "fallback_used": False,
    }


@router.post("/api/admin/config/api-key/generate", name="api.admin_config_api_key_generate_resource")
async def api_admin_config_api_key_generate_resource(request: Request):
    if permission_error := _permission_error(request):
        return permission_error
    _payload, payload_error = await _confirmed_payload(request, set())
    if payload_error:
        return payload_error
    try:
        result = _service(request).generate(corp_id=_corp_id(request), operator=_operator(request), base_url=_base_url(request))
    except (KeyError, PermissionError, TypeError, ValueError) as exc:
        response = _error(exc)
        response.headers.update(NO_STORE_HEADERS)
        return response
    return JSONResponse(
        {"ok": True, **result, "source_status": "auth_platform_command", "fallback_used": False},
        status_code=201,
        headers=NO_STORE_HEADERS,
    )


@router.post("/api/admin/config/api-key/rotate", name="api.admin_config_api_key_rotate_resource")
async def api_admin_config_api_key_rotate_resource(request: Request):
    if permission_error := _permission_error(request):
        return permission_error
    _payload, payload_error = await _confirmed_payload(request, set())
    if payload_error:
        return payload_error
    try:
        result = _service(request).rotate(operator=_operator(request), base_url=_base_url(request))
    except (KeyError, PermissionError, TypeError, ValueError) as exc:
        response = _error(exc)
        response.headers.update(NO_STORE_HEADERS)
        return response
    return JSONResponse(
        {"ok": True, **result, "source_status": "auth_platform_command", "fallback_used": False},
        headers=NO_STORE_HEADERS,
    )


@router.put("/api/admin/config/api-key/enabled", name="api.admin_config_api_key_enabled_resource")
async def api_admin_config_api_key_enabled_resource(request: Request):
    if permission_error := _permission_error(request):
        return permission_error
    payload, payload_error = await _confirmed_payload(request, {"enabled"})
    if payload_error:
        return payload_error
    if payload is None or payload.get("enabled") is not False:
        return JSONResponse({"ok": False, "error": "direct_api_key_reactivation_requires_rotation"}, status_code=409)
    try:
        status = _service(request).disable(operator=_operator(request), base_url=_base_url(request))
    except (KeyError, PermissionError, TypeError, ValueError) as exc:
        return _error(exc)
    return {"ok": True, "api_key_status": status, "source_status": "auth_platform_command", "fallback_used": False}


__all__ = ["router"]
