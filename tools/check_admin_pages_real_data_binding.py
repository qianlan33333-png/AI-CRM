#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
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


ADMIN_PAGES = [
    "/admin/cloud-orchestrator",
    "/admin/cloud-orchestrator/campaigns",
    "/admin/user-ops",
    "/admin/hxc-dashboard",
    "/admin/user-ops/ui",
    "/admin/customers",
    "/admin/questionnaires",
    "/admin/wecom-tags",
    "/admin/wechat-pay/transactions",
    "/admin/wechat-pay/products",
    "/admin/image-library",
    "/admin/miniprogram-library",
    "/admin/attachment-library",
    "/admin/runtime-config",
    "/admin/api-docs",
    "/admin/automation-conversion",
]

BAD_MARKERS = [
    "Sunset / State",
    "placeholder state",
    "已由 AI-CRM Next 承载",
    "adapter guard",
    "admin login required",
    "加载失败",
    "fixture",
    "demo-only",
    "暂未接入",
    "disabled timers",
    "只在这里处理列表",
]

SAMPLE_CUSTOMERS = ["张小蓝", "李未绑", "王缺失", "wx_ext_001"]

PRODUCTION_CONFIG_PATTERNS = ("nginx", "systemd", ".service", ".timer", "deploy/", ".github/workflows/deploy")


@contextmanager
def local_probe_env():
    old = {
        "SECRET_KEY": os.environ.get("SECRET_KEY"),
    }
    os.environ.setdefault("SECRET_KEY", "admin-pages-real-data-binding")
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _client() -> TestClient:
    module = importlib.import_module("aicrm_next.main")
    return TestClient(module.create_app())


def _strip_non_visible_html(html: str) -> str:
    stripped = re.sub(r"<script\b.*?</script>", "", html, flags=re.I | re.S)
    stripped = re.sub(r"<style\b.*?</style>", "", stripped, flags=re.I | re.S)
    stripped = re.sub(r"\splaceholder=(\"[^\"]*\"|'[^']*')", "", stripped, flags=re.I)
    return stripped


def _row_count(html: str) -> int:
    bodies = re.findall(r"<tbody[^>]*>(.*?)</tbody>", html, flags=re.I | re.S)
    return sum(len(re.findall(r"<tr\b", body, flags=re.I)) for body in bodies)


def _card_count(html: str) -> int:
    return len(re.findall(r"class=\"[^\"]*(admin-stat-card|admin-mini-stat|admin-quick-link)[^\"]*\"", html))


def _bad_marker_hits(route: str, html: str) -> list[str]:
    visible = _strip_non_visible_html(html)
    lower = visible.lower()
    hits = [f"{route}:{marker}" for marker in BAD_MARKERS if marker.lower() in lower]
    if route == "/admin/customers":
        hits.extend(f"{route}:sample_customer:{marker}" for marker in SAMPLE_CUSTOMERS if marker in visible)
    if route == "/admin/runtime-config" and re.search(r">\s*可用\s*<", visible):
        hits.append(f"{route}:empty_config_available_card")
    return hits


def _has_real_data(route: str, html: str) -> tuple[bool, int]:
    rows = _row_count(html)
    cards = _card_count(html)
    if route in {"/admin/user-ops", "/admin/hxc-dashboard"} and "hxc-dashboard-data" in html:
        hxc_stats = html.count('class="hxc-stat')
        return hxc_stats >= 5 and "/api/admin/hxc-dashboard/refresh" in html, max(rows, hxc_stats)
    if route == "/admin/api-docs":
        return rows >= 10, rows
    if route in {"/admin/user-ops", "/admin/user-ops/ui", "/admin/runtime-config", "/admin/cloud-orchestrator"}:
        return cards >= 3 or rows > 0, max(rows, cards)
    if route == "/admin/attachment-library":
        return rows > 0 or "生产数据为空" in html, rows
    return rows > 0 or cards > 0, max(rows, cards)


def _api_contracts(client: TestClient) -> list[str]:
    blockers: list[str] = []
    production_probe = bool(os.getenv("DATABASE_URL")) or str(os.getenv("AICRM_NEXT_ENV", "")).lower() in {"prod", "production"}
    ai_audience = client.get("/api/admin/ai-audience/packages")
    if ai_audience.status_code == 200:
        payload = ai_audience.json()
        if not bool(payload.get("ok")):
            blockers.append("ai_audience_packages_not_ok")
        if not isinstance(payload.get("items"), list):
            blockers.append("ai_audience_packages_items_missing")
    questionnaires = client.get("/api/admin/questionnaires")
    if questionnaires.status_code == 200:
        payload = questionnaires.json()
        names = json.dumps(payload, ensure_ascii=False)
        if production_probe and "hxc-activation-v1" in names and "disabled-demo" in names and int(payload.get("total") or 0) <= 2:
            blockers.append("questionnaires_demo_only_fixture")
    customers = client.get("/api/customers")
    if customers.status_code == 200 and int(customers.json().get("total") or 0) <= 0:
        blockers.append("customers_total_not_positive")
    return blockers


def _git_modified_files() -> list[str]:
    proc = subprocess.run(["git", "status", "--short", "--untracked-files=all"], cwd=ROOT, text=True, capture_output=True, check=False)
    return [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]


def production_config_modified() -> bool:
    for path in _git_modified_files():
        normalized = path.lower()
        if normalized.startswith(("docs/", "tests/", "tools/")):
            continue
        if any(pattern in normalized for pattern in PRODUCTION_CONFIG_PATTERNS):
            return True
    return False


def run_check() -> dict[str, Any]:
    page_results: dict[str, Any] = {}
    bad_marker_hits: list[str] = []
    auth_failures: list[str] = []
    placeholder_pages: list[str] = []
    empty_data_pages: list[str] = []
    route_404_blockers: list[str] = []
    with local_probe_env():
        client = _client()
        for route in ADMIN_PAGES:
            response = client.get(route, follow_redirects=False)
            effective_response = response
            if route in {"/admin/cloud-orchestrator", "/admin/user-ops"} and response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                expected_location = {
                    "/admin/cloud-orchestrator": "/admin/cloud-orchestrator/campaigns",
                    "/admin/user-ops": "/admin/hxc-dashboard",
                }.get(route)
                if location == expected_location:
                    effective_response = client.get(location, follow_redirects=False)
            html = effective_response.text
            markers = _bad_marker_hits(route, html)
            has_real_data, count = _has_real_data(route, html)
            page_results[route] = {
                "status": response.status_code,
                "effective_status": effective_response.status_code,
                "has_real_data": has_real_data,
                "bad_markers": markers,
                "row_count": count,
            }
            if response.status_code == 404 or effective_response.status_code == 404:
                route_404_blockers.append(route)
            if "admin login required" in html.lower():
                auth_failures.append(route)
            if markers:
                bad_marker_hits.extend(markers)
            if not has_real_data:
                empty_data_pages.append(route)
        data_blockers = _api_contracts(client)
    placeholder_pages = sorted({hit.split(":", 1)[0] for hit in bad_marker_hits})
    result = {
        "ok": not route_404_blockers
        and not bad_marker_hits
        and not auth_failures
        and not empty_data_pages
        and not data_blockers
        and not production_config_modified(),
        "page_results": page_results,
        "bad_marker_hits": sorted(set(bad_marker_hits)),
        "auth_failures": auth_failures,
        "placeholder_pages": placeholder_pages,
        "empty_data_pages": empty_data_pages,
        "route_404_blockers": route_404_blockers,
        "data_blockers": data_blockers,
        "warnings": [],
        "production_config_modified": production_config_modified(),
        "recommendation": "ADMIN_PAGES_REAL_DATA_BOUND_TO_NEXT_READ_ONLY_PAYLOADS",
    }
    return result


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Admin Pages Real Data Binding",
            "",
            f"- ok: {result['ok']}",
            f"- bad_marker_hits: {result['bad_marker_hits']}",
            f"- auth_failures: {result['auth_failures']}",
            f"- placeholder_pages: {result['placeholder_pages']}",
            f"- empty_data_pages: {result['empty_data_pages']}",
            f"- route_404_blockers: {result['route_404_blockers']}",
            f"- data_blockers: {result['data_blockers']}",
            f"- production_config_modified: {result['production_config_modified']}",
            "",
            "## Pages",
        ]
        for route, payload in result["page_results"].items():
            lines.append(f"- `{route}`: status={payload['status']} has_real_data={payload['has_real_data']} row_count={payload['row_count']}")
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-md")
    parser.add_argument("--output-json")
    args = parser.parse_args()
    result = run_check()
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
