#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.routing import Match

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_MARKERS = ("local_contract", "fixture", "demo")

ROUTE_MATRIX: list[dict[str, Any]] = [
    {
        "route_pattern": "/sidebar/*",
        "probe_method": "GET",
        "probe_path": "/sidebar/bind-mobile",
        "current_owner": "next exact readonly",
        "future_next_owner": "identity_contact",
        "data_source": "identity_contact readonly page",
        "access": "readonly_page",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.identity_contact.admin_pages",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/sidebar/*",
        "probe_method": "GET",
        "probe_path": "/api/sidebar/contact-binding-status",
        "current_owner": "next exact readonly",
        "future_next_owner": "identity_contact",
        "data_source": "identity_contact",
        "access": "read_identity_binding",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.identity_contact.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/sidebar/customer-context",
        "probe_method": "GET",
        "probe_path": "/api/sidebar/customer-context",
        "current_owner": "next exact readonly",
        "future_next_owner": "customer_read_model",
        "data_source": "customer_read_model",
        "access": "readonly_customer_context",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/sidebar/bind-mobile",
        "probe_method": "POST",
        "probe_path": "/api/sidebar/bind-mobile",
        "probe_json": {},
        "current_owner": "next command",
        "future_next_owner": "identity_contact",
        "data_source": "identity_contact",
        "access": "write_identity_mobile_binding",
        "write_guard": "guarded_invalid_payload_probe_only",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.sidebar_write.api",
        "next_exact_owner_status": "next command",
    },
    {
        "route_pattern": "/api/sidebar/lead-pool/*",
        "probe_method": "GET",
        "probe_path": "/api/sidebar/lead-pool/status",
        "current_owner": "next exact readonly",
        "future_next_owner": "automation_engine",
        "data_source": "customer_read_model",
        "access": "readonly_lead_pool_status",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/sidebar/signup-tags/*",
        "probe_method": "GET",
        "probe_path": "/api/sidebar/signup-tags/status",
        "current_owner": "next exact readonly",
        "future_next_owner": "customer_read_model",
        "data_source": "customer_read_model",
        "access": "readonly_signup_tag_status",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/sidebar/marketing-status*",
        "probe_method": "GET",
        "probe_path": "/api/sidebar/marketing-status",
        "current_owner": "next exact readonly",
        "future_next_owner": "automation_engine",
        "data_source": "customer_read_model",
        "access": "readonly_marketing_status",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/admin/customers/profile",
        "probe_method": "GET",
        "probe_path": "/api/admin/customers/profile",
        "current_owner": "next exact readonly",
        "future_next_owner": "customer_read_model",
        "data_source": "customer_read_model",
        "access": "readonly_customer_profile",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
    {
        "route_pattern": "/api/admin/customers/profile/*",
        "probe_method": "GET",
        "probe_path": "/api/admin/customers/profile/tags",
        "current_owner": "next exact readonly",
        "future_next_owner": "customer_read_model",
        "data_source": "customer_read_model",
        "access": "readonly_profile_sections",
        "write_guard": "n/a",
        "expected_facade": "",
        "expected_endpoint_module": "aicrm_next.crm.customer_read_model.api",
        "next_exact_owner_status": "next exact readonly",
    },
]


@contextmanager
def production_sidebar_probe_env():
    keys = {
        "AICRM_NEXT_ENV": os.environ.get("AICRM_NEXT_ENV"),
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "SECRET_KEY": os.environ.get("SECRET_KEY"),
    }
    os.environ["AICRM_NEXT_ENV"] = "production"
    os.environ.setdefault("DATABASE_URL", "postgresql://sidebar:sidebar@127.0.0.1:1/aicrm_sidebar_probe")
    os.environ.setdefault("SECRET_KEY", "sidebar-profile-next-owner-readiness")
    try:
        yield
    finally:
        for key, value in keys.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _client() -> TestClient:
    with production_sidebar_probe_env():
        module = importlib.import_module("aicrm_next.main")
        return TestClient(module.create_app())


def _contains_fixture_marker(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in FIXTURE_MARKERS)


def _probe_route(client: TestClient, record: dict[str, Any]) -> dict[str, Any]:
    method = str(record["probe_method"]).lower()
    path = str(record["probe_path"])
    kwargs: dict[str, Any] = {}
    if "probe_json" in record:
        kwargs["json"] = record["probe_json"]
    response = getattr(client, method)(path, **kwargs)
    endpoint_module = _first_matching_endpoint_module(client, method=str(record["probe_method"]), path=path)
    body = response.text
    return {
        "route_pattern": record["route_pattern"],
        "method": record["probe_method"],
        "path": path,
        "status_code": response.status_code,
        "route_owner_header": response.headers.get("X-AICRM-Route-Owner", ""),
        "compatibility_facade": response.headers.get("X-AICRM-Compatibility-Facade", ""),
        "fixture_marker_present": _contains_fixture_marker(body),
        "expected_facade": record["expected_facade"],
        "endpoint_module": endpoint_module,
        "expected_endpoint_module": record.get("expected_endpoint_module", ""),
        "write_probe": str(record["access"]).startswith("write_"),
        "state_changing_probe": record["probe_method"] in {"POST", "PUT", "PATCH", "DELETE"},
    }


def _first_matching_endpoint_module(client: TestClient, *, method: str, path: str) -> str:
    scope = {"type": "http", "method": method.upper(), "path": path, "root_path": "", "headers": []}
    for route in client.app.routes:
        match, _ = route.matches(scope)
        if match == Match.NONE:
            continue
        endpoint = getattr(route, "endpoint", None)
        return getattr(endpoint, "__module__", "") if endpoint else ""
    return ""


def _validate_matrix() -> list[str]:
    blockers: list[str] = []
    required_fields = {
        "route_pattern",
        "current_owner",
        "future_next_owner",
        "data_source",
        "access",
        "write_guard",
        "expected_facade",
        "next_exact_owner_status",
    }
    allowed_future_owners = {"customer_read_model", "identity_contact", "frontend_compat", "automation_engine"}
    allowed_current_owners = {
        "production_compat legacy_forward",
        "exact compatibility facade",
        "next exact readonly",
        "next command",
        "missing Next exact owner",
        "blocked",
    }
    for record in ROUTE_MATRIX:
        missing = sorted(required_fields - set(record))
        if missing:
            blockers.append(f"matrix_missing_fields:{record.get('route_pattern', '<unknown>')}:{','.join(missing)}")
        if record.get("future_next_owner") not in allowed_future_owners:
            blockers.append(f"matrix_invalid_future_owner:{record.get('route_pattern')}")
        if record.get("current_owner") not in allowed_current_owners:
            blockers.append(f"matrix_invalid_current_owner:{record.get('route_pattern')}")
        if str(record.get("access", "")).startswith("write_") and not str(record.get("write_guard", "")).startswith("guarded"):
            blockers.append(f"matrix_write_not_guarded:{record.get('route_pattern')}")
        if not record.get("current_owner"):
            blockers.append(f"matrix_missing_current_owner:{record.get('route_pattern')}")
    return blockers


def run_check() -> dict[str, Any]:
    blockers = _validate_matrix()
    client = _client()
    probes = [_probe_route(client, record) for record in ROUTE_MATRIX]
    for probe in probes:
        label = f"{probe['method']} {probe['path']}"
        if probe["status_code"] == 404:
            blockers.append(f"route_404:{label}")
        if int(probe["status_code"]) >= 500:
            blockers.append(f"route_5xx:{label}:{probe['status_code']}")
        if probe["fixture_marker_present"]:
            blockers.append(f"fixture_marker_present:{label}")
        if not probe["route_owner_header"]:
            blockers.append(f"missing_route_owner_header:{label}")
        if probe["compatibility_facade"] != probe["expected_facade"]:
            blockers.append(
                f"unexpected_facade:{label}:expected={probe['expected_facade']}:actual={probe['compatibility_facade']}"
            )
        if probe["expected_endpoint_module"] and probe["endpoint_module"] != probe["expected_endpoint_module"]:
            blockers.append(
                f"unexpected_endpoint_owner:{label}:expected={probe['expected_endpoint_module']}:actual={probe['endpoint_module']}"
            )
        if probe["write_probe"] and probe["state_changing_probe"] and 200 <= int(probe["status_code"]) < 300:
            blockers.append(f"write_probe_not_guarded:{label}")
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "route_matrix": ROUTE_MATRIX,
        "probes": probes,
        "business_impact": "Prevents WeCom sidebar, mobile binding, and customer profile routes from becoming 404/500 during AI-CRM Next cutover.",
        "runtime_changed": False,
    }


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Sidebar/Profile Next Owner Readiness",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- runtime_changed: `{str(result['runtime_changed']).lower()}`",
            f"- business_impact: {result['business_impact']}",
            "",
            "## Probes",
        ]
        for probe in result["probes"]:
            lines.append(
                f"- {probe['method']} {probe['path']}: status `{probe['status_code']}`, "
                f"facade `{probe['compatibility_facade']}`, fixture_marker `{str(probe['fixture_marker_present']).lower()}`"
            )
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {blocker}" for blocker in result["blockers"])
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check sidebar/profile Next owner readiness.")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()
    result = run_check()
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps({"ok": result["ok"], "blockers": result["blockers"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
