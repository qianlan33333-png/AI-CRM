from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform.shared.db_session import get_session_factory

from .dto import DataHealthCheckResult, HealthStatus


@dataclass(frozen=True)
class DataHealthSnapshotRecord:
    generation_id: str
    source_release_sha: str
    overall_status: HealthStatus
    checks: tuple[DataHealthCheckResult, ...]
    duration_ms: int
    captured_at: datetime


class DataHealthSnapshotRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def replace_latest(self, snapshot: DataHealthSnapshotRecord) -> None:
        payload = json.dumps(
            [check.model_dump(mode="json") for check in snapshot.checks],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._session_factory() as session:
            try:
                session.execute(
                    text(
                        """
                        INSERT INTO data_health_snapshot (
                            singleton,
                            generation_id,
                            source_release_sha,
                            overall_status,
                            check_count,
                            checks_json,
                            duration_ms,
                            captured_at
                        ) VALUES (
                            TRUE,
                            CAST(:generation_id AS UUID),
                            :source_release_sha,
                            :overall_status,
                            :check_count,
                            CAST(:checks_json AS JSONB),
                            :duration_ms,
                            :captured_at
                        )
                        ON CONFLICT (singleton) DO UPDATE SET
                            generation_id = EXCLUDED.generation_id,
                            source_release_sha = EXCLUDED.source_release_sha,
                            overall_status = EXCLUDED.overall_status,
                            check_count = EXCLUDED.check_count,
                            checks_json = EXCLUDED.checks_json,
                            duration_ms = EXCLUDED.duration_ms,
                            captured_at = EXCLUDED.captured_at
                        """
                    ),
                    {
                        "generation_id": snapshot.generation_id,
                        "source_release_sha": snapshot.source_release_sha,
                        "overall_status": snapshot.overall_status,
                        "check_count": len(snapshot.checks),
                        "checks_json": payload,
                        "duration_ms": snapshot.duration_ms,
                        "captured_at": snapshot.captured_at,
                    },
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def load_latest(self) -> DataHealthSnapshotRecord | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT
                            generation_id::text AS generation_id,
                            source_release_sha,
                            overall_status,
                            check_count,
                            checks_json,
                            duration_ms,
                            captured_at
                        FROM data_health_snapshot
                        WHERE singleton IS TRUE
                        LIMIT 1
                        """
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        checks_json = _json_array(row["checks_json"])
        checks = tuple(DataHealthCheckResult.model_validate(item) for item in checks_json)
        if len(checks) != int(row["check_count"]):
            raise ValueError("data_health_snapshot_check_count_mismatch")
        overall_status = str(row["overall_status"])
        if overall_status not in {"ok", "warn", "fail", "not_applicable"}:
            raise ValueError("data_health_snapshot_status_invalid")
        return DataHealthSnapshotRecord(
            generation_id=str(row["generation_id"]),
            source_release_sha=str(row["source_release_sha"]),
            overall_status=cast(HealthStatus, overall_status),
            checks=checks,
            duration_ms=int(row["duration_ms"]),
            captured_at=row["captured_at"],
        )


def _json_array(value: Any) -> list[dict[str, Any]]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("data_health_snapshot_checks_invalid")
    return parsed
