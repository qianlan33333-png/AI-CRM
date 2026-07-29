#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and not str(sys.executable).startswith(str(ROOT / ".venv")):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])
    raise


FIXTURE_SLUGS = {"hxc-activation-v1", "disabled-demo"}
FIXTURE_MARKERS = ("local_contract", "fixture", "disabled-demo")
DEMO_MARKERS = ("demo",)
REAL_OAUTH_MARKERS = (
    "real_oauth_executed",
    "side_effect_executed",
    "access_token",
    "refresh_token",
    "api.weixin.qq.com",
)
SAFE_OAUTH_SOURCE_STATUSES = {
    "fake",
    "staging_fake",
    "missing_config",
    "adapter_error",
    "production_guard_failed",
    "next_oauth_adapter",
    "state_error",
}

LOCAL_FIXTURE_ROUTES = [
    "/admin/questionnaires",
    "/api/admin/questionnaires?limit=1",
    "/api/h5/questionnaires/hxc-activation-v1",
    "/s/hxc-activation-v1",
    "/api/h5/wechat/oauth/start?slug=hxc-activation-v1",
    "/api/h5/wechat/oauth/callback?state=hxc-activation-v1",
    "/api/h5/questionnaires/hxc-activation-v1/result",
]

PRODUCTION_PROBE_ROUTES = [
    "/admin/questionnaires",
    "/api/admin/questionnaires?limit=1",
    "/api/h5/questionnaires/hxc-activation-v1",
    "/s/hxc-activation-v1",
    "/api/h5/wechat/oauth/start?slug=hxc-activation-v1",
    "/api/h5/wechat/oauth/callback?state=hxc-activation-v1",
    "/api/h5/questionnaires/hxc-activation-v1/result",
]

SERVER_READONLY_ROUTES = [
    "/admin/questionnaires",
    "/api/admin/questionnaires?limit=1",
    "/api/h5/questionnaires/hxc-activation-v1",
    "/s/hxc-activation-v1",
    "/api/h5/wechat/oauth/start?slug=hxc-activation-v1",
    "/api/h5/wechat/oauth/callback?state=hxc-activation-v1",
    "/api/h5/questionnaires/hxc-activation-v1/result",
]


@contextmanager
def _patched_env(values: dict[str, str | None]):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def local_fixture_env():
    with _patched_env(
        {
            "AICRM_NEXT_ENV": None,
            "DATABASE_URL": None,
            "AICRM_NEXT_WECHAT_OAUTH_MODE": "fake",
            "SECRET_KEY": "questionnaire-h5-oauth-readiness-local",
        }
    ):
        yield


@contextmanager
def production_probe_env():
    with _patched_env(
        {
            "AICRM_NEXT_ENV": "production",
            "AICRM_NEXT_WECHAT_OAUTH_MODE": "fake",
            "DATABASE_URL": "postgresql://probe:probe@127.0.0.1:1/aicrm_probe",
            "SECRET_KEY": "questionnaire-h5-oauth-readiness-production",
            "WECHAT_MP_APP_ID": "wx-questionnaire-probe",
            "WECHAT_MP_APP_SECRET": "questionnaire-probe-secret",
            "WECHAT_SHOP_CALLBACK_TOKEN": "questionnaire-readiness-probe-token",
            "AICRM_PUBLIC_BASE_URL": "https://www.youcangogogo.com",
        }
    ):
        yield


def _client() -> TestClient:
    module = importlib.import_module("aicrm_next.main")
    session_module = importlib.import_module("aicrm_next.platform.shared.wechat_h5_session")
    client = TestClient(module.create_app())
    client.cookies.set(
        session_module.WECHAT_PAYMENT_IDENTITY_COOKIE,
        session_module.sign_payment_session_payload(
            {
                "openid": "openid_questionnaire_readiness_probe",
                "unionid": "unionid_questionnaire_readiness_probe",
            }
        ),
    )
    return client


def _json_or_none(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _body_text(response: Any) -> str:
    return getattr(response, "text", "") or ""


def _probe_testclient(client: TestClient, routes: list[str]) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    for path in routes:
        response = client.get(path, follow_redirects=False)
        probes[f"GET {path}"] = {
            "method": "GET",
            "path": path,
            "status_code": int(response.status_code),
            "headers": {
                "x-aicrm-route-owner": response.headers.get("X-AICRM-Route-Owner", ""),
                "x-aicrm-compatibility-facade": response.headers.get("X-AICRM-Compatibility-Facade", ""),
                "location": response.headers.get("location", ""),
                "content-type": response.headers.get("content-type", ""),
            },
            "json": _json_or_none(response),
            "body_preview": _body_text(response)[:700],
        }
    return probes


def _lower_text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, ensure_ascii=False, default=str).lower()


def _contains_localhost(value: Any) -> bool:
    text = _lower_text(value)
    return "localhost" in text or "127.0.0.1" in text


def _marker_hits(value: Any, markers: tuple[str, ...] = FIXTURE_MARKERS + DEMO_MARKERS) -> list[str]:
    text = _lower_text(value)
    return [marker for marker in markers if marker in text]


def _questionnaire_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("questionnaires") or payload.get("items") or []
    return [item for item in raw_items if isinstance(item, dict)]


def _source_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("source_status") or "").strip()


def _oauth_source_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("source_status") or "").strip()


def _add_shape_blockers(probes: dict[str, Any]) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    admin_probe = probes.get("GET /api/admin/questionnaires?limit=1", {})
    admin_status = int(admin_probe.get("status_code") or 0)
    admin_payload = admin_probe.get("json")
    if admin_status in {401, 403}:
        warnings.append("admin_questionnaires_auth_protected")
    else:
        items = _questionnaire_items(admin_payload)
        if not isinstance(admin_payload, dict):
            blockers.append("admin_questionnaires_api_not_json")
        elif "items" not in admin_payload and "questionnaires" not in admin_payload:
            blockers.append("admin_questionnaires_missing_items_or_questionnaires")
        elif not items:
            warnings.append("admin_questionnaires_empty_in_local_fixture_probe")
        for item in items:
            for key in ("slug", "title", "created_at", "updated_at"):
                if key not in item:
                    blockers.append(f"admin_questionnaire_item_missing_{key}")
            for key in ("created_at", "updated_at"):
                if key in item and item[key] is not None and not isinstance(item[key], str):
                    blockers.append(f"admin_questionnaire_item_{key}_not_serialized")

    public_payload = probes.get("GET /api/h5/questionnaires/hxc-activation-v1", {}).get("json")
    if isinstance(public_payload, dict):
        questionnaire = public_payload.get("questionnaire")
        questions = public_payload.get("questions")
        if not isinstance(questionnaire, dict):
            blockers.append("public_questionnaire_missing_questionnaire")
        if not isinstance(questions, list):
            blockers.append("public_questionnaire_missing_questions")
    else:
        blockers.append("public_questionnaire_not_json")

    result_probe = probes.get("GET /api/h5/questionnaires/hxc-activation-v1/result", {})
    result_payload = result_probe.get("json")
    result_status = int(result_probe.get("status_code") or 0)
    if (
        result_status == 403
        and isinstance(result_payload, dict)
        and (result_payload.get("error_code") or result_payload.get("error"))
        == "questionnaire_result_access_forbidden"
    ):
        pass
    elif isinstance(result_payload, dict):
        if "result" not in result_payload:
            blockers.append("submission_result_missing_result")
        if "result_message" not in result_payload:
            blockers.append("submission_result_missing_result_message")
    else:
        blockers.append("submission_result_not_json")
    return blockers, warnings


def _oauth_blockers(probes: dict[str, Any], *, production: bool) -> list[str]:
    blockers: list[str] = []
    for route_key in [
        "GET /api/h5/wechat/oauth/start?slug=hxc-activation-v1",
        "GET /api/h5/wechat/oauth/callback?state=hxc-activation-v1",
    ]:
        probe = probes.get(route_key) or {}
        status = int(probe.get("status_code") or 0)
        payload = probe.get("json")
        location = str(((probe.get("headers") or {}).get("location")) or "")
        if status == 404:
            blockers.append(f"oauth_route_404:{route_key}")
        if status >= 500:
            blockers.append(f"oauth_route_5xx:{route_key}:{status}")
        if route_key.endswith("/oauth/start?slug=hxc-activation-v1") and 300 <= status < 400:
            if not location.startswith("https://open.weixin.qq.com/connect/oauth2/authorize?"):
                blockers.append(f"oauth_unexpected_authorize_redirect:{route_key}:{location or '<missing>'}")
            if production and _contains_localhost(location):
                blockers.append(f"oauth_redirect_uri_localhost:{route_key}")
            continue
        if route_key.endswith("/oauth/callback?state=hxc-activation-v1") and status == 400:
            error = str(payload.get("error") or "") if isinstance(payload, dict) else ""
            if error == "code is required":
                continue
        if not isinstance(payload, dict):
            blockers.append(f"oauth_payload_not_json:{route_key}")
            continue
        source_status = _oauth_source_status(payload)
        if not source_status:
            blockers.append(f"oauth_missing_source_status:{route_key}")
        elif source_status not in SAFE_OAUTH_SOURCE_STATUSES:
            blockers.append(f"oauth_unsafe_source_status:{route_key}:{source_status}")
        if production and _contains_localhost(payload):
            blockers.append(f"oauth_redirect_uri_localhost:{route_key}")
        for marker in REAL_OAUTH_MARKERS:
            value = payload.get(marker)
            if value is True or (isinstance(value, str) and value.strip()):
                blockers.append(f"oauth_real_external_marker:{route_key}:{marker}")
    return blockers


def _route_availability_blockers(probes: dict[str, Any], *, allow_local_probe_5xx: bool) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    for key, probe in probes.items():
        status = int(probe.get("status_code") or 0)
        if status == 404:
            blockers.append(f"route_404:{key}")
        elif status >= 500:
            item = f"route_5xx:{key}:{status}"
            if allow_local_probe_5xx:
                warnings.append(item)
            else:
                blockers.append(item)
    return blockers, warnings


def _production_fixture_blockers(probes: dict[str, Any], *, local_probe_database: bool) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    for key, probe in probes.items():
        status = int(probe.get("status_code") or 0)
        payload = probe.get("json")
        text = {"json": payload, "body_preview": probe.get("body_preview", "")}
        if status >= 500 and local_probe_database:
            warnings.append(f"production_probe_data_unavailable:{key}:{status}")
            continue
        if status != 200:
            continue
        source_status = _source_status(payload).lower()
        hits = _marker_hits(text)
        if source_status in {"fixture", "local_contract", "demo", "fixture_boundary"}:
            blockers.append(f"production_fixture_source_status:{key}:{source_status}")
        if hits:
            blocker = f"production_fixture_or_demo_marker:{key}:{','.join(hits)}"
            if local_probe_database and key in {
                "GET /api/h5/questionnaires/hxc-activation-v1/result",
                "GET /s/hxc-activation-v1",
            }:
                warnings.append(blocker)
            else:
                blockers.append(blocker)
        items = _questionnaire_items(payload)
        slugs = {str(item.get("slug") or "") for item in items}
        if slugs and slugs.issubset(FIXTURE_SLUGS):
            blockers.append(f"production_questionnaire_fixture_slug_success:{key}")
        questionnaire = payload.get("questionnaire") if isinstance(payload, dict) else None
        if isinstance(questionnaire, dict) and str(questionnaire.get("slug") or "") in FIXTURE_SLUGS:
            blockers.append(f"production_public_fixture_slug_success:{key}")
    return blockers, warnings


def _join_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _fetch_server_get(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = _join_url(base_url, path)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "aicrm-questionnaire-h5-oauth-readiness/1.0"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in response.headers.items()}
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in exc.headers.items()}
        status_code = int(exc.code)
    except Exception as exc:
        return {
            "method": "GET",
            "path": path,
            "status_code": 0,
            "error": f"{exc.__class__.__name__}: {exc}",
            "elapsed_ms": int((time.time() - started) * 1000),
            "readonly": True,
        }
    try:
        payload = json.loads(body)
    except Exception:
        payload = None
    return {
        "method": "GET",
        "path": path,
        "status_code": status_code,
        "elapsed_ms": int((time.time() - started) * 1000),
        "readonly": True,
        "headers": {
            "x-aicrm-route-owner": headers.get("x-aicrm-route-owner", ""),
            "x-aicrm-app": headers.get("x-aicrm-app", ""),
            "x-aicrm-release-sha": headers.get("x-aicrm-release-sha", ""),
            "location": headers.get("location", ""),
            "content-type": headers.get("content-type", ""),
        },
        "json": payload,
        "body_preview": body[:700],
    }


def collect_server_evidence(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    probes = [_fetch_server_get(base_url, path, timeout) for path in SERVER_READONLY_ROUTES]
    blockers: list[str] = []
    warnings: list[str] = []
    probe_map = {f"GET {probe['path']}": probe for probe in probes}
    route_blockers, route_warnings = _route_availability_blockers(probe_map, allow_local_probe_5xx=False)
    blockers.extend(route_blockers)
    warnings.extend(route_warnings)
    blockers.extend(_oauth_blockers(probe_map, production=True))
    fixture_blockers, fixture_warnings = _production_fixture_blockers(probe_map, local_probe_database=False)
    blockers.extend(fixture_blockers)
    warnings.extend(fixture_warnings)
    for probe in probes:
        if probe.get("status_code") == 0:
            blockers.append(f"server_request_failed:GET {probe.get('path')}:{probe.get('error')}")
        headers = probe.get("headers") or {}
        if headers and headers.get("x-aicrm-route-owner") not in {"ai_crm_next", "production_compat"}:
            blockers.append(f"unexpected_route_owner:GET {probe.get('path')}:{headers.get('x-aicrm-route-owner') or '<missing>'}")
        if any(marker in _lower_text(probe) for marker in REAL_OAUTH_MARKERS):
            blockers.append(f"real_oauth_marker_in_server_response:GET {probe.get('path')}")
    return {
        "ok": not blockers,
        "base_url": base_url,
        "readonly": True,
        "post_requests_executed": 0,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "probes": probes,
        "evidence_classification": {
            "local_checker_evidence": False,
            "server_readonly_evidence": True,
            "production_canary_evidence": False,
        },
    }


def run_check(*, base_url: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    with local_fixture_env():
        local_client = _client()
        local_probes = _probe_testclient(local_client, LOCAL_FIXTURE_ROUTES)
    local_route_blockers, local_route_warnings = _route_availability_blockers(local_probes, allow_local_probe_5xx=False)
    shape_blockers, shape_warnings = _add_shape_blockers(local_probes)
    blockers.extend(local_route_blockers + shape_blockers + _oauth_blockers(local_probes, production=False))
    warnings.extend(local_route_warnings + shape_warnings)

    with production_probe_env():
        production_client = _client()
        health = production_client.get("/health").json()
        production_probes = _probe_testclient(production_client, PRODUCTION_PROBE_ROUTES)
    production_route_blockers, production_route_warnings = _route_availability_blockers(
        production_probes,
        allow_local_probe_5xx=True,
    )
    production_fixture_blockers, production_fixture_warnings = _production_fixture_blockers(
        production_probes,
        local_probe_database=True,
    )
    blockers.extend(production_route_blockers + _oauth_blockers(production_probes, production=True) + production_fixture_blockers)
    warnings.extend(production_route_warnings + production_fixture_warnings)

    server_evidence = None
    if base_url:
        server_evidence = collect_server_evidence(base_url, timeout=timeout)
        blockers.extend(server_evidence["blockers"])
        warnings.extend(server_evidence["warnings"])

    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "evidence_classification": {
            "local_checker_evidence": True,
            "server_readonly_evidence": bool(base_url),
            "production_canary_evidence": False,
            "note": "This checker proves local route/shape/OAuth guardrails and optional readonly server evidence only; it does not approve real OAuth or production cutover.",
        },
        "post_requests_executed": 0,
        "real_oauth_executed": False,
        "safe_to_enable_real_oauth": False,
        "health": health,
        "local_fixture_probes": local_probes,
        "production_probe_probes": production_probes,
        "server_evidence": server_evidence,
    }


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Questionnaire H5 / OAuth Readiness",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- warnings: `{len(result['warnings'])}`",
            f"- post_requests_executed: `{result['post_requests_executed']}`",
            f"- real_oauth_executed: `{str(result['real_oauth_executed']).lower()}`",
            f"- safe_to_enable_real_oauth: `{str(result['safe_to_enable_real_oauth']).lower()}`",
            "",
            "## Evidence Classification",
        ]
        for key, value in result["evidence_classification"].items():
            lines.append(f"- {key}: `{value}`")
        lines.extend(["", "## Local Fixture Probes"])
        for name, probe in result["local_fixture_probes"].items():
            lines.append(f"- {name}: status=`{probe['status_code']}`")
        lines.extend(["", "## Production Probe Probes"])
        for name, probe in result["production_probe_probes"].items():
            payload = probe.get("json")
            source_status = _source_status(payload) if isinstance(payload, dict) else ""
            lines.append(f"- {name}: status=`{probe['status_code']}` source_status=`{source_status}`")
        if result.get("server_evidence"):
            lines.extend(["", "## Server Readonly Evidence"])
            for probe in result["server_evidence"]["probes"]:
                headers = probe.get("headers") or {}
                lines.append(
                    f"- GET {probe['path']}: status=`{probe.get('status_code')}` "
                    f"route_owner=`{headers.get('x-aicrm-route-owner', '')}` "
                    f"sha=`{headers.get('x-aicrm-release-sha', '')}`"
                )
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in result["blockers"])
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in result["warnings"])
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check questionnaire H5 and OAuth readiness without real OAuth calls.")
    parser.add_argument("--base-url", default="", help="Optional server base URL for readonly GET evidence.")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    result = run_check(base_url=args.base_url or None, timeout=args.timeout)
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps({"ok": result["ok"], "blockers": result["blockers"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
