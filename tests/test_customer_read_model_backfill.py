from __future__ import annotations

from aicrm_next.crm.customer_read_model.backfill import CustomerReadModelBackfillService, FixtureCustomerReadModelSource
from aicrm_next.crm.customer_read_model.repo import FixtureCustomerReadRepository


class ClosableFixtureCustomerReadRepository(FixtureCustomerReadRepository):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _empty_repo() -> FixtureCustomerReadRepository:
    repo = FixtureCustomerReadRepository()
    repo.replace_all(customers=[], timeline_by_external_userid={}, messages_by_external_userid={})
    return repo


def test_customer_read_model_backfill_dry_run_does_not_write_target() -> None:
    target = _empty_repo()

    result = CustomerReadModelBackfillService(source=FixtureCustomerReadModelSource(), target_repo=target).run(dry_run=True, limit=2)

    assert result.dry_run is True
    assert result.source_count == 2
    assert result.written_customers == 0
    assert target.list_customers() == []
    assert result.masked_samples


def test_customer_read_model_backfill_execute_writes_read_model() -> None:
    target = _empty_repo()

    result = CustomerReadModelBackfillService(source=FixtureCustomerReadModelSource(), target_repo=target).run(
        dry_run=False,
        limit=2,
        external_userids=["wx_ext_001"],
    )

    assert result.dry_run is False
    assert result.written_customers == 1
    assert target.get_customer("wx_ext_001") is not None
    assert target.list_timeline("wx_ext_001")
    assert target.list_recent_messages("wx_ext_001")
    assert result.reconciliation["source_count"] == 1
    assert result.reconciliation["target_count"] == 1


def test_customer_read_model_backfill_closes_internally_created_target_repo(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import backfill

    target = ClosableFixtureCustomerReadRepository()
    monkeypatch.setattr(backfill, "build_customer_read_model_repository", lambda: target)

    result = CustomerReadModelBackfillService(source=FixtureCustomerReadModelSource()).run(dry_run=True, limit=1)

    assert result.dry_run is True
    assert target.closed is True


def test_customer_read_model_backfill_does_not_close_injected_target_repo() -> None:
    target = ClosableFixtureCustomerReadRepository()

    result = CustomerReadModelBackfillService(source=FixtureCustomerReadModelSource(), target_repo=target).run(dry_run=True, limit=1)

    assert result.dry_run is True
    assert target.closed is False
