#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from aicrm_next.shared.repository_provider import (
    RepositoryProviderError,
    allow_fixture_repo_in_prod,
    evaluate_repository,
)


PRODUCTION_ENV = {
    "AICRM_NEXT_ENV": "production",
    "DATABASE_URL": "postgresql://probe:probe@127.0.0.1:1/aicrm_probe",
    "SECRET_KEY": "repository-provider-hardening",
}

CAPABILITIES = {
    "commerce": ("aicrm_next.extensions.commerce.commerce.repo", "build_commerce_repository"),
    "media_library": ("aicrm_next.media_library.repo", "build_media_library_repository"),
    "questionnaire": ("aicrm_next.extensions.forms.questionnaire.repo", "build_questionnaire_repository"),
    "automation_engine": ("aicrm_next.automation_engine.repo", "build_automation_repository"),
    "customer_read_model": ("aicrm_next.customer_read_model.repo", "build_customer_read_model_repository"),
    "ops_enrollment": ("aicrm_next.ops_enrollment.repo", "build_user_ops_repository"),
    "admin_read_model": ("aicrm_next.admin_read_model.repo", "build_admin_read_repository"),
}

FIXTURE_MARKERS = ("local_contract", "fixture", "demo-only", "hxc-activation-v1", "disabled-demo")


@contextmanager
def patched_env(values: dict[str, str | None]):
    old = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _builder(module_name: str, function_name: str) -> Callable[[], object]:
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def _capability_matrix() -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    with patched_env({**PRODUCTION_ENV, "AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD": None}):
        for capability, (module_name, function_name) in CAPABILITIES.items():
            try:
                repository = _builder(module_name, function_name)()
            except RepositoryProviderError as exc:
                matrix[capability] = {
                    "ok": True,
                    "production_data_ready": True,
                    "result": "blocked",
                    "error_code": "fixture_repository_blocked_in_production",
                    "message": str(exc),
                }
                continue
            except Exception as exc:
                matrix[capability] = {
                    "ok": True,
                    "production_data_ready": True,
                    "result": "production_unavailable",
                    "error_code": exc.__class__.__name__,
                    "message": str(exc),
                }
                continue
            decision = evaluate_repository(repository, capability_owner=capability)
            matrix[capability] = {
                "ok": decision.ok and decision.repository_kind != "fixture",
                "production_data_ready": decision.production_data_ready,
                "result": "repository",
                "repository_class": decision.repository_class,
                "repository_kind": decision.repository_kind,
                "allow_fixture_repo_in_prod": decision.allow_fixture_repo_in_prod,
                "error_code": decision.error_code,
                "message": decision.message,
            }
    return matrix


def _fixture_mode_matrix() -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    with patched_env(
        {
            "AICRM_NEXT_ENV": "experiment",
            "DATABASE_URL": None,
            "AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD": None,
        }
    ):
        for capability, (module_name, function_name) in CAPABILITIES.items():
            try:
                repository = _builder(module_name, function_name)()
            except Exception as exc:
                matrix[capability] = {"ok": False, "error_code": exc.__class__.__name__, "message": str(exc)}
                continue
            decision = evaluate_repository(repository, capability_owner=capability)
            matrix[capability] = {
                "ok": decision.ok,
                "repository_class": decision.repository_class,
                "repository_kind": decision.repository_kind,
                "fixture_allowed": decision.fixture_allowed,
            }
    return matrix


def _create_app_does_not_reset_fixtures_in_production() -> list[str]:
    blockers: list[str] = []
    with patched_env(PRODUCTION_ENV):
        module = importlib.import_module("aicrm_next.main")
        calls: list[str] = []
        names = [
            "reset_user_ops_fixture_state",
            "reset_questionnaire_fixture_state",
            "reset_automation_fixture_state",
            "reset_commerce_fixture_state",
            "reset_media_library_fixture_state",
        ]
        old = {name: getattr(module, name) for name in names}
        for name in names:
            setattr(module, name, lambda name=name: calls.append(name))
        try:
            module.create_app()
        finally:
            for name, value in old.items():
                setattr(module, name, value)
        if calls:
            blockers.append(f"production_create_app_reset_fixture_state:{','.join(calls)}")
    return blockers


def _production_success_marker_blockers() -> list[str]:
    blockers: list[str] = []
    with patched_env(PRODUCTION_ENV):
        module = importlib.import_module("aicrm_next.main")
        client = TestClient(module.create_app(), raise_server_exceptions=False)
        probes = {
            "customer_read_model": client.get("/api/customers"),
            "questionnaire": client.get("/api/admin/questionnaires"),
            "ai_audience_ops": client.get("/api/admin/ai-audience/packages"),
            "commerce": client.get("/api/admin/wechat-pay/products"),
            "media_library": client.get("/api/admin/image-library"),
        }
        for capability, response in probes.items():
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            if isinstance(payload, dict) and (not payload.get("ok", True) or payload.get("degraded") is True):
                continue
            text = "\n".join(_string_values(payload)).lower()
            for marker in FIXTURE_MARKERS:
                if marker in text:
                    blockers.append(f"{capability}:production_success_contains_{marker}")
    return blockers


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_string_values(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_string_values(item))
        return result
    return []


def run_check() -> dict[str, Any]:
    matrix = _capability_matrix()
    fixture_matrix = _fixture_mode_matrix()
    blockers: list[str] = []
    warnings: list[str] = []

    blockers.extend(_create_app_does_not_reset_fixtures_in_production())
    blockers.extend(_production_success_marker_blockers())
    if allow_fixture_repo_in_prod():
        blockers.append("allow_fixture_repo_in_prod_enabled")
    for capability, row in matrix.items():
        if not row.get("ok"):
            blockers.append(f"{capability}:production_fixture_repository_not_blocked")
    for capability, row in fixture_matrix.items():
        if not row.get("ok"):
            blockers.append(f"{capability}:fixture_mode_repository_not_allowed")

    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "capabilities": matrix,
        "fixture_mode_capabilities": fixture_matrix,
        "allow_fixture_repo_in_prod": allow_fixture_repo_in_prod(),
    }


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Repository Provider Hardening",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- allow_fixture_repo_in_prod: `{str(result['allow_fixture_repo_in_prod']).lower()}`",
            "",
            "## Capability Matrix",
        ]
        for capability, row in result["capabilities"].items():
            lines.append(f"- {capability}: {row.get('result')} ok={row.get('ok')} class={row.get('repository_class', '')} error={row.get('error_code', '')}")
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in result["blockers"])
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository provider hardening.")
    parser.add_argument("--output-md")
    parser.add_argument("--output-json")
    args = parser.parse_args()
    result = run_check()
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
