from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from aicrm_next.automation.ops_enrollment.dto import BatchSendRequest, UserOpsFilters
from aicrm_next.automation.ops_enrollment.repo import SqlAlchemyUserOpsRepository
from aicrm_next.automation.ops_enrollment.user_ops import apply_filters, build_overview_cards, resolve_batch_targets

pytestmark = pytest.mark.postgres_integration


def _repo(session: Session) -> SqlAlchemyUserOpsRepository:
    repo = SqlAlchemyUserOpsRepository(session)
    repo.reset()
    return repo


def test_user_ops_sql_repo_runs_on_real_postgres(migrated_postgres_engine) -> None:
    session = Session(migrated_postgres_engine, future=True)
    try:
        repo = _repo(session)
        rows = repo.list_rows()
        assert len(rows) == 4
        assert apply_filters(rows, UserOpsFilters(wecom_status="added"))
        assert len(build_overview_cards(rows)) == 8

        enabled = repo.set_do_not_disturb(
            external_userid="wx_ext_001",
            reason_code="manual_set",
            reason_text="手动暂停",
            is_active=True,
            operator="postgres-test",
        )
        assert enabled is not None
        assert enabled["do_not_disturb"] is True

        canceled = repo.set_do_not_disturb(
            external_userid="wx_ext_001",
            reason_code="manual_set",
            reason_text="手动暂停",
            is_active=False,
            operator="postgres-test",
        )
        assert canceled is not None
        assert canceled["do_not_disturb"] is False

        preview = resolve_batch_targets(repo.list_rows(), BatchSendRequest(selection_mode="all_filtered", content="hello"))
        assert "missing_external_userid" in preview["skipped_by_reason"]
        assert preview["eligible_count"] >= 1

        created = repo.create_send_record(
            {
                "selected_count": preview["selected_count"],
                "eligible_count": preview["eligible_count"],
                "sent_count": preview["eligible_count"],
                "skipped_count": preview["skipped_count"],
                "skipped_reasons": preview["skipped_by_reason"],
                "include_do_not_disturb": False,
                "content_preview": "hello",
                "image_count": 0,
                "sender_userids": ["ZhaoYanFang"],
                "filter_snapshot": {},
                "operator": "postgres-test",
                "status": "created",
                "status_label": "已创建任务",
                "task_results": [{"task_id": "fake_wecom_task_pg_001", "status": "created"}],
            }
        )
        assert repo.list_send_records()[0]["record_id"] == created["record_id"]
        assert repo.get_send_record(created["record_id"]) is not None
        assert repo.get_send_record("missing") is None
    finally:
        session.close()
