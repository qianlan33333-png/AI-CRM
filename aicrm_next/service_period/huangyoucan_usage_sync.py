from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from aicrm_next.integration_ports import (
    HuangYouCanUsageSource,
    build_huangyoucan_usage_source,
)
from aicrm_next.shared.db_session import connect_pooled_postgres
from aicrm_next.shared.runtime import raw_database_url
from aicrm_next.shared.safe_logging import safe_log_exception
from aicrm_next.shared.sensitive_data import redact_sensitive_text


LOGGER = logging.getLogger(__name__)
MAX_ERROR_SUMMARY_LENGTH = 1000


class HuangYouCanUsageProjectionRepository(Protocol):
    def replace_all(
        self,
        rows: list[dict[str, Any]],
        *,
        refreshed_at: datetime,
        started_at: datetime,
        trigger_source: str,
    ) -> dict[str, Any]: ...

    def record_failure(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        trigger_source: str,
        source_row_count: int,
        error_summary: str,
    ) -> None: ...


class PostgresHuangYouCanUsageProjectionRepository:
    def __init__(self, database_url: str, *, connect: Callable[..., Any] | None = None) -> None:
        self._database_url = database_url
        self._connect = connect or connect_pooled_postgres

    def replace_all(
        self,
        rows: list[dict[str, Any]],
        *,
        refreshed_at: datetime,
        started_at: datetime,
        trigger_source: str,
    ) -> dict[str, Any]:
        normalized_rows = [_normalize_source_row(row, refreshed_at=refreshed_at) for row in rows]
        with self._connect(self._database_url) as connection:
            connection.execute("DELETE FROM service_period_huangyoucan_usage_snapshot")
            if normalized_rows:
                cursor = connection.cursor()
                cursor.executemany(
                    """
                    INSERT INTO service_period_huangyoucan_usage_snapshot (
                        huangyoucan_user_id,
                        unionid,
                        mobile_md5,
                        formally_logged_in,
                        has_token_usage,
                        learning_plan_id,
                        learning_plan_current,
                        learning_plan_total,
                        open_count_7d,
                        last_open_at,
                        refreshed_at
                    ) VALUES (
                        %(huangyoucan_user_id)s,
                        %(unionid)s,
                        %(mobile_md5)s,
                        %(formally_logged_in)s,
                        %(has_token_usage)s,
                        %(learning_plan_id)s,
                        %(learning_plan_current)s,
                        %(learning_plan_total)s,
                        %(open_count_7d)s,
                        %(last_open_at)s,
                        %(refreshed_at)s
                    )
                    """,
                    normalized_rows,
                )
            connection.execute(
                """
                INSERT INTO service_period_huangyoucan_usage_sync_runs (
                    trigger_source,
                    status,
                    source_row_count,
                    snapshot_row_count,
                    started_at,
                    finished_at,
                    error_summary
                ) VALUES (%s, 'succeeded', %s, %s, %s, %s, '')
                """,
                (
                    str(trigger_source or "manual"),
                    len(rows),
                    len(normalized_rows),
                    started_at,
                    refreshed_at,
                ),
            )
        return {
            "ok": True,
            "source_row_count": len(rows),
            "snapshot_row_count": len(normalized_rows),
            "refreshed_at": refreshed_at.isoformat(),
        }

    def record_failure(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        trigger_source: str,
        source_row_count: int,
        error_summary: str,
    ) -> None:
        with self._connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO service_period_huangyoucan_usage_sync_runs (
                    trigger_source,
                    status,
                    source_row_count,
                    snapshot_row_count,
                    started_at,
                    finished_at,
                    error_summary
                ) VALUES (%s, 'failed', %s, 0, %s, %s, %s)
                """,
                (
                    str(trigger_source or "manual"),
                    max(0, int(source_row_count)),
                    started_at,
                    finished_at,
                    sanitize_huangyoucan_usage_error(error_summary),
                ),
            )


def sync_huangyoucan_usage(
    *,
    source: HuangYouCanUsageSource | None = None,
    repository: HuangYouCanUsageProjectionRepository | None = None,
    dry_run: bool = False,
    trigger_source: str = "manual",
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = now or (lambda: datetime.now(timezone.utc))
    started_at = _as_utc(clock())
    source = source or build_huangyoucan_usage_source()
    database_url = raw_database_url()
    if repository is None and not dry_run:
        if not database_url:
            raise RuntimeError("DATABASE_URL is required for HuangYouCan usage sync")
        repository = PostgresHuangYouCanUsageProjectionRepository(database_url)
    source_rows: list[dict[str, Any]] = []
    try:
        source_rows = source.fetch_usage_snapshot(refreshed_at=started_at)
        normalized_rows = [_normalize_source_row(row, refreshed_at=started_at) for row in source_rows]
        _assert_unique_source_ids(normalized_rows)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "source_row_count": len(source_rows),
                "snapshot_row_count": len(normalized_rows),
                "refreshed_at": started_at.isoformat(),
                "trigger_source": trigger_source,
            }
        assert repository is not None
        result = repository.replace_all(
            normalized_rows,
            refreshed_at=started_at,
            started_at=started_at,
            trigger_source=trigger_source,
        )
        return {**result, "dry_run": False, "trigger_source": trigger_source}
    except Exception as exc:
        finished_at = _as_utc(clock())
        if repository is not None and not dry_run:
            try:
                repository.record_failure(
                    started_at=started_at,
                    finished_at=finished_at,
                    trigger_source=trigger_source,
                    source_row_count=len(source_rows),
                    error_summary=sanitize_huangyoucan_usage_error(exc),
                )
            except Exception as record_exc:
                safe_log_exception(LOGGER, "failed to record HuangYouCan usage sync failure", record_exc)
        raise


def _normalize_source_row(row: dict[str, Any], *, refreshed_at: datetime) -> dict[str, Any]:
    current = _optional_nonnegative_int(row.get("learning_plan_current"))
    total = _optional_nonnegative_int(row.get("learning_plan_total"))
    plan_id = str(row.get("learning_plan_id") or "").strip()
    if not plan_id or current is None or total is None:
        plan_id = ""
        current = None
        total = None
    elif current > total:
        current = total
    return {
        "huangyoucan_user_id": str(row.get("huangyoucan_user_id") or "").strip(),
        "unionid": str(row.get("unionid") or "").strip(),
        "mobile_md5": str(row.get("mobile_md5") or "").strip().lower(),
        "formally_logged_in": bool(row.get("formally_logged_in")),
        "has_token_usage": bool(row.get("has_token_usage")),
        "learning_plan_id": plan_id,
        "learning_plan_current": current,
        "learning_plan_total": total,
        "open_count_7d": max(0, int(row.get("open_count_7d") or 0)),
        "last_open_at": _source_beijing_datetime(row.get("last_open_at")),
        "refreshed_at": _as_utc(refreshed_at),
    }


def _assert_unique_source_ids(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("HuangYouCan usage source returned an empty snapshot")
    ids = [str(row.get("huangyoucan_user_id") or "") for row in rows]
    if any(not value for value in ids):
        raise RuntimeError("HuangYouCan usage source returned an empty user id")
    if len(ids) != len(set(ids)):
        raise RuntimeError("HuangYouCan usage source returned duplicate user ids")


def _optional_nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return max(0, int(value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_beijing_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def sanitize_huangyoucan_usage_error(value: Any) -> str:
    return redact_sensitive_text(str(value or "sync_failed")).replace("\n", " ")[:MAX_ERROR_SUMMARY_LENGTH]
