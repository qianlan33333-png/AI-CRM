from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from aicrm_next.integration_gateway.huangyoucan_usage_client import (
    HUANGYOUCAN_USAGE_SQL,
    HuangYouCanReadonlyDatabaseConfig,
    PyMySQLHuangYouCanUsageSource,
)
from aicrm_next.service_period.huangyoucan_usage import (
    normalized_mobile_md5,
    resolve_huangyoucan_usage_for_identity,
)
from aicrm_next.service_period.huangyoucan_usage_sync import (
    PostgresHuangYouCanUsageProjectionRepository,
    sync_huangyoucan_usage,
)
from aicrm_next.service_period.repo import PostgresServicePeriodRepository
from aicrm_next.shared import runtime_settings


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(user_id: str, *, unionid: str = "", mobile: str = "") -> dict:
    return {
        "huangyoucan_user_id": user_id,
        "unionid": unionid,
        "mobile_md5": normalized_mobile_md5(mobile),
        "formally_logged_in": True,
        "has_token_usage": True,
        "learning_plan_current": 3,
        "learning_plan_total": 8,
        "open_count_7d": 5,
        "last_open_at": "2026-07-13T01:30:00+00:00",
        "refreshed_at": "2026-07-13T01:00:00+00:00",
    }


def test_readonly_database_config_keeps_legacy_environment_parsing(monkeypatch) -> None:
    def unavailable_engine():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime_settings, "get_engine", unavailable_engine)
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_HOST", "readonly.example")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_NAME", "huangyoucan")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_USER", "readonly")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_PASSWORD", "environment-password")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_PORT", "3307")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_CONNECT_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_READ_TIMEOUT_SECONDS", "61")

    config = HuangYouCanReadonlyDatabaseConfig.from_env()

    assert config.port == 3307
    assert config.connect_timeout_seconds == 11
    assert config.read_timeout_seconds == 61
    assert config.password == "environment-password"

    monkeypatch.setenv("AICRM_HUANGYOUCAN_DB_PORT", "invalid")
    with pytest.raises(ValueError):
        HuangYouCanReadonlyDatabaseConfig.from_env()


def test_identity_match_prefers_unionid_then_unique_mobile_and_isolates_conflicts() -> None:
    snapshots = [
        _snapshot("hyc_union", unionid="union_exact", mobile="13800138000"),
        _snapshot("hyc_phone", unionid="union_other", mobile="13900139000"),
    ]

    by_union = resolve_huangyoucan_usage_for_identity(unionid="union_exact", mobile="13800138000", snapshots=snapshots)
    by_phone = resolve_huangyoucan_usage_for_identity(unionid="union_missing", mobile="13900139000", snapshots=snapshots)
    conflict = resolve_huangyoucan_usage_for_identity(unionid="union_exact", mobile="13900139000", snapshots=snapshots)

    assert normalized_mobile_md5("138-0013-8000") == normalized_mobile_md5("13800138000")
    assert by_union["huangyoucan_match_status"] == "matched_unionid"
    assert by_phone["huangyoucan_match_status"] == "matched_mobile"
    assert conflict["huangyoucan_match_status"] == "ambiguous"
    assert conflict["huangyoucan_formally_logged_in"] is None


def test_identity_match_rejects_duplicate_mobile_and_handles_empty_usage() -> None:
    snapshots = [
        _snapshot("hyc_a", mobile="13800138000"),
        _snapshot("hyc_b", mobile="13800138000"),
        {
            **_snapshot("hyc_empty", unionid="union_empty"),
            "formally_logged_in": False,
            "has_token_usage": False,
            "learning_plan_current": None,
            "learning_plan_total": None,
            "open_count_7d": 0,
            "last_open_at": None,
        },
    ]

    duplicate = resolve_huangyoucan_usage_for_identity(unionid="", mobile="13800138000", snapshots=snapshots)
    empty = resolve_huangyoucan_usage_for_identity(unionid="union_empty", mobile="", snapshots=snapshots)
    missing = resolve_huangyoucan_usage_for_identity(unionid="missing", mobile="", snapshots=snapshots)

    assert duplicate["huangyoucan_match_status"] == "ambiguous"
    assert duplicate["huangyoucan_open_count_7d"] is None
    assert empty["huangyoucan_match_status"] == "matched_unionid"
    assert empty["huangyoucan_learning_plan_progress"] is None
    assert empty["huangyoucan_open_count_7d"] == 0
    assert empty["huangyoucan_last_open_at"] is None
    assert missing["huangyoucan_match_status"] == "not_found"


def test_source_sql_uses_one_readonly_aggregate_and_returns_no_plaintext_phone() -> None:
    executed: list[tuple[str, object]] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchall(self):
            return [{"huangyoucan_user_id": "42", "unionid": "u", "mobile_md5": "abc"}]

    class Connection:
        def cursor(self):
            return Cursor()

        def rollback(self):
            executed.append(("ROLLBACK", None))

        def close(self):
            executed.append(("CLOSE", None))

    source = PyMySQLHuangYouCanUsageSource(
        HuangYouCanReadonlyDatabaseConfig(
            host="readonly.example",
            port=3306,
            database="hyc",
            user="readonly",
            password="secret",
        ),
        connect=lambda **_kwargs: Connection(),
    )

    rows = source.fetch_usage_snapshot(refreshed_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc))

    assert rows == [{"huangyoucan_user_id": "42", "unionid": "u", "mobile_md5": "abc"}]
    assert executed[0][0] == "SET TRANSACTION READ ONLY"
    assert executed[1][0] == HUANGYOUCAN_USAGE_SQL
    assert executed[1][1]["window_start"] == datetime(2026, 7, 6, 9)
    for table in (
        "new_version_users",
        "new_version_messages",
        "new_version_user_path_progress",
        "new_version_lesson_path_items",
        "new_version_card_open_log",
    ):
        assert table in HUANGYOUCAN_USAGE_SQL
    assert "ROW_NUMBER() OVER" in HUANGYOUCAN_USAGE_SQL
    assert "ranked_plans.user_id COLLATE utf8mb4_general_ci = users.id" in HUANGYOUCAN_USAGE_SQL
    assert "open_usage.user_id COLLATE utf8mb4_general_ci = users.id" in HUANGYOUCAN_USAGE_SQL
    assert "users.phone AS" not in HUANGYOUCAN_USAGE_SQL
    assert "mobile_md5" in HUANGYOUCAN_USAGE_SQL


def test_sync_dry_run_does_not_replace_projection() -> None:
    class Source:
        def fetch_usage_snapshot(self, *, refreshed_at):
            return [_snapshot("hyc_1", unionid="union_1")]

    class Repository:
        def replace_all(self, *_args, **_kwargs):
            raise AssertionError("dry-run must not write the projection")

        def record_failure(self, **_kwargs):
            raise AssertionError("dry-run success must not write a run")

    payload = sync_huangyoucan_usage(
        source=Source(),
        repository=Repository(),
        dry_run=True,
        trigger_source="test_dry_run",
        now=lambda: datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
    )

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["source_row_count"] == 1


def test_sync_failure_records_error_without_replacing_last_successful_snapshot() -> None:
    class Source:
        def fetch_usage_snapshot(self, *, refreshed_at):
            raise RuntimeError("source unavailable")

    class Repository:
        def __init__(self):
            self.snapshot = [_snapshot("last_good", unionid="last_good")]
            self.failures = []

        def replace_all(self, *_args, **_kwargs):
            self.snapshot = []

        def record_failure(self, **kwargs):
            self.failures.append(kwargs)

    repository = Repository()
    with pytest.raises(RuntimeError, match="source unavailable"):
        sync_huangyoucan_usage(
            source=Source(),
            repository=repository,
            trigger_source="test_failure",
            now=lambda: datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
        )

    assert [item["huangyoucan_user_id"] for item in repository.snapshot] == ["last_good"]
    assert repository.failures[0]["trigger_source"] == "test_failure"
    assert repository.failures[0]["source_row_count"] == 0


def test_empty_source_is_treated_as_failure_instead_of_clearing_projection() -> None:
    class Source:
        def fetch_usage_snapshot(self, *, refreshed_at):
            return []

    class Repository:
        def __init__(self):
            self.replaced = False
            self.failures = []

        def replace_all(self, *_args, **_kwargs):
            self.replaced = True

        def record_failure(self, **kwargs):
            self.failures.append(kwargs)

    repository = Repository()
    with pytest.raises(RuntimeError, match="empty snapshot"):
        sync_huangyoucan_usage(
            source=Source(),
            repository=repository,
            trigger_source="test_empty",
            now=lambda: datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
        )

    assert repository.replaced is False
    assert repository.failures[0]["source_row_count"] == 0


def test_postgres_sync_replaces_atomically_and_failure_preserves_last_success(
    next_pg_schema,
) -> None:
    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ["DATABASE_URL"]
    repository = PostgresHuangYouCanUsageProjectionRepository(database_url)
    sync_time = datetime(2026, 7, 13, 1, tzinfo=timezone.utc)

    class Source:
        def fetch_usage_snapshot(self, *, refreshed_at):
            return [
                {
                    **_snapshot("hyc_pg", unionid="union_pg", mobile="13800138000"),
                    "learning_plan_id": "plan_pg",
                }
            ]

    result = sync_huangyoucan_usage(
        source=Source(),
        repository=repository,
        trigger_source="postgres_success",
        now=lambda: sync_time,
    )
    assert result["snapshot_row_count"] == 1

    class FailedSource:
        def fetch_usage_snapshot(self, *, refreshed_at):
            raise RuntimeError("password=must-not-leak")

    with pytest.raises(RuntimeError):
        sync_huangyoucan_usage(
            source=FailedSource(),
            repository=repository,
            trigger_source="postgres_failure",
            now=lambda: sync_time,
        )

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        snapshots = connection.execute("SELECT * FROM service_period_huangyoucan_usage_snapshot").fetchall()
        runs = connection.execute("SELECT status, error_summary FROM service_period_huangyoucan_usage_sync_runs ORDER BY id").fetchall()

    assert [row["huangyoucan_user_id"] for row in snapshots] == ["hyc_pg"]
    assert [row["status"] for row in runs] == ["succeeded", "failed"]
    assert runs[-1]["error_summary"] == "password=[redacted]"


def test_postgres_member_api_projection_keeps_legacy_fields_and_adds_usage(
    next_pg_schema,
) -> None:
    import psycopg

    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as connection:
        trade_product_id = connection.execute(
            """
            INSERT INTO wechat_pay_products (product_code, name, amount_total, currency, status, enabled)
            VALUES ('hyc_projection_product', '黄小璨投影测试', 99900, 'CNY', 'active', TRUE)
            RETURNING id
            """
        ).fetchone()[0]
        service_product_id = connection.execute(
            """
            INSERT INTO service_period_products (
                trade_product_id, link_slug, membership_config_id, membership_config_name, duration_days
            ) VALUES (%s, 'hyc-projection-product', 'vip', '会员', 90)
            RETURNING id
            """,
            (trade_product_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, mobile, mobile_normalized, customer_name
            ) VALUES ('union_hyc_projection', 'wm_hyc_projection', '13800138000', '13800138000', '投影会员')
            """
        )
        connection.execute(
            """
            INSERT INTO service_period_entitlements (
                service_product_id, trade_product_id, unionid, external_userid_snapshot,
                membership_config_id, status, start_at, end_at
            ) VALUES (
                %s, %s, 'union_hyc_projection', 'wm_hyc_projection',
                'vip', 'active', '2026-07-01T00:00:00+00:00', '2099-07-01T00:00:00+00:00'
            )
            """,
            (service_product_id, trade_product_id),
        )
        connection.execute(
            """
            INSERT INTO service_period_huangyoucan_usage_snapshot (
                huangyoucan_user_id, unionid, mobile_md5, formally_logged_in, has_token_usage,
                learning_plan_id, learning_plan_current, learning_plan_total,
                open_count_7d, last_open_at, refreshed_at
            ) VALUES (
                'hyc_projection', 'union_hyc_projection', %s, TRUE, TRUE,
                'plan_projection', 2, 5, 7, '2026-07-13T00:30:00+00:00', '2026-07-13T01:00:00+00:00'
            )
            """,
            (normalized_mobile_md5("13800138000"),),
        )

    payload = PostgresServicePeriodRepository(database_url).members(
        str(service_product_id),
        status=None,
        limit=20,
        offset=0,
    )
    member = payload["items"][0]

    assert member["external_userid"] == "wm_hyc_projection"
    assert member["status"] == "active"
    assert member["end_at"].startswith("2099-07-01")
    assert member["huangyoucan_match_status"] == "matched_unionid"
    assert member["huangyoucan_formally_logged_in"] is True
    assert member["huangyoucan_has_token_usage"] is True
    assert member["huangyoucan_learning_plan_progress"] == {"current": 2, "total": 5}
    assert member["huangyoucan_open_count_7d"] == 7
    assert datetime.fromisoformat(member["huangyoucan_last_open_at"]).astimezone(timezone.utc) == datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc)


def test_systemd_timer_and_service_keep_readonly_enablement_prerequisites() -> None:
    timer = (ROOT / "deploy" / "aicrm-huangyoucan-usage-sync.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "aicrm-huangyoucan-usage-sync.service").read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* 09,21:00:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "EnvironmentFile=/home/ubuntu/.aicrm-huangyoucan-readonly.env" in service
    assert "scripts.run_huangyoucan_usage_sync" in service
