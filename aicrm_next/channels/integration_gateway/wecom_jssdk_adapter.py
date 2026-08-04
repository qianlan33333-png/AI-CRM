from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from threading import Condition
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from aicrm_next.platform.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform.shared.runtime import production_environment
from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting


DEFAULT_JS_API_LIST = ("getCurExternalContact", "sendChatMessage")
RUNTIME_SETTING_KEYS = frozenset({"AICRM_SIDEBAR_JSSDK_REAL_ENABLED"})
_audit_ledger = InMemoryAuditLedger()
_REAL_CACHE: dict[str, tuple[str, float]] = {}
_REAL_CACHE_CONDITION = Condition()
_REAL_CACHE_INFLIGHT: set[str] = set()


@dataclass(frozen=True)
class ExternalCallAttempt:
    adapter_name: str
    adapter_mode: str
    operation: str
    target_url: str
    status: str
    real_external_call_executed: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SidebarJSSDKInputError(ValueError):
    pass


class SidebarJSSDKConfigError(RuntimeError):
    def __init__(self, message: str, *, real_external_call_executed: bool = False) -> None:
        super().__init__(message)
        self.real_external_call_executed = real_external_call_executed


def reset_sidebar_jssdk_attempts() -> None:
    with _REAL_CACHE_CONDITION:
        _REAL_CACHE.clear()
        _REAL_CACHE_INFLIGHT.clear()
        _REAL_CACHE_CONDITION.notify_all()
    global _audit_ledger
    _audit_ledger = InMemoryAuditLedger()


def list_sidebar_jssdk_attempts() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def sidebar_jssdk_adapter_mode() -> str:
    explicit = managed_runtime_setting("AICRM_SIDEBAR_JSSDK_ADAPTER_MODE").strip().lower()
    if explicit in {"fake", "sandbox", "real_blocked"}:
        return explicit
    if explicit == "real_enabled" and _env_flag("AICRM_SIDEBAR_JSSDK_REAL_ENABLED"):
        return "real_enabled"
    if production_environment():
        return "real_blocked"
    return "fake"


def build_sidebar_jssdk_config(
    *,
    url: str,
    js_api_list: list[str] | tuple[str, ...] | None = None,
    debug: bool = False,
    corp_context: dict[str, str] | None = None,
    adapter_mode: str | None = None,
    http_get_json: Any | None = None,
) -> dict[str, Any]:
    mode = adapter_mode or sidebar_jssdk_adapter_mode()
    normalized_url = normalize_jssdk_url(url)
    context = dict(corp_context or {})
    corp_id = str(
        context.get("corp_id")
        or managed_runtime_setting("WECOM_CORP_ID")
        or "ww-next-sidebar-fixture"
    ).strip()
    agent_id = str(
        context.get("agent_id")
        or managed_runtime_setting("WECOM_AGENT_ID")
        or "1000002"
    ).strip()
    apis = [str(item).strip() for item in (js_api_list or DEFAULT_JS_API_LIST) if str(item).strip()]
    if mode == "real_enabled":
        try:
            payload = _build_real_jssdk_config(
                url=normalized_url,
                corp_id=corp_id,
                agent_id=agent_id,
                apis=apis,
                debug=debug,
                http_get_json=http_get_json,
            )
        except Exception as exc:
            real_external_call_executed = bool(getattr(exc, "real_external_call_executed", True))
            _record_attempt(
                ExternalCallAttempt(
                    adapter_name="wecom_jssdk",
                    adapter_mode=mode,
                    operation="build_jssdk_config",
                    target_url=normalized_url,
                    status="failed",
                    reason="real_wecom_signing_failed",
                    real_external_call_executed=real_external_call_executed,
                )
            )
            raise
        attempt = ExternalCallAttempt(
            adapter_name="wecom_jssdk",
            adapter_mode=mode,
            operation="build_jssdk_config",
            target_url=normalized_url,
            status="success",
            reason="real_wecom_signing_material_fetched",
            real_external_call_executed=True,
        )
        _record_attempt(attempt)
        return {
            **payload,
            "source_status": "next_jssdk_adapter",
            "adapter_mode": mode,
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": True,
            "external_call_blocked": False,
            "external_call_attempt": attempt.to_dict(),
        }

    timestamp = "1700000000"
    nonce = "next-sidebar-jssdk-nonce"
    config_signature = _fake_signature("config", mode, corp_id, agent_id, normalized_url, timestamp, nonce)
    agent_signature = _fake_signature("agent", mode, corp_id, agent_id, normalized_url, timestamp, nonce)
    blocked = mode == "real_blocked"
    attempt = ExternalCallAttempt(
        adapter_name="wecom_jssdk",
        adapter_mode=mode,
        operation="build_jssdk_config",
        target_url=normalized_url,
        status="blocked" if blocked else "planned",
        reason="real_wecom_signing_blocked_by_default" if blocked else "fake_contract_generated",
    )
    _record_attempt(attempt)
    config = {
        "url": normalized_url,
        "debug": bool(debug),
        "timestamp": timestamp,
        "nonceStr": nonce,
        "signature": config_signature,
        "jsApiList": apis,
    }
    agent_config = {
        "url": normalized_url,
        "timestamp": timestamp,
        "nonceStr": nonce,
        "signature": agent_signature,
        "jsApiList": apis,
    }
    return {
        "ok": True,
        "appId": corp_id,
        "corpId": corp_id,
        "corp_id": corp_id,
        "agentId": agent_id,
        "agent_id": agent_id,
        "timestamp": timestamp,
        "nonceStr": nonce,
        "signature": config_signature,
        "jsApiList": apis,
        "config": config,
        "agent_config": agent_config,
        "source_status": "next_jssdk_adapter",
        "adapter_mode": mode,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "external_call_blocked": blocked,
        "external_call_attempt": attempt.to_dict(),
    }


def normalize_jssdk_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise SidebarJSSDKInputError("url is required")
    if value.startswith("/"):
        value = f"http://localhost{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SidebarJSSDKInputError("url must be an absolute http(s) URL or a relative path starting with /")
    return parsed._replace(fragment="").geturl()


def _fake_signature(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _build_real_jssdk_config(
    *,
    url: str,
    corp_id: str,
    agent_id: str,
    apis: list[str],
    debug: bool,
    http_get_json: Any | None,
) -> dict[str, Any]:
    secret = managed_runtime_setting("WECOM_SECRET") or managed_runtime_setting(
        "AICRM_SIDEBAR_JSSDK_SECRET"
    )
    if not corp_id:
        raise SidebarJSSDKConfigError("WECOM_CORP_ID is required")
    if not agent_id:
        raise SidebarJSSDKConfigError("WECOM_AGENT_ID is required")
    if not secret:
        raise SidebarJSSDKConfigError("WECOM_SECRET is required")

    api_base = (
        managed_runtime_setting("WECOM_API_BASE") or "https://qyapi.weixin.qq.com"
    ).strip().rstrip("/")
    timeout = int(managed_runtime_setting("AICRM_SIDEBAR_JSSDK_TIMEOUT_SECONDS", "10") or "10")
    getter = http_get_json or _http_get_json
    access_token = _cached_value(
        f"access_token:{corp_id}",
        lambda: _fetch_access_token(getter=getter, api_base=api_base, timeout=timeout, corp_id=corp_id, secret=secret),
    )
    corp_ticket = _cached_value(
        f"jsapi_ticket:{corp_id}",
        lambda: _fetch_ticket(getter=getter, api_base=api_base, timeout=timeout, access_token=access_token, ticket_type="jsapi"),
    )
    agent_ticket = _cached_value(
        f"agent_jsapi_ticket:{corp_id}:{agent_id}",
        lambda: _fetch_ticket(
            getter=getter,
            api_base=api_base,
            timeout=timeout,
            access_token=access_token,
            ticket_type="agent_config",
        ),
    )
    config = _sign_jsapi(url=url, ticket=corp_ticket, apis=apis, debug=debug)
    agent_config = _sign_jsapi(url=url, ticket=agent_ticket, apis=apis, debug=debug)
    return {
        "ok": True,
        "appId": corp_id,
        "corpId": corp_id,
        "corp_id": corp_id,
        "agentId": agent_id,
        "agent_id": agent_id,
        "timestamp": config["timestamp"],
        "nonceStr": config["nonceStr"],
        "signature": config["signature"],
        "jsApiList": apis,
        "config": config,
        "agent_config": agent_config,
    }


def _cached_value(cache_key: str, factory: Any) -> str:
    with _REAL_CACHE_CONDITION:
        while True:
            value, expires_at = _REAL_CACHE.get(cache_key, ("", 0.0))
            if value and expires_at > time.time():
                return value
            if cache_key not in _REAL_CACHE_INFLIGHT:
                _REAL_CACHE_INFLIGHT.add(cache_key)
                break
            _REAL_CACHE_CONDITION.wait(timeout=10.0)
    try:
        payload = factory()
        value = str(payload["value"])
        with _REAL_CACHE_CONDITION:
            _REAL_CACHE[cache_key] = (
                value,
                time.time() + max(int(payload["expires_in"]) - 60, 60),
            )
        return value
    finally:
        with _REAL_CACHE_CONDITION:
            _REAL_CACHE_INFLIGHT.discard(cache_key)
            _REAL_CACHE_CONDITION.notify_all()


def _fetch_access_token(*, getter: Any, api_base: str, timeout: int, corp_id: str, secret: str) -> dict[str, Any]:
    query = urlencode({"corpid": corp_id, "corpsecret": secret})
    payload = getter(f"{api_base}/cgi-bin/gettoken?{query}", timeout=timeout)
    if int(payload.get("errcode") or 0) != 0 or not str(payload.get("access_token") or "").strip():
        raise SidebarJSSDKConfigError(
            f"WeCom access token request failed: errcode={payload.get('errcode')}",
            real_external_call_executed=True,
        )
    return {"value": str(payload["access_token"]), "expires_in": int(payload.get("expires_in") or 7200)}


def _fetch_ticket(*, getter: Any, api_base: str, timeout: int, access_token: str, ticket_type: str) -> dict[str, Any]:
    path = "/cgi-bin/get_jsapi_ticket"
    query = {"access_token": access_token}
    if ticket_type == "agent_config":
        path = "/cgi-bin/ticket/get"
        query["type"] = "agent_config"
    payload = getter(f"{api_base}{path}?{urlencode(query)}", timeout=timeout)
    if int(payload.get("errcode") or 0) != 0 or not str(payload.get("ticket") or "").strip():
        raise SidebarJSSDKConfigError(
            f"WeCom {ticket_type} ticket request failed: errcode={payload.get('errcode')}",
            real_external_call_executed=True,
        )
    return {"value": str(payload["ticket"]), "expires_in": int(payload.get("expires_in") or 7200)}


def _sign_jsapi(*, url: str, ticket: str, apis: list[str], debug: bool) -> dict[str, Any]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    plain = "&".join([f"jsapi_ticket={ticket}", f"noncestr={nonce}", f"timestamp={timestamp}", f"url={url}"])
    return {
        "url": url,
        "debug": bool(debug),
        "timestamp": timestamp,
        "nonceStr": nonce,
        "signature": hashlib.sha1(plain.encode("utf-8")).hexdigest(),
        "jsApiList": list(apis),
    }


def _http_get_json(url: str, *, timeout: int) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as response:
            return dict(json.loads(response.read().decode("utf-8")) or {})
    except (OSError, URLError, ValueError) as exc:
        raise SidebarJSSDKConfigError(
            f"WeCom JSSDK request failed: {exc}",
            real_external_call_executed=True,
        ) from exc


def _record_attempt(attempt: ExternalCallAttempt) -> None:
    _audit_ledger.record_event(
        event_type=f"sidebar.jssdk.{attempt.status}",
        actor_id="sidebar_jssdk_adapter",
        actor_type="system",
        target_type="url",
        target_id=attempt.target_url,
        source_route="/api/sidebar/jssdk-config",
        payload={
            "adapter_mode": attempt.adapter_mode,
            "operation": attempt.operation,
            "status": attempt.status,
            "reason": attempt.reason,
            "real_external_call_executed": attempt.real_external_call_executed,
        },
    )


def _env_flag(name: str) -> bool:
    return managed_runtime_bool(name)
