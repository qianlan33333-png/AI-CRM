#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_admin_pages_real_data_binding as real_data_checker
from aicrm_next.admin_shell.navigation import nav_items

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and not str(sys.executable).startswith(str(ROOT / ".venv")):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])
    raise

TARGET_NAV_GROUPS = [
    ("运营", ["自动化运营", "运营闭环", "群运营计划", "渠道码中心", "AI 助手", "客户激活 / 客户列表", "漏斗 / 数据看板", "问卷", "内容雷达", "企微标签管理"]),
    ("交易", ["交易管理", "商品管理", "周期商品管理", "优惠券"]),
    ("素材", ["图片素材库", "小程序素材库", "附件素材库"]),
    ("配置及后台", ["自动化话术", "负责人迁移", "配置", "API 文档"]),
]

ADMIN_PAGES = [
    "/admin",
    "/admin/automation-conversion",
    "/admin/automation-conversion/group-ops/ui",
    "/admin/channels",
    "/admin/channels/new",
    "/admin/cloud-orchestrator",
    "/admin/customers",
    "/admin/user-ops",
    "/admin/user-ops/ui",
    "/admin/questionnaires",
    "/admin/radar-links",
    "/admin/wecom-tags",
    "/admin/wechat-pay/transactions",
    "/admin/wechat-pay/products",
    "/admin/image-library",
    "/admin/miniprogram-library",
    "/admin/attachment-library",
    "/admin/owner-migration",
    "/admin/api-docs",
]

PRODUCTION_DATA_ROUTES = [
    "/api/admin/ai-audience/packages",
    "/api/admin/questionnaires",
    "/api/customers",
    "/api/admin/wechat-pay/products",
    "/api/admin/image-library",
    "/api/admin/miniprogram-library",
    "/api/admin/attachment-library",
]

FIXTURE_MARKERS = [
    "fixture adapter",
    "fixture 方案",
    "demo-only",
    "partial adapter",
    "默认转化方案",
    "AI-CRM Next 自动化转化 fixture",
]

PRODUCTION_CONFIG_PATTERNS = ("nginx", "systemd", ".service", ".timer", "deploy/", ".github/workflows/deploy")


@contextmanager
def local_admin_probe_env():
    old = {
        "AICRM_NEXT_ENV": os.environ.get("AICRM_NEXT_ENV"),
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "SECRET_KEY": os.environ.get("SECRET_KEY"),
    }
    os.environ.pop("AICRM_NEXT_ENV", None)
    os.environ.pop("DATABASE_URL", None)
    os.environ.setdefault("SECRET_KEY", "next-admin-ui-data-parity")
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


def _nav_groups_ready(shell_context: dict[str, Any]) -> bool:
    nav = shell_context.get("nav_groups") or shell_context.get("navigation") or []
    observed = [(group.get("title"), [item.get("label") for item in list(group.get("items") or [])]) for group in nav]
    return observed == TARGET_NAV_GROUPS


def _admin_pages(client: TestClient) -> tuple[dict[str, int], list[str], list[str]]:
    statuses: dict[str, int] = {}
    route_404_blockers: list[str] = []
    fixture_markers: list[str] = []
    for route in ADMIN_PAGES:
        response = client.get(route, follow_redirects=False)
        statuses[route] = response.status_code
        if response.status_code == 404:
            route_404_blockers.append(route)
        html = response.text.lower()
        for marker in FIXTURE_MARKERS:
            if marker.lower() in html:
                fixture_markers.append(f"{route}:{marker}")
        if route == "/admin" and "user ops" in html:
            fixture_markers.append(f"{route}:User Ops")
        if route == "/admin" and "自动化转化" in response.text:
            fixture_markers.append(f"{route}:自动化转化")
    return statuses, route_404_blockers, fixture_markers


def _static_production_data_contracts_ready() -> tuple[bool, list[str]]:
    blockers: list[str] = []
    checks = {
        "ai_audience_admin_read_api": ROOT / "aicrm_next" / "extensions" / "ai" / "ai_audience_ops" / "admin_api.py",
        "questionnaire_legacy_facade": ROOT / "aicrm_next" / "extensions" / "forms" / "questionnaire" / "api.py",
        "customer_legacy_facade": ROOT / "aicrm_next" / "customer_read_model" / "api.py",
    }
    for name, path in checks.items():
        source = path.read_text(encoding="utf-8")
        if name == "ai_audience_admin_read_api":
            if "list_admin_package_summaries" not in source:
                blockers.append(f"{name}:missing_admin_read_model")
            continue
        if "production_data_ready()" not in source:
            blockers.append(f"{name}:missing_production_data_ready_guard")
    frontend_compat_facade = ROOT / "aicrm_next" / "frontend_compat" / "legacy_routes.py"
    if frontend_compat_facade.exists():
        blockers.append("frontend_compat_legacy_routes:should_be_removed")
    for facade in (
        ROOT / "aicrm_next" / "integration_gateway" / "legacy_automation_facade.py",
        ROOT / "aicrm_next" / "integration_gateway" / "legacy_questionnaire_facade.py",
    ):
        if facade.exists():
            blockers.append(f"{facade.name}:orphan_legacy_facade_should_be_removed")
    return not blockers, blockers


def run_check() -> dict[str, Any]:
    with local_admin_probe_env():
        client = _client()
        page_statuses, route_404_blockers, fixture_markers = _admin_pages(client)
    real_data_result = real_data_checker.run_check()
    nav_groups_ready = _nav_groups_ready({"nav_groups": nav_items("")})
    production_data_ready, data_blockers = _static_production_data_contracts_ready()
    data_blockers.extend(real_data_result["data_blockers"])
    data_blockers.extend(real_data_result["empty_data_pages"])
    fixture_markers.extend(real_data_result["bad_marker_hits"])
    route_404_blockers.extend(real_data_result["route_404_blockers"])
    admin_pages_ready = not route_404_blockers
    if not nav_groups_ready:
        data_blockers.append("grouped_navigation_mismatch")
    result = {
        "ok": nav_groups_ready
        and admin_pages_ready
        and production_data_ready
        and not fixture_markers
        and not production_config_modified()
        and real_data_result["ok"],
        "nav_groups_ready": nav_groups_ready,
        "admin_pages_ready": admin_pages_ready,
        "production_data_ready": production_data_ready,
        "fixture_markers": sorted(set(fixture_markers)),
        "route_404_blockers": route_404_blockers,
        "data_blockers": data_blockers,
        "warnings": ["automation-jobs-run-due and campaign-run-due timers are intentionally not enabled"],
        "safe_to_continue_automation_job_recovery": nav_groups_ready and admin_pages_ready and production_data_ready and not fixture_markers and real_data_result["ok"],
        "admin_page_statuses": page_statuses,
        "real_data_binding": real_data_result,
        "target_nav_groups": TARGET_NAV_GROUPS,
        "production_config_modified": production_config_modified(),
    }
    return result


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Next Admin UI Data Parity",
            "",
            f"- ok: {result['ok']}",
            f"- nav_groups_ready: {result['nav_groups_ready']}",
            f"- admin_pages_ready: {result['admin_pages_ready']}",
            f"- production_data_ready: {result['production_data_ready']}",
            f"- fixture_markers: {result['fixture_markers']}",
            f"- route_404_blockers: {result['route_404_blockers']}",
            f"- data_blockers: {result['data_blockers']}",
            f"- safe_to_continue_automation_job_recovery: {result['safe_to_continue_automation_job_recovery']}",
        ]
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
