from __future__ import annotations


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_build_customer_read_model_repository_uses_injected_session(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import repo as repo_module

    monkeypatch.setenv("CUSTOMER_READ_MODEL_REPO_BACKEND", "sqlalchemy")
    injected_session = FakeSession()

    def fail_get_session_factory(**kwargs):
        raise AssertionError("session factory should not be used for injected session")

    monkeypatch.setattr(repo_module, "get_session_factory", fail_get_session_factory)

    repository = repo_module.build_customer_read_model_repository(session=injected_session)

    assert repository._session is injected_session


def test_build_customer_read_model_repository_uses_shared_session_factory(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import repo as repo_module

    monkeypatch.setenv("CUSTOMER_READ_MODEL_REPO_BACKEND", "sqlalchemy")
    session = FakeSession()
    calls: list[object] = []

    def fake_get_session_factory(*, settings):
        calls.append(settings)
        return lambda: session

    monkeypatch.setattr(repo_module, "get_session_factory", fake_get_session_factory)

    repository = repo_module.build_customer_read_model_repository()

    assert repository._session is session
    assert calls


def test_build_customer_live_source_repository_uses_shared_session_factory(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import repo as repo_module

    session = FakeSession()
    calls: list[object] = []

    def fake_get_session_factory(*, settings):
        calls.append(settings)
        return lambda: session

    monkeypatch.setattr(repo_module, "get_session_factory", fake_get_session_factory)

    repository = repo_module.build_customer_live_source_repository()

    assert repository._session is session
    assert calls


def test_customer_sqlalchemy_repository_close_rolls_back_and_closes_session() -> None:
    from aicrm_next.crm.customer_read_model.repo import SqlAlchemyCustomerReadModelRepository

    session = FakeSession()

    SqlAlchemyCustomerReadModelRepository(session).close()

    assert session.rolled_back is True
    assert session.closed is True
