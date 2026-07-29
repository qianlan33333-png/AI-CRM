from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from aicrm_next.platform.shared.errors import ContractError

TARGET_TYPES = {"h5", "mini_program", "url_link"}
OPEN_STRATEGIES = {"h5_redirect", "wechat_open_tag", "url_link"}
ENV_VERSIONS = {"release", "trial", "develop"}

DEFAULT_COMPLETION_TARGET: dict[str, Any] = {
    "enabled": False,
    "target_type": "h5",
    "open_strategy": "h5_redirect",
    "h5_url": "",
    "fallback_url": "",
    "mini_program": {
        "appid": "",
        "username": "",
        "path": "",
        "query": "",
        "env_version": "release",
    },
    "url_link": {
        "enabled": False,
        "url": "",
        "source_url": "",
        "response_url_key": "url_link",
        "expire_type": 0,
        "expire_interval": 30,
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def safe_completion_url(value: Any) -> str:
    normalized = _text(value)
    if not normalized:
        return ""
    if any(char.isspace() for char in normalized) or "\\" in normalized:
        return ""
    if normalized.startswith("/") and not normalized.startswith("//"):
        return normalized
    parsed = urlparse(normalized)
    if parsed.scheme == "https" and parsed.netloc:
        return normalized
    return ""


def validate_completion_url(value: Any, *, field_name: str) -> str:
    raw = _text(value)
    safe_url = safe_completion_url(raw)
    if raw and not safe_url:
        raise ContractError(f"{field_name} must be an https URL or safe internal path")
    return safe_url


def validate_url_link_source_url(value: Any, *, field_name: str = "url_link.source_url") -> str:
    raw = _text(value)
    if not raw:
        return ""
    if any(char.isspace() for char in raw) or "\\" in raw:
        raise ContractError(f"{field_name} must be an https URL")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ContractError(f"{field_name} must be an https URL")
    return raw


def validate_mini_program_path(value: Any) -> str:
    path = _text(value)
    if not path:
        return ""
    lower = path.lower()
    if (
        not path.startswith("/")
        or any(char.isspace() for char in path)
        or "\\" in path
        or "javascript:" in lower
        or "data:" in lower
    ):
        raise ContractError("mini_program.path must start with / and must not contain unsafe characters")
    return path


def completion_target_from_legacy_h5(url: Any, *, enabled: Any | None = None) -> dict[str, Any]:
    safe_url = validate_completion_url(url, field_name="h5_url")
    configured = bool(safe_url) if enabled is None else _bool(enabled)
    target = deepcopy(DEFAULT_COMPLETION_TARGET)
    target.update(
        {
            "enabled": configured and bool(safe_url),
            "target_type": "h5",
            "open_strategy": "h5_redirect",
            "h5_url": safe_url,
            "fallback_url": "",
        }
    )
    return target


def normalize_completion_target(
    value: Any,
    *,
    legacy_h5_url: Any = "",
    legacy_enabled: Any | None = None,
    field_name: str = "completion_target",
) -> dict[str, Any]:
    if value in (None, ""):
        return completion_target_from_legacy_h5(legacy_h5_url, enabled=legacy_enabled)
    if not isinstance(value, dict):
        raise ContractError(f"{field_name} must be an object")

    raw = dict(value)
    target = deepcopy(DEFAULT_COMPLETION_TARGET)
    target_type = _text(raw.get("target_type") or target["target_type"])
    if target_type not in TARGET_TYPES:
        raise ContractError("completion_target.target_type must be h5, mini_program, or url_link")
    default_strategy = {"h5": "h5_redirect", "mini_program": "wechat_open_tag", "url_link": "url_link"}[target_type]
    open_strategy = _text(raw.get("open_strategy") or default_strategy)
    if open_strategy not in OPEN_STRATEGIES:
        raise ContractError("completion_target.open_strategy must be h5_redirect, wechat_open_tag, or url_link")

    mini_raw = raw.get("mini_program") if isinstance(raw.get("mini_program"), dict) else {}
    url_link_raw = raw.get("url_link") if isinstance(raw.get("url_link"), dict) else {}
    env_version = _text(mini_raw.get("env_version") or "release")
    if env_version not in ENV_VERSIONS:
        raise ContractError("mini_program.env_version must be release, trial, or develop")

    h5_url = validate_completion_url(raw.get("h5_url") or "", field_name="h5_url")
    fallback_url = validate_completion_url(raw.get("fallback_url") or "", field_name="fallback_url")
    url_link_url = validate_completion_url(url_link_raw.get("url") or "", field_name="url_link.url")
    url_link_source_url = validate_url_link_source_url(url_link_raw.get("source_url") or "")
    response_url_key = _text(url_link_raw.get("response_url_key") or "url_link") or "url_link"
    mini_program = {
        "appid": _text(mini_raw.get("appid")),
        "username": _text(mini_raw.get("username")),
        "path": validate_mini_program_path(mini_raw.get("path")),
        "query": _text(mini_raw.get("query")),
        "env_version": env_version,
    }
    url_link = {
        "enabled": _bool(url_link_raw.get("enabled")),
        "url": url_link_url,
        "source_url": url_link_source_url,
        "response_url_key": response_url_key,
        "expire_type": int(url_link_raw.get("expire_type") or 0),
        "expire_interval": int(url_link_raw.get("expire_interval") or 30),
    }
    enabled = _bool(raw.get("enabled"))
    if enabled and target_type == "h5" and not h5_url:
        raise ContractError("completion_target.h5_url is required when h5 target is enabled")
    if enabled and target_type == "mini_program":
        if not (mini_program["username"] or mini_program["appid"]):
            raise ContractError("mini_program.username or mini_program.appid is required when mini_program target is enabled")
        if not mini_program["path"]:
            raise ContractError("mini_program.path is required when mini_program target is enabled")
    if enabled and target_type == "url_link" and not (url_link_url or url_link_source_url):
        raise ContractError("url_link.url or url_link.source_url is required when url_link target is enabled")
    if target_type == "url_link" and (url_link_url or url_link_source_url):
        url_link["enabled"] = True

    return {
        "enabled": enabled,
        "target_type": target_type,
        "open_strategy": open_strategy,
        "h5_url": h5_url,
        "fallback_url": fallback_url,
        "mini_program": mini_program,
        "url_link": url_link,
    }


def completion_target_projection(
    value: Any,
    *,
    legacy_h5_url: Any = "",
    legacy_enabled: Any | None = None,
) -> dict[str, Any]:
    target = normalize_completion_target(value, legacy_h5_url=legacy_h5_url, legacy_enabled=legacy_enabled)
    return {
        "completion_target": target,
        "completion_target_enabled": bool(target.get("enabled")),
        "completion_target_type": str(target.get("target_type") or "h5"),
    }


def h5_url_for_legacy_fields(target: dict[str, Any]) -> str:
    target_type = str(target.get("target_type") or "h5")
    if target_type == "url_link":
        return safe_completion_url((target.get("url_link") or {}).get("url"))
    if target_type == "mini_program":
        return safe_completion_url(target.get("fallback_url") or target.get("h5_url"))
    return safe_completion_url(target.get("h5_url"))


def completion_action_for_target(target: dict[str, Any], *, legacy_redirect_url: Any = "", legacy_enabled: Any | None = None) -> dict[str, Any]:
    normalized = normalize_completion_target(target)
    if not normalized.get("enabled"):
        legacy_url = safe_completion_url(legacy_redirect_url)
        if (legacy_enabled is None or _bool(legacy_enabled)) and legacy_url:
            return {"type": "redirect", "redirect_url": legacy_url}
        return {"type": "default", "redirect_url": ""}
    target_type = str(normalized.get("target_type") or "h5")
    if target_type == "mini_program":
        return {"type": "mini_program", "navigation_target": normalized}
    if target_type == "url_link":
        link = normalized.get("url_link") or {}
        source_url = _text(link.get("source_url"))
        if source_url:
            return {"type": "url_link", "navigation_target": normalized, "redirect_url": ""}
        url = safe_completion_url(link.get("url"))
        return {"type": "redirect", "redirect_url": url} if url else {"type": "default", "redirect_url": ""}
    url = safe_completion_url(normalized.get("h5_url"))
    return {"type": "redirect", "redirect_url": url} if url else {"type": "default", "redirect_url": ""}


def completion_action_with_lead_qr(
    target: dict[str, Any],
    *,
    lead_qr: dict[str, Any] | None = None,
    legacy_redirect_url: Any = "",
    legacy_enabled: Any | None = None,
) -> dict[str, Any]:
    """Project one completion action while preserving legacy redirect precedence."""

    direct_action = completion_action_for_target(
        target,
        legacy_redirect_url=legacy_redirect_url,
        legacy_enabled=legacy_enabled,
    )
    if direct_action.get("type") != "default":
        return direct_action
    qr = dict(lead_qr or {})
    qr_url = safe_completion_url(qr.get("qr_url"))
    if qr_url:
        return {
            "type": "lead_qr",
            "lead_qr": {
                "channel_id": int(qr.get("channel_id") or 0),
                "channel_name": _text(qr.get("channel_name")),
                "qr_url": qr_url,
            },
            "redirect_url": "",
        }
    return direct_action
