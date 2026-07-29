#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


DEFAULT_WEB_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_INGRESS_BASE_URL = "http://127.0.0.1:5002"
DEFAULT_INVALID_CALLBACK_PATHS = (
    "/wecom/external-contact/callback?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe",
    "/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe-api-events",
)


def _url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _probe(url: str, *, method: str = "GET", body: bytes | None = None, timeout_seconds: float = 3.0) -> dict[str, Any]:
    request = Request(
        url,
        data=body,
        headers={"User-Agent": "aicrm-wecom-callback-deploy-smoke/1.0"},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return {
                "checked": True,
                "status_code": int(response.status),
                "body": response.read(16384).decode("utf-8", errors="replace"),
                "error": "",
            }
    except HTTPError as exc:
        return {
            "checked": True,
            "status_code": int(exc.code),
            "body": exc.read(16384).decode("utf-8", errors="replace"),
            "error": "",
        }
    except (OSError, URLError) as exc:
        return {"checked": False, "status_code": None, "body": "", "error": str(exc)}


def _status(probe: dict[str, Any]) -> int | None:
    status = probe.get("status_code")
    return status if isinstance(status, int) else None


def _is_2xx(probe: dict[str, Any]) -> bool:
    status = _status(probe)
    return status is not None and 200 <= status < 300


def _is_route_deployed(probe: dict[str, Any]) -> bool:
    status = _status(probe)
    return status is not None and (200 <= status < 400 or status in {401, 403})


def _payload_path(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _json_payload(probe: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(probe.get("body") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_ok_or_auth(probe: dict[str, Any], *, required_list_paths: tuple[str, ...] = ()) -> bool:
    status = _status(probe)
    if status in {401, 403}:
        return True
    if status is None or not (200 <= status < 300):
        return False
    payload = _json_payload(probe)
    if payload.get("ok") is not True:
        return False
    return all(isinstance(_payload_path(payload, path), list) for path in required_list_paths)


def _detail_route_deployed(probe: dict[str, Any]) -> bool:
    status = _status(probe)
    if status in {401, 403}:
        return True
    if status is None or not (200 <= status < 300):
        if status != 404:
            return False
        try:
            payload = json.loads(str(probe.get("body") or "{}"))
        except json.JSONDecodeError:
            return False
        return payload.get("error") == "webhook_inbox_item_not_found"
    try:
        payload = json.loads(str(probe.get("body") or "{}"))
    except json.JSONDecodeError:
        return False
    return bool(payload.get("ok") is True and isinstance(payload.get("processing_chain"), dict))


def _is_app_level_invalid_callback_rejection(probe: dict[str, Any]) -> bool:
    status = _status(probe)
    return status in {400, 401, 403, 422}


def _plain_success(probe: dict[str, Any]) -> bool:
    return _status(probe) == 200 and str(probe.get("body") or "").strip() == "success"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post-deploy local smoke check for WeCom callback ingress and webhook inbox admin routes."
    )
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL)
    parser.add_argument("--ingress-base-url", default=DEFAULT_INGRESS_BASE_URL)
    parser.add_argument("--probe-timeout", type=float, default=3.0)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    web_base_url = str(args.web_base_url).rstrip("/")
    ingress_base_url = str(args.ingress_base_url).rstrip("/")
    timeout = float(args.probe_timeout)
    base_urls_distinct = bool(web_base_url and ingress_base_url and web_base_url != ingress_base_url)

    probes = {
        "web_health": _probe(_url(web_base_url, "/health"), timeout_seconds=timeout),
        "ingress_health": _probe(_url(ingress_base_url, "/health"), timeout_seconds=timeout),
        "admin_webhook_inbox_metrics": _probe(
            _url(web_base_url, "/api/admin/webhook-inbox/metrics?provider=wecom&event_family=external_contact"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_inbox_items": _probe(
            _url(web_base_url, "/api/admin/webhook-inbox/items?provider=wecom&event_family=external_contact&limit=1"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_inbox_detail": _probe(
            _url(web_base_url, "/api/admin/webhook-inbox/0"),
            timeout_seconds=timeout,
        ),
        "admin_webhook_reconciliation": _probe(
            _url(web_base_url, "/api/admin/wecom/callback/reconciliation?limit=1"),
            timeout_seconds=timeout,
        ),
    }
    invalid_callback_probes: list[dict[str, Any]] = []
    for index, path in enumerate(DEFAULT_INVALID_CALLBACK_PATHS):
        key = "ingress_invalid_callback" if index == 0 else f"ingress_invalid_callback_{index + 1}"
        probe = _probe(
            _url(ingress_base_url, path),
            method="POST",
            body=b"<xml>invalid</xml>",
            timeout_seconds=timeout,
        )
        probes[key] = probe
        invalid_callback_probes.append({"path": path, "probe_key": key, "probe": probe})

    web_health_ok = _is_2xx(probes["web_health"])
    ingress_health_ok = _is_2xx(probes["ingress_health"])
    ingress_health_payload = _json_payload(probes["ingress_health"])
    ingress_durable_ack_ready = bool(
        ingress_health_ok
        and ingress_health_payload.get("runtime") == "ai_crm_wecom_ingress"
        and ingress_health_payload.get("durable_inbox_only") is True
        and ingress_health_payload.get("ack_boundary") == "signature_decrypt_and_durable_inbox_only"
    )
    admin_api_deployed = bool(
        _json_ok_or_auth(
            probes["admin_webhook_inbox_metrics"],
            required_list_paths=(
                "queue_metrics.provider_distribution",
                "queue_metrics.route_distribution",
                "queue_metrics.recent_errors",
            ),
        )
        and _json_ok_or_auth(probes["admin_webhook_inbox_items"], required_list_paths=("items",))
        and _detail_route_deployed(probes["admin_webhook_inbox_detail"])
        and _json_ok_or_auth(probes["admin_webhook_reconciliation"], required_list_paths=("recent_items",))
    )
    ingress_callback_route_signals = [
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
    ingress_callback_routes_ready = bool(
        len(ingress_callback_route_signals) >= 2
        and all(item["app_level_callback_signal"] for item in ingress_callback_route_signals)
        and any(item["path"].startswith("/wecom/external-contact/callback") for item in ingress_callback_route_signals)
        and any(item["path"].startswith("/api/wecom/events") for item in ingress_callback_route_signals)
    )
    ok = bool(
        base_urls_distinct
        and web_health_ok
        and ingress_health_ok
        and ingress_durable_ack_ready
        and admin_api_deployed
        and ingress_callback_routes_ready
    )

    warnings: list[str] = []
    if not base_urls_distinct:
        warnings.append("web-base-url and ingress-base-url must be distinct to prove 5001/5002 runtime isolation")
    if not web_health_ok:
        warnings.append("5001 web health is not 2xx")
    if not ingress_health_ok:
        warnings.append("5002 callback ingress health is not 2xx")
    if not ingress_durable_ack_ready:
        warnings.append("5002 callback ingress health does not prove the durable-only ACK boundary")
    if not admin_api_deployed:
        warnings.append(
            "webhook inbox admin JSON APIs are not deployed, not authorized, not returning ok=true, or missing required list fields"
        )
    if not _detail_route_deployed(probes["admin_webhook_inbox_detail"]):
        warnings.append("webhook inbox detail processing-chain route is not deployed or is returning 5xx")
    if not ingress_callback_routes_ready:
        warnings.append("5002 callback ingress routes do not both reject invalid callback POST with app-level 4xx")

    return {
        "ok": ok,
        "web_base_url": web_base_url,
        "ingress_base_url": ingress_base_url,
        "base_urls_distinct": base_urls_distinct,
        "web_health_ok": web_health_ok,
        "ingress_health_ok": ingress_health_ok,
        "ingress_durable_ack_ready": ingress_durable_ack_ready,
        "admin_api_deployed": admin_api_deployed,
        "admin_detail_route_deployed": _detail_route_deployed(probes["admin_webhook_inbox_detail"]),
        "ingress_callback_routes_ready": ingress_callback_routes_ready,
        "ingress_callback_route_signals": ingress_callback_route_signals,
        "probes": probes,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
