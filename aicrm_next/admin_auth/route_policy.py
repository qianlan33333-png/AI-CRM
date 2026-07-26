from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict, deque
from threading import RLock
from time import monotonic
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from aicrm_next.platform_foundation.auth_platform.api import (
    auth_client_service,
    auth_session_service,
    auth_webhook_verifier,
    request_id,
    request_ip,
)
from aicrm_next.platform_foundation.auth_platform.client_authentication import ClientAuthenticationError
from aicrm_next.platform_foundation.auth_platform.context import AuthContext, PrincipalType
from aicrm_next.platform_foundation.auth_platform.service import AuthError
from aicrm_next.shared.route_policy import RoutePolicy, RoutePolicyIndex, match_route_policy
from aicrm_next.shared.runtime import test_environment
from aicrm_next.shared.runtime_settings import managed_runtime_setting
from aicrm_next.shared.signed_context import SIDEBAR_VIEWER_SESSION_COOKIE, validate_sidebar_owner_context

from .capabilities import context_can, viewer_only
from .guards import admin_auth_required_response, admin_page_auth_redirect, current_admin_introspection, current_auth_context
from .service import CSRF_COOKIE, SESSION_COOKIE, normalize_text, route_headers


SIDEBAR_OWNER_TOKEN_HEADER = "x-aicrm-sidebar-owner-token"
CSRF_HEADER = "x-csrf-token"
RATE_LIMIT_PROFILES: dict[str, tuple[int, int]] = {
    "auth_strict": (20, 60),
    "authenticated": (600, 60),
    "callback_burst": (600, 60),
    "health": (600, 60),
    "integration": (300, 60),
    "internal": (600, 60),
    "public_standard": (120, 60),
    "public_strict": (30, 60),
}


class RouteRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def allow(self, *, profile: str, principal: str, route_key: str, now: float | None = None) -> bool:
        limit, window = RATE_LIMIT_PROFILES[profile]
        timestamp = monotonic() if now is None else float(now)
        key = (principal, route_key)
        with self._lock:
            events = self._events[key]
            cutoff = timestamp - window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(timestamp)
            return True


RATE_LIMITER = RouteRateLimiter()


def route_policy_enforcement_enabled() -> bool:
    if not test_environment():
        return True
    test_override = normalize_text(managed_runtime_setting("AICRM_ROUTE_POLICY_ENFORCED")).lower()
    if not test_override:
        test_override = normalize_text(managed_runtime_setting("AICRM_ADMIN_AUTH_ENFORCED")).lower()
    return test_override not in {"0", "false", "no", "off"}


async def route_policy_required_response(
    request: Request,
    *,
    app: FastAPI,
    index: RoutePolicyIndex,
) -> Response | None:
    matched = match_route_policy(app, request.scope, index)
    if matched.static or matched.builtin:
        return None
    if matched.route is None:
        return admin_auth_required_response(request)
    enforced = route_policy_enforcement_enabled()
    policy = matched.policy
    if policy is None:
        return _error("route_policy_missing", status_code=403) if enforced else None

    request.state.route_policy = policy
    request.state.route_path_params = dict(matched.path_params or {})
    _set_pii_principal(request, actor_type="anonymous", actor_id="anonymous", policy_scope=policy.access_scope)
    if enforced and not _rate_limit_allows(request, policy):
        return _error("route_rate_limited", status_code=429)
    if not enforced:
        return None

    if policy.auth_scheme == "human_session":
        if _bearer_token(request):
            return _error("principal_type_forbidden", status_code=403)
        return await _enforce_human_session(request, policy)
    if policy.auth_scheme == "human_or_service":
        if normalize_text(request.cookies.get(SESSION_COOKIE)):
            return await _enforce_human_session(request, policy)
        return _enforce_api_client(
            request,
            policy,
            audience=policy.service_audience,
            capability=policy.service_capability,
        )
    if policy.auth_scheme == "api_client_jwt":
        return _enforce_api_client(request, policy)
    if policy.auth_scheme == "webhook_hmac":
        return await _enforce_webhook_hmac(request, policy)
    if policy.auth_scheme == "sidebar_grant":
        return await _enforce_sidebar_grant(request, policy)
    if policy.auth_scheme == "public_result_grant":
        return _enforce_public_result_grant(request, policy)
    if policy.auth_scheme == "payment_identity_session":
        return _enforce_payment_identity_session(request, policy)
    if policy.auth_scheme in {
        "public",
        "client_credentials",
        "provider_oauth_state",
        "provider_signature",
    }:
        return None
    return _error("unsupported_auth_scheme", status_code=403)


async def _enforce_human_session(request: Request, policy: RoutePolicy) -> Response | None:
    introspection = current_admin_introspection(request)
    context = introspection.context
    if not introspection.active or context is None:
        if str(request.url.path).startswith(("/admin", "/setup")):
            return admin_page_auth_redirect(request)
        error = introspection.error if introspection.error != "session_required" else "admin_auth_required"
        return _error(error or "admin_auth_required", status_code=401)
    context = context.with_request_id(request_id(request))
    if introspection.record is not None:
        request.state.auth_session_id = introspection.record.session_id
    if not _principal_allowed(context, policy):
        return _error("principal_type_forbidden", status_code=403)
    _install_context(request, context, policy)
    if (
        _request_is_write(request)
        and viewer_only(context)
        and policy.capability not in {"admin_read", "service_period_grid_access"}
    ):
        return _error("admin_capability_required", status_code=403, capability=policy.capability)
    if policy.capability not in {"public", "health_read"} and not context_can(context, policy.capability):
        return _error("admin_capability_required", status_code=403, capability=policy.capability)
    if policy.csrf and _request_is_write(request):
        return await _csrf_error(request, introspection)
    return None


def _enforce_api_client(
    request: Request,
    policy: RoutePolicy,
    *,
    audience: str = "",
    capability: str = "",
) -> Response | None:
    token = _bearer_token(request)
    if not token:
        return _error("access_token_required", status_code=401)
    required_audience = normalize_text(audience) or policy.audience
    required_capability = normalize_text(capability) or policy.capability
    try:
        context = auth_client_service(request).verify_access_token(
            token,
            audience=required_audience,
            source_ip=request_ip(request),
            request_id=request_id(request),
            client_purpose=policy.client_purpose,
        )
    except ClientAuthenticationError as exc:
        return _error(exc.error, status_code=exc.status_code)
    except AuthError as exc:
        return _error(exc.error, status_code=exc.status_code)
    except (RuntimeError, ValueError):
        return _error("auth_runtime_unavailable", status_code=503)
    if not _principal_allowed(context, policy):
        return _error("principal_type_forbidden", status_code=403)
    if not context.permits(
        capability=required_capability,
        scope="write" if _request_is_write(request) else "read",
        resource=_request_resource(request),
    ):
        return _error("scope_or_capability_required", status_code=403, capability=required_capability)
    _install_context(request, context, policy)
    return None


async def _enforce_webhook_hmac(request: Request, policy: RoutePolicy) -> Response | None:
    if normalize_text(request.method).upper() == "OPTIONS":
        return None
    try:
        source_ip = request_ip(request)
    except ClientAuthenticationError as exc:
        return _error(exc.error, status_code=exc.status_code)
    result = auth_webhook_verifier(request).verify(
        headers=request.headers,
        body=await request.body(),
        capability=policy.capability,
        source_ip=source_ip,
        request_id=request_id(request),
    )
    context = result.context
    if not result.ok or context is None:
        return _error(result.error or "invalid_webhook_signature", status_code=401)
    if not _principal_allowed(context, policy):
        return _error("principal_type_forbidden", status_code=403)
    if not context.permits(capability=policy.capability, scope="webhook.write", resource=_request_resource(request)):
        return _error("webhook_scope_or_capability_required", status_code=403, capability=policy.capability)
    _install_context(request, context, policy)
    return None


async def _enforce_sidebar_grant(request: Request, policy: RoutePolicy) -> Response | None:
    token = normalize_text(request.headers.get(SIDEBAR_OWNER_TOKEN_HEADER))
    claimed_values = [
        normalize_text(request.query_params.get(key))
        for key in ("owner_userid", "current_userid", "bind_by_userid", "viewer_userid")
    ]
    target_external_userid = normalize_text(request.query_params.get("external_userid") or request.query_params.get("user_id"))
    content_type = normalize_text(request.headers.get("content-type")).lower()
    if content_type.startswith("application/json"):
        try:
            body = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        if isinstance(body, dict):
            target_external_userid = target_external_userid or normalize_text(body.get("external_userid") or body.get("user_id"))
            claimed_values.extend(
                normalize_text(body.get(key))
                for key in ("owner_userid", "current_userid", "bind_by_userid", "viewer_userid", "actor_id")
            )
    result = validate_sidebar_owner_context(
        token=token,
        viewer_session_cookie=normalize_text(request.cookies.get(SIDEBAR_VIEWER_SESSION_COOKIE)),
        external_userid=target_external_userid,
        expected_corp_id=normalize_text(managed_runtime_setting("WECOM_CORP_ID")),
    )
    if not result.get("ok"):
        status = normalize_text(result.get("status"))
        status_code = 401 if status in {"missing", "invalid", "expired", "viewer_session_required", "viewer_session_invalid"} else 403
        return _error("sidebar_context_required" if status_code == 401 else "sidebar_customer_scope_forbidden", status_code=status_code)
    grant = dict(result.get("context") or {})
    owner_userid = normalize_text(grant.get("owner_userid") or grant.get("viewer_userid"))
    if any(value and not hmac.compare_digest(value, owner_userid) for value in claimed_values):
        return _error("sidebar_owner_scope_forbidden", status_code=403)
    context = AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id=f"wecom-user:{owner_userid}",
        corp_id=normalize_text(grant.get("corp_id")),
        capabilities=(policy.capability,),
        scopes=("write" if _request_is_write(request) else "read",),
        owner_scope={"owner_userid": owner_userid},
        request_id=request_id(request),
    )
    request.state.sidebar_context = grant
    request.state.sidebar_owner_userid = owner_userid
    request.state.sidebar_external_userid = normalize_text(grant.get("external_userid"))
    request.state.sidebar_capability = policy.capability
    _install_context(request, context, policy)
    return None


def _enforce_public_result_grant(request: Request, policy: RoutePolicy) -> Response | None:
    from .public_result_grant import (
        RESULT_GRANT_COOKIE_NAME,
        questionnaire_result_token_from_grant,
    )

    slug = normalize_text((getattr(request.state, "route_path_params", {}) or {}).get("slug"))
    result_access_token = questionnaire_result_token_from_grant(
        request.cookies.get(RESULT_GRANT_COOKIE_NAME),
        slug=slug,
    )
    if not result_access_token:
        return _error("questionnaire_result_access_forbidden", status_code=403)
    context = AuthContext(
        principal_type=PrincipalType.PUBLIC,
        principal_id=f"questionnaire-result:{hashlib.sha256(result_access_token.encode()).hexdigest()[:24]}",
        capabilities=(policy.capability,),
        scopes=("read",),
        request_id=request_id(request),
    )
    if not _principal_allowed(context, policy):
        return _error("principal_type_forbidden", status_code=403)
    request.state.questionnaire_result_access_token = result_access_token
    _install_context(request, context, policy)
    return None


def _enforce_payment_identity_session(request: Request, policy: RoutePolicy) -> Response | None:
    from aicrm_next.shared.wechat_h5_session import payment_identity_from_request

    identity = payment_identity_from_request(request)
    openid = normalize_text(identity.get("openid"))
    if not openid:
        return _error("payment_identity_required", status_code=401)
    context = AuthContext(
        principal_type=PrincipalType.PUBLIC,
        principal_id=f"wechat-payment:{hashlib.sha256(openid.encode()).hexdigest()[:24]}",
        capabilities=(policy.capability,),
        scopes=("write" if _request_is_write(request) else "read",),
        request_id=request_id(request),
    )
    if not _principal_allowed(context, policy):
        return _error("principal_type_forbidden", status_code=403)
    request.state.payment_identity = identity
    _install_context(request, context, policy)
    return None


def _principal_allowed(context: AuthContext, policy: RoutePolicy) -> bool:
    return not policy.principal_types or context.principal_type.value in policy.principal_types


def _install_context(request: Request, context: AuthContext, policy: RoutePolicy) -> None:
    request.state.auth_context = context
    _set_pii_principal(
        request,
        actor_type=context.principal_type.value,
        actor_id=context.principal_id,
        policy_scope=policy.access_scope,
    )


def _request_resource(request: Request) -> dict[str, Any]:
    resource = dict(getattr(request.state, "route_path_params", {}) or {})
    for key in (
        "corp_id",
        "external_userid",
        "owner_userid",
        "package_id",
        "agent_id",
        "questionnaire_id",
        "job_id",
    ):
        value = normalize_text(request.query_params.get(key))
        if value and key not in resource:
            resource[key] = value
    return resource


def _set_pii_principal(request: Request, *, actor_type: str, actor_id: str, policy_scope: str = "") -> None:
    request.state.pii_actor_type = normalize_text(actor_type) or "anonymous"
    request.state.pii_actor_id = normalize_text(actor_id) or "anonymous"
    if policy_scope:
        request.state.pii_policy_scope = normalize_text(policy_scope)


async def _csrf_error(request: Request, introspection) -> JSONResponse | None:
    cookie_token = normalize_text(request.cookies.get(CSRF_COOKIE))
    request_token = normalize_text(request.headers.get(CSRF_HEADER))
    if not request_token:
        content_type = normalize_text(request.headers.get("content-type")).lower()
        if content_type.startswith("application/x-www-form-urlencoded"):
            values = parse_qs((await request.body()).decode("utf-8", errors="ignore"), keep_blank_values=True).get(
                "csrf_token"
            ) or []
            request_token = normalize_text(values[-1] if values else "")
        elif content_type.startswith("multipart/form-data"):
            request_token = _multipart_form_value(await request.body(), content_type=content_type, field_name="csrf_token")
    if auth_session_service(request).verify_csrf(introspection, cookie_token, request_token):
        return None
    return _error("admin_csrf_required", status_code=403)


def _multipart_form_value(body: bytes, *, content_type: str, field_name: str) -> str:
    if "boundary=" not in content_type:
        return ""
    boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        return ""
    delimiter = f"--{boundary}".encode()
    expected_name = f'name="{field_name}"'.encode()
    for part in body.split(delimiter):
        headers, separator, content = part.partition(b"\r\n\r\n")
        if separator and expected_name in headers:
            return normalize_text(content.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore"))
    return ""


def _bearer_token(request: Request) -> str:
    authorization = normalize_text(request.headers.get("authorization"))
    return normalize_text(authorization[7:]) if authorization.startswith("Bearer ") else ""


def _request_is_write(request: Request) -> bool:
    return normalize_text(request.method).upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}


def _rate_limit_allows(request: Request, policy: RoutePolicy) -> bool:
    return RATE_LIMITER.allow(
        profile=policy.rate_limit,
        principal=_rate_limit_principal(request, policy),
        route_key=policy.key,
    )


def _rate_limit_principal(request: Request, policy: RoutePolicy) -> str:
    if policy.auth_scheme in {"human_session", "human_or_service"}:
        context = current_auth_context(request)
        if context:
            return f"human:{context.principal_id}:{context.request_id[:16]}"
    if policy.auth_scheme in {"api_client_jwt", "human_or_service"}:
        token = _bearer_token(request)
        if token:
            return f"jwt:{hashlib.sha256(token.encode()).hexdigest()[:24]}"
    if policy.auth_scheme == "sidebar_grant":
        token = normalize_text(request.headers.get(SIDEBAR_OWNER_TOKEN_HEADER))
        if token:
            return f"sidebar:{hashlib.sha256(token.encode()).hexdigest()[:24]}"
    if policy.auth_scheme == "payment_identity_session":
        from aicrm_next.shared.wechat_h5_session import WECHAT_PAYMENT_IDENTITY_COOKIE

        token = normalize_text(request.cookies.get(WECHAT_PAYMENT_IDENTITY_COOKIE))
        if token:
            return f"payment:{hashlib.sha256(token.encode()).hexdigest()[:24]}"
    if policy.auth_scheme == "webhook_hmac":
        client_id = normalize_text(request.headers.get("x-aicrm-client-id"))
        if client_id:
            return f"webhook:{hashlib.sha256(client_id.encode()).hexdigest()[:24]}"
    forwarded = normalize_text(request.headers.get("x-forwarded-for")).split(",", 1)[0].strip()
    real_ip = normalize_text(request.headers.get("x-real-ip"))
    client_host = normalize_text(getattr(request.client, "host", ""))
    return f"ip:{(forwarded or real_ip or client_host or 'unknown')[:128]}"


def _error(error: str, *, status_code: int, capability: str = "") -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "error": error,
        "route_owner": "ai_crm_next",
        "real_external_call_executed": False,
    }
    if capability:
        payload["required_capability"] = capability
    return JSONResponse(payload, status_code=status_code, headers=route_headers())
