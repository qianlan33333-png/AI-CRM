from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


DAILY_QUERY_TIMEOUT_RECOVERY_SOURCE_TYPE = "daily_query_timeout_runtime_v1"
DAILY_QUERY_TIMEOUT_RECOVERY_EVENT_KEY = "daily-query-timeout-runtime-v1"
STATEMENT_TIMEOUT_ERROR_FRAGMENT = "canceling statement due to statement timeout"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _refresh_kind(value: Any) -> str:
    normalized = _text(value).lower()
    if normalized in {"daily", "snapshot", "snapshot_current"}:
        return "daily"
    if normalized == "manual":
        return "manual"
    return "incremental"


def _daily_sql_available(package: Any) -> bool:
    return bool(_text(package.get("snapshot_sql_text"))) or (
        _text(package.get("query_mode")) == "simple_sql"
        and not bool(package.get("incremental_enabled"))
        and bool(_text(package.get("simple_compiled_sql_text")))
    )


def blocked_incompatible_config_recoverable(
    *,
    current: Any,
    package: Any,
    requested_kind: str,
) -> bool:
    if _text(current.get("status")) != "blocked" or _refresh_kind(requested_kind) != "daily":
        return False
    if not bool(package.get("daily_enabled")):
        return False
    blocked_by_wrong_incremental_kind = (
        _text(current.get("target_refresh_kind")) == "incremental"
        and not bool(package.get("incremental_enabled"))
        and "incremental_sql_not_configured" in _text(current.get("last_error_message"))
    )
    blocked_by_legacy_daily_simple_shape = (
        _text(current.get("target_refresh_kind")) == "daily"
        and _text(package.get("query_mode")) == "simple_sql"
        and not bool(_text(package.get("snapshot_sql_text")))
        and bool(_text(package.get("simple_compiled_sql_text")))
        and "daily_sql_not_configured" in _text(current.get("last_error_message"))
    )
    return _daily_sql_available(package) and (
        blocked_by_wrong_incremental_kind or blocked_by_legacy_daily_simple_shape
    )


def blocked_daily_query_timeout_recoverable(
    *,
    current: Any,
    package: Any,
    requested_kind: str,
) -> bool:
    return bool(
        _text(current.get("status")) == "blocked"
        and _refresh_kind(requested_kind) == "daily"
        and _text(current.get("target_refresh_kind")) == "daily"
        and _text(current.get("last_error_code")) == "refresh_failed"
        and STATEMENT_TIMEOUT_ERROR_FRAGMENT in _text(current.get("last_error_message"))
        and bool(package.get("daily_enabled"))
        and _daily_sql_available(package)
    )


def claim_daily_query_timeout_recovery_marker(
    session: Session,
    *,
    current: Any,
    package: Any,
    requested_kind: str,
    execution_id: str,
) -> bool:
    if not blocked_daily_query_timeout_recoverable(
        current=current,
        package=package,
        requested_kind=requested_kind,
    ):
        return False
    marker = session.execute(
        text(
            """
            INSERT INTO ai_audience_refresh_source_receipt (
                package_id, source_event_key, source_type, source_key,
                refresh_kind, generation, execution_id,
                parent_execution_id, created_at
            ) VALUES (
                :package_id, :source_event_key, :source_type, '',
                'daily', :generation, :execution_id,
                :parent_execution_id, CURRENT_TIMESTAMP
            )
            ON CONFLICT (package_id, source_event_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "package_id": int(current.get("package_id") or 0),
            "source_event_key": "sha256:"
            + hashlib.sha256(DAILY_QUERY_TIMEOUT_RECOVERY_EVENT_KEY.encode("utf-8")).hexdigest(),
            "source_type": DAILY_QUERY_TIMEOUT_RECOVERY_SOURCE_TYPE,
            "generation": int(current.get("dirty_generation") or 0),
            "execution_id": _text(execution_id),
            "parent_execution_id": _text(current.get("execution_id")),
        },
    ).mappings().fetchone()
    return bool(marker)
