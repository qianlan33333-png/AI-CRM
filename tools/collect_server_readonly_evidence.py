#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


READONLY_ROUTES = [
    {"path": "/health", "expected_runtime_owner": "next"},
    {"path": "/api/system/health", "expected_runtime_owner": "next"},
    {"path": "/admin", "expected_runtime_owner": "next"},
    {"path": "/admin/customers", "expected_runtime_owner": "next"},
    {"path": "/admin/questionnaires", "expected_runtime_owner": "next"},
    {"path": "/admin/automation-conversion", "expected_runtime_owner": "next"},
    {"path": "/sidebar/bind-mobile", "expected_runtime_owner": "next"},
    {"path": "/api/customers?limit=1", "expected_runtime_owner": "next"},
    {"path": "/api/admin/questionnaires?limit=1", "expected_runtime_owner": "next"},
]

FIXTURE_MARKERS = ("local_contract", "fixture", "demo")
EXTERNAL_SIDE_EFFECT_MARKERS = (
    '"side_effect_executed": true',
    '"real_external_call": true',
    '"external_call_executed": true',
    '"timer_enabled": true',
    '"sent_to_wecom": true',
    '"payment_provider_called": true',
)


def _join_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _marker_hits(text: str) -> list[str]:
    lower = text.lower()
    return [marker for marker in FIXTURE_MARKERS if marker in lower]


def _external_side_effect_hits(text: str) -> list[str]:
    lower = text.lower()
    return [marker for marker in EXTERNAL_SIDE_EFFECT_MARKERS if marker in lower]


def _runtime_owner(headers: dict[str, str]) -> str:
    if headers.get("x-aicrm-compatibility-facade"):
        return "production_compat"
    return "next"


def _fetch_get(base_url: str, route: dict[str, str], timeout: float) -> dict[str, Any]:
    path = route["path"]
    url = _join_url(base_url, path)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "aicrm-server-readonly-evidence/1.0"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in response.headers.items()}
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in exc.headers.items()}
        status_code = int(exc.code)
    except Exception as exc:
        return {
            "path": path,
            "url": url,
            "ok": False,
            "error": f"{exc.__class__.__name__}: {exc}",
            "status_code": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "method": "GET",
            "readonly": True,
        }
    return {
        "path": path,
        "url": url,
        "ok": status_code < 500,
        "status_code": status_code,
        "elapsed_ms": int((time.time() - started) * 1000),
        "method": "GET",
        "readonly": True,
        "headers": {
            "x-aicrm-route-owner": headers.get("x-aicrm-route-owner", ""),
            "x-aicrm-compatibility-facade": headers.get("x-aicrm-compatibility-facade", ""),
            "x-aicrm-app": headers.get("x-aicrm-app", ""),
            "x-aicrm-release-sha": headers.get("x-aicrm-release-sha", ""),
            "content-type": headers.get("content-type", ""),
        },
        "runtime_owner": _runtime_owner(headers),
        "json": _json_or_none(text),
        "body_preview": text[:500],
        "fixture_marker_hits": _marker_hits(text),
        "external_side_effect_hits": _external_side_effect_hits(text),
        "expected_runtime_owner": route.get("expected_runtime_owner", ""),
        "expected_facade": route.get("expected_facade", ""),
    }


def _health_metadata(probes: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {
        "production_data_ready": None,
        "database_mode": "",
    }
    for probe in probes:
        payload = probe.get("json")
        if not isinstance(payload, dict):
            continue
        if probe.get("path") in {"/health", "/api/system/health"}:
            if "production_data_ready" in payload:
                metadata["production_data_ready"] = payload.get("production_data_ready")
            if payload.get("database_mode"):
                metadata["database_mode"] = payload.get("database_mode")
    return metadata


def _route_blockers(probe: dict[str, Any], *, database_mode: str) -> list[str]:
    blockers: list[str] = []
    label = f"GET {probe.get('path')}"
    if probe.get("status_code") == 0:
        blockers.append(f"request_failed:{label}:{probe.get('error')}")
        return blockers
    if probe.get("status_code") == 404:
        blockers.append(f"route_404:{label}")
    if int(probe.get("status_code") or 0) >= 500:
        blockers.append(f"route_5xx:{label}:{probe.get('status_code')}")
    headers = probe.get("headers") or {}
    route_owner = headers.get("x-aicrm-route-owner") or ""
    allowed_route_owners = {"ai_crm_next"}
    if probe.get("expected_runtime_owner") == "production_compat":
        allowed_route_owners.add("production_compat")
    if route_owner not in allowed_route_owners:
        blockers.append(f"unexpected_route_owner:{label}:expected=ai_crm_next:actual={route_owner or '<missing>'}")
    if not headers.get("x-aicrm-app"):
        blockers.append(f"missing_x_aicrm_app:{label}")
    if not headers.get("x-aicrm-release-sha"):
        blockers.append(f"missing_x_aicrm_release_sha:{label}")
    expected_runtime_owner = probe.get("expected_runtime_owner")
    if expected_runtime_owner and probe.get("runtime_owner") != expected_runtime_owner:
        blockers.append(
            f"unexpected_runtime_owner:{label}:expected={expected_runtime_owner}:actual={probe.get('runtime_owner')}"
        )
    expected_facade = probe.get("expected_facade")
    if expected_facade and headers.get("x-aicrm-compatibility-facade") != expected_facade:
        blockers.append(
            f"unexpected_compatibility_facade:{label}:expected={expected_facade}:actual={headers.get('x-aicrm-compatibility-facade') or '<missing>'}"
        )
    if database_mode == "postgres" and probe.get("fixture_marker_hits"):
        blockers.append(f"fixture_marker_in_postgres_response:{label}:{','.join(probe['fixture_marker_hits'])}")
    for marker in probe.get("external_side_effect_hits") or []:
        blockers.append(f"external_side_effect_marker:{label}:{marker}")
    return blockers


def collect(base_url: str, *, timeout: float, allow_server_timer_evidence: bool = False) -> dict[str, Any]:
    probes = [_fetch_get(base_url, route, timeout) for route in READONLY_ROUTES]
    metadata = _health_metadata(probes)
    database_mode = str(metadata.get("database_mode") or "")
    blockers: list[str] = []
    warnings: list[str] = []
    for probe in probes:
        blockers.extend(_route_blockers(probe, database_mode=database_mode))
    if not any((probe.get("headers") or {}).get("x-aicrm-release-sha") for probe in probes):
        warnings.append("release_sha_absent_from_all_probes")
    safe_to_enable_timers = False
    if allow_server_timer_evidence:
        warnings.append("timer_evidence_flag_set_but_no_write_or_timer_probe_executed")
    evidence_classification = {
        "local_checker_evidence": False,
        "server_readonly_evidence": True,
        "production_canary_evidence": False,
        "note": "This script only performs readonly GET probes and cannot approve production cutover, timers, external calls, or legacy fallback removal.",
    }
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "base_url": base_url,
        "collected_at_epoch": int(time.time()),
        "readonly": True,
        "post_requests_executed": 0,
        "real_external_calls_executed": False,
        "production_data_ready": metadata.get("production_data_ready"),
        "database_mode": database_mode,
        "safe_to_enable_timers": safe_to_enable_timers,
        "safe_to_enable_real_external_calls": False,
        "safe_to_remove_legacy_fallback": False,
        "evidence_classification": evidence_classification,
        "probes": probes,
    }


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Server Readonly Evidence",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- warnings: `{len(result['warnings'])}`",
            f"- base_url: `{result['base_url']}`",
            f"- readonly: `{str(result['readonly']).lower()}`",
            f"- post_requests_executed: `{result['post_requests_executed']}`",
            f"- database_mode: `{result['database_mode']}`",
            f"- production_data_ready: `{result['production_data_ready']}`",
            f"- safe_to_enable_timers: `{str(result['safe_to_enable_timers']).lower()}`",
            f"- safe_to_enable_real_external_calls: `{str(result['safe_to_enable_real_external_calls']).lower()}`",
            f"- safe_to_remove_legacy_fallback: `{str(result['safe_to_remove_legacy_fallback']).lower()}`",
            "",
            "## Evidence Classification",
        ]
        for key, value in result["evidence_classification"].items():
            lines.append(f"- {key}: `{value}`")
        lines.extend(["", "## Probes"])
        for probe in result["probes"]:
            headers = probe.get("headers") or {}
            lines.append(
                f"- GET {probe['path']}: status=`{probe.get('status_code')}` "
                f"route_owner=`{headers.get('x-aicrm-route-owner', '')}` "
                f"runtime_owner=`{probe.get('runtime_owner', '')}` "
                f"app=`{headers.get('x-aicrm-app', '')}` "
                f"sha=`{headers.get('x-aicrm-release-sha', '')}` "
                f"fixture_markers=`{probe.get('fixture_marker_hits', [])}`"
            )
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in result["blockers"])
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in result["warnings"])
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect AI-CRM Next server readonly evidence.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-server-timer-evidence", action="store_true")
    args = parser.parse_args()
    result = collect(args.base_url, timeout=args.timeout, allow_server_timer_evidence=args.allow_server_timer_evidence)
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps({"ok": result["ok"], "blockers": result["blockers"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
