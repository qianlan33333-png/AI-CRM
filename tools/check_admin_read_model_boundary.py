#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.admin_read_model.application import GetAdminProductsPageQuery
from aicrm_next.admin_read_model.dto import AdminReadDiagnostics
from aicrm_next.admin_read_model.errors import AdminReadModelError


FRONTEND_ADMIN_REAL_DATA = ROOT / "aicrm_next" / "app" / "admin_console" / "admin_real_data.py"
ADMIN_REPO = ROOT / "aicrm_next" / "admin_read_model" / "repo.py"

DIRECT_SQL_PATTERN = re.compile(r"SELECT\s+|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|psycopg|dict_row|\.connect\(", re.I)


class _FailingProductionRepo:
    source_status = "production_postgres"

    @property
    def is_production(self) -> bool:
        return True

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def count(self, table: str) -> int:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def runtime_health(self) -> dict[str, Any]:
        return {"production_data_ready": True, "database_mode": "postgres"}

    def diagnostics(self) -> AdminReadDiagnostics:
        return AdminReadDiagnostics(source_status=self.source_status, details={"database_mode": "postgres"})


class _SuccessfulProductionRepo:
    source_status = "production_postgres"

    @property
    def is_production(self) -> bool:
        return True

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [
            {
                "product_code": "prod_product",
                "name": "Production Product",
                "amount_total": 100,
                "currency": "CNY",
                "status": "available",
                "enabled": True,
                "slice_count": 1,
                "updated_at": "2026-05-22T00:00:00Z",
            }
        ]

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = self.rows(query, params)
        return rows[0] if rows else {}

    def count(self, table: str) -> int:
        return 1

    def runtime_health(self) -> dict[str, Any]:
        return {"production_data_ready": True, "database_mode": "postgres"}

    def diagnostics(self) -> AdminReadDiagnostics:
        return AdminReadDiagnostics(source_status=self.source_status, details={"database_mode": "postgres"})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _production_failure_contract() -> list[str]:
    blockers: list[str] = []
    payload = GetAdminProductsPageQuery(_FailingProductionRepo())()
    if payload.get("ok") is not False:
        blockers.append("production_sql_failure_not_marked_ok_false")
    if payload.get("degraded") is not True:
        blockers.append("production_sql_failure_not_marked_degraded")
    if payload.get("source_status") != "production_unavailable":
        blockers.append("production_sql_failure_source_status_not_unavailable")
    if not payload.get("error_code"):
        blockers.append("production_sql_failure_missing_error_code")
    if not (payload.get("page_error") or payload.get("diagnostics")):
        blockers.append("production_sql_failure_missing_page_error_or_diagnostics")
    if "local_contract" in json.dumps(payload, ensure_ascii=False):
        blockers.append("production_sql_failure_leaked_local_contract_marker")
    return blockers


def _production_success_contract() -> list[str]:
    blockers: list[str] = []
    payload = GetAdminProductsPageQuery(_SuccessfulProductionRepo())()
    payload_text = json.dumps(payload, ensure_ascii=False)
    if payload.get("source_status") != "production_postgres":
        blockers.append("production_success_source_status_not_postgres")
    if payload.get("ok") is not True or payload.get("degraded") is not False:
        blockers.append("production_success_not_ok")
    if "local_contract" in payload_text or "local_contract_probe" in payload_text:
        blockers.append("production_success_contains_local_contract_marker")
    return blockers


def run_check() -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    admin_real_data = _read(FRONTEND_ADMIN_REAL_DATA)
    admin_repo = _read(ADMIN_REPO)

    if "psycopg" in admin_real_data:
        blockers.append("frontend_compat_admin_real_data_imports_psycopg")
    if DIRECT_SQL_PATTERN.search(admin_real_data):
        blockers.append("frontend_compat_admin_real_data_contains_sql_or_db_driver")
    if "psycopg" not in admin_repo:
        blockers.append("admin_read_model_repo_missing_psycopg_boundary")

    blockers.extend(_production_failure_contract())
    blockers.extend(_production_success_contract())

    result = {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "checks": {
            "frontend_compat_admin_real_data_no_psycopg": "frontend_compat_admin_real_data_imports_psycopg" not in blockers,
            "admin_read_model_repo_is_db_boundary": "admin_read_model_repo_missing_psycopg_boundary" not in blockers,
            "production_sql_failure_degrades": not any(blocker.startswith("production_sql_failure") for blocker in blockers),
            "production_success_no_local_contract": "production_success_contains_local_contract_marker" not in blockers,
        },
    }
    return result


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Admin Read Model Boundary",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- warnings: `{len(result['warnings'])}`",
            "",
            "## Blockers",
            *(f"- {item}" for item in result["blockers"]),
            "",
            "## Warnings",
            *(f"- {item}" for item in result["warnings"]),
        ]
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Admin Read Model boundary rules.")
    parser.add_argument("--output-md")
    parser.add_argument("--output-json")
    args = parser.parse_args()

    result = run_check()
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
