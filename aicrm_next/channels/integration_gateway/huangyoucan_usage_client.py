from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting


HUANGYOUCAN_USAGE_SQL = """
WITH token_usage AS (
    SELECT
        user_id,
        MAX(CASE WHEN COALESCE(total_tokens, 0) > 0 THEN 1 ELSE 0 END) AS has_token_usage
    FROM new_version_messages
    WHERE COALESCE(is_deleted, 0) = 0
    GROUP BY user_id
),
lesson_totals AS (
    SELECT path_id, COUNT(*) AS total_lessons
    FROM new_version_lesson_path_items
    GROUP BY path_id
),
ranked_plans AS (
    SELECT
        progress.user_id,
        progress.path_id,
        LEAST(
            GREATEST(COALESCE(progress.current_seq, 0), 0),
            COALESCE(totals.total_lessons, 0)
        ) AS current_lessons,
        COALESCE(totals.total_lessons, 0) AS total_lessons,
        ROW_NUMBER() OVER (
            PARTITION BY progress.user_id
            ORDER BY
                CASE WHEN progress.status = 'active' THEN 0 ELSE 1 END,
                progress.updated_at DESC,
                progress.id DESC
        ) AS plan_rank
    FROM new_version_user_path_progress progress
    LEFT JOIN lesson_totals totals ON totals.path_id = progress.path_id
    WHERE progress.status IN ('active', 'done', 'paused')
),
open_usage AS (
    SELECT
        user_id,
        SUM(CASE WHEN opened_at >= %(window_start)s THEN 1 ELSE 0 END) AS open_count_7d,
        MAX(opened_at) AS last_open_at
    FROM new_version_card_open_log
    GROUP BY user_id
)
SELECT
    CAST(users.id AS CHAR) AS huangyoucan_user_id,
    COALESCE(users.unionid, '') AS unionid,
    CASE
        WHEN REGEXP_REPLACE(COALESCE(users.phone, ''), '[^0-9]', '') = '' THEN ''
        ELSE LOWER(MD5(REGEXP_REPLACE(users.phone, '[^0-9]', '')))
    END AS mobile_md5,
    CASE WHEN users.first_login_at IS NOT NULL THEN 1 ELSE 0 END AS formally_logged_in,
    COALESCE(token_usage.has_token_usage, 0) AS has_token_usage,
    COALESCE(CAST(ranked_plans.path_id AS CHAR), '') AS learning_plan_id,
    ranked_plans.current_lessons AS learning_plan_current,
    ranked_plans.total_lessons AS learning_plan_total,
    COALESCE(open_usage.open_count_7d, 0) AS open_count_7d,
    open_usage.last_open_at AS last_open_at
FROM new_version_users users
LEFT JOIN token_usage ON token_usage.user_id = users.id
LEFT JOIN ranked_plans
    ON ranked_plans.user_id COLLATE utf8mb4_general_ci = users.id
    AND ranked_plans.plan_rank = 1
LEFT JOIN open_usage
    ON open_usage.user_id COLLATE utf8mb4_general_ci = users.id
WHERE COALESCE(users.is_deleted, 0) = 0
ORDER BY users.id
""".strip()


class HuangYouCanUsageSource(Protocol):
    def fetch_usage_snapshot(self, *, refreshed_at: datetime) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HuangYouCanReadonlyDatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "HuangYouCanReadonlyDatabaseConfig":
        values = {
            "host": managed_runtime_setting("AICRM_HUANGYOUCAN_DB_HOST").strip(),
            "database": managed_runtime_setting("AICRM_HUANGYOUCAN_DB_NAME").strip(),
            "user": managed_runtime_setting("AICRM_HUANGYOUCAN_DB_USER").strip(),
            "password": managed_runtime_setting("AICRM_HUANGYOUCAN_DB_PASSWORD"),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"missing HuangYouCan readonly database configuration: {', '.join(sorted(missing))}")
        return cls(
            host=values["host"],
            port=int(managed_runtime_setting("AICRM_HUANGYOUCAN_DB_PORT", "3306")),
            database=values["database"],
            user=values["user"],
            password=values["password"],
            connect_timeout_seconds=int(
                managed_runtime_setting(
                    "AICRM_HUANGYOUCAN_DB_CONNECT_TIMEOUT_SECONDS",
                    "10",
                )
            ),
            read_timeout_seconds=int(
                managed_runtime_setting(
                    "AICRM_HUANGYOUCAN_DB_READ_TIMEOUT_SECONDS",
                    "60",
                )
            ),
        )


class PyMySQLHuangYouCanUsageSource:
    def __init__(
        self,
        config: HuangYouCanReadonlyDatabaseConfig,
        *,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._connect = connect

    def fetch_usage_snapshot(self, *, refreshed_at: datetime) -> list[dict[str, Any]]:
        connect = self._connect
        if connect is None:
            import pymysql

            connect = pymysql.connect
            cursor_class = pymysql.cursors.DictCursor
        else:
            cursor_class = None
        kwargs: dict[str, Any] = {
            "host": self._config.host,
            "port": self._config.port,
            "user": self._config.user,
            "password": self._config.password,
            "database": self._config.database,
            "charset": "utf8mb4",
            "autocommit": False,
            "connect_timeout": self._config.connect_timeout_seconds,
            "read_timeout": self._config.read_timeout_seconds,
        }
        if cursor_class is not None:
            kwargs["cursorclass"] = cursor_class
        connection = connect(**kwargs)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    HUANGYOUCAN_USAGE_SQL,
                    {"window_start": _mysql_beijing_datetime(refreshed_at) - timedelta(days=7)},
                )
                rows = cursor.fetchall()
            connection.rollback()
        finally:
            connection.close()
        return [dict(row) for row in rows]


def build_huangyoucan_usage_source() -> HuangYouCanUsageSource:
    return PyMySQLHuangYouCanUsageSource(HuangYouCanReadonlyDatabaseConfig.from_env())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _mysql_beijing_datetime(value: datetime) -> datetime:
    return _as_utc(value).astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
