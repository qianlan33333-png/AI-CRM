from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from aicrm_next.shared.typing import JsonDict
from aicrm_next.shared.safe_logging import safe_log_exception

from .repo import CustomerReadRepository, FixtureCustomerReadRepository, build_customer_read_model_repository
from .reconciliation import CustomerReadModelReconciliationRun, reconcile_customer_read_model

LOGGER = logging.getLogger(__name__)


class CustomerReadModelSource(Protocol):
    source_name: str

    def list_customers(self, *, limit: int | None = None, external_userids: set[str] | None = None) -> list[JsonDict]: ...

    def get_customer_detail(self, external_userid: str) -> JsonDict | None: ...

    def list_timeline(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]: ...

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]: ...


class FixtureCustomerReadModelSource:
    source_name = "fixture"

    def __init__(self, repo: FixtureCustomerReadRepository | None = None) -> None:
        self._repo = repo or FixtureCustomerReadRepository()

    def list_customers(self, *, limit: int | None = None, external_userids: set[str] | None = None) -> list[JsonDict]:
        rows = self._repo.list_customers(limit=limit, offset=0)
        if external_userids:
            rows = [item for item in rows if str(item.get("external_userid") or "") in external_userids]
        return rows

    def get_customer_detail(self, external_userid: str) -> JsonDict | None:
        return self._repo.get_customer(external_userid)

    def list_timeline(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        return self._repo.list_timeline(external_userid, limit=limit, offset=0)

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        return self._repo.list_recent_messages(external_userid, limit=limit)


class JsonFileCustomerReadModelSource:
    source_name = "file_json"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            payload = {"customers": payload}
        self._customers = list(payload.get("customers") or [])
        self._details = {
            str(item.get("external_userid") or ""): item
            for item in list(payload.get("customer_details") or []) + self._customers
            if str(item.get("external_userid") or "").strip()
        }
        self._timeline = dict(payload.get("timeline_by_external_userid") or {})
        self._messages = dict(payload.get("messages_by_external_userid") or {})

    def list_customers(self, *, limit: int | None = None, external_userids: set[str] | None = None) -> list[JsonDict]:
        rows = list(self._customers)
        if external_userids:
            rows = [item for item in rows if str(item.get("external_userid") or "") in external_userids]
        return rows[:limit] if limit is not None else rows

    def get_customer_detail(self, external_userid: str) -> JsonDict | None:
        return self._details.get(external_userid)

    def list_timeline(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        rows = list(self._timeline.get(external_userid) or [])
        return rows[:limit] if limit is not None else rows

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        rows = list(self._messages.get(external_userid) or [])
        return rows[:limit] if limit is not None else rows


@dataclass(frozen=True)
class CustomerReadModelBackfillResult:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    source_name: str = ""
    dry_run: bool = True
    source_count: int = 0
    target_count: int = 0
    written_customers: int = 0
    written_timeline_events: int = 0
    written_recent_messages: int = 0
    reconciliation: JsonDict = field(default_factory=dict)
    masked_samples: list[JsonDict] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return asdict(self)


def _masked_samples(customers: list[JsonDict], *, limit: int = 3) -> list[JsonDict]:
    samples: list[JsonDict] = []
    for item in customers[:limit]:
        external_userid = str(item.get("external_userid") or "")
        mobile = str(item.get("mobile") or "")
        samples.append(
            {
                "external_userid": f"{external_userid[:2]}***{external_userid[-2:]}" if external_userid else "",
                "mobile": f"{mobile[:3]}****{mobile[-2:]}" if mobile else "",
                "owner_userid": str(item.get("owner_userid") or ""),
            }
        )
    return samples


class CustomerReadModelBackfillService:
    def __init__(
        self,
        *,
        source: CustomerReadModelSource | None = None,
        target_repo: CustomerReadRepository | None = None,
    ) -> None:
        self._source = source or FixtureCustomerReadModelSource()
        self._owns_target_repo = target_repo is None
        self._target_repo = target_repo or build_customer_read_model_repository()

    def _close_owned_target_repo(self) -> None:
        if not self._owns_target_repo:
            return
        close = getattr(self._target_repo, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "failed to close customer read model backfill target repository",
                exc,
                level=logging.WARNING,
            )

    def run(
        self,
        *,
        dry_run: bool = True,
        limit: int | None = None,
        external_userids: list[str] | None = None,
    ) -> CustomerReadModelBackfillResult:
        try:
            allowlist = {str(item).strip() for item in (external_userids or []) if str(item).strip()} or None
            customers = self._source.list_customers(limit=limit, external_userids=allowlist)
            detailed_customers: list[JsonDict] = []
            timeline_by_external_userid: dict[str, list[JsonDict]] = {}
            messages_by_external_userid: dict[str, list[JsonDict]] = {}
            for customer in customers:
                external_userid = str(customer.get("external_userid") or "").strip()
                if not external_userid:
                    continue
                detail = self._source.get_customer_detail(external_userid) or customer
                detailed_customers.append(detail)
                timeline_by_external_userid[external_userid] = self._source.list_timeline(external_userid, limit=limit)
                messages_by_external_userid[external_userid] = self._source.list_recent_messages(external_userid, limit=limit)

            reconciliation: CustomerReadModelReconciliationRun
            if dry_run:
                reconciliation = CustomerReadModelReconciliationRun(
                    source_count=len(detailed_customers),
                    target_count=len(self._target_repo.list_customers(limit=None, offset=0)),
                    diff_count=0,
                    status="dry_run",
                )
                written_customers = 0
            else:
                replace_all = getattr(self._target_repo, "replace_all", None)
                if not callable(replace_all):
                    raise RuntimeError("target repository does not support replace_all")
                replace_all(
                    customers=detailed_customers,
                    timeline_by_external_userid=timeline_by_external_userid,
                    messages_by_external_userid=messages_by_external_userid,
                )
                reconciliation = reconcile_customer_read_model(source_customers=detailed_customers, target_repo=self._target_repo)
                written_customers = len(detailed_customers)

            return CustomerReadModelBackfillResult(
                source_name=self._source.source_name,
                dry_run=dry_run,
                source_count=len(detailed_customers),
                target_count=len(self._target_repo.list_customers(limit=None, offset=0)),
                written_customers=written_customers,
                written_timeline_events=0 if dry_run else sum(len(items) for items in timeline_by_external_userid.values()),
                written_recent_messages=0 if dry_run else sum(len(items) for items in messages_by_external_userid.values()),
                reconciliation=reconciliation.to_dict(),
                masked_samples=_masked_samples(detailed_customers),
            )
        finally:
            self._close_owned_target_repo()
