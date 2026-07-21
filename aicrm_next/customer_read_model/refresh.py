from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import time
from typing import Any, Callable

from sqlalchemy import text

from aicrm_next.shared.db_session import get_session_factory

from .repo import (
    CustomerReadRepository,
    build_customer_live_source_repository,
    build_customer_read_model_repository,
)


DEFAULT_MAX_CUSTOMERS = 100_000


@dataclass(frozen=True)
class CustomerReadModelRefreshResult:
    ok: bool
    dry_run: bool
    source_count: int
    target_count_before: int
    target_count_after: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CustomerReadModelRefreshService:
    """Atomically rebuild the list/detail projection from canonical live sources.

    The refresh stores list/detail snapshots plus a bounded recent-message and
    timeline projection. It does not call any external provider and emits only
    count-based diagnostics.
    """

    def __init__(
        self,
        *,
        source_repo: CustomerReadRepository | None = None,
        target_repo: CustomerReadRepository | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._source_repo = source_repo
        self._target_repo = target_repo
        self._session_factory = session_factory or get_session_factory()

    def run(self, *, dry_run: bool = True, max_customers: int | None = None) -> CustomerReadModelRefreshResult:
        started = time.monotonic()
        source = self._source_repo or build_customer_live_source_repository()
        target = self._target_repo or build_customer_read_model_repository()
        owns_source = self._source_repo is None
        owns_target = self._target_repo is None
        bounded_max = max(
            1,
            min(
                int(max_customers or os.getenv("CUSTOMER_READ_MODEL_REFRESH_MAX_CUSTOMERS", DEFAULT_MAX_CUSTOMERS)),
                500_000,
            ),
        )
        try:
            source_count_hint = int(source.count_customers() or 0)
            if source_count_hint <= 0:
                raise RuntimeError("customer_read_model_source_empty")
            if source_count_hint > bounded_max:
                raise RuntimeError("customer_read_model_source_exceeds_safety_limit")

            customers = source.list_customers(limit=min(bounded_max, source_count_hint + 1_000), offset=0)
            unionids = [str(item.get("unionid") or "").strip() for item in customers]
            if not customers or any(not unionid for unionid in unionids):
                raise RuntimeError("customer_read_model_source_contains_empty_unionid")
            if len(unionids) != len(set(unionids)):
                raise RuntimeError("customer_read_model_source_contains_duplicate_unionid")
            if len(customers) > bounded_max:
                raise RuntimeError("customer_read_model_source_exceeds_safety_limit")

            snapshot_loader = getattr(source, "snapshot_recent_messages_by_unionid", None)
            recent_messages_by_unionid = (
                snapshot_loader(unionids, per_customer_limit=100)
                if callable(snapshot_loader)
                else {}
            )
            activity_loader = getattr(source, "snapshot_customer_activity_by_unionid", None)
            activity_by_unionid = (
                activity_loader(unionids, per_customer_limit=500)
                if callable(activity_loader)
                else {}
            )
            messages_by_projection_key: dict[str, list[dict[str, Any]]] = {}
            timeline_by_projection_key: dict[str, list[dict[str, Any]]] = {}
            for customer in customers:
                unionid = str(customer.get("unionid") or "").strip()
                projection_key = str(customer.get("external_userid") or "").strip() or unionid
                messages = list(recent_messages_by_unionid.get(unionid) or [])
                messages_by_projection_key[projection_key] = messages
                message_events = [
                    {
                        "event_id": f"message:{item.get('source_id') or item.get('msgid')}",
                        "event_type": "message",
                        "event_time": item.get("send_time"),
                        "title": f"消息 · {item.get('msgtype') or 'unknown'}",
                        "summary": item.get("content") or "",
                        "source_table": "archived_messages",
                        "source_id": str(item.get("source_id") or ""),
                        "metadata": dict(item),
                    }
                    for item in messages
                ]
                timeline_by_projection_key[projection_key] = [
                    *message_events,
                    *[dict(item) for item in activity_by_unionid.get(unionid) or []],
                ]

            target_count_before = int(target.count_customers() or 0)
            if dry_run:
                return CustomerReadModelRefreshResult(
                    ok=True,
                    dry_run=True,
                    source_count=len(customers),
                    target_count_before=target_count_before,
                    target_count_after=target_count_before,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            replace_all = getattr(target, "replace_all", None)
            if not callable(replace_all):
                raise RuntimeError("customer_read_model_target_replace_all_missing")
            replace_all(
                customers=customers,
                timeline_by_external_userid=timeline_by_projection_key,
                messages_by_external_userid=messages_by_projection_key,
            )
            target_count_after = int(target.count_customers() or 0)
            if target_count_after != len(customers):
                raise RuntimeError("customer_read_model_refresh_count_mismatch")

            duration_ms = int((time.monotonic() - started) * 1000)
            self._record_success(
                source_count=len(customers),
                target_count=target_count_after,
                duration_ms=duration_ms,
            )
            return CustomerReadModelRefreshResult(
                ok=True,
                dry_run=False,
                source_count=len(customers),
                target_count_before=target_count_before,
                target_count_after=target_count_after,
                duration_ms=duration_ms,
            )
        finally:
            if owns_source:
                _close_repo(source)
            if owns_target:
                _close_repo(target)

    def _record_success(self, *, source_count: int, target_count: int, duration_ms: int) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO customer_read_model_refresh_state (
                        singleton_id, last_succeeded_at, source_count, target_count,
                        duration_ms, updated_at
                    ) VALUES (1, CURRENT_TIMESTAMP, :source_count, :target_count, :duration_ms, CURRENT_TIMESTAMP)
                    ON CONFLICT (singleton_id) DO UPDATE SET
                        last_succeeded_at = EXCLUDED.last_succeeded_at,
                        source_count = EXCLUDED.source_count,
                        target_count = EXCLUDED.target_count,
                        duration_ms = EXCLUDED.duration_ms,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "source_count": int(source_count),
                    "target_count": int(target_count),
                    "duration_ms": int(duration_ms),
                },
            )


def _close_repo(repo: CustomerReadRepository) -> None:
    close = getattr(repo, "close", None)
    if callable(close):
        close()
