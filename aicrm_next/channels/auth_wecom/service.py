from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from time import time
from typing import Any
from urllib.parse import urlencode

from aicrm_next.admin_auth.service import (
    normalize_text,
    route_headers,
    safe_next_path,
)
from aicrm_next.admin_config.repository import AdminConfigRepository
from aicrm_next.integration_ports import (
    WeComAdminAuthClient,
    WeComAdminAuthClientError,
    build_wecom_admin_auth_client,
)
from aicrm_next.shared.runtime import require_signing_secret
from aicrm_next.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting


REAL_AUTH_ENV = "AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL"
RUNTIME_SETTING_KEYS = frozenset({REAL_AUTH_ENV})
STATE_MAX_AGE_SECONDS = 10 * 60


@dataclass(frozen=True)
class WeComAuthConfig:
    enabled: bool
    corp_id: str
    agent_id: str
    corp_secret: str
    redirect_uri: str

    @property
    def missing_keys(self) -> list[str]:
        missing = []
        if not self.corp_id:
            missing.append("WECOM_CORP_ID")
        if not self.agent_id:
            missing.append("WECOM_AGENT_ID")
        if not self.corp_secret:
            missing.append("WECOM_SECRET")
        if not self.redirect_uri:
            missing.append("ADMIN_LOGIN_REDIRECT_URI")
        return missing


@dataclass(frozen=True)
class AuthStartResult:
    ok: bool
    redirect_url: str = ""
    state: str = ""
    error_code: str = ""
    missing_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthCallbackResult:
    ok: bool
    next_path: str = "/admin"
    identity_claims: dict[str, Any] | None = None
    admin_user_id: int = 0
    error_code: str = ""
    external_error_code: str = ""


def build_config(*, request_base_url: str = "") -> WeComAuthConfig:
    redirect_uri = normalize_text(managed_runtime_setting("ADMIN_LOGIN_REDIRECT_URI"))
    if not redirect_uri and request_base_url:
        redirect_uri = f"{request_base_url.rstrip('/')}/auth/wecom/callback"
    return WeComAuthConfig(
        enabled=managed_runtime_bool(REAL_AUTH_ENV, False),
        corp_id=normalize_text(managed_runtime_setting("WECOM_CORP_ID")),
        agent_id=normalize_text(managed_runtime_setting("WECOM_AGENT_ID")),
        corp_secret=normalize_text(managed_runtime_setting("WECOM_SECRET")),
        redirect_uri=redirect_uri,
    )


def build_authorize_url(*, mode: str, next_path: str, request_base_url: str = "") -> AuthStartResult:
    config = build_config(request_base_url=request_base_url)
    if not config.enabled:
        return AuthStartResult(ok=False, error_code="wecom_admin_auth_not_enabled")
    missing = tuple(config.missing_keys)
    if missing:
        return AuthStartResult(ok=False, error_code="wecom_admin_auth_config_missing", missing_keys=missing)

    state = sign_auth_state({"next": safe_next_path(next_path), "nonce": secrets.token_urlsafe(16), "iat": int(time())})
    normalized_mode = normalize_text(mode) or "qr"
    if normalized_mode == "oauth":
        query = urlencode(
            {
                "appid": config.corp_id,
                "redirect_uri": config.redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return AuthStartResult(
            ok=True,
            redirect_url=f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect",
            state=state,
        )

    query = urlencode(
        {
            "appid": config.corp_id,
            "agentid": config.agent_id,
            "redirect_uri": config.redirect_uri,
            "state": state,
        }
    )
    return AuthStartResult(ok=True, redirect_url=f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{query}", state=state)


def handle_callback(
    *,
    code: str,
    state: str,
    request_base_url: str = "",
    client: WeComAdminAuthClient | None = None,
    repo: AdminConfigRepository | None = None,
    ip: str = "",
    user_agent: str = "",
) -> AuthCallbackResult:
    config = build_config(request_base_url=request_base_url)
    state_payload = verify_auth_state(state)
    next_path = safe_next_path(state_payload.get("next") if state_payload else "")
    if not config.enabled:
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="wecom_admin_auth_not_enabled")
    missing = config.missing_keys
    if missing:
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="wecom_admin_auth_config_missing")
    if not state_payload:
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="invalid_state")
    if not normalize_text(code):
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="missing_code")

    repo = repo or AdminConfigRepository()
    client = client or build_wecom_admin_auth_client()
    try:
        token_payload = client.fetch_access_token(corp_id=config.corp_id, corp_secret=config.corp_secret)
        if _wecom_errcode(token_payload):
            return AuthCallbackResult(
                ok=False,
                next_path=next_path,
                error_code="wecom_access_token_failed",
                external_error_code=str(token_payload.get("errcode") or ""),
            )
        access_token = normalize_text(token_payload.get("access_token"))
        if not access_token:
            return AuthCallbackResult(ok=False, next_path=next_path, error_code="wecom_access_token_missing")
        user_payload = client.fetch_user_info(access_token=access_token, code=normalize_text(code))
        if _wecom_errcode(user_payload):
            return AuthCallbackResult(
                ok=False,
                next_path=next_path,
                error_code="wecom_userinfo_failed",
                external_error_code=str(user_payload.get("errcode") or ""),
            )
    except WeComAdminAuthClientError as exc:
        return AuthCallbackResult(ok=False, next_path=next_path, error_code=exc.error_code)

    wecom_userid = normalize_text(user_payload.get("UserId") or user_payload.get("userid") or user_payload.get("user_id"))
    if not wecom_userid:
        _record_login(repo, admin_user_id=0, result="missing_userid", ip=ip, user_agent=user_agent)
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="wecom_userid_missing")

    admin_user = repo.get_admin_user_by_wecom_userid(wecom_userid)
    admin_user_id = int((admin_user or {}).get("id") or 0)
    if not admin_user:
        _record_login(repo, admin_user_id=0, result="unauthorized_user", ip=ip, user_agent=user_agent)
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="admin_user_not_authorized")
    if not _bool(admin_user.get("is_active")) or not _bool(admin_user.get("login_enabled")):
        _record_login(repo, admin_user_id=admin_user_id, result="disabled_user", ip=ip, user_agent=user_agent)
        return AuthCallbackResult(ok=False, next_path=next_path, error_code="admin_user_disabled", admin_user_id=admin_user_id)

    roles = _admin_roles(repo, admin_user_id=admin_user_id, admin_level=normalize_text(admin_user.get("admin_level")))
    identity_claims = {
        "auth_source": "wecom_sso",
        "login_type": "wecom_sso",
        "admin_user_id": admin_user_id,
        "session_version": int(admin_user.get("session_version") or 1),
        "username": wecom_userid,
        "wecom_userid": wecom_userid,
        "corp_id": normalize_text(admin_user.get("wecom_corpid")) or config.corp_id,
        "display_name": normalize_text(admin_user.get("display_name")) or wecom_userid,
        "roles": roles,
        "iat": int(time()),
    }
    _record_login(repo, admin_user_id=admin_user_id, result="success", ip=ip, user_agent=user_agent)
    repo.update_admin_last_login(admin_user_id=admin_user_id)
    return AuthCallbackResult(ok=True, next_path=next_path, identity_claims=identity_claims, admin_user_id=admin_user_id)


def sign_auth_state(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_auth_state(value: str) -> dict[str, Any] | None:
    token = normalize_text(value)
    if "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except Exception:
        return None
    issued_at = int(payload.get("iat") or 0)
    if issued_at <= 0 or time() - issued_at > STATE_MAX_AGE_SECONDS:
        return None
    return payload if isinstance(payload, dict) else None


def auth_route_headers() -> dict[str, str]:
    return route_headers()


def auth_safe_next_path(value: Any) -> str:
    return safe_next_path(value)


def _admin_roles(repo: AdminConfigRepository, *, admin_user_id: int, admin_level: str) -> list[str]:
    role_rows = repo.list_admin_user_roles([admin_user_id])
    roles = [normalize_text(row.get("role_code")) for row in role_rows if normalize_text(row.get("role_code"))]
    if admin_level == "super_admin" and "super_admin" not in roles:
        roles = ["super_admin"]
    return roles or ["viewer"]


def _record_login(repo: AdminConfigRepository, *, admin_user_id: int, result: str, ip: str, user_agent: str) -> None:
    repo.insert_admin_login_audit(
        admin_user_id=admin_user_id,
        login_type="wecom_sso",
        login_result=result,
        ip=ip,
        user_agent=user_agent,
    )


def _wecom_errcode(payload: dict[str, Any]) -> bool:
    errcode = payload.get("errcode")
    return errcode not in (None, 0, "0")


def _truthy(value: Any) -> bool:
    return normalize_text(value).lower() in {"1", "true", "yes", "on"}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return normalize_text(value).lower() not in {"", "0", "false", "no", "off"}


def _secret() -> bytes:
    return require_signing_secret("SECRET_KEY", local_fallback="aicrm-next-admin-auth-local-secret")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padded = data + ("=" * (-len(data) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))
