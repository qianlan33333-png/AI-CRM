from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker

from aicrm_next.crm.customer_read_model.application import (
    GetCustomerTimelineQuery,
    ListCustomersQuery,
    ListRecentMessagesQuery,
)
from aicrm_next.crm.customer_read_model.dto import (
    CustomerTimelineRequest,
    ListCustomersRequest,
    RecentMessagesRequest,
)
from aicrm_next.crm.customer_read_model.models import (
    customer_detail_snapshot_next,
    customer_list_index_next,
    customer_recent_message_next,
    customer_timeline_event_next,
)
from aicrm_next.crm.customer_read_model.repo import SqlAlchemyCustomerReadModelRepository
from aicrm_next.crm.identity_contact.dto import IdentityResolution, IdentityResolveResult


def _row(row_id: int, *, unionid: str = "", external_userid: str = "", owner_userid: str = "owner-a", mobile: str = "") -> dict:
    now = datetime(2026, 6, row_id, tzinfo=timezone.utc)
    return {
        "id": row_id,
        "unionid": unionid or f"union_customer_{row_id:03d}",
        "customer_name": f"客户{row_id}",
        "owner_userid": owner_userid,
        "owner_display_name": "顾问甲",
        "remark": "重点客户" if row_id == 2 else "",
        "description": "",
        "mobile": mobile,
        "is_bound": bool(mobile),
        "binding_status": "bound" if mobile else "unbound",
        "tags_json": ["重点跟进"] if row_id == 2 else [],
        "class_user_status_json": {"current_status": "lead"},
        "last_message_at": now,
        "last_touch_at": now,
        "updated_at": now,
        "created_at": now,
    }


def test_sqlalchemy_customer_list_uses_sql_limit_offset_and_count() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    customer_list_index_next.create(engine)
    session = sessionmaker(bind=engine, future=True)()
    session.execute(
        insert(customer_list_index_next),
        [
            _row(1, unionid="union_customer_001", owner_userid="owner-a", mobile="13800138001"),
            _row(2, unionid="union_customer_002", owner_userid="owner-a", mobile="13800138002"),
            _row(3, unionid="union_customer_003", owner_userid="owner-a", mobile="13800138003"),
            _row(4, unionid="union_customer_004", owner_userid="owner-b", mobile="13900139004"),
        ],
    )
    session.commit()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    repo = SqlAlchemyCustomerReadModelRepository(session)

    rows = repo.list_customers({"owner_userid": "owner-a"}, limit=1, offset=1)
    total = repo.count_customers({"owner_userid": "owner-a"})

    assert [row["unionid"] for row in rows] == ["union_customer_002"]
    assert total == 3
    list_sql = next(statement for statement in statements if "FROM customer_list_index_next" in statement and "LIMIT" in statement.upper())
    assert "LIMIT" in list_sql.upper()
    assert "OFFSET" in list_sql.upper()


def test_projection_watermark_reads_singleton_state_without_customer_scan() -> None:
    class WatermarkResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "last_succeeded_at": datetime(2026, 7, 26, 19, 7, tzinfo=timezone.utc),
                "source_count": 23823,
                "target_count": 23823,
                "dirty_generation": 203,
                "completed_generation": 203,
                "refresh_status": "idle",
            }

    class WatermarkSession:
        def __init__(self) -> None:
            self.statement = ""

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            self.statement = str(statement)
            return WatermarkResult()

    session = WatermarkSession()

    watermark = SqlAlchemyCustomerReadModelRepository(session).get_projection_watermark()

    assert watermark == {
        "last_succeeded_at": "2026-07-26T19:07:00+00:00",
        "source_count": 23823,
        "target_count": 23823,
        "dirty_generation": 203,
        "completed_generation": 203,
        "refresh_status": "idle",
    }
    assert "customer_read_model_refresh_state" in session.statement
    assert "customer_read_model_refresh_intent" in session.statement
    assert "customer_list_index_next" not in session.statement


def test_list_customers_query_response_schema_uses_repo_page_and_total(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://customer:customer@127.0.0.1:1/aicrm_customer")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")

    class RecordingRepo:
        def __init__(self) -> None:
            self.list_calls: list[tuple[dict, int | None, int]] = []
            self.count_calls: list[dict] = []

        def list_customers(self, filters=None, *, limit=None, offset=0):
            self.list_calls.append((dict(filters or {}), limit, offset))
            return [_row(2, external_userid="wx_ext_002", owner_userid="owner-a", mobile="13800138002")]

        def count_customers(self, filters=None) -> int:
            self.count_calls.append(dict(filters or {}))
            return 12

    repo = RecordingRepo()

    payload = ListCustomersQuery(repo)(ListCustomersRequest(owner_userid="owner-a", limit=1, offset=1))

    assert set(payload) >= {"customers", "items", "count", "total", "limit", "offset", "filters", "status_code"}
    assert payload["customers"] == payload["items"]
    assert payload["count"] == 1
    assert payload["total"] == 12
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert repo.list_calls == [(payload["filters"], 1, 1)]
    assert repo.count_calls == [payload["filters"]]


def test_postgres_customer_lookup_resolves_alias_then_reads_one_unionid_snapshot(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    customer_detail_snapshot_next.create(engine)
    session = sessionmaker(bind=engine, future=True)()
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    session.execute(
        insert(customer_detail_snapshot_next),
        [
            {
                "id": row_id,
                "unionid": unionid,
                "customer_json": {"external_userid": external_userid, "unionid": unionid},
                "binding_json": {},
                "identity_json": {"unionid": unionid},
                "follow_users_json": [],
                "marketing_summary_json": {},
                "marketing_profile_json": {},
                "contact_json": {},
                "sidebar_context_json": {},
                "updated_at": now,
                "created_at": now,
            }
            for row_id, unionid, external_userid in (
                (1, "union-first", "wm-first"),
                (2, "union-target", "wm-target"),
            )
        ],
    )
    session.commit()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    monkeypatch.setattr("aicrm_next.crm.customer_read_model.repo.is_sqlite_session", lambda _session: False)
    monkeypatch.setattr(
        "aicrm_next.crm.customer_read_model.repo.SQLAlchemyIdentityResolver.resolve",
        lambda _resolver, _request: IdentityResolveResult(
            status="resolved",
            identity=IdentityResolution(
                person_id=None,
                external_userid="wm-target",
                mobile=None,
                unionid="union-target",
            ),
        ),
    )

    customer = SqlAlchemyCustomerReadModelRepository(session).get_customer("wm-target")

    assert customer is not None
    assert customer["unionid"] == "union-target"
    detail_statements = [statement for statement in statements if "FROM customer_detail_snapshot_next" in statement]
    assert len(detail_statements) == 1
    assert "WHERE customer_detail_snapshot_next.unionid" in detail_statements[0]
    assert "LIMIT" in detail_statements[0].upper()


def test_timeline_and_recent_messages_apply_bounds_in_sql() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    customer_timeline_event_next.create(engine)
    customer_recent_message_next.create(engine)
    session = sessionmaker(bind=engine, future=True)()
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    session.execute(
        insert(customer_timeline_event_next),
        [
            {
                "id": row_id,
                "event_id": f"event-{row_id}",
                "unionid": unionid,
                "event_type": "radar_opened",
                "event_time": now.replace(minute=row_id),
                "title": f"事件{row_id}",
                "summary": "",
                "source_table": "radar_click_events",
                "source_id": str(row_id),
                "metadata_json": {},
                "created_at": now,
            }
            for row_id, unionid in ((1, "union-target"), (2, "union-target"), (3, "union-other"))
        ],
    )
    session.execute(
        insert(customer_recent_message_next),
        [
            {
                "id": row_id,
                "msgid": f"message-{row_id}",
                "unionid": unionid,
                "msgtype": "text",
                "content": f"消息{row_id}",
                "send_time": now.replace(minute=row_id),
                "owner_userid": "owner-a",
                "chat_type": "single",
                "metadata_json": {},
                "created_at": now,
            }
            for row_id, unionid in ((1, "union-target"), (2, "union-target"), (3, "union-other"))
        ],
    )
    session.commit()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    repo = SqlAlchemyCustomerReadModelRepository(session)
    timeline = repo.list_timeline_by_unionid("union-target", limit=1, offset=1)
    messages = repo.list_recent_messages_by_unionid("union-target", limit=1)

    assert [item["event_id"] for item in timeline] == ["event-1"]
    assert [item["msgid"] for item in messages] == ["message-2"]
    timeline_sql = next(statement for statement in statements if "FROM customer_timeline_event_next" in statement)
    message_sql = next(statement for statement in statements if "FROM customer_recent_message_next" in statement)
    assert "LIMIT" in timeline_sql.upper()
    assert "OFFSET" in timeline_sql.upper()
    assert "LIMIT" in message_sql.upper()


def test_activity_queries_reuse_one_resolved_customer_and_unionid(monkeypatch) -> None:
    monkeypatch.setattr(
        "aicrm_next.crm.customer_read_model.application._production_customer_data_required",
        lambda: True,
    )
    monkeypatch.setattr(
        "aicrm_next.crm.customer_read_model.application._customer_read_model_next_primary_enabled",
        lambda: True,
    )

    class RecordingRepo:
        def __init__(self) -> None:
            self.customer_calls: list[str] = []
            self.timeline_calls: list[tuple[str, dict, int | None, int]] = []
            self.message_calls: list[tuple[str, int | None]] = []

        def get_customer(self, external_userid):
            self.customer_calls.append(external_userid)
            return {"external_userid": external_userid, "unionid": "union-target"}

        def customer_exists(self, _external_userid):
            raise AssertionError("activity query must not resolve the same customer twice")

        def list_timeline_by_unionid(self, unionid, filters, *, limit=None, offset=0):
            self.timeline_calls.append((unionid, dict(filters), limit, offset))
            return []

        def count_timeline_by_unionid(self, unionid, filters):
            assert unionid == "union-target"
            assert filters == {"event_type": "radar_opened"}
            return 0

        def list_recent_messages_by_unionid(self, unionid, *, limit=None):
            self.message_calls.append((unionid, limit))
            return []

    repo = RecordingRepo()

    timeline = GetCustomerTimelineQuery(repo)(
        CustomerTimelineRequest(
            external_userid="wm-target",
            event_type="radar_opened",
            limit=25,
            offset=50,
        )
    )
    messages = ListRecentMessagesQuery(repo)(
        RecentMessagesRequest(external_userid="wm-target", limit=15)
    )

    assert timeline["ok"] is True
    assert messages["ok"] is True
    assert repo.customer_calls == ["wm-target", "wm-target"]
    assert repo.timeline_calls == [
        ("union-target", {"event_type": "radar_opened"}, 25, 50)
    ]
    assert repo.message_calls == [("union-target", 15)]
