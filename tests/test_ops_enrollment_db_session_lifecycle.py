from __future__ import annotations

import pytest

from aicrm_next.ops_enrollment.dto import BroadcastPreviewRequest, UserOpsListRequest


def _row() -> dict:
    return {
        "id": 1,
        "unionid": "union_ops_001",
        "mobile": "13800138000",
        "external_userid": "wx_ext_001",
        "customer_name": "张小蓝",
        "owner_userid": "owner-a",
        "owner_display_name": "顾问甲",
        "class_term_no": "2026-05-A",
        "class_term_label": "2026 五月 A 班",
        "source_type": "lead_pool",
        "created_at": "2026-05-01T09:00:00+08:00",
        "updated_at": "2026-05-18T10:00:00+08:00",
        "activation_bucket": "activated",
        "tags": ["黄小璨"],
        "is_added_wecom": True,
        "is_mobile_bound": True,
        "do_not_disturb": False,
        "do_not_disturb_reasons": [],
    }


class FakeUserOpsRepository:
    def __init__(self, *, fail_list: bool = False, fail_close: bool = False) -> None:
        self.closed = False
        self.fail_list = fail_list
        self.fail_close = fail_close

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")

    def list_rows(self) -> list[dict]:
        if self.fail_list:
            raise RuntimeError("list failed")
        return [_row()]


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_user_ops_repo(monkeypatch):
    from aicrm_next.ops_enrollment import application

    application._REPO = None
    monkeypatch.delenv("USER_OPS_REPO_BACKEND", raising=False)
    yield
    application._REPO = None


def test_default_repo_does_not_cache_sql_repository(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    created: list[FakeUserOpsRepository] = []

    def fake_build_user_ops_repository():
        repo = FakeUserOpsRepository()
        created.append(repo)
        return repo

    monkeypatch.setattr(application, "build_user_ops_repository", fake_build_user_ops_repository)

    first = application._default_repo()
    second = application._default_repo()

    assert first is created[0]
    assert second is created[1]
    assert first is not second
    assert application._REPO is None


def test_fixture_reset_does_not_cache_sql_repository(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")

    def fail_build_user_ops_repository():
        raise AssertionError("sql repository should not be cached by fixture reset")

    monkeypatch.setattr(application, "build_user_ops_repository", fail_build_user_ops_repository)

    application.reset_user_ops_fixture_state()

    assert application._REPO is None


def test_overview_query_closes_internally_created_repo_after_success(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    repo = FakeUserOpsRepository()
    monkeypatch.setattr(application, "build_user_ops_repository", lambda: repo)

    payload = application.GetUserOpsOverviewQuery()(UserOpsListRequest())

    assert payload["ok"] is True
    assert repo.closed is True


def test_overview_query_closes_internal_repo_after_exception_without_masking(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    repo = FakeUserOpsRepository(fail_list=True, fail_close=True)
    monkeypatch.setattr(application, "build_user_ops_repository", lambda: repo)

    with pytest.raises(RuntimeError, match="list failed"):
        application.GetUserOpsOverviewQuery()(UserOpsListRequest())

    assert repo.closed is True


def test_overview_query_does_not_close_injected_repo(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    repo = FakeUserOpsRepository()

    payload = application.GetUserOpsOverviewQuery(repo)(UserOpsListRequest())

    assert payload["ok"] is True
    assert repo.closed is False


def test_broadcast_preview_handler_closes_internally_created_repo(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import application
    from aicrm_next.platform.platform_foundation.command_bus import Command, CommandContext

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    repo = FakeUserOpsRepository()
    monkeypatch.setattr(application, "build_user_ops_repository", lambda: repo)
    request = BroadcastPreviewRequest()
    command = Command(
        command_name="user_ops.broadcast.preview",
        payload={"request": request.model_dump(), "target_id": "broadcast_preview"},
        context=CommandContext(actor_id="tester", actor_type="admin", source_route="/test"),
    )

    payload = application._handle_broadcast_preview(command)

    assert payload["ok"] is True
    assert repo.closed is True


def test_build_user_ops_repository_uses_injected_session(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import repo as repo_module

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    injected_session = FakeSession()

    def fail_get_session_factory(**kwargs):
        raise AssertionError("session factory should not be used for injected session")

    monkeypatch.setattr(repo_module, "get_session_factory", fail_get_session_factory)

    repository = repo_module.build_user_ops_repository(session=injected_session)

    assert repository._session is injected_session


def test_build_user_ops_repository_uses_shared_session_factory(monkeypatch) -> None:
    from aicrm_next.ops_enrollment import repo as repo_module

    monkeypatch.setenv("USER_OPS_REPO_BACKEND", "sqlalchemy")
    session = FakeSession()
    calls: list[object] = []

    def fake_get_session_factory(*, settings):
        calls.append(settings)
        return lambda: session

    monkeypatch.setattr(repo_module, "get_session_factory", fake_get_session_factory)

    repository = repo_module.build_user_ops_repository()

    assert repository._session is session
    assert calls


def test_sqlalchemy_user_ops_repository_close_rolls_back_and_closes_session() -> None:
    from aicrm_next.ops_enrollment.repo import SqlAlchemyUserOpsRepository

    session = FakeSession()

    SqlAlchemyUserOpsRepository(session).close()

    assert session.rolled_back is True
    assert session.closed is True
