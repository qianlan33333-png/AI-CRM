from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from aicrm_next.crm.customer_read_model.models import (
    customer_detail_snapshot_next,
    customer_detail_snapshot_next_shadow,
    customer_list_index_next,
    customer_list_index_next_shadow,
    customer_recent_message_next,
    customer_recent_message_next_shadow,
    customer_timeline_event_next,
)
from aicrm_next.crm.customer_read_model.refresh import CustomerReadModelRefreshService
from aicrm_next.crm.customer_read_model.refresh_scope_repository import (
    CustomerReadModelIncrementalScope,
    CustomerReadModelRefreshScopeRepository,
)
from aicrm_next.crm.customer_read_model.repo import SqlAlchemyCustomerReadModelRepository


def _customer(unionid: str, *, name: str) -> dict:
    return {
        "unionid": unionid,
        "external_userid": f"external-{unionid}",
        "customer_name": name,
        "updated_at": "2026-07-27T00:00:00+00:00",
        "created_at": "2026-07-01T00:00:00+00:00",
        "identity": {"unionid": unionid},
        "binding": {"is_bound": False, "binding_status": "unbound"},
    }


def _create_projection_tables(engine) -> None:
    customer_list_index_next.create(engine)
    customer_detail_snapshot_next.create(engine)
    customer_timeline_event_next.create(engine)
    customer_recent_message_next.create(engine)


def test_incremental_repository_upserts_and_deletes_only_requested_unionids() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_projection_tables(engine)
    session = Session(engine, future=True)
    repository = SqlAlchemyCustomerReadModelRepository(session)
    repository.seed(
        customers=[_customer("union-1", name="old one"), _customer("union-2", name="old two")],
        messages_by_external_userid={},
        timeline_by_external_userid={},
    )
    old_timestamp = datetime(2026, 7, 1, tzinfo=timezone.utc)
    session.execute(
        insert(customer_recent_message_next),
        [
            {
                "msgid": "old-message-1",
                "unionid": "union-1",
                "msgtype": "text",
                "content": "old",
                "send_time": old_timestamp,
                "chat_type": "single",
                "metadata_json": {},
                "created_at": old_timestamp,
            },
            {
                "msgid": "unrelated-message",
                "unionid": "union-unrelated",
                "msgtype": "text",
                "content": "keep",
                "send_time": old_timestamp,
                "chat_type": "single",
                "metadata_json": {},
                "created_at": old_timestamp,
            },
        ],
    )
    session.commit()

    result = repository.upsert_customer_snapshots(
        requested_unionids=["union-1", "union-2", "union-3"],
        customers=[_customer("union-1", name="new one"), _customer("union-3", name="new three")],
        messages_by_unionid={
            "union-1": [
                {
                    "msgid": "new-message-1",
                    "unionid": "union-1",
                    "msgtype": "text",
                    "content": "new",
                    "send_time": "2026-07-27T00:00:00+00:00",
                    "source_id": "101",
                }
            ]
        },
    )

    assert result == {
        "requested_count": 3,
        "upserted_count": 2,
        "deleted_count": 1,
        "message_count": 1,
        "timeline_event_count": 1,
    }
    list_rows = session.execute(
        select(customer_list_index_next.c.unionid, customer_list_index_next.c.customer_name).order_by(
            customer_list_index_next.c.unionid
        )
    ).all()
    assert list_rows == [("union-1", "new one"), ("union-3", "new three")]
    detail_unionids = session.execute(
        select(customer_detail_snapshot_next.c.unionid).order_by(customer_detail_snapshot_next.c.unionid)
    ).scalars().all()
    assert detail_unionids == ["union-1", "union-3"]
    messages = session.execute(
        select(customer_recent_message_next.c.unionid, customer_recent_message_next.c.msgid).order_by(
            customer_recent_message_next.c.unionid
        )
    ).all()
    assert messages == [("union-1", "new-message-1"), ("union-unrelated", "unrelated-message")]
    timeline = session.execute(
        select(customer_timeline_event_next.c.event_id, customer_timeline_event_next.c.unionid)
    ).one()
    assert timeline == ("message:101", "union-1")


def test_postgres_incremental_repository_uses_unionid_upsert(next_pg_schema) -> None:
    del next_pg_schema
    database_url = str(os.environ["DATABASE_URL"]).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine, future=True) as session:
            repository = SqlAlchemyCustomerReadModelRepository(session)
            repository.seed(
                customers=[
                    _customer("union-pg-1", name="old one"),
                    _customer("union-pg-2", name="old two"),
                    _customer("union-pg-unrelated", name="keep"),
                ],
                messages_by_external_userid={},
                timeline_by_external_userid={},
            )
            session.commit()

            result = repository.upsert_customer_snapshots(
                requested_unionids=["union-pg-1", "union-pg-2"],
                customers=[_customer("union-pg-1", name="new one")],
                messages_by_unionid={},
            )

            assert result["upserted_count"] == 1
            assert result["deleted_count"] == 1
            assert session.execute(
                select(
                    customer_list_index_next.c.unionid,
                    customer_list_index_next.c.customer_name,
                ).order_by(customer_list_index_next.c.unionid)
            ).all() == [
                ("union-pg-1", "new one"),
                ("union-pg-unrelated", "keep"),
            ]
    finally:
        engine.dispose()


def test_postgres_full_calibration_flips_slots_and_incremental_writes_active_slot(
    next_pg_schema,
) -> None:
    del next_pg_schema
    database_url = str(os.environ["DATABASE_URL"]).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine, future=True) as session:
            repository = SqlAlchemyCustomerReadModelRepository(session)
            repository.seed(
                customers=[_customer("union-slot-old", name="old primary")],
                messages_by_external_userid={},
                timeline_by_external_userid={},
            )
            session.commit()

            repository.replace_all(
                customers=[
                    _customer("union-slot-1", name="shadow one"),
                    _customer("union-slot-2", name="shadow two"),
                ],
                messages_by_external_userid={
                    "external-union-slot-1": [
                        {
                            "msgid": "shadow-message",
                            "unionid": "union-slot-1",
                            "content": "shadow",
                            "send_time": "2026-07-27T01:00:00+00:00",
                        }
                    ]
                },
                timeline_by_external_userid={},
            )

            state = session.execute(
                text(
                    """
                    SELECT active_slot, active_generation
                    FROM customer_read_model_refresh_state
                    WHERE singleton_id = 1
                    """
                )
            ).one()
            assert state == ("shadow", 2)
            assert session.execute(
                select(customer_list_index_next.c.unionid)
            ).scalars().all() == ["union-slot-old"]
            assert session.execute(
                select(customer_list_index_next_shadow.c.unionid).order_by(
                    customer_list_index_next_shadow.c.unionid
                )
            ).scalars().all() == ["union-slot-1", "union-slot-2"]
            assert repository.count_customers() == 2
            assert repository.get_customer_by_unionid("union-slot-1")["customer_name"] == "shadow one"
            assert repository.list_recent_messages_by_unionid("union-slot-1", limit=1)[0][
                "msgid"
            ] == "shadow-message"

            repository.upsert_customer_snapshots(
                requested_unionids=["union-slot-1", "union-slot-2"],
                customers=[_customer("union-slot-1", name="shadow incremental")],
                messages_by_unionid={},
            )
            assert session.execute(
                select(
                    customer_list_index_next_shadow.c.unionid,
                    customer_list_index_next_shadow.c.customer_name,
                )
            ).all() == [("union-slot-1", "shadow incremental")]
            assert session.execute(
                select(customer_list_index_next.c.unionid)
            ).scalars().all() == ["union-slot-old"]

            repository.replace_all(
                customers=[_customer("union-slot-final", name="new primary")],
                messages_by_external_userid={},
                timeline_by_external_userid={},
            )
            assert session.execute(
                text(
                    """
                    SELECT active_slot, active_generation
                    FROM customer_read_model_refresh_state
                    WHERE singleton_id = 1
                    """
                )
            ).one() == ("primary", 3)
            assert session.execute(
                select(customer_list_index_next.c.unionid)
            ).scalars().all() == ["union-slot-final"]
            assert session.execute(
                select(customer_list_index_next_shadow.c.unionid)
            ).scalars().all() == ["union-slot-1"]
            assert repository.get_customer_by_unionid("union-slot-final")["customer_name"] == "new primary"
    finally:
        engine.dispose()


def test_postgres_failed_full_calibration_preserves_active_projection(next_pg_schema) -> None:
    del next_pg_schema
    database_url = str(os.environ["DATABASE_URL"]).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine, future=True) as session:
            repository = SqlAlchemyCustomerReadModelRepository(session)
            session.execute(
                text(
                    """
                    INSERT INTO customer_read_model_refresh_state (
                        singleton_id, last_succeeded_at, source_count, target_count,
                        duration_ms, updated_at, active_slot, active_generation
                    ) VALUES (1, CURRENT_TIMESTAMP, 1, 1, 0, CURRENT_TIMESTAMP, 'primary', 1)
                    """
                )
            )
            repository.seed(
                customers=[_customer("union-slot-stable", name="stable")],
                messages_by_external_userid={},
                timeline_by_external_userid={},
            )
            session.commit()

            duplicate = _customer("union-slot-duplicate", name="duplicate")
            try:
                repository.replace_all(
                    customers=[duplicate, dict(duplicate)],
                    messages_by_external_userid={},
                    timeline_by_external_userid={},
                )
            except Exception:
                pass
            else:
                raise AssertionError("duplicate inactive projection must fail closed")

            assert session.execute(
                text(
                    """
                    SELECT active_slot, active_generation
                    FROM customer_read_model_refresh_state
                    WHERE singleton_id = 1
                    """
                )
            ).one() == ("primary", 1)
            assert session.execute(
                select(customer_list_index_next.c.unionid)
            ).scalars().all() == ["union-slot-stable"]
            assert session.execute(
                select(customer_list_index_next_shadow.c.unionid)
            ).scalars().all() == []
            assert session.execute(
                select(customer_detail_snapshot_next_shadow.c.unionid)
            ).scalars().all() == []
            assert session.execute(
                select(customer_recent_message_next_shadow.c.unionid)
            ).scalars().all() == []
            assert repository.get_customer_by_unionid("union-slot-stable")["customer_name"] == "stable"
    finally:
        engine.dispose()


class _IncrementalSource:
    def __init__(self) -> None:
        self.full_scan_called = False

    def count_customers(self, filters=None) -> int:
        self.full_scan_called = True
        raise AssertionError("incremental refresh must not count the full source")

    def list_customers(self, filters=None, *, limit=None, offset=0):
        self.full_scan_called = True
        raise AssertionError("incremental refresh must not list the full source")

    def list_customers_by_unionids(self, unionids: list[str]) -> list[dict]:
        assert unionids == ["union-1"]
        return [_customer("union-1", name="incremental")]

    def snapshot_recent_messages_by_unionid(self, unionids: list[str], *, per_customer_limit: int):
        assert unionids == ["union-1"]
        assert per_customer_limit == 100
        return {"union-1": []}


class _IncrementalTarget:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def count_customers(self, filters=None) -> int:
        return 7

    def upsert_customer_snapshots(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"upserted_count": 1}


class _ScopeRepository:
    def load(self, **kwargs) -> CustomerReadModelIncrementalScope:
        assert kwargs == {"completed_generation": 4, "target_generation": 5}
        return CustomerReadModelIncrementalScope(unionids=("union-1",), event_count=1)


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params) -> None:
        self.calls.append((str(statement), dict(params)))


class _Begin:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    def __enter__(self) -> _RecordingSession:
        return self._session

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _SessionFactory:
    def __init__(self) -> None:
        self.session = _RecordingSession()

    def begin(self) -> _Begin:
        return _Begin(self.session)


def test_refresh_service_uses_incremental_scope_without_full_source_scan() -> None:
    source = _IncrementalSource()
    target = _IncrementalTarget()
    sessions = _SessionFactory()
    result = CustomerReadModelRefreshService(
        source_repo=source,
        target_repo=target,
        session_factory=sessions,
        scope_repository=_ScopeRepository(),
    ).run(dry_run=False, generation=5, completed_generation=4)

    assert result.ok is True
    assert result.mode == "incremental"
    assert result.affected_count == 1
    assert result.target_count_before == result.target_count_after == 7
    assert source.full_scan_called is False
    assert target.calls[0]["requested_unionids"] == ["union-1"]
    assert sessions.session.calls[0][1]["source_count"] == 7


def test_refresh_scope_resolves_direct_and_archive_batch_customer_keys() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE customer_read_model_refresh_source_receipt (
                    id INTEGER PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    source_event_id TEXT NOT NULL,
                    source_event_type TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE internal_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text("CREATE TABLE archived_messages (id INTEGER PRIMARY KEY, unionid TEXT NOT NULL)")
        )
        connection.execute(
            text(
                """
                INSERT INTO customer_read_model_refresh_source_receipt
                    (id, generation, source_event_id, source_event_type)
                VALUES
                    (1, 1, 'event-direct', 'identity.resolved'),
                    (2, 2, 'event-archive', 'message_archive.batch_ingested')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO internal_event
                    (event_id, event_type, subject_type, subject_id, payload_json)
                VALUES
                    ('event-direct', 'identity.resolved', 'unionid', 'union-direct', :direct_payload),
                    ('event-archive', 'message_archive.batch_ingested', '', '', :archive_payload)
                """
            ),
            {
                "direct_payload": json.dumps({"unionid": "union-direct"}),
                "archive_payload": json.dumps(
                    {"inserted_count": 2, "message_row_ids": [10, 11]}
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO archived_messages (id, unionid)
                VALUES (10, 'union-message'), (11, 'union-message')
                """
            )
        )

    scope = CustomerReadModelRefreshScopeRepository(
        sessionmaker(bind=engine, future=True)
    ).load(completed_generation=0, target_generation=2)

    assert scope.requires_full_refresh is False
    assert scope.unionids == ("union-direct", "union-message")
    assert scope.external_userids == ()
    assert scope.event_count == 2
