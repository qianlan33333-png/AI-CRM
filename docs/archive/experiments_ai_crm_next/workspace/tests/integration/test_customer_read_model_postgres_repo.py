from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from aicrm_next.customer_read_model.application import GetCustomerDetailQuery
from aicrm_next.customer_read_model.dto import CustomerDetailRequest
from aicrm_next.customer_read_model.parity_spec import CUSTOMER_LIST_ITEM_KEYS
from aicrm_next.customer_read_model.projections import list_item_projection
from aicrm_next.customer_read_model.repo import (
    InMemoryCustomerReadModelRepository,
    SqlAlchemyCustomerReadModelRepository,
)
from aicrm_next.platform.shared.errors import NotFoundError

pytestmark = pytest.mark.postgres_integration


def _repo(session: Session) -> SqlAlchemyCustomerReadModelRepository:
    repo = SqlAlchemyCustomerReadModelRepository(session)
    repo.reset()
    return repo


def test_customer_read_model_sql_repo_runs_on_real_postgres(migrated_postgres_engine) -> None:
    session = Session(migrated_postgres_engine, future=True)
    try:
        repo = _repo(session)
        rows = repo.list_customers()
        assert len(rows) == 5
        assert [row["external_userid"] for row in repo.list_customers({"owner_userid": "ZhaoYanFang"}, limit=1)] == ["wx_ext_001"]
        assert [row["external_userid"] for row in repo.list_customers({"tag": "复访"})] == ["wx_ext_004"]
        assert [row["external_userid"] for row in repo.list_customers({"status": "followup"})] == ["wx_ext_004"]
        assert [row["external_userid"] for row in repo.list_customers({"is_bound": "false"})] == ["wx_ext_002"]
        assert [row["external_userid"] for row in repo.list_customers({"mobile": "13700137"})] == ["wx_ext_004"]
        assert len(repo.list_customers({"keyword": "赵艳芳"})) >= 2
        assert repo.list_customers(limit=1, offset=1)[0]["external_userid"] == "wx_ext_002"

        detail = repo.get_customer_detail("wx_ext_001")
        assert detail is not None
        assert detail["external_userid"] == "wx_ext_001"
        assert repo.get_customer_detail("wx_missing") is None
        with pytest.raises(NotFoundError):
            GetCustomerDetailQuery(repo)(CustomerDetailRequest(external_userid="wx_missing"))

        timeline = repo.get_customer_timeline("wx_ext_001")
        assert len(timeline) == 2
        assert repo.get_customer_timeline("wx_ext_001", {"event_type": "tag"})[0]["event_type"] == "tag"
        assert repo.get_customer_timeline("wx_ext_001", limit=1, offset=1)[0]["event_id"] == "evt_002"

        messages = repo.get_recent_messages("wx_ext_001", limit=1)
        assert len(messages) == 1
        assert messages[0]["external_userid"] == "wx_ext_001"

        memory_item = list_item_projection(InMemoryCustomerReadModelRepository().list_customers()[0])
        sql_item = list_item_projection(rows[0])
        assert set(CUSTOMER_LIST_ITEM_KEYS) <= set(memory_item)
        assert set(CUSTOMER_LIST_ITEM_KEYS) <= set(sql_item)
    finally:
        session.close()
