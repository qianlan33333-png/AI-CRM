from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid4

from aicrm_next.shared.release import current_release_sha

from .checks import run_all_checks
from .dto import DataHealthCheckResult, HealthStatus
from .snapshot_repository import DataHealthSnapshotRecord, DataHealthSnapshotRepository


_FULL_RELEASE_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RAW_IDENTITY_KEYS = {
    "external_userid",
    "external_userids",
    "mobile",
    "openid",
    "phone",
    "raw_payload",
    "raw_payload_json",
    "unionid",
    "user_id",
    "userid",
}


def overall_status_for_checks(checks: Sequence[DataHealthCheckResult]) -> HealthStatus:
    statuses = {check.status for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _assert_aggregate_only(value: object) -> None:
    if isinstance(value, dict):
        forbidden = _RAW_IDENTITY_KEYS.intersection(str(key).lower() for key in value)
        if forbidden:
            raise ValueError("data_health_snapshot_raw_identity_key_forbidden")
        for nested in value.values():
            _assert_aggregate_only(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_aggregate_only(nested)


def capture_data_health_snapshot(
    *,
    repository: DataHealthSnapshotRepository | None = None,
    check_runner: Callable[[], Sequence[DataHealthCheckResult]] = run_all_checks,
    release_sha: str | None = None,
    generation_id: UUID | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> DataHealthSnapshotRecord:
    started = monotonic()
    # Pin provenance before any potentially long-running checks. A production
    # deploy can switch the checkout while an already-started timer is still
    # draining; reading .release-sha after the checks would mislabel that old
    # generation as belonging to the new release.
    normalized_release_sha = str(release_sha if release_sha is not None else current_release_sha()).strip().lower()
    if _FULL_RELEASE_SHA.fullmatch(normalized_release_sha) is None:
        normalized_release_sha = "unknown"
    checks = tuple(DataHealthCheckResult.model_validate(check) for check in check_runner())
    if not checks:
        raise ValueError("data_health_snapshot_checks_empty")
    check_ids = [check.check_id for check in checks]
    if len(check_ids) != len(set(check_ids)):
        raise ValueError("data_health_snapshot_check_ids_not_unique")
    _assert_aggregate_only([check.model_dump(mode="json") for check in checks])

    captured_at = (now or (lambda: datetime.now(timezone.utc)))()
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("data_health_snapshot_captured_at_must_be_timezone_aware")
    duration_ms = max(0, int(round((monotonic() - started) * 1000)))
    snapshot = DataHealthSnapshotRecord(
        generation_id=str(generation_id or uuid4()),
        source_release_sha=normalized_release_sha,
        overall_status=overall_status_for_checks(checks),
        checks=checks,
        duration_ms=duration_ms,
        captured_at=captured_at,
    )
    (repository or DataHealthSnapshotRepository()).replace_latest(snapshot)
    return snapshot
