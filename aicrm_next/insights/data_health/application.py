from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from aicrm_next.platform.shared.runtime import production_repository_required
from aicrm_next.platform.shared.safe_logging import safe_log_exception
from aicrm_next.platform.release_governance.manifest import data_health_registry_digest, load_release_gate_manifest

from .checks import run_all_checks
from .dto import DataHealthCheckResult, DataHealthSummary
from .snapshot_repository import DataHealthSnapshotRepository


LOGGER = logging.getLogger(__name__)
# 15-minute cadence + 2-minute random delay + 4-minute service boundary = 21
# minutes. Keep four minutes of deploy/clock margin without masking two missed runs.
SNAPSHOT_MAX_AGE_SECONDS = 25 * 60


def _snapshot_error(error_code: str) -> dict:
    return {
        "ok": False,
        "status_code": 503,
        "error_code": error_code,
    }


def _current_checks(
    *,
    snapshot_repository: DataHealthSnapshotRepository | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[DataHealthCheckResult, ...] | dict:
    if not production_repository_required():
        return tuple(run_all_checks())
    try:
        snapshot = (snapshot_repository or DataHealthSnapshotRepository()).load_latest()
    except Exception as exc:
        safe_log_exception(LOGGER, "failed to load aggregate data health snapshot", exc)
        return _snapshot_error("data_health_snapshot_unavailable")
    if snapshot is None:
        return _snapshot_error("data_health_snapshot_unavailable")
    captured_at = snapshot.captured_at
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        return _snapshot_error("data_health_snapshot_invalid")
    checked_at = (now or (lambda: datetime.now(timezone.utc)))()
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("data_health_snapshot_now_must_be_timezone_aware")
    age_seconds = (checked_at - captured_at).total_seconds()
    if age_seconds < -60 or age_seconds > SNAPSHOT_MAX_AGE_SECONDS:
        return _snapshot_error("data_health_snapshot_stale")
    return snapshot.checks


def data_health_summary(
    *,
    snapshot_repository: DataHealthSnapshotRepository | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict:
    resolved = _current_checks(snapshot_repository=snapshot_repository, now=now)
    if isinstance(resolved, dict):
        return resolved
    checks = resolved
    counts = {"ok": 0, "warn": 0, "fail": 0, "not_applicable": 0}
    gate_counts = {"pass": 0, "warn": 0, "block": 0}
    for check in checks:
        counts[check.status] += 1
        gate_counts[str(check.gate_decision)] += 1
    manifest = load_release_gate_manifest()
    actual_ids = tuple(sorted(check.check_id for check in checks))
    registry_matches = actual_ids == manifest.data_health_check_ids
    overall_status = "fail" if gate_counts["block"] or not registry_matches else "warn" if gate_counts["warn"] else "ok"
    return DataHealthSummary(
        ok=gate_counts["block"] == 0 and registry_matches,
        overall_status=overall_status,
        counts=counts,
        checks=list(checks),
        gate_counts=gate_counts,
        registry_sha256=data_health_registry_digest(actual_ids),
        registry_matches_manifest=registry_matches,
    ).model_dump()


def data_health_checks(
    *,
    snapshot_repository: DataHealthSnapshotRepository | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict:
    resolved = _current_checks(snapshot_repository=snapshot_repository, now=now)
    if isinstance(resolved, dict):
        return resolved
    checks = resolved
    manifest = load_release_gate_manifest()
    actual_ids = tuple(sorted(check.check_id for check in checks))
    return {
        "ok": all(check.gate_decision != "block" for check in checks) and actual_ids == manifest.data_health_check_ids,
        "checks": [check.model_dump() for check in checks],
        "registry_sha256": data_health_registry_digest(actual_ids),
        "registry_matches_manifest": actual_ids == manifest.data_health_check_ids,
    }


def data_health_check_detail(
    check_id: str,
    *,
    snapshot_repository: DataHealthSnapshotRepository | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict:
    resolved = _current_checks(snapshot_repository=snapshot_repository, now=now)
    if isinstance(resolved, dict):
        return resolved
    result = next((check for check in resolved if check.check_id == check_id), None)
    if result is None:
        return {
            "ok": False,
            "status_code": 404,
            "error_code": "data_health_check_not_found",
            "check_id": check_id,
        }
    return {"ok": result.gate_decision != "block", "check": result.model_dump()}
