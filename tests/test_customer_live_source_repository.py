from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from aicrm_next.customer_read_model.repo import (
    _DEFAULT_LIVE_SOURCE_LIST_LIMIT,
    LiveSourceCustomerReadRepository,
    build_customer_live_source_repository,
)
from aicrm_next.shared.database import get_sqlalchemy_database_url


def _execute_all(session, statements: list[str]) -> None:
    for statement in statements:
        session.execute(text(statement))
    session.commit()


def test_live_source_repository_reads_existing_customer_source_tables(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://customer:customer@127.0.0.1:1/aicrm_customer")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")

    engine = create_engine("sqlite:///:memory:", future=True)
    session = sessionmaker(bind=engine, future=True)()
    _execute_all(
        session,
        [
            """
            CREATE TABLE crm_user_identity (
                unionid TEXT PRIMARY KEY,
                openids_json TEXT,
                external_userids_json TEXT,
                mobile TEXT,
                mobile_normalized TEXT,
                mobile_verified INTEGER,
                mobile_source TEXT,
                customer_name TEXT,
                remark TEXT,
                description TEXT,
                avatar TEXT,
                gender INTEGER,
                profile_json TEXT,
                follow_users_json TEXT,
                legacy_person_id TEXT,
                legacy_identity_map_ids_json TEXT,
                legacy_sources_json TEXT,
                primary_external_userid TEXT,
                primary_openid TEXT,
                primary_owner_userid TEXT,
                identity_status TEXT,
                unionid_resolved_at TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                last_polled_at TEXT,
                next_poll_at TEXT,
                poll_attempt_count INTEGER,
                last_poll_error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE contact_tags (
                id INTEGER PRIMARY KEY,
                unionid TEXT,
                userid TEXT,
                tag_id TEXT,
                tag_name TEXT,
                created_at TEXT
            )
            """,
            """
            CREATE TABLE class_user_status_current (
                unionid TEXT PRIMARY KEY,
                signup_status TEXT,
                signup_label_name TEXT,
                customer_name_snapshot TEXT,
                owner_userid_snapshot TEXT,
                status_flags_json TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE archived_messages (
                id INTEGER PRIMARY KEY,
                msgid TEXT,
                chat_type TEXT,
                unionid TEXT,
                owner_userid TEXT,
                sender TEXT,
                receiver TEXT,
                msgtype TEXT,
                content TEXT,
                send_time TEXT,
                raw_payload TEXT,
                created_at TEXT
            )
            """,
            "CREATE TABLE wechat_pay_orders (id INTEGER, unionid TEXT, status TEXT, trade_state TEXT, paid_at TEXT, updated_at TEXT, created_at TEXT)",
            "CREATE TABLE questionnaire_submissions (id INTEGER, unionid TEXT)",
            """
            CREATE TABLE automation_channel_contact (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                unionid TEXT,
                owner_staff_id TEXT,
                source_payload_json TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE owner_role_map (
                userid TEXT PRIMARY KEY,
                display_name TEXT
            )
            """,
            """
            INSERT INTO crm_user_identity VALUES
            (
                'union-1', '["openid-1"]', '["wx_ext_001"]',
                '13800138000', '13800138000', 1, 'test_fixture',
                '身份客户名', '重点备注', '客户描述', '', NULL,
                '{"name":"身份客户名","remark":"重点备注","description":"客户描述"}',
                '[]', 'person-1', '[]', '{}', 'wx_ext_001', 'openid-1', 'owner-a',
                'active', '2026-06-02T08:02:00+00:00',
                '2026-06-02T08:02:00+00:00', '2026-06-02T08:02:00+00:00',
                '2026-06-02T08:02:00+00:00', NULL, 0, '',
                '2026-06-02T08:02:00+00:00', '2026-06-02T08:02:00+00:00'
            )
            """,
            "INSERT INTO contact_tags VALUES (1, 'union-1', 'owner-a', 'tag-1', '重点跟进', '2026-06-02T08:04:00+00:00')",
            """
            INSERT INTO class_user_status_current VALUES
            ('union-1', 'activated', '已激活', '状态快照名', 'owner-a',
             '{"activation_bucket":"activated"}', '2026-06-02T08:05:00+00:00')
            """,
            """
            INSERT INTO archived_messages VALUES
            (1, 'msg-1', 'single', 'union-1', 'owner-a', 'customer', 'owner-a', 'text',
             '你好', '2026-06-02T08:06:00+00:00', '{}', '2026-06-02T08:06:00+00:00')
            """,
            "INSERT INTO owner_role_map VALUES ('owner-a', '顾问甲')",
        ],
    )

    repo = build_customer_live_source_repository(session=session)

    rows = repo.list_customers({"keyword": "重点"}, limit=50, offset=0)
    detail = repo.get_customer("wx_ext_001")
    messages = repo.list_recent_messages("wx_ext_001", limit=10)
    timeline = repo.list_timeline("wx_ext_001", limit=10)

    assert rows[0]["external_userid"] == "wx_ext_001"
    assert rows[0]["customer_name"] == "状态快照名"
    assert rows[0]["owner_display_name"] == "顾问甲"
    assert rows[0]["mobile"] == "13800138000"
    assert rows[0]["binding"]["binding_status"] == "bound"
    assert rows[0]["tags"] == ["重点跟进"]
    assert rows[0]["class_user_status"]["activation_bucket"] == "activated"
    assert detail["identity"]["unionid"] == "union-1"
    assert messages[0]["msgid"] == "msg-1"
    assert messages[0]["unionid"] == "union-1"
    assert timeline[0]["event_type"] == "message"


def test_live_source_repository_uses_safe_default_limit_when_limit_is_none(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://customer:customer@127.0.0.1:1/aicrm_customer")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")

    executed_params: list[dict] = []

    class FakeResult:
        def mappings(self):
            return []

        def scalar_one(self):
            return 0

    class FakeSession:
        def execute(self, statement, params=None):
            executed_params.append(dict(params or {}))
            return FakeResult()

    repo = build_customer_live_source_repository(session=FakeSession())

    assert repo.list_customers(limit=None, offset=0) == []
    assert executed_params[-1]["limit"] == _DEFAULT_LIVE_SOURCE_LIST_LIMIT
    assert executed_params[-1]["limit"] != 100000


def test_recent_message_snapshot_supports_legacy_text_send_time(next_pg_schema):
    engine = create_engine(get_sqlalchemy_database_url(os.environ["DATABASE_URL"]), future=True)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True)()
    try:
        session.execute(
            text(
                """
                CREATE TEMPORARY TABLE archived_messages (
                    id BIGINT PRIMARY KEY,
                    msgid TEXT,
                    chat_type TEXT,
                    unionid TEXT,
                    owner_userid TEXT,
                    msgtype TEXT,
                    content TEXT,
                    send_time TEXT,
                    created_at TIMESTAMPTZ NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO archived_messages (
                    id, msgid, chat_type, unionid, owner_userid, msgtype,
                    content, send_time, created_at
                ) VALUES
                    (1, 'msg-older', 'single', 'union-legacy', 'owner-a', 'text',
                     'older', '', '2026-07-12T08:00:00+00:00'),
                    (2, 'msg-newer', 'single', 'union-legacy', 'owner-a', 'text',
                     'newer', '2026-07-13T09:00:00+00:00', '2026-07-13T08:00:00+00:00')
                """
            )
        )

        snapshot = LiveSourceCustomerReadRepository(session).snapshot_recent_messages_by_unionid(
            ["union-legacy"],
            per_customer_limit=100,
        )

        assert [item["msgid"] for item in snapshot["union-legacy"]] == ["msg-newer", "msg-older"]
        assert snapshot["union-legacy"][1]["send_time"].startswith("2026-07-12 ")
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_recent_message_snapshot_large_postgres_scope_uses_one_array_parameter(next_pg_schema):
    engine = create_engine(get_sqlalchemy_database_url(os.environ["DATABASE_URL"]), future=True)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True)()
    try:
        session.execute(
            text(
                """
                CREATE TEMPORARY TABLE archived_messages (
                    id BIGINT PRIMARY KEY,
                    msgid TEXT,
                    chat_type TEXT,
                    unionid TEXT,
                    owner_userid TEXT,
                    msgtype TEXT,
                    content TEXT,
                    send_time TEXT,
                    created_at TIMESTAMPTZ NOT NULL
                ) ON COMMIT DROP
                """
            )
        )

        snapshot = LiveSourceCustomerReadRepository(session).snapshot_recent_messages_by_unionid(
            [f"union-large-{index}" for index in range(70_000)],
            per_customer_limit=100,
        )

        assert snapshot == {}
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
