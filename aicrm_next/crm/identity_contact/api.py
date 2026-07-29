from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting
from aicrm_next.platform.shared.signed_context import (
    SIDEBAR_VIEWER_SESSION_COOKIE,
    validate_sidebar_owner_context,
)

from .application import GetSidebarContactBindingStatusQuery, ResolvePersonIdentityQuery
from .dto import ResolvePersonIdentityRequest

router = APIRouter()
SIDEBAR_OWNER_TOKEN_HEADER = "x-aicrm-sidebar-owner-token"


def _identity_error(*, error_code: str, message: str, source_status: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "route_owner": "ai_crm_next",
            "source_status": source_status,
            "fallback_used": False,
        },
        status_code=status_code,
    )


def _identity_links(identity) -> dict:
    data = identity.model_dump() if hasattr(identity, "model_dump") else dict(identity or {})
    return {
        "person_id": data.get("person_id") or "",
        "external_userid": data.get("external_userid") or "",
        "mobile": data.get("mobile") or "",
        "openid": data.get("openid") or "",
        "unionid": data.get("unionid") or "",
        "user_id": data.get("external_userid") or "",
        "buyer_id": data.get("openid") or "",
    }


@router.get("/api/identity/resolve")
def resolve_identity(
    external_userid: str | None = None,
    mobile: str | None = None,
    openid: str | None = None,
    unionid: str | None = None,
) -> JSONResponse:
    resolution = ResolvePersonIdentityQuery().execute_result(
        ResolvePersonIdentityRequest(
            external_userid=external_userid,
            mobile=mobile,
            openid=openid,
            unionid=unionid,
        )
    )
    if resolution.status != "resolved" or resolution.identity is None:
        return _identity_error(
            error_code=f"identity_{resolution.status}",
            message=resolution.reason or f"identity {resolution.status}",
            source_status="next_identity_resolve",
            status_code=404 if resolution.status == "not_found" else 409,
        )
    return JSONResponse({"ok": True, "identity": resolution.identity.model_dump()})


@router.get(
    "/api/admin/identity/resolve",
    summary="后台身份解析",
    description=(
        "Session Cookie 后台身份解析 wrapper，复用 ResolvePersonIdentityQuery。"
        "支持 external_userid/mobile/openid/unionid；user_id 会安全映射到 external_userid，buyer_id 会映射到 openid，transaction_id 仅进入 warnings。"
    ),
)
def admin_resolve_identity(
    external_userid: str | None = Query(None, description="企业微信外部联系人 external_userid"),
    mobile: str | None = Query(None, description="手机号"),
    openid: str | None = Query(None, description="公众号或支付侧 openid"),
    unionid: str | None = Query(None, description="微信 unionid"),
    user_id: str | None = Query(None, description="后台兼容 user_id；本阶段按 external_userid 尝试解析"),
    buyer_id: str | None = Query(None, description="支付宝 buyer_id；本阶段按 openid 尝试解析"),
    transaction_id: str | None = Query(None, description="支付平台交易号；当前 ResolvePersonIdentityRequest 不支持，返回 warnings"),
) -> JSONResponse:
    warnings: list[str] = []
    mapped_external_userid = external_userid or user_id
    mapped_openid = openid or buyer_id
    if user_id and not external_userid:
        warnings.append("user_id was mapped to external_userid for this slice")
    if buyer_id and not openid:
        warnings.append("buyer_id was mapped to openid for this slice")
    if transaction_id:
        warnings.append("transaction_id cannot be mapped by ResolvePersonIdentityRequest in this slice")
    resolution = ResolvePersonIdentityQuery().execute_result(
        ResolvePersonIdentityRequest(
            external_userid=mapped_external_userid,
            mobile=mobile,
            openid=mapped_openid,
            unionid=unionid,
        )
    )
    if resolution.status != "resolved" or resolution.identity is None:
        return _identity_error(
            error_code=resolution.status,
            message=resolution.reason or f"identity {resolution.status}",
            source_status="next_admin_identity_resolve",
            status_code=404 if resolution.status == "not_found" else 409,
        )
    result = resolution.identity
    return JSONResponse(
        {
            "ok": True,
            "identity": result.model_dump(),
            "warnings": warnings,
            "route_owner": "ai_crm_next",
            "source_status": "next_admin_identity_resolve",
            "fallback_used": False,
        }
    )


@router.get(
    "/api/admin/identity/links/{identity_key}",
    summary="后台统一身份链路",
    description="按 external_userid、mobile、unionid、openid 等任意 identity_key 解析统一身份链路。找不到时返回结构化 not_found。",
)
def admin_identity_links(
    identity_key: str = Path(..., description="external_userid、mobile、openid、unionid、user_id 或 buyer_id"),
) -> JSONResponse:
    query = ResolvePersonIdentityQuery()
    key = str(identity_key or "").strip()
    attempts = [
        ResolvePersonIdentityRequest(external_userid=key),
        ResolvePersonIdentityRequest(mobile=key),
        ResolvePersonIdentityRequest(unionid=key),
        ResolvePersonIdentityRequest(openid=key),
    ]
    resolved_by_unionid: dict[str, object] = {}
    pending = False
    for request in attempts:
        try:
            resolution = query.execute_result(request)
        except ContractError:
            continue
        if resolution.status == "conflict":
            return _identity_error(
                error_code="conflict",
                message=resolution.reason or "identity conflict",
                source_status="next_admin_identity_links",
                status_code=409,
            )
        if resolution.status == "pending":
            pending = True
        if resolution.status == "resolved" and resolution.identity is not None:
            resolved_by_unionid[str(resolution.identity.unionid or "")] = resolution.identity
    if len(resolved_by_unionid) > 1:
        return _identity_error(
            error_code="conflict",
            message="identity key resolves to multiple canonical identities",
            source_status="next_admin_identity_links",
            status_code=409,
        )
    if resolved_by_unionid:
        result = next(iter(resolved_by_unionid.values()))
        return JSONResponse(
            {
                "ok": True,
                "identity_key": key,
                "links": _identity_links(result),
                "identity": result.model_dump(),
                "route_owner": "ai_crm_next",
                "source_status": "next_admin_identity_links",
                "fallback_used": False,
            }
        )
    return _identity_error(
        error_code="pending" if pending else "not_found",
        message="identity resolution pending" if pending else "identity links not found",
        source_status="next_admin_identity_links",
        status_code=409 if pending else 404,
    )


@router.get("/api/sidebar/contact-binding-status")
def sidebar_contact_binding_status(
    request: Request,
    external_userid: str | None = None,
    owner_userid: str | None = None,
):
    resolved_owner_userid = _sidebar_owner_userid_from_request(
        request,
        external_userid=external_userid,
        owner_userid=owner_userid,
    )
    result = _binding_status_query(request)(
        external_userid=external_userid,
        owner_userid=resolved_owner_userid,
        require_owner_scope=True,
    )
    status_code = int(result.pop("status_code", 200) or 200)
    return JSONResponse(result, status_code=status_code)


@router.get("/api/sidebar/binding-status")
def sidebar_binding_status(
    request: Request,
    external_userid: str | None = None,
    owner_userid: str | None = None,
):
    resolved_owner_userid = _sidebar_owner_userid_from_request(
        request,
        external_userid=external_userid,
        owner_userid=owner_userid,
    )
    result = _binding_status_query(request)(
        external_userid=external_userid,
        owner_userid=resolved_owner_userid,
        require_owner_scope=True,
    )
    status_code = int(result.pop("status_code", 200) or 200)
    return JSONResponse(result, status_code=status_code)


def _binding_status_query(request: Request) -> GetSidebarContactBindingStatusQuery:
    factory = getattr(request.app.state, "sidebar_contact_binding_status_query_factory", None)
    return factory() if callable(factory) else GetSidebarContactBindingStatusQuery()


def _sidebar_owner_userid_from_request(
    request: Request,
    *,
    external_userid: str | None,
    owner_userid: str | None = None,
) -> str:
    context = dict(getattr(request.state, "sidebar_context", {}) or {})
    if not context:
        result = validate_sidebar_owner_context(
            token=str(request.headers.get(SIDEBAR_OWNER_TOKEN_HEADER) or "").strip(),
            viewer_session_cookie=str(request.cookies.get(SIDEBAR_VIEWER_SESSION_COOKIE) or "").strip(),
            external_userid=str(external_userid or "").strip(),
            expected_corp_id=str(
                managed_runtime_setting("WECOM_CORP_ID") or ""
            ).strip(),
        )
        if not result.get("ok"):
            status = str(result.get("status") or "").strip()
            status_code = 401 if status in {"missing", "invalid", "expired", "viewer_session_required", "viewer_session_invalid"} else 403
            raise HTTPException(status_code=status_code, detail="sidebar context required")
        context = dict(result.get("context") or {})
    viewer = str(context.get("owner_userid") or context.get("viewer_userid") or "").strip()
    claimed_owner = str(owner_userid or "").strip()
    if claimed_owner and claimed_owner != viewer:
        raise HTTPException(status_code=403, detail="sidebar owner scope forbidden")
    if str(context.get("external_userid") or "").strip() != str(external_userid or "").strip():
        raise HTTPException(status_code=403, detail="sidebar customer scope forbidden")
    return viewer
