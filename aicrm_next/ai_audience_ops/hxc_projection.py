from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from aicrm_next.shared.db_session import get_session_factory

from .hxc_projection_sql import (
    HXC_OPTIONAL_SOURCE_REQUIRED_COLUMNS,
    build_hxc_full_projection_insert_sql,
)


HXC_PROJECTION_ADVISORY_LOCK_KEY = "ai_audience.hxc_member_usage_projection.v1"
HXC_PROJECTION_QUERY_TIMEOUT_SECONDS = 30
HXC_PROJECTION_LOCK_TIMEOUT_SECONDS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _public_datetime(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, DBAPIError):
        sqlstate = str(
            getattr(exc.orig, "sqlstate", "")
            or getattr(exc.orig, "pgcode", "")
            or ""
        )
        if sqlstate == "57014":
            return "projection_query_timeout"
        if sqlstate == "55P03":
            return "projection_lock_timeout"
    return "projection_refresh_failed"


class HxcMemberUsageProjectionService:
    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._session_factory = session_factory or get_session_factory()
        self._clock = clock or _utcnow

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT active_generation,
                               status,
                               projected_row_count,
                               last_incremental_watermark_at,
                               last_full_refreshed_at,
                               last_refresh_started_at,
                               last_refresh_finished_at,
                               source_watermarks_json,
                               last_error_code,
                               updated_at
                        FROM ai_audience_hxc_member_usage_projection_control
                        WHERE singleton = TRUE
                        """
                    )
                )
                .mappings()
                .one()
            )
        return self._public_status(dict(row))

    def refresh_full(self) -> dict[str, Any]:
        started_at = self._clock()
        try:
            with self._session_factory() as session:
                self._set_local_safety_limits(session)
                locked = bool(
                    session.execute(
                        text(
                            """
                            SELECT pg_try_advisory_xact_lock(
                                hashtextextended(:lock_key, 0)
                            )
                            """
                        ),
                        {"lock_key": HXC_PROJECTION_ADVISORY_LOCK_KEY},
                    ).scalar_one()
                )
                if not locked:
                    session.rollback()
                    return {
                        "ok": False,
                        "error_code": "projection_refresh_busy",
                        "real_external_call_executed": False,
                    }

                control = (
                    session.execute(
                        text(
                            """
                            SELECT active_generation
                            FROM ai_audience_hxc_member_usage_projection_control
                            WHERE singleton = TRUE
                            FOR UPDATE
                            """
                        )
                    )
                    .mappings()
                    .one()
                )
                generation = int(control["active_generation"] or 0) + 1
                session.execute(
                    text(
                        """
                        UPDATE ai_audience_hxc_member_usage_projection_control
                        SET status = 'building',
                            last_refresh_started_at = :started_at,
                            last_error_code = '',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE singleton = TRUE
                        """
                    ),
                    {"started_at": started_at},
                )
                optional_sources = self._available_optional_sources(session)
                session.execute(
                    text(build_hxc_full_projection_insert_sql(optional_sources)),
                    {"generation": generation},
                )
                projected_row_count = int(
                    session.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM ai_audience_hxc_member_usage_projection
                            WHERE generation = :generation
                            """
                        ),
                        {"generation": generation},
                    ).scalar_one()
                    or 0
                )
                finished_at = self._clock()
                source_watermarks = json.dumps(
                    {"full_calibration_at": _public_datetime(finished_at)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                status_row = (
                    session.execute(
                        text(
                            """
                            UPDATE ai_audience_hxc_member_usage_projection_control
                            SET active_generation = :generation,
                                status = 'ready',
                                projected_row_count = :projected_row_count,
                                last_incremental_watermark_at = :finished_at,
                                last_full_refreshed_at = :finished_at,
                                last_refresh_finished_at = :finished_at,
                                source_watermarks_json = CAST(
                                    :source_watermarks_json AS jsonb
                                ),
                                last_error_code = '',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE singleton = TRUE
                            RETURNING active_generation,
                                      status,
                                      projected_row_count,
                                      last_incremental_watermark_at,
                                      last_full_refreshed_at,
                                      last_refresh_started_at,
                                      last_refresh_finished_at,
                                      source_watermarks_json,
                                      last_error_code,
                                      updated_at
                            """
                        ),
                        {
                            "generation": generation,
                            "projected_row_count": projected_row_count,
                            "finished_at": finished_at,
                            "source_watermarks_json": source_watermarks,
                        },
                    )
                    .mappings()
                    .one()
                )
                session.commit()
            return {
                "ok": True,
                "refresh_kind": "full",
                "generation": generation,
                "projected_row_count": projected_row_count,
                "status": self._public_status(dict(status_row)),
                "real_external_call_executed": False,
            }
        except Exception as exc:
            error_code = _failure_code(exc)
            self._record_failure(started_at=started_at, error_code=error_code)
            return {
                "ok": False,
                "error_code": error_code,
                "real_external_call_executed": False,
            }

    @staticmethod
    def _set_local_safety_limits(session: Session) -> None:
        session.execute(
            text("SELECT set_config('statement_timeout', :value, TRUE)"),
            {"value": f"{HXC_PROJECTION_QUERY_TIMEOUT_SECONDS}s"},
        )
        session.execute(
            text("SELECT set_config('lock_timeout', :value, TRUE)"),
            {"value": f"{HXC_PROJECTION_LOCK_TIMEOUT_SECONDS}s"},
        )
        session.execute(text("SELECT set_config('jit', 'off', TRUE)"))

    @staticmethod
    def _available_optional_sources(session: Session) -> set[str]:
        rows = session.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'new_version_user_subscriptions',
                      'automation_laohuang_chat_job',
                      'user_ops_huangxiaocan_activation_source',
                      'user_ops_activation_status_source'
                  )
                """
            )
        ).all()
        columns_by_table: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            columns_by_table.setdefault(str(table_name), set()).add(str(column_name))
        return {
            table_name
            for table_name, required_columns in HXC_OPTIONAL_SOURCE_REQUIRED_COLUMNS.items()
            if required_columns <= columns_by_table.get(table_name, set())
        }

    def _record_failure(self, *, started_at: datetime, error_code: str) -> None:
        try:
            with self._session_factory() as session:
                session.execute(
                    text(
                        """
                        UPDATE ai_audience_hxc_member_usage_projection_control
                        SET status = CASE
                                WHEN active_generation > 0 THEN 'ready'
                                ELSE 'failed'
                            END,
                            last_refresh_started_at = :started_at,
                            last_refresh_finished_at = CURRENT_TIMESTAMP,
                            last_error_code = :error_code,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE singleton = TRUE
                          AND COALESCE(
                              last_refresh_finished_at,
                              '-infinity'::timestamptz
                          ) <= :started_at
                        """
                    ),
                    {"started_at": started_at, "error_code": error_code},
                )
                session.commit()
        except Exception:
            # Failure telemetry must never mask or replace the original safe
            # result. No exception text or bound identity is persisted.
            return

    @staticmethod
    def _public_status(row: dict[str, Any]) -> dict[str, Any]:
        source_watermarks = row.get("source_watermarks_json")
        if not isinstance(source_watermarks, dict):
            source_watermarks = {}
        return {
            "active_generation": int(row.get("active_generation") or 0),
            "status": str(row.get("status") or "empty"),
            "projected_row_count": int(row.get("projected_row_count") or 0),
            "last_incremental_watermark_at": _public_datetime(
                row.get("last_incremental_watermark_at")
            ),
            "last_full_refreshed_at": _public_datetime(
                row.get("last_full_refreshed_at")
            ),
            "last_refresh_started_at": _public_datetime(
                row.get("last_refresh_started_at")
            ),
            "last_refresh_finished_at": _public_datetime(
                row.get("last_refresh_finished_at")
            ),
            "source_watermarks": dict(source_watermarks),
            "last_error_code": str(row.get("last_error_code") or ""),
            "updated_at": _public_datetime(row.get("updated_at")),
        }
