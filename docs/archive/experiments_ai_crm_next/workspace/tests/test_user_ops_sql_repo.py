from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrm_next.automation.ops_enrollment.dto import BatchSendRequest, UserOpsFilters
from aicrm_next.automation.ops_enrollment.models import (
    user_ops_do_not_disturb_next,
    user_ops_pool_current_next,
    user_ops_send_records_next,
)
from aicrm_next.automation.ops_enrollment.repo import InMemoryUserOpsRepository, SqlAlchemyUserOpsRepository
from aicrm_next.automation.ops_enrollment.user_ops import apply_filters, build_overview_cards, resolve_batch_targets
from aicrm_next.platform.shared.database import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]


def _sql_repo() -> SqlAlchemyUserOpsRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, future=True)
    repo = SqlAlchemyUserOpsRepository(session)
    repo.reset()
    return repo


def _ids(rows: list[dict]) -> set[int]:
    return {int(row["id"]) for row in rows}


def test_user_ops_migration_file_exists() -> None:
    migration = PROJECT_ROOT / "migrations" / "versions" / "0001_user_ops_postgresql_ready.py"
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    assert "user_ops_pool_current_next" in text
    assert "user_ops_do_not_disturb_next" in text
    assert "user_ops_send_records_next" in text


def test_user_ops_sqlalchemy_metadata_includes_tables() -> None:
    assert user_ops_pool_current_next.name in Base.metadata.tables
    assert user_ops_do_not_disturb_next.name in Base.metadata.tables
    assert user_ops_send_records_next.name in Base.metadata.tables


def test_sql_repo_seed_and_list_matches_in_memory_fields() -> None:
    memory_rows = InMemoryUserOpsRepository().list_rows()
    sql_rows = _sql_repo().list_rows()
    assert _ids(sql_rows) == _ids(memory_rows) == {1, 2, 3, 4}
    for key in [
        "external_userid",
        "mobile",
        "owner_userid",
        "is_added_wecom",
        "is_mobile_bound",
        "activation_bucket",
        "do_not_disturb",
        "can_batch_send",
    ]:
        assert sql_rows[0][key] == memory_rows[0][key]


def test_sql_repo_filters_match_in_memory() -> None:
    filters = UserOpsFilters(wecom_status="added", activation_bucket="not_activated", class_term_no="2026-05-B")
    memory_rows = apply_filters(InMemoryUserOpsRepository().list_rows(), filters)
    sql_rows = apply_filters(_sql_repo().list_rows(), filters)
    assert _ids(sql_rows) == _ids(memory_rows) == {4}


def test_sql_repo_overview_cards_match_in_memory_counts() -> None:
    filters = UserOpsFilters(class_term_no="2026-05-A")
    memory_cards = build_overview_cards(apply_filters(InMemoryUserOpsRepository().list_rows(), filters))
    sql_cards = build_overview_cards(apply_filters(_sql_repo().list_rows(), filters))
    assert sql_cards == memory_cards
    assert len(sql_cards) == 8


def test_sql_repo_do_not_disturb_enable_and_cancel() -> None:
    repo = _sql_repo()
    enabled = repo.set_do_not_disturb(
        external_userid="wx_ext_001",
        reason_code="manual_set",
        reason_text="手动暂停",
        is_active=True,
        operator="tester",
    )
    assert enabled is not None
    assert enabled["do_not_disturb"] is True
    assert any(reason["reason_text"] == "手动暂停" for reason in enabled["do_not_disturb_reasons"])

    canceled = repo.set_do_not_disturb(
        external_userid="wx_ext_001",
        reason_code="manual_set",
        reason_text="手动暂停",
        is_active=False,
        operator="tester",
    )
    assert canceled is not None
    assert canceled["do_not_disturb"] is False
    assert canceled["do_not_disturb_reasons"] == []


def test_sql_repo_cancel_manual_dnd_preserves_auto_reason() -> None:
    repo = _sql_repo()
    repo.set_do_not_disturb(
        external_userid="wx_ext_002",
        reason_code="manual_set",
        reason_text="手动暂停",
        is_active=True,
        operator="tester",
    )
    canceled = repo.set_do_not_disturb(
        external_userid="wx_ext_002",
        reason_code="manual_set",
        reason_text="手动暂停",
        is_active=False,
        operator="tester",
    )
    assert canceled is not None
    assert canceled["do_not_disturb"] is True
    assert [reason["source"] for reason in canceled["do_not_disturb_reasons"]] == ["auto"]


def test_sql_repo_preview_skip_reasons_match_in_memory() -> None:
    request = BatchSendRequest(selection_mode="all_filtered", content="hello")
    memory_preview = resolve_batch_targets(InMemoryUserOpsRepository().list_rows(), request)
    sql_preview = resolve_batch_targets(_sql_repo().list_rows(), request)
    assert sql_preview["selected_count"] == memory_preview["selected_count"] == 4
    assert sql_preview["skipped_by_reason"] == memory_preview["skipped_by_reason"]
    assert sql_preview["eligible_count"] == memory_preview["eligible_count"]


def test_sql_repo_send_record_create_list_detail() -> None:
    repo = _sql_repo()
    created = repo.create_send_record(
        {
            "selected_count": 1,
            "eligible_count": 1,
            "sent_count": 1,
            "skipped_count": 0,
            "skipped_reasons": {},
            "include_do_not_disturb": False,
            "content_preview": "hello",
            "image_count": 0,
            "sender_userids": ["ZhaoYanFang"],
            "filter_snapshot": {"wecom_status": "added"},
            "operator": "tester",
            "status": "created",
            "status_label": "已创建任务",
            "task_results": [{"task_id": "fake_wecom_task_001", "status": "created"}],
        }
    )
    listed = repo.list_send_records()
    detail = repo.get_send_record(created["record_id"])
    assert listed[0]["record_id"] == created["record_id"]
    assert detail is not None
    assert detail["task_results"][0]["task_id"] == "fake_wecom_task_001"
    assert repo.get_send_record("missing") is None


def test_sql_repo_source_does_not_import_old_backend_packages() -> None:
    for path in [
        REPO_ROOT / "aicrm_next" / "automation" / "ops_enrollment" / "repo.py",
        REPO_ROOT / "aicrm_next" / "automation" / "ops_enrollment" / "models.py",
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
