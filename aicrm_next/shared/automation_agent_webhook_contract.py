from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from .runtime import runtime_setting


_AGENT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_AGENT_WEBHOOK_PATH_PATTERN = re.compile(r"^/api/ai/agents/([^/]+)/audience-webhook$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_CANONICAL_FIRST_PARTY_ORIGINS = {
    ("https", "www.youcangogogo.com", 443),
    ("https", "id-dev.youcangogogo.com", 443),
}


def _origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlparse(value)
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return None
    try:
        port = int(parsed.port or (443 if scheme == "https" else 80))
    except ValueError:
        return None
    return scheme, host, port


def _first_party_webhook_origin(raw: str) -> bool:
    parsed = urlparse(raw)
    if not parsed.scheme and not parsed.netloc:
        return raw.startswith("/")
    candidate = _origin(raw)
    if candidate is None:
        return False
    if candidate[1] in _LOOPBACK_HOSTS:
        return True
    configured = {
        origin
        for name in ("AICRM_PUBLIC_BASE_URL", "AICRM_AUTH_ISSUER")
        if (origin := _origin(str(runtime_setting(name, "") or "").strip())) is not None
    }
    return candidate in (_CANONICAL_FIRST_PARTY_ORIGINS | configured)


def automation_agent_code_from_webhook_url(value: Any) -> str:
    """Recognize the exact first-party Automation Agent webhook contract."""

    raw = str(value or "").strip()
    if not raw or not _first_party_webhook_origin(raw):
        return ""
    match = _AGENT_WEBHOOK_PATH_PATTERN.fullmatch(urlparse(raw).path)
    if not match:
        return ""
    agent_code = unquote(match.group(1)).strip()
    return agent_code if _AGENT_CODE_PATTERN.fullmatch(agent_code) else ""


__all__ = ["automation_agent_code_from_webhook_url"]
