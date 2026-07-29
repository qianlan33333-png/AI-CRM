from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrm_next.customer_read_model.application import (
    GetCustomerDetailQuery,
    GetCustomerTimelineQuery,
    ListCustomersQuery,
    ListRecentMessagesQuery,
)
from aicrm_next.customer_read_model.dto import (
    CustomerDetailRequest,
    CustomerTimelineRequest,
    ListCustomersRequest,
    RecentMessagesRequest,
)
from aicrm_next.customer_read_model.models import (
    customer_detail_snapshot_next,
    customer_list_index_next,
    customer_recent_message_next,
    customer_timeline_event_next,
)
from aicrm_next.customer_read_model.parity_spec import (
    CUSTOMER_DETAIL_KEYS,
    CUSTOMER_LIST_ITEM_KEYS,
    RECENT_MESSAGE_ITEM_KEYS,
    TIMELINE_ITEM_KEYS,
)
from aicrm_next.customer_read_model.projections import detail_projection, list_item_projection
from aicrm_next.customer_read_model.repo import (
    InMemoryCustomerReadModelRepository,
    SqlAlchemyCustomerReadModelRepository,
)
from aicrm_next.platform.shared.database import Base
from aicrm_next.platform.shared.errors import NotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]


def _sql_repo() -> SqlAlchemyCustomerReadModelRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, future=True)
    repo = SqlAlchemyCustomerReadModelRepository(session)
    repo.reset()
    return repo


def test_customer_read_model_migration_file_exists() -> None:
    migration = PROJECT_ROOT / "migrations" / "versions" / "0002_customer_read_model_postgresql_ready.py"
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    for table in [
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_timeline_event_next",
        "customer_recent_message_next",
    ]:
        assert table in text


def test_customer_read_model_metadata_includes_tables() -> None:
    for table in [
        customer_list_index_next,
        customer_detail_snapshot_next,
        customer_timeline_event_next,
        customer_recent_message_next,
    ]:
        assert table.name in Base.metadata.tables


def test_sql_repo_seed_list_and_filters_work() -> None:
    repo = _sql_repo()
    rows = repo.list_customers()
    assert len(rows) == 5
    assert rows[0]["external_userid"] == "wx_ext_001"
    assert [row["external_userid"] for row in repo.list_customers({"tag": "复访"})] == ["wx_ext_004"]
    assert [row["external_userid"] for row in repo.list_customers({"owner_userid": "ZhaoYanFang"}, limit=1, offset=1)] == ["wx_ext_004"]


def test_sql_repo_detail_and_unknown_detail_work() -> None:
    repo = _sql_repo()
    detail = repo.get_customer_detail("wx_ext_001")
    assert detail is not None
    projected = detail_projection(detail)
    for key in CUSTOMER_DETAIL_KEYS:
        assert key in projected
    assert repo.get_customer_detail("wx_missing") is None
    try:
        GetCustomerDetailQuery(repo)(CustomerDetailRequest(external_userid="wx_missing"))
    except NotFoundError:
        pass
    else:
        raise AssertionError("unknown customer should map to application 404")


def test_sql_repo_timeline_filters_and_paging_work() -> None:
    repo = _sql_repo()
    timeline = repo.get_customer_timeline("wx_ext_001")
    assert len(timeline) == 2
    assert repo.get_customer_timeline("wx_ext_001", {"event_type": "tag"})[0]["event_type"] == "tag"
    assert repo.get_customer_timeline("wx_ext_001", limit=1, offset=1)[0]["event_id"] == "evt_002"
    for key in TIMELINE_ITEM_KEYS:
        assert key in timeline[0]


def test_sql_repo_recent_messages_and_limit_work() -> None:
    repo = _sql_repo()
    messages = repo.get_recent_messages("wx_ext_001", limit=1)
    assert len(messages) == 1
    for key in RECENT_MESSAGE_ITEM_KEYS:
        assert key in messages[0]


def test_sql_repo_and_memory_required_shape_match() -> None:
    memory = InMemoryCustomerReadModelRepository()
    sql = _sql_repo()
    memory_item = list_item_projection(memory.list_customers()[0])
    sql_item = list_item_projection(sql.list_customers()[0])
    assert set(CUSTOMER_LIST_ITEM_KEYS) <= set(memory_item)
    assert set(CUSTOMER_LIST_ITEM_KEYS) <= set(sql_item)
    assert {key: type(sql_item[key]) for key in CUSTOMER_LIST_ITEM_KEYS} == {
        key: type(memory_item[key]) for key in CUSTOMER_LIST_ITEM_KEYS
    }


def test_sql_repo_application_contracts_match_memory_shape() -> None:
    repo = _sql_repo()
    list_payload = ListCustomersQuery(repo)(ListCustomersRequest(owner_userid="ZhaoYanFang"))
    detail_payload = GetCustomerDetailQuery(repo)(CustomerDetailRequest(external_userid="wx_ext_001"))
    timeline_payload = GetCustomerTimelineQuery(repo)(CustomerTimelineRequest(external_userid="wx_ext_001", event_type="tag"))
    messages_payload = ListRecentMessagesQuery(repo)(RecentMessagesRequest(external_userid="wx_ext_001", limit=1))
    assert list_payload["ok"] is True and list_payload["items"]
    assert detail_payload["customer"]["external_userid"] == "wx_ext_001"
    assert timeline_payload["timeline"]["items"][0]["event_type"] == "tag"
    assert messages_payload["messages"][0]["external_userid"] == "wx_ext_001"


def test_customer_sql_repo_source_does_not_import_old_backend_packages() -> None:
    for path in [
        REPO_ROOT / "aicrm_next" / "customer_read_model" / "repo.py",
        REPO_ROOT / "aicrm_next" / "customer_read_model" / "models.py",
    ]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.startswith("wecom_ability_service") for name in names)
            assert not any(name.startswith("openclaw_service") for name in names)
