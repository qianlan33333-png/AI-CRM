from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from fastapi.responses import JSONResponse, RedirectResponse, Response

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime_settings import managed_runtime_int, managed_runtime_setting

from .domain import safe_completion_url


def _text(value: Any) -> str:
    return str(value or "").strip()


RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_COMPLETION_URL_LINK_API_ALLOWLIST",
        "AICRM_COMPLETION_URL_LINK_API_PATH_PREFIXES",
        "AICRM_COMPLETION_URL_LINK_TARGET_ALLOWLIST",
        "AICRM_COMPLETION_URL_LINK_TIMEOUT_SECONDS",
    }
)


def _csv_setting(name: str, default: str) -> set[str]:
    raw = managed_runtime_setting(name, default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _timeout() -> float:
    return float(
        managed_runtime_int(
            "AICRM_COMPLETION_URL_LINK_TIMEOUT_SECONDS",
            5,
            minimum=1,
        )
    )


def _allowed_source_hosts() -> set[str]:
    return _csv_setting("AICRM_COMPLETION_URL_LINK_API_ALLOWLIST", "ip.lhbl.com.cn")


def _allowed_source_path_prefixes() -> tuple[str, ...]:
    raw = managed_runtime_setting(
        "AICRM_COMPLETION_URL_LINK_API_PATH_PREFIXES",
        "/api/wxlink",
    )
    prefixes = tuple(item.strip() for item in raw.split(",") if item.strip())
    return prefixes or ("/api/wxlink",)


def _allowed_target_hosts() -> set[str]:
    return _csv_setting(
        "AICRM_COMPLETION_URL_LINK_TARGET_ALLOWLIST",
        "wxaurl.cn,wxmpurl.cn",
    )


def _normalize_response_key(value: Any) -> str:
    key = _text(value) or "url_link"
    if not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ContractError("response_url_key is invalid")
    return key


def _validate_source_url(value: Any) -> str:
    raw = _text(value)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ContractError("url_link.source_url must be an allowed https URL")
    if host not in _allowed_source_hosts():
        raise ContractError("url_link.source_url host is not allowed")
    if not any(parsed.path.startswith(prefix) for prefix in _allowed_source_path_prefixes()):
        raise ContractError("url_link.source_url path is not allowed")
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True), doseq=True)
    return urlunparse(("https", parsed.netloc, parsed.path, "", query, ""))


def _value_at_key(payload: dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _extract_url(payload: dict[str, Any], response_url_key: str) -> str:
    keys = [response_url_key, "url_link", "url", "http", "link"]
    for key in keys:
        value = _value_at_key(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _validate_target_url(value: Any) -> str:
    raw = _text(value)
    safe_url = safe_completion_url(raw)
    if not safe_url:
        raise ContractError("resolved url_link is not a safe https URL")
    host = (urlparse(safe_url).hostname or "").lower()
    if host not in _allowed_target_hosts():
        raise ContractError("resolved url_link host is not allowed")
    return safe_url


def resolve_dynamic_url_link(source_url: Any, *, response_url_key: Any = "url_link") -> str:
    safe_source_url = _validate_source_url(source_url)
    key = _normalize_response_key(response_url_key)
    request = Request(safe_source_url, headers={"Accept": "application/json", "User-Agent": "AI-CRM-Next/1.0"})
    with urlopen(request, timeout=_timeout()) as response:
        body = response.read(64 * 1024)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ContractError("url_link resolver response must be JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("url_link resolver response must be an object")
    return _validate_target_url(_extract_url(payload, key))


def url_link_resolver_response(
    *,
    source_url: Any,
    response_url_key: Any = "url_link",
    fallback_url: Any = "",
) -> Response:
    safe_fallback = safe_completion_url(fallback_url)
    try:
        resolved_url = resolve_dynamic_url_link(source_url, response_url_key=response_url_key)
    except Exception as exc:
        if safe_fallback:
            return RedirectResponse(
                safe_fallback,
                status_code=302,
                headers={
                    "X-AICRM-Route-Owner": "ai_crm_next",
                    "X-AICRM-Real-External-Call-Executed": "false",
                    "X-AICRM-Url-Link-Resolved": "false",
                    "X-AICRM-Url-Link-Fallback-Used": "true",
                },
            )
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc) or "url_link_resolve_failed",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
                "real_external_call_executed": False,
            },
            status_code=502,
            headers={
                "X-AICRM-Route-Owner": "ai_crm_next",
                "X-AICRM-Real-External-Call-Executed": "false",
                "X-AICRM-Url-Link-Resolved": "false",
            },
        )
    return RedirectResponse(
        resolved_url,
        status_code=302,
        headers={
            "X-AICRM-Route-Owner": "ai_crm_next",
            "X-AICRM-Real-External-Call-Executed": "true",
            "X-AICRM-Url-Link-Resolved": "true",
            "X-AICRM-Url-Link-Fallback-Used": "false",
        },
    )
