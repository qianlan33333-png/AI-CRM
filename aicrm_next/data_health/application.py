from __future__ import annotations

from .checks import run_all_checks, run_check
from .dto import DataHealthCheckResult, DataHealthSummary


def data_health_summary() -> dict:
    checks = run_all_checks()
    counts = {"ok": 0, "warn": 0, "fail": 0, "not_applicable": 0}
    for check in checks:
        counts[check.status] += 1
    overall_status = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return DataHealthSummary(
        ok=counts["fail"] == 0,
        overall_status=overall_status,
        counts=counts,
        checks=checks,
    ).model_dump()


def data_health_checks() -> dict:
    checks = run_all_checks()
    return {
        "ok": all(check.status != "fail" for check in checks),
        "checks": [check.model_dump() for check in checks],
    }


def data_health_check_detail(check_id: str) -> dict:
    result: DataHealthCheckResult | None = run_check(check_id)
    if result is None:
        return {
            "ok": False,
            "status_code": 404,
            "error_code": "data_health_check_not_found",
            "check_id": check_id,
        }
    return {"ok": result.status != "fail", "check": result.model_dump()}
