from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from aicrm_next.commerce import wechat_shop_service
from aicrm_next.customer_read_model.repo import (
    LiveSourceCustomerReadRepository,
    SqlAlchemyCustomerReadModelRepository,
)
from aicrm_next.customer_read_model.timeline_projection import (
    CustomerTimelineProjectionRepository,
    customer_timeline_projection_consumer,
    timeline_projection_from_internal_event,
)
from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.radar_links.application import ResolveRadarLandingQuery
from aicrm_next.radar_links.domain import sign_viewer_session
from aicrm_next.radar_links import repo as radar_repo
from aicrm_next.radar_links.repo import InMemoryRadarLinksRepository
from aicrm_next.service_period import repo as service_period_repo
from aicrm_next.shared.db_session import get_session_factory


def _event(event_type: str, payload: dict, **values) -> InternalEvent:
    return InternalEvent(
        event_id=values.get("event_id", f"event-{event_type}"),
        event_type=event_type,
        aggregate_id=values.get("aggregate_id", "aggregate-1"),
        subject_type=values.get("subject_type", "unionid"),
        subject_id=values.get("subject_id", "union-1"),
        source_command_id=values.get("source_command_id", "command-1"),
        idempotency_key=values.get("idempotency_key", f"key-{event_type}"),
        occurred_at=values.get("occurred_at", "2026-07-17T10:00:00Z"),
        payload_json=payload,
    )


def test_four_customer_activity_types_map_to_stable_safe_projection_types() -> None:
    channel = timeline_projection_from_internal_event(
        _event(
            "channel_entry.entered",
            {"unionid": "union-1", "channel_id": 8, "channel_name": "直播间", "channel_code": "live"},
            source_command_id="entry-8",
        )
    )
    questionnaire = timeline_projection_from_internal_event(
        _event(
            "questionnaire.submitted",
            {
                "questionnaire": {"id": 3, "title": "需求调研"},
                "submission": {"submission_id": "sub-3", "unionid": "union-1", "submitted_at": "2026-07-17T09:00:00Z"},
            },
        )
    )
    product = timeline_projection_from_internal_event(
        _event(
            "payment.succeeded",
            {
                "order": {
                    "unionid": "union-1",
                    "out_trade_no": "pay-9",
                    "product_code": "course-9",
                    "product_name": "课程九",
                    "paid_at": "2026-07-17T08:00:00Z",
                }
            },
        )
    )
    radar = timeline_projection_from_internal_event(
        _event(
            "radar.opened",
            {
                "unionid": "union-1",
                "click_event_id": "click-4",
                "radar_id": 4,
                "radar_title": "白皮书",
                "target_type": "pdf",
            },
        )
    )

    assert [channel["event_type"], questionnaire["event_type"], product["event_type"], radar["event_type"]] == [
        "channel_entry",
        "questionnaire_submitted",
        "product_enrolled",
        "radar_opened",
    ]
    assert channel["event_id"] == "channel_entry:entry-8"
    assert questionnaire["event_id"] == "questionnaire:sub-3"
    assert product["event_id"] == "product:payment:pay-9"
    assert radar["event_id"] == "radar:click-4"


def test_payment_and_service_period_projection_dedupe_by_payment_number() -> None:
    payment = timeline_projection_from_internal_event(
        _event("payment.succeeded", {"order": {"unionid": "union-1", "out_trade_no": "pay-same", "product_name": "周期课"}})
    )
    entitlement = timeline_projection_from_internal_event(
        _event(
            "commerce.product_enrolled",
            {
                "source_table": "service_period_events",
                "source_id": "period-event-1",
                "order": {"unionid": "union-1", "out_trade_no": "pay-same", "product_name": "周期课"},
            },
        )
    )

    assert payment["event_id"] == entitlement["event_id"] == "product:payment:pay-same"


def test_unattributable_events_do_not_enter_a_personal_timeline() -> None:
    assert timeline_projection_from_internal_event(
        _event("radar.opened", {"click_event_id": "anonymous"}, subject_type="anonymous", subject_id="")
    ) is None
    assert timeline_projection_from_internal_event(
        _event(
            "questionnaire.submitted",
            {"questionnaire": {"id": 1}, "submission": {"submission_id": "sub-anon"}},
            subject_type="questionnaire_submission",
            subject_id="sub-anon",
        )
    ) is None


def test_projection_consumer_is_idempotent_and_retryable() -> None:
    class Repository:
        def __init__(self) -> None:
            self.events = {}

        def upsert(self, item):
            self.events[item["event_id"]] = dict(item)
            return {"ok": True, "projected": True}

    repository = Repository()
    event = _event(
        "radar.opened",
        {"unionid": "union-1", "click_event_id": "click-idempotent", "radar_title": "资料"},
    )
    run = InternalEventConsumerRun(consumer_name="customer_timeline_projection_consumer")

    first = customer_timeline_projection_consumer(event, run, repository=repository)
    second = customer_timeline_projection_consumer(event, run, repository=repository)

    assert first.status == second.status == "succeeded"
    assert list(repository.events) == ["radar:click-idempotent"]

    class FailingRepository:
        def upsert(self, _item):
            raise RuntimeError("temporary database failure")

    failed = customer_timeline_projection_consumer(event, run, repository=FailingRepository())
    assert failed.status == "failed_retryable"
    assert failed.retry_after_seconds == 30


def test_radar_emits_one_logical_open_for_an_identified_landing(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "timeline-radar-test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = InMemoryRadarLinksRepository()
    link = repo.save_link(
        {
            "title": "雷达资料",
            "target_type": "link",
            "original_url": "https://example.com/content",
            "enabled": True,
            "auth_required": True,
        }
    )

    ResolveRadarLandingQuery(repo)(link["code"], request_meta={})
    assert repo._logical_open_events == []

    viewer_session = sign_viewer_session(
        code=link["code"],
        unionid="union-radar",
        openid="openid-radar",
        secret_key="timeline-radar-test",
    )
    ResolveRadarLandingQuery(repo)(link["code"], request_meta={}, viewer_session=viewer_session)

    assert len(repo._logical_open_events) == 1
    assert repo._logical_open_events[0]["click_event_id"] > 0
    assert [event["stage"] for event in repo._events].count("landing") == 2
    assert "viewer_open" not in [event["stage"] for event in repo._events]


def test_reconciliation_uses_one_set_query_for_all_unionids() -> None:
    class Result:
        def mappings(self):
            return []

    class Session:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), dict(params)))
            return Result()

    session = Session()
    result = LiveSourceCustomerReadRepository(session).snapshot_customer_activity_by_unionid(
        ["union-1", "union-2"],
        per_customer_limit=20,
    )

    assert result == {}
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    for source_table in (
        "automation_channel_entry_effect_log",
        "questionnaire_submissions",
        "wechat_pay_orders",
        "wechat_shop_orders",
        "service_period_events",
        "radar_click_events",
    ):
        assert source_table in sql
    assert sql.count(":external_userids") == 1
    assert "unnest(CAST(:external_userids AS TEXT[]))" in sql
    assert sql.count("IN (SELECT unionid FROM requested_unionids)") == 6
    assert params["external_userids"] == ["union-1", "union-2"]
    assert params["per_customer_limit"] == 20


def test_reconciliation_large_postgres_scope_stays_below_driver_parameter_limit(next_pg_schema) -> None:
    unionids = [f"union-parameter-limit-{index}" for index in range(24_000)]

    with get_session_factory()() as session:
        result = LiveSourceCustomerReadRepository(session).snapshot_customer_activity_by_unionid(
            unionids,
            per_customer_limit=20,
        )

    assert result == {}


def test_bulk_timeline_upsert_stays_below_postgres_driver_parameter_limit(next_pg_schema) -> None:
    events = [
        {
            "event_id": f"timeline-parameter-limit-{index}",
            "unionid": f"union-timeline-parameter-limit-{index}",
            "event_type": "parameter_limit_proof",
            "event_time": "2026-07-18T12:00:00+00:00",
            "title": "参数上限回归",
            "summary": "",
            "source_table": "test_customer_timeline_projection",
            "source_id": str(index),
            "metadata": {},
        }
        for index in range(7_000)
    ]

    with get_session_factory()() as session:
        repository = SqlAlchemyCustomerReadModelRepository(session)
        assert repository.upsert_timeline_events(events, commit=False) == 7_000
        inserted = session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM customer_timeline_event_next
                WHERE event_id LIKE 'timeline-parameter-limit-%'
                """
            )
        ).scalar_one()
        assert int(inserted or 0) == 7_000
        session.rollback()


def test_timeline_migration_only_deduplicates_identical_event_ids_and_adds_indexes() -> None:
    migration = Path("migrations/versions/0133_sidebar_customer_timeline.py").read_text(encoding="utf-8")

    assert "PARTITION BY event_id" in migration
    assert "uq_customer_timeline_event_next_event_id" in migration
    assert "ADD CONSTRAINT uq_customer_timeline_event_next_event_id" in migration
    assert "unionid, event_time DESC, id DESC" in migration
    assert "TRUNCATE" not in migration


def test_postgres_timeline_upsert_is_unique_and_newest_first(next_pg_schema) -> None:
    repository = CustomerTimelineProjectionRepository()
    base = {
        "event_id": "radar:pg-1",
        "unionid": "union-pg-timeline",
        "event_type": "radar_opened",
        "event_time": "2026-07-17T09:00:00Z",
        "title": "首次标题",
        "summary": "已打开追踪链接",
        "source_table": "radar_click_events",
        "source_id": "pg-1",
        "metadata": {"radar_id": "1"},
    }
    repository.upsert(base)
    repository.upsert({**base, "event_time": "2026-07-17T10:00:00Z", "title": "更新标题"})
    repository.upsert(
        {
            **base,
            "event_id": "radar:pg-2",
            "source_id": "pg-2",
            "event_time": "2026-07-17T11:00:00Z",
            "title": "最新标题",
        }
    )

    with get_session_factory()() as session:
        rows = session.execute(
            text(
                """
                SELECT event_id, title
                FROM customer_timeline_event_next
                WHERE unionid = :unionid
                ORDER BY event_time DESC, id DESC
                """
            ),
            {"unionid": "union-pg-timeline"},
        ).mappings().all()
        indexes = {
            str(row["indexname"])
            for row in session.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'customer_timeline_event_next'
                    """
                )
            ).mappings()
        }

    assert [(row["event_id"], row["title"]) for row in rows] == [
        ("radar:pg-2", "最新标题"),
        ("radar:pg-1", "更新标题"),
    ]
    assert "uq_customer_timeline_event_next_event_id" in indexes
    assert "ix_customer_timeline_event_next_unionid_time_id" in indexes


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ConnectionContext:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *_args):
        return False


def test_wechat_shop_first_deal_and_outbox_share_one_transaction(monkeypatch) -> None:
    class Connection:
        def execute(self, statement, _params):
            normalized = " ".join(str(statement).split())
            if normalized.startswith("INSERT INTO wechat_shop_orders"):
                return _Cursor(
                    {
                        "id": 1,
                        "order_id": "shop-1",
                        "deal_recorded": True,
                        "unionid": "union-shop",
                        "product_code": "shop-product",
                        "product_name": "小店商品",
                        "paid_at": "2026-07-17T10:00:00Z",
                    }
                )
            raise AssertionError(normalized)

    connection = Connection()
    emitted = {}
    emission_attempts = []
    monkeypatch.setattr(wechat_shop_service, "database_mode", lambda: "postgres")
    monkeypatch.setattr(wechat_shop_service, "_connect", lambda: _ConnectionContext(connection))
    monkeypatch.setattr(wechat_shop_service, "project_wechat_shop_order_mobile", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        wechat_shop_service,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: (
            emission_attempts.append((conn, request)),
            emitted.setdefault(request.idempotency_key, (conn, request)),
        )[1],
    )
    order = {
        "order_id": "shop-1",
        "provider": "wechat_shop",
        "provider_label": "微信小店",
        "deal_recorded": True,
        "returned_recorded": False,
        "business_status": "paid",
        "status_code": "paid",
        "status_label": "已支付",
        "paid_at": "2026-07-17T10:00:00Z",
        "returned_at": None,
        "amount_total": 990,
        "refunded_amount_total": 0,
        "currency": "CNY",
        "transaction_id": "tx-1",
        "payment_method": "wechat",
        "unionid": "union-shop",
        "product_name": "小店商品",
        "product_code": "shop-product",
        "product_count": 1,
        "deliver_method": "virtual",
        "is_virtual_delivery": True,
        "virtual_account_no": "",
        "virtual_account_type": "",
        "aftersale_order_count": 0,
        "on_aftersale_order_count": 0,
        "finish_aftersale_sku_count": 0,
        "raw_order_json": {},
        "synced_at": "2026-07-17T10:00:00Z",
        "sync_status": "synced",
        "last_error": "",
        "created_at": "2026-07-17T10:00:00Z",
    }

    wechat_shop_service._upsert_order(order)
    wechat_shop_service._upsert_order(order)

    assert len(emission_attempts) == 2
    assert len(emitted) == 1
    emitted_connection, emitted_request = emitted["commerce.product_enrolled:wechat_shop:shop-1"]
    assert emitted_connection is connection
    assert emitted_request.event_type == "commerce.product_enrolled"


def test_radar_logical_click_and_outbox_share_one_transaction(monkeypatch) -> None:
    connection = object()
    emitted = []
    repository = radar_repo.PostgresRadarLinksRepository("postgresql://unused")
    monkeypatch.setattr(repository, "_connect", lambda: _ConnectionContext(connection))
    monkeypatch.setattr(
        repository,
        "_insert_click_event",
        lambda conn, payload: {
            **payload,
            "id": 91,
            "link_id": 9,
            "unionid": "union-radar",
            "created_at": "2026-07-17T10:00:00Z",
        },
    )
    monkeypatch.setattr(
        radar_repo,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: emitted.append((conn, request)) or {"ok": True},
    )

    repository.record_logical_open_event(
        {"link_id": 9, "unionid": "union-radar", "target_type_snapshot": "pdf", "stage": "authorized"},
        radar_title="白皮书",
    )

    assert len(emitted) == 1
    assert emitted[0][0] is connection
    assert emitted[0][1].event_type == "radar.opened"
    assert emitted[0][1].idempotency_key == "radar.opened:91"


def test_service_period_enrollment_and_outbox_share_caller_transaction(monkeypatch) -> None:
    class Connection:
        def execute(self, _statement, _params):
            return _Cursor(
                {
                    "id": 31,
                    "event_id": "period-event-31",
                    "created_at": "2026-07-17T10:00:00Z",
                }
            )

    connection = Connection()
    emitted = []
    monkeypatch.setattr(
        service_period_repo,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: emitted.append((conn, request)) or {"ok": True},
    )
    repository = service_period_repo.PostgresServicePeriodRepository("postgresql://unused")

    repository._insert_event(
        connection,
        product={"id": 4, "trade_product_id": 5, "product_code": "period-5", "name": "周期课"},
        entitlement_id=8,
        order={"id": 11},
        out_trade_no="pay-period-11",
        unionid="union-period",
        event_type="activated",
        duration_days=30,
        before=None,
        after={"start_at": None, "end_at": None},
        payload={},
    )

    assert len(emitted) == 1
    assert emitted[0][0] is connection
    assert emitted[0][1].event_type == "commerce.product_enrolled"
    assert emitted[0][1].payload["order"]["out_trade_no"] == "pay-period-11"
