from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from itsdangerous import BadSignature, URLSafeSerializer

from aicrm_next.platform.shared.runtime import runtime_setting
from aicrm_next.platform.shared.signed_session import verify_session_payload

SIDEBAR_PRODUCT_CONTEXT_SOURCE = "sidebar_product_link"
SIDEBAR_PRODUCT_CONTEXT_RESOLVED_SOURCE = "signed_sidebar_product_link"
SIDEBAR_PRODUCT_CONTEXT_SALT = "aicrm-sidebar-product-context-v1"
SIDEBAR_PRODUCT_CONTEXT_COOKIE = "aicrm_sidebar_product_context"
DEFAULT_SIDEBAR_PRODUCT_CONTEXT_TTL_SECONDS = 30 * 86400
SIDEBAR_VIEWER_SESSION_COOKIE = "aicrm_sidebar_viewer_session"
SIDEBAR_OWNER_CONTEXT_SOURCE = "sidebar_owner_context_v2"
SIDEBAR_OWNER_CONTEXT_RESOLVED_SOURCE = "signed_sidebar_owner_context"
SIDEBAR_OWNER_CONTEXT_SALT = "aicrm-sidebar-owner-context-v2"
DEFAULT_SIDEBAR_OWNER_CONTEXT_TTL_SECONDS = 15 * 60
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_NEXT_ACTION_TOKEN_SECRET",
        "SECRET_KEY",
        "SIDEBAR_PRODUCT_CONTEXT_TOKEN_TTL_SECONDS",
        "SIDEBAR_OWNER_CONTEXT_TOKEN_TTL_SECONDS",
        "SIDEBAR_CONTEXT_TOKEN_TTL_SECONDS",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _setting(name: str, default: str = "") -> str:
    return _text(runtime_setting(name, default))


def _secret() -> str:
    return (
        _setting("AICRM_NEXT_ACTION_TOKEN_SECRET")
        or _setting("SECRET_KEY")
        or "aicrm-sidebar-context-dev-secret"
    )


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(_secret(), salt=SIDEBAR_PRODUCT_CONTEXT_SALT)


def _owner_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(_secret(), salt=SIDEBAR_OWNER_CONTEXT_SALT)


def sidebar_product_context_ttl_seconds() -> int:
    raw = _setting("SIDEBAR_PRODUCT_CONTEXT_TOKEN_TTL_SECONDS") or _setting("SIDEBAR_CONTEXT_TOKEN_TTL_SECONDS")
    try:
        value = int(raw or DEFAULT_SIDEBAR_PRODUCT_CONTEXT_TTL_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SIDEBAR_PRODUCT_CONTEXT_TTL_SECONDS
    return max(3600, min(value, 180 * 86400))


def sidebar_owner_context_ttl_seconds() -> int:
    raw = _setting("SIDEBAR_OWNER_CONTEXT_TOKEN_TTL_SECONDS") or _setting("SIDEBAR_CONTEXT_TOKEN_TTL_SECONDS")
    try:
        value = int(raw or DEFAULT_SIDEBAR_OWNER_CONTEXT_TTL_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SIDEBAR_OWNER_CONTEXT_TTL_SECONDS
    return max(60, min(value, 24 * 3600))


def build_sidebar_product_context_token(
    *,
    external_userid: str,
    owner_userid: str = "",
    bind_by_userid: str = "",
    ttl_seconds: int | None = None,
) -> str:
    normalized_external = _text(external_userid)
    if not normalized_external:
        raise ValueError("external_userid is required")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = int(ttl_seconds or sidebar_product_context_ttl_seconds())
    payload = {
        "external_userid": normalized_external,
        "owner_userid": _text(owner_userid),
        "bind_by_userid": _text(bind_by_userid) or _text(owner_userid),
        "source": SIDEBAR_PRODUCT_CONTEXT_SOURCE,
        "issued_at": now,
        "expires_at": now + max(60, ttl),
    }
    return _serializer().dumps(payload)


def build_sidebar_owner_context_token(
    *,
    viewer_userid: str,
    external_userid: str,
    session_id: str,
    corp_id: str = "",
    ttl_seconds: int | None = None,
) -> str:
    normalized_viewer = _text(viewer_userid)
    normalized_external = _text(external_userid)
    session_fingerprint = sidebar_session_fingerprint(session_id)
    if not normalized_viewer:
        raise ValueError("viewer_userid is required")
    if not normalized_external:
        raise ValueError("external_userid is required")
    if not session_fingerprint:
        raise ValueError("session_id is required")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = int(ttl_seconds or sidebar_owner_context_ttl_seconds())
    payload = {
        "viewer_userid": normalized_viewer,
        "owner_userid": normalized_viewer,
        "bind_by_userid": normalized_viewer,
        "external_userid": normalized_external,
        "session_fingerprint": session_fingerprint,
        "corp_id": _text(corp_id),
        "source": SIDEBAR_OWNER_CONTEXT_SOURCE,
        "issued_at": now,
        "expires_at": now + max(60, ttl),
    }
    return _owner_serializer().dumps(payload)


def load_sidebar_product_context_token(token: str) -> dict[str, Any]:
    normalized_token = _text(token)
    if not normalized_token:
        return {"ok": False, "status": "missing", "context": {}}
    try:
        payload = _serializer().loads(normalized_token)
    except (BadSignature, ValueError, TypeError):
        return {"ok": False, "status": "invalid", "context": {}}
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    external_userid = _text(source.get("external_userid"))
    if _text(source.get("source")) != SIDEBAR_PRODUCT_CONTEXT_SOURCE or not external_userid:
        return {"ok": False, "status": "invalid", "context": {}}
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        expires_at = int(source.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at and expires_at < now:
        return {"ok": False, "status": "expired", "context": {}}
    context = {
        "external_userid": external_userid,
        "owner_userid": _text(source.get("owner_userid")),
        "bind_by_userid": _text(source.get("bind_by_userid")) or _text(source.get("owner_userid")),
        "source": SIDEBAR_PRODUCT_CONTEXT_RESOLVED_SOURCE,
        "issued_at": int(source.get("issued_at") or 0),
        "expires_at": expires_at,
    }
    return {"ok": True, "status": "valid", "context": context}


def load_sidebar_owner_context_token(token: str) -> dict[str, Any]:
    normalized_token = _text(token)
    if not normalized_token:
        return {"ok": False, "status": "missing", "context": {}}
    try:
        payload = _owner_serializer().loads(normalized_token)
    except (BadSignature, ValueError, TypeError):
        return {"ok": False, "status": "invalid", "context": {}}
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    viewer_userid = _text(source.get("viewer_userid") or source.get("owner_userid"))
    external_userid = _text(source.get("external_userid"))
    session_fingerprint = _text(source.get("session_fingerprint"))
    if (
        _text(source.get("source")) != SIDEBAR_OWNER_CONTEXT_SOURCE
        or not viewer_userid
        or not external_userid
        or not session_fingerprint
    ):
        return {"ok": False, "status": "invalid", "context": {}}
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        issued_at = int(source.get("issued_at") or 0)
        expires_at = int(source.get("expires_at") or 0)
    except (TypeError, ValueError):
        issued_at = 0
        expires_at = 0
    if issued_at <= 0 or issued_at > now + 60 or expires_at <= now:
        return {"ok": False, "status": "expired", "context": {}}
    context = {
        "viewer_userid": viewer_userid,
        "owner_userid": viewer_userid,
        "bind_by_userid": viewer_userid,
        "external_userid": external_userid,
        "session_fingerprint": session_fingerprint,
        "corp_id": _text(source.get("corp_id")),
        "source": SIDEBAR_OWNER_CONTEXT_RESOLVED_SOURCE,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    return {"ok": True, "status": "valid", "context": context}


def sidebar_session_fingerprint(session_id: Any) -> str:
    normalized = _text(session_id)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_sidebar_owner_context(
    *,
    token: str,
    viewer_session_cookie: str,
    external_userid: str = "",
    expected_corp_id: str = "",
) -> dict[str, Any]:
    """Bind a signed owner token to the current OAuth session and target customer."""

    token_result = load_sidebar_owner_context_token(token)
    if not token_result.get("ok"):
        return token_result
    context = dict(token_result.get("context") or {})
    session = verify_session_payload(viewer_session_cookie)
    if not session or _text(session.get("auth_source")) != "wecom_sidebar_oauth":
        return {"ok": False, "status": "viewer_session_required", "context": {}}
    viewer_userid = _text(session.get("wecom_userid"))
    session_external = _text(session.get("external_userid"))
    session_fingerprint = sidebar_session_fingerprint(session.get("session_id"))
    if not viewer_userid or not session_external or not session_fingerprint:
        return {"ok": False, "status": "viewer_session_invalid", "context": {}}
    if viewer_userid != _text(context.get("viewer_userid")):
        return {"ok": False, "status": "viewer_session_mismatch", "context": {}}
    if session_external != _text(context.get("external_userid")):
        return {"ok": False, "status": "viewer_customer_mismatch", "context": {}}
    if session_fingerprint != _text(context.get("session_fingerprint")):
        return {"ok": False, "status": "viewer_session_mismatch", "context": {}}
    requested_external = _text(external_userid)
    if requested_external and requested_external != session_external:
        return {"ok": False, "status": "sidebar_customer_scope_forbidden", "context": {}}
    configured_corp = _text(expected_corp_id)
    token_corp = _text(context.get("corp_id"))
    session_corp = _text(session.get("corp_id"))
    if not token_corp or token_corp != session_corp:
        return {"ok": False, "status": "sidebar_corp_scope_forbidden", "context": {}}
    if configured_corp and (token_corp != configured_corp or session_corp != configured_corp):
        return {"ok": False, "status": "sidebar_corp_scope_forbidden", "context": {}}
    return {"ok": True, "status": "valid", "context": context}


def append_ctx_fragment(path: str, token: str) -> str:
    normalized_path = _text(path)
    normalized_token = _text(token)
    if not normalized_path or not normalized_token:
        return normalized_path
    return f"{normalized_path}#aicrm_ctx={quote(normalized_token, safe='')}"


def product_context_fragment_bootstrap_script() -> str:
    """Exchange a URL-fragment credential for an HttpOnly cookie without server-log exposure."""

    return """
<script>
(async function () {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const contextToken = String(params.get("aicrm_ctx") || "").trim();
  if (!contextToken) return;
  window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
  try {
    const response = await fetch("/api/h5/product-context/session", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({context_token: contextToken})
    });
    if (response.ok) window.location.reload();
  } catch (_error) {
    // The page remains usable without owner attribution; payment never trusts an invalid context.
  }
})();
</script>
""".strip()
