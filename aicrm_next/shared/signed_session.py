from __future__ import annotations

import base64
import hashlib
import hmac
import json
from time import time
from typing import Any

from aicrm_next.shared.runtime import require_signing_secret, secure_cookie_environment
from aicrm_next.shared.runtime_settings import managed_runtime_bool


ADMIN_SESSION_COOKIE = "aicrm_next_admin_session"
DEFAULT_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
DEFAULT_STATE_MAX_AGE_SECONDS = 10 * 60


def sign_session_payload(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_session_payload(cookie_value: str | None, *, max_age_seconds: int = DEFAULT_SESSION_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    payload = _load_signed_payload(cookie_value)
    if payload is None:
        return None
    issued_at = _int(payload.get("iat"))
    if issued_at <= 0 or time() - issued_at > max_age_seconds:
        return None
    return payload


def sign_state_payload(payload: dict[str, Any]) -> str:
    state_payload = dict(payload or {})
    state_payload["iat"] = _int(state_payload.get("iat")) or int(time())
    return sign_session_payload(state_payload)


def verify_state_payload(value: str | None, *, max_age_seconds: int = DEFAULT_STATE_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    return verify_session_payload(value, max_age_seconds=max_age_seconds)


def session_cookie_secure() -> bool:
    if secure_cookie_environment():
        return True
    return managed_runtime_bool("AICRM_ADMIN_SESSION_COOKIE_SECURE", False)


def _load_signed_payload(value: str | None) -> dict[str, Any] | None:
    token = str(value or "").strip()
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
    return payload if isinstance(payload, dict) else None


def _secret() -> bytes:
    return require_signing_secret("SECRET_KEY", local_fallback="aicrm-next-admin-auth-local-secret")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padded = data + ("=" * (-len(data) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
