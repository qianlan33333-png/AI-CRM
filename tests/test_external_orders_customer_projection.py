from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from aicrm_next.crm.customer_read_model.backfill import CustomerReadModelBackfillService
from aicrm_next.crm.customer_read_model.repo import FixtureCustomerReadRepository, LiveSourceCustomerReadRepository


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    session = sessionmaker(bind=engine, future=True)()
    for ddl in [
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
            unionid_resolved_at TIMESTAMP,
            first_seen_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            last_polled_at TIMESTAMP,
            next_poll_at TIMESTAMP,
            poll_attempt_count INTEGER,
            last_poll_error TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE automation_channel_contact (
            id INTEGER, channel_id INTEGER, unionid TEXT,
            owner_staff_id TEXT, source_payload_json TEXT, updated_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE wechat_pay_orders (
            id INTEGER, unionid TEXT, status TEXT, trade_state TEXT,
            order_source TEXT, paid_at TIMESTAMP, updated_at TIMESTAMP, created_at TIMESTAMP
        )
        """,
        "CREATE TABLE questionnaire_submissions (id INTEGER, unionid TEXT)",
        "CREATE TABLE contact_tags (unionid TEXT, tag_name TEXT, tag_id TEXT)",
        """
        CREATE TABLE class_user_status_current (
            unionid TEXT, owner_userid_snapshot TEXT, customer_name_snapshot TEXT,
            signup_status TEXT, signup_label_name TEXT,
            status_flags_json TEXT, updated_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE archived_messages (
            id INTEGER, msgid TEXT, chat_type TEXT, unionid TEXT,
            owner_userid TEXT, sender TEXT, receiver TEXT, msgtype TEXT,
            content TEXT, send_time TIMESTAMP, raw_payload TEXT, created_at TIMESTAMP
        )
        """,
        "CREATE TABLE owner_role_map (userid TEXT, display_name TEXT)",
    ]:
        session.execute(text(ddl))
    return session


def _empty_target() -> FixtureCustomerReadRepository:
    target = FixtureCustomerReadRepository()
    target.replace_all(customers=[], timeline_by_external_userid={}, messages_by_external_userid={})
    return target


def _insert_paid_h5_order_with_channel_contact(session, *, external_userid: str = "wm_projection_001", unionid: str = "union_projection_001") -> None:
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO crm_user_identity (
                unionid, openids_json, external_userids_json, mobile,
                mobile_normalized, mobile_verified, mobile_source,
                customer_name, remark, description, avatar, gender, profile_json,
                follow_users_json, legacy_person_id, legacy_identity_map_ids_json,
                legacy_sources_json,
                primary_external_userid, primary_openid, primary_owner_userid,
                identity_status, unionid_resolved_at,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (
                :unionid, '[]', :external_userids_json, '',
                '', 0, 'test', '', '', '', '', NULL, '{}',
                '[]', '', '[]', '{}', :external_userid, '', '',
                'active', :now, :now, :now, :now, :now
            )
            """
        ),
        {"unionid": unionid, "external_userid": external_userid, "external_userids_json": f'["{external_userid}"]', "now": now},
    )
    session.execute(
        text(
            """
            INSERT INTO wechat_pay_orders (
                id, unionid, status, trade_state, order_source, paid_at, updated_at, created_at
            )
            VALUES (156, :unionid, 'paid', 'SUCCESS', 'h5_checkout', :now, :now, :now)
            """
        ),
        {"unionid": unionid, "now": now},
    )
    session.execute(
        text(
            """
            INSERT INTO automation_channel_contact (
                id, channel_id, unionid, owner_staff_id, source_payload_json, updated_at
            )
            VALUES (
                1, 77, :unionid, 'owner_channel_a',
                '{"customer_name":"H5 Paid Customer","remark":"paid via h5 checkout"}',
                :now
            )
            """
        ),
        {"unionid": unionid, "now": now},
    )
    session.commit()


def test_h5_checkout_paid_order_can_resolve_customer_projection_source() -> None:
    session = _session()
    _insert_paid_h5_order_with_channel_contact(session)
    repo = LiveSourceCustomerReadRepository(session)

    customer = repo.get_customer("wm_projection_001")

    assert customer is not None
    assert customer["external_userid"] == "wm_projection_001"
    assert customer["owner_userid"] == "owner_channel_a"
    assert customer["customer_name"] == "H5 Paid Customer"
    assert customer["remark"] == "paid via h5 checkout"
    assert customer["sidebar_context"]["customer_profile_url"] == "/admin/customers/union_projection_001"


def test_channel_contact_linkage_can_feed_customer_read_model_projection() -> None:
    session = _session()
    _insert_paid_h5_order_with_channel_contact(session)
    source = LiveSourceCustomerReadRepository(session)
    target = _empty_target()

    result = CustomerReadModelBackfillService(source=source, target_repo=target).run(
        dry_run=False,
        external_userids=["wm_projection_001"],
    )

    projected = target.get_customer("wm_projection_001")
    assert result.written_customers == 1
    assert projected is not None
    assert projected["owner_userid"] == "owner_channel_a"
    assert projected["customer_name"] == "H5 Paid Customer"


def test_external_order_projection_source_missing_identity_stays_absent() -> None:
    session = _session()
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO wechat_pay_orders (
                id, unionid, status, trade_state, order_source, paid_at, updated_at, created_at
            )
            VALUES (157, '', 'paid', 'SUCCESS', 'h5_checkout', :now, :now, :now)
            """
        ),
        {"now": now},
    )
    session.commit()
    repo = LiveSourceCustomerReadRepository(session)

    assert repo.get_customer("") is None
    assert repo.count_customers({}) == 0
