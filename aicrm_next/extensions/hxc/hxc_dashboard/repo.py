from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from aicrm_next.shared.repository_provider import RepositoryProviderError, assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, production_environment, raw_database_url


HXC_BROADCAST_SOURCE_TYPE = "hxc_dashboard_broadcast"


class HxcDashboardBroadcastRepository(Protocol):
    source_status: str

    def preview_audience(
        self,
        *,
        selected_customer_ids: list[str],
        audience_filter: dict[str, Any],
        sender_userid: str,
    ) -> dict[str, Any]: ...

    def get_task_by_key(self, *, source_type: str, source_id: str, idempotency_key: str) -> dict[str, Any] | None: ...

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fixture_rows() -> list[dict[str, Any]]:
    return [
        {
            "customer_id": "union_hxc_001",
            "unionid": "union_hxc_001",
            "external_userid": "ext_hxc_001",
            "owner_userid": "QianLan",
            "funnel_state": "hxc_member",
            "do_not_disturb": False,
        },
        {
            "customer_id": "union_hxc_002",
            "unionid": "union_hxc_002",
            "external_userid": "ext_hxc_002",
            "owner_userid": "QianLan",
            "funnel_state": "hxc_user",
            "do_not_disturb": True,
        },
        {
            "customer_id": "mobile_only_001",
            "external_userid": "",
            "owner_userid": "QianLan",
            "funnel_state": "lead_only",
            "do_not_disturb": False,
        },
    ]


class InMemoryHxcDashboardBroadcastRepository:
    source_status = "fixture_local_contract"

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = deepcopy(rows if rows is not None else _fixture_rows())
        self._tasks: dict[tuple[str, str, str], dict[str, Any]] = {}

    def preview_audience(
        self,
        *,
        selected_customer_ids: list[str],
        audience_filter: dict[str, Any],
        sender_userid: str,
    ) -> dict[str, Any]:
        selected = {str(item or "").strip() for item in selected_customer_ids if str(item or "").strip()}
        rows = []
        for row in self._rows:
            unionid = str(row.get("unionid") or "").strip()
            external_userid = str(row.get("external_userid") or "").strip()
            customer_id = str(row.get("customer_id") or unionid or external_userid or "").strip()
            if selected and unionid not in selected and external_userid not in selected and customer_id not in selected:
                continue
            rows.append(deepcopy(row))
        return _build_audience_preview(rows)

    def get_task_by_key(self, *, source_type: str, source_id: str, idempotency_key: str) -> dict[str, Any] | None:
        key = (source_type, source_id, idempotency_key)
        task = self._tasks.get(key)
        return deepcopy(task) if task else None

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = (
            str(payload.get("source_type") or ""),
            str(payload.get("source_id") or ""),
            str(payload.get("idempotency_key") or ""),
        )
        existing = self._tasks.get(key)
        if existing:
            return deepcopy(existing)
        task = {
            "task_id": f"hxc_fixture_{len(self._tasks) + 1}",
            "status": "created",
            "dispatch_status": "pending_external_dispatch",
            "source_status": self.source_status,
            "created_at": _now_iso(),
            **deepcopy(payload),
        }
        self._tasks[key] = task
        return deepcopy(task)


_FIXTURE_REPO = InMemoryHxcDashboardBroadcastRepository()


def reset_hxc_dashboard_fixture_state() -> None:
    global _FIXTURE_REPO
    _FIXTURE_REPO = InMemoryHxcDashboardBroadcastRepository()


def build_hxc_dashboard_broadcast_repository() -> HxcDashboardBroadcastRepository:
    if production_data_ready():
        database_url = raw_database_url()
        if not database_url:
            raise RepositoryProviderError("HXC 群发生产仓库不可用：DATABASE_URL 未配置")
        from .postgres_repo import PostgresHxcDashboardBroadcastRepository

        return assert_repository_allowed(
            PostgresHxcDashboardBroadcastRepository(database_url),
            capability_owner="hxc_dashboard_broadcast",
        )
    if production_environment():
        raise RepositoryProviderError("HXC 群发生产数据不可用：当前运行时未连接 PostgreSQL")
    return assert_repository_allowed(_FIXTURE_REPO, capability_owner="hxc_dashboard_broadcast")


def connect_hxc_dashboard_broadcast_db(database_url: str) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


def _build_audience_preview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    skipped_by_reason: dict[str, int] = {}
    eligible_external_userids: list[str] = []
    eligible_unionids: list[str] = []
    for row in rows:
        unionid = str(row.get("unionid") or "").strip()
        external_userid = str(row.get("external_userid") or "").strip()
        if not unionid:
            skipped_by_reason["missing_unionid"] = skipped_by_reason.get("missing_unionid", 0) + 1
            continue
        if not external_userid:
            skipped_by_reason["missing_external_userid"] = skipped_by_reason.get("missing_external_userid", 0) + 1
            continue
        if bool(row.get("do_not_disturb")):
            skipped_by_reason["do_not_disturb"] = skipped_by_reason.get("do_not_disturb", 0) + 1
            continue
        if unionid not in eligible_unionids:
            eligible_unionids.append(unionid)
        if external_userid not in eligible_external_userids:
            eligible_external_userids.append(external_userid)
    return {
        "audience_total": len(rows),
        "eligible_count": len(eligible_unionids),
        "skipped_count": sum(skipped_by_reason.values()),
        "skipped_by_reason": skipped_by_reason,
        "eligible_unionids": eligible_unionids,
        "eligible_external_userids": eligible_external_userids,
    }


def new_task_id() -> str:
    return f"hxc_{uuid4().hex}"
