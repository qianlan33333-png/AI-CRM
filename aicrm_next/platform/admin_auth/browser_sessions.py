from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from starlette.responses import Response

from aicrm_next.platform.platform_foundation.auth_platform.api import auth_session_service
from aicrm_next.platform.platform_foundation.auth_platform.models import SessionSubject
from aicrm_next.platform.platform_foundation.auth_platform.sessions import IssuedSession

from .capabilities import capabilities_for_roles, normalize_roles
from .service import CSRF_COOKIE, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, admin_cookie_secure, normalize_text


@dataclass(frozen=True)
class BrowserSessionIdentity:
    principal_id: str
    admin_user_id: str
    display_name: str
    session_version: int
    roles: tuple[str, ...]
    corp_id: str = ""

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(capabilities_for_roles(self.roles)))

    @property
    def scopes(self) -> tuple[str, ...]:
        scopes = {"admin.read"}
        if set(self.capabilities) - {"admin_read", "read_customer"}:
            scopes.add("admin.write")
        return tuple(sorted(scopes))


def browser_session_identity(claims: dict[str, Any]) -> BrowserSessionIdentity:
    roles = normalize_roles(claims.get("roles") or ())
    if not roles:
        raise ValueError("admin identity has no roles")
    admin_user_id = normalize_text(claims.get("admin_user_id"))
    username = normalize_text(claims.get("username") or claims.get("wecom_userid"))
    if not admin_user_id:
        raise ValueError("admin identity is missing a stable subject")
    principal_id = f"admin-user:{admin_user_id}"
    session_version = int(claims.get("session_version") or 1)
    if session_version <= 0:
        raise ValueError("admin identity session version must be positive")
    return BrowserSessionIdentity(
        principal_id=principal_id,
        admin_user_id=admin_user_id,
        display_name=normalize_text(claims.get("display_name")) or username or principal_id,
        session_version=session_version,
        roles=roles,
        corp_id=normalize_text(claims.get("corp_id")),
    )


def issue_browser_session(request: Request, claims: dict[str, Any]) -> IssuedSession:
    identity = browser_session_identity(claims)
    service = auth_session_service(request)
    return service.issue(
        subject=SessionSubject(
            principal_id=identity.principal_id,
            admin_user_id=identity.admin_user_id,
            corp_id=identity.corp_id,
        ),
        session_version=identity.session_version,
        scopes=identity.scopes,
        capabilities=identity.capabilities,
    )


def set_browser_session_cookies(response: Response, issued: IssuedSession) -> None:
    secure_cookie = admin_cookie_secure()
    response.set_cookie(
        SESSION_COOKIE,
        issued.session_cookie,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=False,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )


def revoke_browser_session(request: Request, *, reason: str = "logout") -> bool:
    session_cookie = normalize_text(request.cookies.get(SESSION_COOKIE))
    if not session_cookie:
        return False
    return auth_session_service(request).revoke(session_cookie, reason=reason)
