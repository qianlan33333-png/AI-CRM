from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import Request

from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.platform.shared.route_ownership import load_route_manifest
from aicrm_next.platform.shared.route_policy import DEFAULT_ROUTE_POLICY_MANIFEST
from aicrm_next.platform.shared.runtime import require_signing_secret

from .capabilities import context_can
from .guards import current_auth_context
from .service import normalize_text


ACTION_TOKEN_TTL_SECONDS = 10 * 60
ACTION_TOKEN_VERSION = 1
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class ActionTokenValidation:
    ok: bool
    error: str = ""
    claims: dict[str, Any] | None = None


@dataclass(frozen=True)
class ActionTokenRoute:
    method: str
    action: str
    target: str
    capability: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.target}"


def issue_action_token(
    context: AuthContext,
    *,
    capability: str,
    method: str,
    action: str,
    target: str,
    now: int | None = None,
    ttl_seconds: int = ACTION_TOKEN_TTL_SECONDS,
    session_binding: str = "",
) -> str:
    normalized_capability = normalize_text(capability)
    normalized_method = normalize_text(method).upper()
    normalized_action = normalize_text(action)
    normalized_target = _normalize_target(target)
    if not normalized_capability or not normalized_method or not normalized_action or not normalized_target:
        raise ValueError("action token binding is incomplete")
    if normalized_method in SAFE_METHODS:
        raise ValueError("action token cannot be issued for a safe method")
    if not context_can(context, normalized_capability):
        raise PermissionError(f"auth context lacks capability: {normalized_capability}")
    issued_at = int(time.time()) if now is None else int(now)
    ttl = max(1, min(int(ttl_seconds), ACTION_TOKEN_TTL_SECONDS))
    claims = {
        "v": ACTION_TOKEN_VERSION,
        "sub": context.sub,
        "sid": _session_fingerprint(context, session_binding),
        "cap": normalized_capability,
        "m": normalized_method,
        "act": normalized_action,
        "tgt": normalized_target,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "nonce": secrets.token_urlsafe(12),
    }
    body = _b64(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def validate_action_token(
    token: str,
    context: AuthContext,
    *,
    capability: str,
    method: str,
    action: str,
    target: str,
    now: int | None = None,
    session_binding: str = "",
) -> ActionTokenValidation:
    claims = _decode_claims(token)
    if claims is None:
        return ActionTokenValidation(ok=False, error="invalid")
    current_time = int(time.time()) if now is None else int(now)
    try:
        issued_at = int(claims.get("iat") or 0)
        expires_at = int(claims.get("exp") or 0)
    except (TypeError, ValueError):
        return ActionTokenValidation(ok=False, error="invalid")
    if int(claims.get("v") or 0) != ACTION_TOKEN_VERSION:
        return ActionTokenValidation(ok=False, error="invalid")
    if issued_at <= 0 or expires_at <= issued_at or expires_at - issued_at > ACTION_TOKEN_TTL_SECONDS:
        return ActionTokenValidation(ok=False, error="invalid")
    if current_time < issued_at - 30:
        return ActionTokenValidation(ok=False, error="not_yet_valid")
    if current_time > expires_at:
        return ActionTokenValidation(ok=False, error="expired")

    expected = {
        "sub": context.sub,
        "sid": _session_fingerprint(context, session_binding),
        "cap": normalize_text(capability),
        "m": normalize_text(method).upper(),
        "act": normalize_text(action),
        "tgt": _normalize_target(target),
    }
    for key, value in expected.items():
        if not value or not hmac.compare_digest(normalize_text(claims.get(key)), value):
            return ActionTokenValidation(ok=False, error=f"binding_mismatch:{key}")
    if not context_can(context, expected["cap"]):
        return ActionTokenValidation(ok=False, error="capability_revoked")
    if not normalize_text(claims.get("nonce")):
        return ActionTokenValidation(ok=False, error="invalid")
    return ActionTokenValidation(ok=True, claims=claims)


def issue_action_token_for_route(request: Request, *, method: str, target: str) -> str:
    route = _route_by_key().get(f"{normalize_text(method).upper()} {_normalize_target(target)}")
    if route is None:
        raise ValueError(f"unsafe admin route is not registered: {method} {target}")
    context = _request_context(request)
    if context is None:
        raise PermissionError("admin auth context is required")
    return issue_action_token(
        context,
        capability=route.capability,
        method=route.method,
        action=route.action,
        target=route.target,
        session_binding=_request_session_binding(request, context),
    )


def validate_action_token_for_request(request: Request, token: str) -> ActionTokenValidation:
    context = _request_context(request)
    policy = getattr(request.state, "route_policy", None)
    if context is None or policy is None:
        return ActionTokenValidation(ok=False, error="context_missing")
    method = normalize_text(request.method).upper()
    if method in SAFE_METHODS:
        return ActionTokenValidation(ok=False, error="safe_method")
    return validate_action_token(
        token,
        context,
        capability=normalize_text(policy.capability),
        method=method,
        action=normalize_text(policy.route_name),
        target=normalize_text(policy.path),
        session_binding=_request_session_binding(request, context),
    )


def build_admin_action_token_bundle(request: Request) -> dict[str, str]:
    context = _request_context(request)
    if context is None:
        return {}
    tokens: dict[str, str] = {}
    for route in _unsafe_admin_routes():
        if not context_can(context, route.capability):
            continue
        tokens[route.key] = issue_action_token(
            context,
            capability=route.capability,
            method=route.method,
            action=route.action,
            target=route.target,
            session_binding=_request_session_binding(request, context),
        )
    return tokens


def _session_fingerprint(context: AuthContext, session_binding: str = "") -> str:
    material = f"session:{normalize_text(session_binding) or context.token_id}"
    return hmac.new(_secret(), material.encode("utf-8"), hashlib.sha256).hexdigest()


def _request_session_binding(request: Request, context: AuthContext) -> str:
    return normalize_text(getattr(request.state, "auth_session_id", "")) or context.token_id


def _request_context(request: Request) -> AuthContext | None:
    state_context = getattr(request.state, "auth_context", None)
    if isinstance(state_context, AuthContext):
        return state_context
    return current_auth_context(request)


@lru_cache(maxsize=1)
def _unsafe_admin_routes() -> tuple[ActionTokenRoute, ...]:
    routes: list[ActionTokenRoute] = []
    for entry in load_route_manifest(DEFAULT_ROUTE_POLICY_MANIFEST):
        if normalize_text(entry.get("auth_scheme")) not in {"human_session", "human_or_service"}:
            continue
        capability = normalize_text(entry.get("capability"))
        action = normalize_text(entry.get("route_name"))
        target = _normalize_target(entry.get("path"))
        for method in entry.get("methods") or ():
            normalized_method = normalize_text(method).upper()
            if normalized_method in SAFE_METHODS:
                continue
            routes.append(
                ActionTokenRoute(
                    method=normalized_method,
                    action=action,
                    target=target,
                    capability=capability,
                )
            )
    return tuple(routes)


@lru_cache(maxsize=1)
def _route_by_key() -> dict[str, ActionTokenRoute]:
    return {route.key: route for route in _unsafe_admin_routes()}


def _decode_claims(token: str) -> dict[str, Any] | None:
    value = normalize_text(token)
    if "." not in value:
        return None
    body, signature = value.rsplit(".", 1)
    try:
        supplied_signature = _unb64(signature)
    except Exception:
        return None
    expected_signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        claims = json.loads(_unb64(body).decode("utf-8"))
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _normalize_target(value: Any) -> str:
    target = normalize_text(value)
    if target and not target.startswith("/"):
        return f"/{target}"
    return target


def _secret() -> bytes:
    return require_signing_secret(
        "AICRM_NEXT_ACTION_TOKEN_SECRET",
        fallback_env_keys=("SECRET_KEY",),
        local_fallback="aicrm-next-dev-action-token",
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    if _b64(decoded) != value:
        raise ValueError("non-canonical base64")
    return decoded
