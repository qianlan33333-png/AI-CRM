#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


BASE_URL_ENV = "AICRM_CALLBACK_PUBLIC_BASE_URL"
DEFAULT_INVALID_CALLBACK_PATH = "/wecom/external-contact/callback?msg_signature=invalid&timestamp=1&nonce=public-state-probe"
DEFAULT_INVALID_CALLBACK_PATHS = (
    DEFAULT_INVALID_CALLBACK_PATH,
    "/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=public-state-probe-api-events",
)


def _url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _probe(url: str, *, method: str = "GET", body: bytes | None = None, timeout_seconds: float = 3.0) -> dict[str, Any]:
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/xml",
            "User-Agent": "aicrm-wecom-callback-public-state-check/1.0",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(16384).decode("utf-8", errors="replace")
            return {
                "checked": True,
                "status_code": int(response.status),
                "body": payload,
                "error": "",
            }
    except HTTPError as exc:
        payload = exc.read(16384).decode("utf-8", errors="replace")
        return {
            "checked": True,
            "status_code": int(exc.code),
            "body": payload,
            "error": "",
        }
    except (OSError, URLError) as exc:
        return {
            "checked": False,
            "status_code": None,
            "body": "",
            "error": str(exc),
        }


def _is_2xx(probe: dict[str, Any]) -> bool:
    status = probe.get("status_code")
    return isinstance(status, int) and 200 <= status < 300


def _is_2xx_or_3xx(probe: dict[str, Any]) -> bool:
    status = probe.get("status_code")
    return isinstance(status, int) and 200 <= status < 400


def _is_route_deployed_signal(probe: dict[str, Any]) -> bool:
    status = probe.get("status_code")
    return isinstance(status, int) and (200 <= status < 400 or status in {401, 403})


def _json_payload(probe: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(probe.get("body") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_path(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _is_json_api_deployed_signal(probe: dict[str, Any], *, required_list_paths: tuple[str, ...] = ()) -> bool:
    status = probe.get("status_code")
    if status in {401, 403}:
        return True
    if not isinstance(status, int) or not (200 <= status < 300):
        return False
    payload = _json_payload(probe)
    if payload.get("ok") is not True:
        return False
    return all(isinstance(_payload_path(payload, path), list) for path in required_list_paths)


def _is_detail_route_deployed_signal(probe: dict[str, Any]) -> bool:
    status = probe.get("status_code")
    if status in {401, 403}:
        return True
    payload = _json_payload(probe)
    if status == 404:
        return payload.get("error") == "webhook_inbox_item_not_found"
    if isinstance(status, int) and 200 <= status < 300:
        return payload.get("ok") is True and isinstance(payload.get("processing_chain"), dict)
    return False


def _is_app_level_invalid_callback_rejection(probe: dict[str, Any]) -> bool:
    status = probe.get("status_code")
    return isinstance(status, int) and status in {400, 401, 403, 422}


def _plain_success(probe: dict[str, Any]) -> bool:
    return probe.get("status_code") == 200 and str(probe.get("body") or "").strip() == "success"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Public HTTP-only state check for the WeCom callback storm mitigation and permanent-fix deployment signals."
    )
    parser.add_argument("--base-url", default="")
    parser.add_argument("--probe-timeout", type=float, default=3.0)
    parser.add_argument(
        "--invalid-callback-path",
        action="append",
        default=None,
        help="Invalid callback path to probe. Repeat to override the default dual-route probes.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    base_url = str(args.base_url or "").strip() or str(os.getenv(BASE_URL_ENV, "")).strip()
    base_url = base_url.rstrip("/")
    timeout = float(args.probe_timeout)
    invalid_callback_paths = tuple(str(path) for path in (args.invalid_callback_path or DEFAULT_INVALID_CALLBACK_PATHS))
    if not base_url:
        return {
            "ok": False,
            "base_url": "",
            "error": "base_url_required",
            "warnings": [f"pass --base-url or set {BASE_URL_ENV}; this script no longer defaults to production"],
            "probes": {},
            "callback_route_signals": [],
            "user_facing_available": False,
            "admin_webhook_inbox_api_deployed": False,
            "admin_webhook_inbox_detail_route_deployed": False,
            "invalid_callback_plain_success": False,
            "app_level_callback_signal": False,
            "permanent_fix_public_signals_ready": False,
        }

    probes = {
        "health": _probe(_url(base_url, "/health"), timeout_seconds=timeout),
        "sidebar_bind_mobile": _probe(_url(base_url, "/sidebar/bind-mobile"), timeout_seconds=timeout),
        "admin_automation_conversion": _probe(_url(base_url, "/admin/automation-conversion"), timeout_seconds=timeout),
        "admin_webhook_inbox_metrics": _probe(
            _url(base_url, "/api/admin/webhook-inbox/metrics?provider=wecom&event_family=external_contact"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_inbox_items": _probe(
            _url(base_url, "/api/admin/webhook-inbox/items?provider=wecom&event_family=external_contact&limit=1"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_inbox_detail": _probe(
            _url(base_url, "/api/admin/webhook-inbox/0"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_reconciliation": _probe(
            _url(base_url, "/api/admin/wecom/callback/reconciliation?limit=1"),
            timeout_seconds=timeout,
        ),
    }
    invalid_callback_probes: list[dict[str, Any]] = []
    for index, path in enumerate(invalid_callback_paths):
        key = "invalid_callback" if index == 0 else f"invalid_callback_{index + 1}"
        probe = _probe(
            _url(base_url, path),
            method="POST",
            body=b"<xml>invalid</xml>",
            timeout_seconds=timeout,
        )
        probes[key] = probe
        invalid_callback_probes.append({"path": path, "probe_key": key, "probe": probe})

    user_facing_available = bool(
        _is_2xx(probes["health"])
        and _is_2xx(probes["sidebar_bind_mobile"])
        and _is_2xx_or_3xx(probes["admin_automation_conversion"])
    )
    admin_webhook_inbox_api_deployed = bool(
        _is_json_api_deployed_signal(
            probes["admin_webhook_inbox_metrics"],
            required_list_paths=(
                "queue_metrics.provider_distribution",
                "queue_metrics.route_distribution",
                "queue_metrics.recent_errors",
            ),
        )
        and _is_json_api_deployed_signal(probes["admin_webhook_inbox_items"], required_list_paths=("items",))
        and _is_detail_route_deployed_signal(probes["admin_webhook_inbox_detail"])
        and _is_json_api_deployed_signal(probes["admin_webhook_reconciliation"], required_list_paths=("recent_items",))
    )
    admin_webhook_inbox_detail_route_deployed = _is_detail_route_deployed_signal(probes["admin_webhook_inbox_detail"])
    callback_route_signals = [
        {
            "path": item["path"],
            "probe_key": item["probe_key"],
            "checked": item["probe"].get("checked") is True,
            "plain_success": _plain_success(item["probe"]),
            "app_level_callback_signal": bool(
                item["probe"].get("checked")
                and not _plain_success(item["probe"])
                and _is_app_level_invalid_callback_rejection(item["probe"])
            ),
            "status_code": item["probe"].get("status_code"),
            "error": item["probe"].get("error") or "",
        }
        for item in invalid_callback_probes
    ]
    invalid_callback_plain_success = any(item["plain_success"] for item in callback_route_signals)
    app_level_callback_signal = bool(
        callback_route_signals and all(item["app_level_callback_signal"] for item in callback_route_signals)
    )

    warnings: list[str] = []
    if not user_facing_available:
        warnings.append("user-facing health/sidebar/admin probes are not all available")
    if not admin_webhook_inbox_api_deployed:
        warnings.append("admin webhook inbox APIs still look undeployed or unavailable")
    if not admin_webhook_inbox_detail_route_deployed:
        warnings.append("admin webhook inbox detail processing-chain route still looks undeployed or unavailable")
    if invalid_callback_plain_success:
        quick_ack_paths = [item["path"] for item in callback_route_signals if item["plain_success"]]
        warnings.append("invalid callback POST still returns plain success; public routes still look like emergency quick ACK: " + ", ".join(quick_ack_paths))
    if not app_level_callback_signal:
        warnings.append("callback public probes do not prove app-level 4xx verification/decrypt rejection for every callback route")

    permanent_fix_public_signals_ready = bool(
        user_facing_available and admin_webhook_inbox_api_deployed and app_level_callback_signal
    )
    return {
        "ok": permanent_fix_public_signals_ready,
        "base_url": base_url,
        "user_facing_available": user_facing_available,
        "admin_webhook_inbox_api_deployed": admin_webhook_inbox_api_deployed,
        "admin_webhook_inbox_detail_route_deployed": admin_webhook_inbox_detail_route_deployed,
        "invalid_callback_plain_success": invalid_callback_plain_success,
        "app_level_callback_signal": app_level_callback_signal,
        "callback_route_signals": callback_route_signals,
        "permanent_fix_public_signals_ready": permanent_fix_public_signals_ready,
        "probes": probes,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
