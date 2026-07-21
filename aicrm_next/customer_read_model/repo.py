# ruff: noqa: F401
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Text, bindparam, cast, delete, func, insert, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import SQLAlchemyIdentityResolver, classify_identity_candidates
from aicrm_next.shared.config import Settings, get_settings
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import database_mode
from aicrm_next.shared.typing import JsonDict

from .models import (
    customer_detail_snapshot_next,
    customer_list_index_next,
    customer_recent_message_next,
    customer_timeline_event_next,
)
from .sql_dialect import is_sqlite_session, json_text_expression

_DEFAULT_LIVE_SOURCE_LIST_LIMIT = 200
# Ten binds per row today; leave wide headroom below PostgreSQL's 65,535 limit.
_TIMELINE_UPSERT_BATCH_SIZE = 1_000


class CustomerReadRepository(Protocol):
    def list_customers(
        self,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]: ...

    def count_customers(self, filters: JsonDict | None = None) -> int: ...

    def get_customer_detail(self, external_userid: str) -> JsonDict | None: ...

    def get_customer(self, external_userid: str) -> JsonDict | None: ...

    def get_customer_by_unionid(self, unionid: str) -> JsonDict | None: ...

    def get_customer_timeline(
        self,
        external_userid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]: ...

    def list_timeline(
        self,
        external_userid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]: ...

    def get_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]: ...

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]: ...

    def list_timeline_by_unionid(
        self,
        unionid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]: ...

    def count_timeline_by_unionid(self, unionid: str, filters: JsonDict | None = None) -> int: ...

    def list_recent_messages_by_unionid(self, unionid: str, *, limit: int | None = None) -> list[JsonDict]: ...

    def customer_exists(self, external_userid: str) -> bool: ...

    def customer_exists_by_unionid(self, unionid: str) -> bool: ...




class SqlAlchemyCustomerReadModelRepository:
    """PostgreSQL-ready Customer Read Model repository backed by SQLAlchemy Core tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def close(self) -> None:
        try:
            self._session.rollback()
        finally:
            self._session.close()

    def reset(self) -> None:
        self.clear()
        self.seed_from_fixture()
        self._session.commit()

    def clear(self) -> None:
        self._session.execute(delete(customer_recent_message_next))
        self._session.execute(delete(customer_timeline_event_next))
        self._session.execute(delete(customer_detail_snapshot_next))
        self._session.execute(delete(customer_list_index_next))

    def replace_all(
        self,
        *,
        customers: list[JsonDict],
        timeline_by_external_userid: dict[str, list[JsonDict]] | None = None,
        messages_by_external_userid: dict[str, list[JsonDict]] | None = None,
    ) -> None:
        self._session.execute(delete(customer_recent_message_next))
        self._session.execute(delete(customer_detail_snapshot_next))
        self._session.execute(delete(customer_list_index_next))
        self.seed(
            customers=customers,
            timeline_by_external_userid={},
            messages_by_external_userid=messages_by_external_userid,
        )
        customer_by_key: dict[str, JsonDict] = {}
        for customer in customers:
            unionid = str(customer.get("unionid") or dict(customer.get("identity") or {}).get("unionid") or "").strip()
            external_userid = str(customer.get("external_userid") or "").strip()
            if unionid:
                customer_by_key[unionid] = customer
            if external_userid:
                customer_by_key[external_userid] = customer
        events: list[JsonDict] = []
        for projection_key, rows in (timeline_by_external_userid or {}).items():
            customer = customer_by_key.get(str(projection_key)) or {}
            fallback_unionid = str(customer.get("unionid") or dict(customer.get("identity") or {}).get("unionid") or "").strip()
            for item in rows:
                events.append({**dict(item), "unionid": str(item.get("unionid") or fallback_unionid).strip()})
        self.upsert_timeline_events(events, commit=False)
        self._session.commit()

    def upsert_timeline_events(self, events: list[JsonDict], *, commit: bool = True) -> int:
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in events:
            event_id = str(item.get("event_id") or "").strip()
            unionid = str(item.get("unionid") or "").strip()
            event_type = str(item.get("event_type") or "").strip()
            if not event_id or not unionid or not event_type:
                continue
            deduplicated[event_id] = {
                "event_id": event_id,
                "unionid": unionid,
                "event_type": event_type,
                "event_time": _coerce_datetime(item.get("event_time")),
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "source_table": str(item.get("source_table") or ""),
                "source_id": str(item.get("source_id") or ""),
                "metadata_json": dict(item.get("metadata") or item.get("metadata_json") or {}),
                "created_at": _coerce_datetime(item.get("created_at") or item.get("event_time")),
            }
        rows = list(deduplicated.values())
        if not rows:
            return 0
        dialect = str(self._session.get_bind().dialect.name)
        insert_builder = sqlite_insert if dialect == "sqlite" else postgresql_insert
        for offset in range(0, len(rows), _TIMELINE_UPSERT_BATCH_SIZE):
            batch = rows[offset : offset + _TIMELINE_UPSERT_BATCH_SIZE]
            statement = insert_builder(customer_timeline_event_next).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[customer_timeline_event_next.c.event_id],
                set_={
                    "unionid": statement.excluded.unionid,
                    "event_type": statement.excluded.event_type,
                    "event_time": statement.excluded.event_time,
                    "title": statement.excluded.title,
                    "summary": statement.excluded.summary,
                    "source_table": statement.excluded.source_table,
                    "source_id": statement.excluded.source_id,
                    "metadata_json": statement.excluded.metadata_json,
                },
            )
            self._session.execute(statement)
        if commit:
            self._session.commit()
        return len(rows)

    def seed_from_fixture(self, fixture: FixtureCustomerReadRepository | None = None) -> None:
        fixture = fixture or FixtureCustomerReadRepository()
        self.seed(
            customers=fixture.list_customers(),
            timeline_by_external_userid={row["external_userid"]: fixture.list_timeline(row["external_userid"]) for row in fixture.list_customers() if row.get("external_userid")},
            messages_by_external_userid={row["external_userid"]: fixture.list_recent_messages(row["external_userid"]) for row in fixture.list_customers() if row.get("external_userid")},
        )

    def seed(
        self,
        *,
        customers: list[JsonDict],
        timeline_by_external_userid: dict[str, list[JsonDict]] | None = None,
        messages_by_external_userid: dict[str, list[JsonDict]] | None = None,
    ) -> None:
        timeline_by_external_userid = timeline_by_external_userid or {}
        messages_by_external_userid = messages_by_external_userid or {}
        list_rows: list[dict] = []
        detail_rows: list[dict] = []
        timeline_rows: list[dict] = []
        message_rows: list[dict] = []
        for index, customer in enumerate(customers, start=1):
            external_userid = str(customer.get("external_userid") or "")
            identity = dict(customer.get("identity") or {})
            unionid = str(customer.get("unionid") or identity.get("unionid") or "").strip()
            created_at = _coerce_datetime(customer.get("created_at") or customer.get("updated_at"))
            updated_at = _coerce_datetime(customer.get("updated_at"))
            binding = dict(customer.get("binding") or {})
            projection_key = external_userid or unionid
            list_rows.append(
                {
                    "id": index,
                    "unionid": unionid,
                    "customer_name": customer.get("customer_name") or "",
                    "owner_userid": customer.get("owner_userid") or "",
                    "owner_display_name": customer.get("owner_display_name") or "",
                    "remark": customer.get("remark") or "",
                    "description": customer.get("description") or "",
                    "mobile": customer.get("mobile") or "",
                    "is_bound": bool(binding.get("is_bound")),
                    "binding_status": binding.get("binding_status") or customer.get("binding_status") or "unbound",
                    "tags_json": list(customer.get("tags") or []),
                    "class_user_status_json": dict(customer.get("class_user_status") or {}),
                    "last_message_at": _coerce_optional_datetime(customer.get("last_message_at")),
                    "last_touch_at": _coerce_optional_datetime(customer.get("last_touch_at")),
                    "updated_at": updated_at,
                    "created_at": created_at,
                }
            )
            detail_rows.append(
                {
                    "id": index,
                    "unionid": unionid,
                    "customer_json": dict(customer),
                    "binding_json": dict(customer.get("binding") or {}),
                    "identity_json": dict(customer.get("identity") or {}),
                    "follow_users_json": list(customer.get("follow_users") or []),
                    "marketing_summary_json": dict(customer.get("marketing_summary") or {}),
                    "marketing_profile_json": dict(customer.get("marketing_profile") or {}),
                    "contact_json": dict(customer.get("contact") or {}),
                    "sidebar_context_json": dict(customer.get("sidebar_context") or {}),
                    "updated_at": updated_at,
                    "created_at": created_at,
                }
            )
            for event_index, item in enumerate(timeline_by_external_userid.get(projection_key, []), start=1):
                timeline_rows.append(
                    {
                        "id": index * 1000 + event_index,
                        "event_id": item.get("event_id") or f"evt_{index}_{event_index}",
                        "unionid": str(item.get("unionid") or unionid or "").strip(),
                        "event_type": item.get("event_type") or "",
                        "event_time": _coerce_datetime(item.get("event_time")),
                        "title": item.get("title") or "",
                        "summary": item.get("summary") or "",
                        "source_table": item.get("source_table") or "",
                        "source_id": item.get("source_id") or "",
                        "metadata_json": dict(item.get("metadata") or {}),
                        "created_at": created_at,
                    }
                )
            for message_index, item in enumerate(messages_by_external_userid.get(projection_key, []), start=1):
                metadata = {key: value for key, value in item.items() if key not in {"msgid", "external_userid", "msgtype", "content", "send_time", "owner_userid", "chat_type"}}
                message_rows.append(
                    {
                        "id": index * 1000 + message_index,
                        "msgid": item.get("msgid") or f"msg_{index}_{message_index}",
                        "unionid": str(item.get("unionid") or unionid or "").strip(),
                        "msgtype": item.get("msgtype") or "text",
                        "content": item.get("content") or "",
                        "send_time": _coerce_datetime(item.get("send_time")),
                        "owner_userid": item.get("owner_userid") or "",
                        "chat_type": item.get("chat_type") or "single",
                        "metadata_json": metadata,
                        "created_at": created_at,
                    }
                )
        if list_rows:
            self._session.execute(insert(customer_list_index_next), list_rows)
        if detail_rows:
            self._session.execute(insert(customer_detail_snapshot_next), detail_rows)
        if timeline_rows:
            self._session.execute(insert(customer_timeline_event_next), timeline_rows)
        if message_rows:
            self._session.execute(insert(customer_recent_message_next), message_rows)

    def list_customers(
        self,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        stmt = self._customer_list_stmt(filters or {})
        stmt = stmt.order_by(customer_list_index_next.c.id.asc())
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit))).offset(max(0, int(offset or 0)))
        elif offset:
            stmt = stmt.offset(max(0, int(offset or 0)))
        rows = self._session.execute(stmt).mappings()
        customers = [self._list_row_to_customer(row) for row in rows]
        return customers

    def count_customers(self, filters: JsonDict | None = None) -> int:
        stmt = select(func.count()).select_from(self._customer_list_stmt(filters or {}).subquery())
        return int(self._session.execute(stmt).scalar_one() or 0)

    def get_customer(self, external_userid: str) -> JsonDict | None:
        for row in self._session.execute(select(customer_detail_snapshot_next)).mappings():
            customer = self._detail_row_to_customer(row)
            if str(customer.get("external_userid") or "") == external_userid:
                return customer
        return None

    get_customer_detail = get_customer

    def get_customer_by_unionid(self, unionid: str) -> JsonDict | None:
        row = self._session.execute(
            select(customer_detail_snapshot_next)
            .where(customer_detail_snapshot_next.c.unionid == unionid)
            .limit(1)
        ).mappings().first()
        if not row:
            return None
        return self._detail_row_to_customer(row)

    def _detail_row_to_customer(self, row) -> JsonDict:
        customer = dict(row["customer_json"] or {})
        customer.update(
            {
                "unionid": str(row["unionid"] or customer.get("unionid") or ""),
                "binding": dict(row["binding_json"] or {}),
                "identity": dict(row["identity_json"] or {}),
                "follow_users": list(row["follow_users_json"] or []),
                "marketing_summary": dict(row["marketing_summary_json"] or {}),
                "marketing_profile": dict(row["marketing_profile_json"] or {}),
                "contact": dict(row["contact_json"] or {}),
                "sidebar_context": dict(row["sidebar_context_json"] or {}),
                "updated_at": _iso(customer.get("updated_at") or row["updated_at"]),
            }
        )
        identity = dict(customer.get("identity") or {})
        identity.setdefault("unionid", str(row["unionid"] or ""))
        customer["identity"] = identity
        return customer

    def list_timeline(
        self,
        external_userid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        customer = self.get_customer(external_userid)
        unionid = str((customer or {}).get("unionid") or "")
        return self.list_timeline_by_unionid(unionid, filters, limit=limit, offset=offset) if unionid else []

    get_customer_timeline = list_timeline

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        customer = self.get_customer(external_userid)
        unionid = str((customer or {}).get("unionid") or "")
        return self.list_recent_messages_by_unionid(unionid, limit=limit) if unionid else []

    get_recent_messages = list_recent_messages

    def list_timeline_by_unionid(
        self,
        unionid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        stmt = select(customer_timeline_event_next).where(customer_timeline_event_next.c.unionid == unionid)
        event_type = str((filters or {}).get("event_type") or "").strip()
        event_types = [str(value).strip() for value in (filters or {}).get("event_types") or [] if str(value).strip()]
        if event_type:
            stmt = stmt.where(customer_timeline_event_next.c.event_type == event_type)
        elif event_types:
            stmt = stmt.where(customer_timeline_event_next.c.event_type.in_(event_types))
        stmt = stmt.order_by(customer_timeline_event_next.c.event_time.desc(), customer_timeline_event_next.c.id.desc())
        rows = [self._timeline_row_to_dict(row) for row in self._session.execute(stmt).mappings()]
        return _apply_page(rows, limit=limit, offset=offset)

    def count_timeline_by_unionid(self, unionid: str, filters: JsonDict | None = None) -> int:
        stmt = select(func.count()).select_from(customer_timeline_event_next).where(customer_timeline_event_next.c.unionid == unionid)
        event_type = str((filters or {}).get("event_type") or "").strip()
        event_types = [str(value).strip() for value in (filters or {}).get("event_types") or [] if str(value).strip()]
        if event_type:
            stmt = stmt.where(customer_timeline_event_next.c.event_type == event_type)
        elif event_types:
            stmt = stmt.where(customer_timeline_event_next.c.event_type.in_(event_types))
        return int(self._session.execute(stmt).scalar_one() or 0)

    def list_recent_messages_by_unionid(self, unionid: str, *, limit: int | None = None) -> list[JsonDict]:
        stmt = (
            select(customer_recent_message_next)
            .where(customer_recent_message_next.c.unionid == unionid)
            .order_by(customer_recent_message_next.c.send_time.desc(), customer_recent_message_next.c.id.asc())
        )
        rows = [self._message_row_to_dict(row) for row in self._session.execute(stmt).mappings()]
        return _apply_page(rows, limit=limit, offset=0)

    def customer_exists(self, external_userid: str) -> bool:
        return self.get_customer(external_userid) is not None

    def customer_exists_by_unionid(self, unionid: str) -> bool:
        return self.get_customer_by_unionid(unionid) is not None

    def _customer_list_stmt(self, filters: JsonDict):
        stmt = select(customer_list_index_next)
        table = customer_list_index_next.c
        owner_userid = str(filters.get("owner_userid") or "").strip()
        if owner_userid:
            stmt = stmt.where(table.owner_userid == owner_userid)
        tag = str(filters.get("tag") or "").strip()
        if tag:
            escaped_tag = json.dumps(tag, ensure_ascii=True)[1:-1]
            tag_patterns = [f"%{tag}%"]
            if escaped_tag != tag:
                tag_patterns.append(f"%{escaped_tag}%")
            stmt = stmt.where(or_(*(cast(table.tags_json, Text).like(pattern) for pattern in tag_patterns)))
        status = str(filters.get("status") or "").strip()
        if status:
            stmt = stmt.where(
                or_(
                    table.binding_status == status,
                    cast(table.class_user_status_json, Text).like(f"%{status}%"),
                )
            )
        is_bound = _normalize_bool_filter(filters.get("is_bound"))
        if is_bound is not None:
            stmt = stmt.where(table.is_bound.is_(is_bound))
        mobile = str(filters.get("mobile") or "").strip()
        if mobile:
            stmt = stmt.where(table.mobile.like(f"%{mobile}%"))
        keyword = str(filters.get("keyword") or "").strip().lower()
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    func.lower(table.unionid).like(pattern),
                    func.lower(table.customer_name).like(pattern),
                    func.lower(table.owner_userid).like(pattern),
                    func.lower(table.owner_display_name).like(pattern),
                    func.lower(table.remark).like(pattern),
                    func.lower(table.description).like(pattern),
                    func.lower(table.mobile).like(pattern),
                    func.lower(table.binding_status).like(pattern),
                    func.lower(cast(table.tags_json, Text)).like(pattern),
                    func.lower(cast(table.class_user_status_json, Text)).like(pattern),
                )
            )
        return stmt

    def _list_row_to_customer(self, row) -> JsonDict:
        data = dict(row)
        return {
            "unionid": data.get("unionid") or "",
            "person_id": "",
            "external_userid": "",
            "customer_name": data.get("customer_name") or "",
            "remark": data.get("remark") or "",
            "description": data.get("description") or "",
            "owner_userid": data.get("owner_userid") or "",
            "owner_display_name": data.get("owner_display_name") or "",
            "mobile": data.get("mobile") or None,
            "tags": list(data.get("tags_json") or []),
            "class_user_status": dict(data.get("class_user_status_json") or {}),
            "last_message_at": _iso(data.get("last_message_at")),
            "last_touch_at": _iso(data.get("last_touch_at")),
            "updated_at": _iso(data.get("updated_at")),
            "created_at": _iso(data.get("created_at")),
            "binding": {
                "is_bound": bool(data.get("is_bound")),
                "mobile": data.get("mobile") or None,
                "binding_status": data.get("binding_status") or "unbound",
            },
        }

    def _timeline_row_to_dict(self, row) -> JsonDict:
        data = dict(row)
        return {
            "event_id": data.get("event_id") or "",
            "event_type": data.get("event_type") or "",
            "event_time": _iso(data.get("event_time")),
            "title": data.get("title") or "",
            "summary": data.get("summary") or "",
            "source_table": data.get("source_table") or "",
            "source_id": data.get("source_id") or "",
            "metadata": dict(data.get("metadata_json") or {}),
        }

    def _message_row_to_dict(self, row) -> JsonDict:
        data = dict(row)
        payload = {
            "msgid": data.get("msgid") or "",
            "msgtype": data.get("msgtype") or "text",
            "content": data.get("content") or "",
            "send_time": _iso(data.get("send_time")),
            "unionid": data.get("unionid") or "",
            "owner_userid": data.get("owner_userid") or "",
            "chat_type": data.get("chat_type") or "single",
        }
        payload.update(dict(data.get("metadata_json") or {}))
        return payload




def _apply_customer_filters(rows: list[JsonDict], filters: JsonDict) -> list[JsonDict]:
    owner_userid = str(filters.get("owner_userid") or "").strip()
    tag = str(filters.get("tag") or "").strip()
    status = str(filters.get("status") or "").strip()
    mobile = str(filters.get("mobile") or "").strip()
    keyword = str(filters.get("keyword") or "").strip()
    is_bound = _normalize_bool_filter(filters.get("is_bound"))
    if owner_userid:
        rows = [item for item in rows if item.get("owner_userid") == owner_userid]
    if tag:
        rows = [item for item in rows if tag in item.get("tags", [])]
    if status:
        rows = [
            item
            for item in rows
            if status
            in {
                str(item.get("class_user_status", {}).get("current_status") or ""),
                str(item.get("class_user_status", {}).get("signup_status") or ""),
                str(item.get("class_user_status", {}).get("activation_bucket") or ""),
                str(item.get("binding", {}).get("binding_status") or ""),
                str(item.get("binding_status") or ""),
            }
        ]
    if is_bound is not None:
        rows = [item for item in rows if bool(item.get("binding", {}).get("is_bound", item.get("is_bound"))) is is_bound]
    if mobile:
        rows = [item for item in rows if mobile in str(item.get("mobile") or "")]
    if keyword:
        rows = [
            item
            for item in rows
            if keyword in str(item.get("customer_name") or "")
            or keyword in str(item.get("external_userid") or "")
            or keyword in str(item.get("mobile") or "")
            or keyword in str(item.get("owner_userid") or "")
            or keyword in str(item.get("owner_display_name") or "")
        ]
    return rows


def _apply_page(rows: list[JsonDict], *, limit: int | None, offset: int = 0) -> list[JsonDict]:
    if limit is None:
        return rows[offset:] if offset else rows
    return rows[offset : offset + limit]


def _normalize_bool_filter(value: object) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "all"}:
        return None
    if normalized in {"1", "true", "yes", "y", "on", "bound"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "unbound"}:
        return False
    return None


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        normalized = str(value).strip()
        if len(normalized) >= 3 and normalized[-3] in {"+", "-"} and normalized[-2:].isdigit():
            normalized += ":00"
        fractional = re.fullmatch(
            r"(?P<prefix>.+:\d{2})\.(?P<fraction>\d+)(?P<offset>Z|[+-]\d{2}:\d{2})?",
            normalized,
        )
        if fractional:
            fraction = (fractional.group("fraction") + "000000")[:6]
            offset = fractional.group("offset") or ""
            normalized = f"{fractional.group('prefix')}.{fraction}{offset}"
        return datetime.fromisoformat(normalized)
    return datetime.now(timezone.utc)


def _coerce_optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return _coerce_datetime(value)


def _iso(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_dict(value: object) -> JsonDict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[JsonDict]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except Exception:
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


from .repo_fixture import FixtureCustomerReadRepository
from .repo_live_source import LiveSourceCustomerReadRepository

def build_customer_live_source_repository(
    settings: Settings | None = None,
    *,
    session: Session | None = None,
    engine: Engine | None = None,
) -> CustomerReadRepository:
    settings = settings or get_settings()
    if session is not None:
        return assert_repository_allowed(
            LiveSourceCustomerReadRepository(session),
            capability_owner="customer_read_model",
        )
    owned_session = get_session_factory(settings=settings)() if engine is None else Session(bind=engine, future=True)
    return assert_repository_allowed(
        LiveSourceCustomerReadRepository(owned_session),
        capability_owner="customer_read_model",
    )


def build_customer_read_model_repository(
    settings: Settings | None = None,
    *,
    session: Session | None = None,
    engine: Engine | None = None,
) -> CustomerReadRepository:
    settings = settings or get_settings()
    configured_backend = str(os.getenv("CUSTOMER_READ_MODEL_REPO_BACKEND", "") or "").strip().lower()
    backend = configured_backend or settings.customer_read_model_repo_backend.strip().lower()
    if not configured_backend and database_mode() == "postgres":
        backend = "sqlalchemy"
    if backend in {"sql", "sqlalchemy", "postgres", "postgresql"}:
        if session is not None:
            return assert_repository_allowed(
                SqlAlchemyCustomerReadModelRepository(session),
                capability_owner="customer_read_model",
            )
        owned_session = get_session_factory(settings=settings)() if engine is None else Session(bind=engine, future=True)
        return assert_repository_allowed(
            SqlAlchemyCustomerReadModelRepository(owned_session),
            capability_owner="customer_read_model",
        )
    return assert_repository_allowed(InMemoryCustomerReadModelRepository(), capability_owner="customer_read_model")


InMemoryCustomerReadModelRepository = FixtureCustomerReadRepository
