# ruff: noqa: F401
from __future__ import annotations

import logging
from collections.abc import Callable

from aicrm_next.platform.shared.errors import NotFoundError
from aicrm_next.platform.shared.safe_logging import safe_log_exception
from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool
from aicrm_next.platform.shared.typing import JsonDict
from aicrm_next.integration_ports import (
    build_archive_sync_adapter,
    build_contacts_sync_adapter,
    build_customer_projection_sync_gateway,
    customer_sync_side_effect_safety,
)

from .dto import (
    CustomerContextRequest,
    CustomerDetailRequest,
    CustomerTimelineRequest,
    ListCustomersRequest,
    RecentMessagesRequest,
)
from .customer_resolution import get_customer_by_request, resolve_customer_request
from .errors import CustomerScopeForbiddenError
from .projections import detail_projection, list_item_projection
from .repo import CustomerReadRepository, build_customer_live_source_repository, build_customer_read_model_repository

LOGGER = logging.getLogger(__name__)
RUNTIME_SETTING_KEYS = frozenset(
    {
        "CUSTOMER_READ_MODEL_NEXT_PRIMARY",
        "CUSTOMER_READ_MODEL_LIVE_SOURCE_FALLBACK_ENABLED",
    }
)


def _env_flag(name: str, *, default: bool = False) -> bool:
    return managed_runtime_bool(name, default)


def _customer_read_model_next_primary_enabled() -> bool:
    return _env_flag("CUSTOMER_READ_MODEL_NEXT_PRIMARY", default=True)


def _customer_read_model_live_source_fallback_enabled() -> bool:
    return _env_flag("CUSTOMER_READ_MODEL_LIVE_SOURCE_FALLBACK_ENABLED", default=True)


def _production_customer_data_required() -> bool:
    from aicrm_next.platform.shared.runtime import production_data_ready

    return production_data_ready()


def _production_error_message(exc: Exception) -> str:
    return str(exc).replace(
        "production/postgres/legacy facade data",
        "production/postgres read model data",
    )


def _close_repository(repo: CustomerReadRepository | None) -> None:
    if repo is None:
        return
    close = getattr(repo, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            safe_log_exception(LOGGER, "failed to close customer read repository", exc, level=logging.WARNING)
        return

    session = getattr(repo, "session", None) or getattr(repo, "_session", None)
    if session is None:
        return
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception as exc:
            safe_log_exception(LOGGER, "failed to rollback customer read repository session", exc, level=logging.WARNING)
    session_close = getattr(session, "close", None)
    if callable(session_close):
        try:
            session_close()
        except Exception as exc:
            safe_log_exception(LOGGER, "failed to close customer read repository session", exc, level=logging.WARNING)


def _read_model_primary_failed(exc: Exception, repo: CustomerReadRepository | None) -> None:
    _close_repository(repo)
    safe_log_exception(
        LOGGER,
        "customer read model primary failed; switching to live source fallback",
        exc,
        level=logging.WARNING,
    )


def _diagnostics(
    *,
    source_status: str,
    read_model_status: str,
    degraded: bool = False,
    fallback_used: bool = False,
    fallback_reason: str = "",
) -> JsonDict:
    payload: JsonDict = {
        "source_status": source_status,
        "read_model_status": read_model_status,
        "route_owner": "ai_crm_next",
        "degraded": degraded,
        "fallback_used": fallback_used,
    }
    if fallback_used or fallback_reason:
        payload["fallback_reason"] = fallback_reason
    return payload


def _list_customers_unavailable_payload(query: ListCustomersRequest, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "customers": [],
        "items": [],
        "count": 0,
        "total": 0,
        "has_more": False,
        "total_source": "unavailable",
        "projection_watermark": {},
        "limit": query.limit,
        "offset": query.offset,
        "filters": {
            "owner_userid": query.owner_userid or "",
            "tag": query.tag or "",
            "status": query.status or "",
            "is_bound": query.is_bound or "",
            "mobile": query.mobile or "",
            "keyword": query.keyword or "",
            "limit": str(query.limit),
            "offset": str(query.offset),
        },
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "error_code": "customer_list_read_unavailable",
        "page_error": _production_error_message(exc),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 503,
    }


def _customer_detail_unavailable_payload(external_userid: str, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "customer": {},
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "error_code": "customer_detail_read_unavailable",
        "page_error": _production_error_message(exc),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 503,
        "external_userid": external_userid,
    }


def _customer_identity_key(query: CustomerDetailRequest | CustomerTimelineRequest | RecentMessagesRequest) -> str:
    return str(getattr(query, "unionid", None) or getattr(query, "external_userid", None) or "").strip()


def _customer_owner_candidates(customer: JsonDict) -> set[str]:
    candidates: set[str] = set()
    identity = customer.get("identity")
    if isinstance(identity, dict):
        candidates.update(
            str(identity.get(key) or "").strip()
            for key in ("follow_user_userid", "primary_owner_userid")
        )
    follow_users = customer.get("follow_users")
    if isinstance(follow_users, list):
        for item in follow_users:
            if isinstance(item, dict):
                candidates.update(str(item.get(key) or "").strip() for key in ("userid", "user_id", "owner_userid"))
    return {item for item in candidates if item}


def _assert_customer_owner_scope(customer: JsonDict, owner_userid: str | None, *, require_owner: bool = False, owner_verified: bool = False) -> None:
    requested_owner = str(owner_userid or "").strip()
    candidates = _customer_owner_candidates(customer)
    if requested_owner and requested_owner in candidates:
        return
    if not requested_owner and not require_owner:
        return
    raise CustomerScopeForbiddenError("customer scope forbidden")


def _repo_customer_exists_by_request(repo: CustomerReadRepository, query: CustomerTimelineRequest | RecentMessagesRequest) -> bool:
    unionid = str(query.unionid or "").strip()
    if unionid:
        exists = getattr(repo, "customer_exists_by_unionid", None)
        return bool(exists(unionid)) if callable(exists) else False
    external_userid = str(query.external_userid or "").strip()
    return repo.customer_exists(external_userid) if external_userid else False


def _repo_list_timeline_by_request(repo: CustomerReadRepository, query: CustomerTimelineRequest, *, limit: int | None = None, offset: int = 0) -> list[JsonDict]:
    unionid = str(query.unionid or "").strip()
    filters = {"event_type": query.event_type or ""}
    if unionid:
        list_by_unionid = getattr(repo, "list_timeline_by_unionid", None)
        return list_by_unionid(unionid, filters, limit=limit, offset=offset) if callable(list_by_unionid) else []
    external_userid = str(query.external_userid or "").strip()
    return repo.list_timeline(external_userid, filters, limit=limit, offset=offset) if external_userid else []


def _repo_list_recent_messages_by_request(repo: CustomerReadRepository, query: RecentMessagesRequest) -> list[JsonDict]:
    unionid = str(query.unionid or "").strip()
    if unionid:
        list_by_unionid = getattr(repo, "list_recent_messages_by_unionid", None)
        return list_by_unionid(unionid, limit=query.limit) if callable(list_by_unionid) else []
    external_userid = str(query.external_userid or "").strip()
    return repo.list_recent_messages(external_userid, limit=query.limit) if external_userid else []


def _customer_timeline_unavailable_payload(query: CustomerTimelineRequest, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "timeline": {
            "external_userid": query.external_userid,
            "items": [],
            "count": 0,
            "limit": query.limit,
            "offset": query.offset,
            "filters": {
                "event_type": query.event_type or "",
                "limit": str(query.limit),
                "offset": str(query.offset),
            },
            "total": 0,
        },
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "error_code": "customer_timeline_read_unavailable",
        "page_error": _production_error_message(exc),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 503,
    }


def _recent_messages_unavailable_payload(query: RecentMessagesRequest, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "messages": [],
        "items": [],
        "count": 0,
        "external_userid": query.external_userid,
        "limit": query.limit,
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "error_code": "recent_messages_read_unavailable",
        "page_error": _production_error_message(exc),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 503,
    }


def _list_filters(query: ListCustomersRequest) -> JsonDict:
    return {
        "owner_userid": query.owner_userid or "",
        "tag": query.tag or "",
        "status": query.status or "",
        "is_bound": query.is_bound or "",
        "mobile": query.mobile or "",
        "keyword": query.keyword or "",
        "limit": str(query.limit),
        "offset": str(query.offset),
    }


def _has_customer_list_filters(filters: JsonDict) -> bool:
    if any(str(filters.get(key) or "").strip() for key in ("owner_userid", "tag", "status", "mobile", "keyword")):
        return True
    return _normalize_bool_filter(filters.get("is_bound")) is not None


def _projection_watermark(repo: CustomerReadRepository, filters: JsonDict) -> JsonDict:
    if _has_customer_list_filters(filters):
        return {}
    read_watermark = getattr(repo, "get_projection_watermark", None)
    if not callable(read_watermark):
        return {}
    try:
        raw = dict(read_watermark() or {})
    except Exception as exc:
        safe_log_exception(LOGGER, "customer read model projection watermark unavailable", exc, level=logging.WARNING)
        return {}
    if not raw.get("last_succeeded_at") or raw.get("target_count") is None:
        return {}
    source_count = max(0, int(raw.get("source_count") or 0))
    target_count = max(0, int(raw.get("target_count") or 0))
    dirty_generation = max(0, int(raw.get("dirty_generation") or 0))
    completed_generation = max(0, int(raw.get("completed_generation") or 0))
    refresh_status = str(raw.get("refresh_status") or "idle")
    freshness = "fresh"
    if source_count != target_count or dirty_generation > completed_generation or refresh_status != "idle":
        freshness = "refresh_pending"
    return {
        "last_succeeded_at": str(raw.get("last_succeeded_at") or ""),
        "source_count": source_count,
        "target_count": target_count,
        "dirty_generation": dirty_generation,
        "completed_generation": completed_generation,
        "refresh_status": refresh_status,
        "freshness": freshness,
    }


def _count_customers(
    repo: CustomerReadRepository,
    filters: JsonDict,
    page: list[JsonDict],
    *,
    limit: int,
    offset: int,
    projection_watermark: JsonDict | None = None,
) -> int:
    if projection_watermark and projection_watermark.get("target_count") is not None:
        return max(int(projection_watermark["target_count"]), offset + len(page))
    count = getattr(repo, "count_customers", None)
    if callable(count):
        return int(count(filters) or 0)
    if offset == 0 and len(page) < limit:
        return len(page)
    return len(repo.list_customers(filters, limit=None, offset=0))


def _list_customers_live_source_payload(query: ListCustomersRequest, exc: Exception, repo: CustomerReadRepository | None = None) -> JsonDict:
    if not _customer_read_model_live_source_fallback_enabled():
        raise exc
    owned_repo = repo is None
    repo = repo or build_customer_live_source_repository()
    try:
        filters = _list_filters(query)
        page = [list_item_projection(item) for item in repo.list_customers(filters, limit=query.limit, offset=query.offset)]
        total = _count_customers(repo, filters, page, limit=query.limit, offset=query.offset)
        return {
            "ok": True,
            "customers": page,
            "items": page,
            "count": len(page),
            "total": total,
            "has_more": query.offset + len(page) < total,
            "total_source": "exact_query",
            "projection_watermark": {},
            "limit": query.limit,
            "offset": query.offset,
            "filters": filters,
            "status_code": 200,
            **_diagnostics(
                source_status="live_source_fallback",
                read_model_status="fallback",
                degraded=True,
                fallback_used=True,
                fallback_reason=_production_error_message(exc),
            ),
        }
    finally:
        if owned_repo:
            _close_repository(repo)


def _customer_detail_live_source_payload(query: CustomerDetailRequest, exc: Exception, repo: CustomerReadRepository | None = None) -> JsonDict:
    if not _customer_read_model_live_source_fallback_enabled():
        raise exc
    owned_repo = repo is None
    repo = repo or build_customer_live_source_repository()
    try:
        customer = get_customer_by_request(repo, query)
        if not customer:
            raise NotFoundError("customer not found")
        return {
            "ok": True,
            "customer": detail_projection(customer),
            "status_code": 200,
            **_diagnostics(
                source_status="live_source_fallback",
                read_model_status="fallback",
                degraded=True,
                fallback_used=True,
                fallback_reason=_production_error_message(exc),
            ),
        }
    finally:
        if owned_repo:
            _close_repository(repo)


def _customer_timeline_live_source_payload(query: CustomerTimelineRequest, exc: Exception, repo: CustomerReadRepository | None = None) -> JsonDict:
    if not _customer_read_model_live_source_fallback_enabled():
        raise exc
    owned_repo = repo is None
    repo = repo or build_customer_live_source_repository()
    try:
        if not _repo_customer_exists_by_request(repo, query):
            raise NotFoundError("customer not found")
        items = _repo_list_timeline_by_request(repo, query, limit=None, offset=0)
        total = len(items)
        page = items[query.offset : query.offset + query.limit]
        return {
            "ok": True,
            "timeline": {
                "external_userid": query.external_userid,
                "items": page,
                "count": len(page),
                "limit": query.limit,
                "offset": query.offset,
                "filters": {"event_type": query.event_type or "", "limit": str(query.limit), "offset": str(query.offset)},
                "total": total,
            },
            "status_code": 200,
            **_diagnostics(
                source_status="live_source_fallback",
                read_model_status="fallback",
                degraded=True,
                fallback_used=True,
                fallback_reason=_production_error_message(exc),
            ),
        }
    finally:
        if owned_repo:
            _close_repository(repo)


def _recent_messages_live_source_payload(query: RecentMessagesRequest, exc: Exception, repo: CustomerReadRepository | None = None) -> JsonDict:
    if not _customer_read_model_live_source_fallback_enabled():
        raise exc
    owned_repo = repo is None
    repo = repo or build_customer_live_source_repository()
    try:
        if not _repo_customer_exists_by_request(repo, query):
            raise NotFoundError("customer not found")
        messages = _repo_list_recent_messages_by_request(repo, query)
        return {
            "ok": True,
            "messages": messages,
            "items": messages,
            "count": len(messages),
            "external_userid": query.external_userid,
            "limit": query.limit,
            "status_code": 200,
            **_diagnostics(
                source_status="live_source_fallback",
                read_model_status="fallback",
                degraded=True,
                fallback_used=True,
                fallback_reason=_production_error_message(exc),
            ),
        }
    finally:
        if owned_repo:
            _close_repository(repo)


def _normalize_bool_filter(value: str | None) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "all"}:
        return None
    if normalized in {"1", "true", "yes", "y", "on", "bound"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "unbound"}:
        return False
    return None


class ListCustomersQuery:
    def __init__(
        self,
        repo: CustomerReadRepository | None = None,
        contacts_adapter=None,
        projection_gateway=None,
        live_source_repo: CustomerReadRepository | None = None,
    ) -> None:
        self._repo = repo
        self._contacts_adapter = contacts_adapter
        self._projection_gateway = projection_gateway
        self._live_source_repo = live_source_repo

    def execute(self, query: ListCustomersRequest) -> JsonDict:
        if _production_customer_data_required():
            try:
                if not _customer_read_model_next_primary_enabled():
                    raise RuntimeError("customer read model next primary disabled")
                repo = self._repo or build_customer_read_model_repository()
                try:
                    filters = _list_filters(query)
                    page = [list_item_projection(item) for item in repo.list_customers(filters, limit=query.limit, offset=query.offset)]
                    projection_watermark = _projection_watermark(repo, filters)
                    total = _count_customers(
                        repo,
                        filters,
                        page,
                        limit=query.limit,
                        offset=query.offset,
                        projection_watermark=projection_watermark,
                    )
                finally:
                    if self._repo is None:
                        _close_repository(repo)
                return {
                    "ok": True,
                    "customers": page,
                    "items": page,
                    "count": len(page),
                    "total": total,
                    "has_more": query.offset + len(page) < total,
                    "total_source": "refresh_watermark" if projection_watermark else "exact_query",
                    "projection_watermark": projection_watermark,
                    "limit": query.limit,
                    "offset": query.offset,
                    "filters": filters,
                    "status_code": 200,
                    **_diagnostics(source_status="next_read_model", read_model_status="primary"),
                }
            except Exception as exc:
                try:
                    return _list_customers_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    return _list_customers_unavailable_payload(query, exc)

        repo = self._repo or build_customer_read_model_repository()
        try:
            contacts_adapter = self._contacts_adapter or build_contacts_sync_adapter()
            projection_gateway = self._projection_gateway or build_customer_projection_sync_gateway()
            contacts_contract = contacts_adapter.fetch_external_contacts(
                follow_user_userid=query.owner_userid or "",
                limit=query.limit,
                sync_cursor=f"offset:{query.offset}",
            )
            projection_contract = projection_gateway.update_customer_list_projection(
                projection_name="customer_list",
                sync_cursor=f"offset:{query.offset}:limit:{query.limit}",
            )
            filters = _list_filters(query)
            rows = [list_item_projection(item) for item in repo.list_customers()]
            if query.owner_userid:
                rows = [item for item in rows if item.get("owner_userid") == query.owner_userid]
            if query.mobile:
                rows = [item for item in rows if query.mobile in str(item.get("mobile") or "")]
            if query.tag:
                rows = [item for item in rows if query.tag in item.get("tags", [])]
            if query.status:
                rows = [
                    item
                    for item in rows
                    if query.status in {
                        str(item.get("class_user_status", {}).get("current_status") or ""),
                        str(item.get("class_user_status", {}).get("signup_status") or ""),
                        str(item.get("class_user_status", {}).get("activation_bucket") or ""),
                        str(item.get("binding_status") or ""),
                    }
                ]
            is_bound = _normalize_bool_filter(query.is_bound)
            if is_bound is not None:
                rows = [item for item in rows if bool(item.get("is_bound")) is is_bound]
            if query.keyword:
                rows = [
                    item
                    for item in rows
                    if query.keyword in str(item.get("customer_name") or "")
                    or query.keyword in str(item.get("external_userid") or "")
                    or query.keyword in str(item.get("mobile") or "")
                    or query.keyword in str(item.get("owner_userid") or "")
                    or query.keyword in str(item.get("owner_display_name") or "")
                ]
            total = len(rows)
            page = rows[query.offset : query.offset + query.limit]
            return {
                "ok": True,
                "customers": page,
                "items": page,
                "count": len(page),
                "total": total,
                "limit": query.limit,
                "offset": query.offset,
                "filters": filters,
                "adapter_contract": {
                    "contacts_sync": contacts_contract,
                    "customer_projection": projection_contract,
                },
                "side_effect_safety": customer_sync_side_effect_safety(),
                **_diagnostics(source_status="local_contract_probe", read_model_status="fixture"),
            }
        finally:
            if self._repo is None:
                _close_repository(repo)

    __call__ = execute


class GetCustomerDetailQuery:
    def __init__(
        self,
        repo: CustomerReadRepository | None = None,
        contacts_adapter=None,
        projection_gateway=None,
        live_source_repo: CustomerReadRepository | None = None,
    ) -> None:
        self._repo = repo
        self._contacts_adapter = contacts_adapter
        self._projection_gateway = projection_gateway
        self._live_source_repo = live_source_repo

    def execute(self, query: CustomerDetailRequest) -> JsonDict:
        if _production_customer_data_required():
            repo: CustomerReadRepository | None = None
            try:
                if not _customer_read_model_next_primary_enabled():
                    raise RuntimeError("customer read model next primary disabled")
                repo = self._repo or build_customer_read_model_repository()
                customer = get_customer_by_request(repo, query)
                if not customer:
                    raise NotFoundError("customer not found")
            except NotFoundError as exc:
                if self._repo is None:
                    _close_repository(repo)
                    repo = None
                try:
                    return _customer_detail_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    raise exc
            except Exception as exc:
                if self._repo is None:
                    _read_model_primary_failed(exc, repo)
                    repo = None
                try:
                    return _customer_detail_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    return _customer_detail_unavailable_payload(_customer_identity_key(query), exc)
            finally:
                if self._repo is None:
                    _close_repository(repo)
            return {
                "ok": True,
                "customer": detail_projection(customer),
                "status_code": 200,
                **_diagnostics(source_status="next_read_model", read_model_status="primary"),
            }

        repo = self._repo or build_customer_read_model_repository()
        try:
            contacts_adapter = self._contacts_adapter or build_contacts_sync_adapter()
            projection_gateway = self._projection_gateway or build_customer_projection_sync_gateway()
            if query.external_userid:
                contacts_contract = contacts_adapter.fetch_contact_detail(external_userid=query.external_userid)
                projection_contract = projection_gateway.update_customer_detail_projection(external_userid=query.external_userid)
            else:
                contacts_contract = {"ok": True, "skipped": True, "reason": "unionid_native_query"}
                projection_contract = {"ok": True, "skipped": True, "reason": "unionid_native_query"}
            customer = get_customer_by_request(repo, query)
            if not customer:
                raise NotFoundError("customer not found")
            return {
                "ok": True,
                "customer": detail_projection(customer),
                "adapter_contract": {
                    "contacts_sync": contacts_contract,
                    "customer_projection": projection_contract,
                },
                "side_effect_safety": customer_sync_side_effect_safety(),
                **_diagnostics(source_status="local_contract_probe", read_model_status="fixture"),
            }
        finally:
            if self._repo is None:
                _close_repository(repo)

    __call__ = execute


class GetCustomerTimelineQuery:
    def __init__(
        self,
        repo: CustomerReadRepository | None = None,
        projection_gateway=None,
        live_source_repo: CustomerReadRepository | None = None,
    ) -> None:
        self._repo = repo
        self._projection_gateway = projection_gateway
        self._live_source_repo = live_source_repo

    def execute(self, query: CustomerTimelineRequest) -> JsonDict:
        if _production_customer_data_required():
            repo: CustomerReadRepository | None = None
            try:
                if not _customer_read_model_next_primary_enabled():
                    raise RuntimeError("customer read model next primary disabled")
                repo = self._repo or build_customer_read_model_repository()
                _, resolved_unionid, resolved_external_userid = resolve_customer_request(
                    repo,
                    unionid=query.unionid,
                    external_userid=query.external_userid,
                )
                resolved_query = query.model_copy(
                    update={
                        "unionid": resolved_unionid or None,
                        "external_userid": resolved_external_userid or None,
                    }
                )
                page = _repo_list_timeline_by_request(
                    repo,
                    resolved_query,
                    limit=query.limit,
                    offset=query.offset,
                )
                counter = getattr(repo, "count_timeline_by_unionid", None)
                if resolved_unionid and callable(counter):
                    total = int(counter(resolved_unionid, {"event_type": query.event_type or ""}))
                else:
                    total = len(_repo_list_timeline_by_request(repo, resolved_query, limit=None, offset=0))
            except NotFoundError as exc:
                if self._repo is None:
                    _close_repository(repo)
                    repo = None
                try:
                    return _customer_timeline_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    raise exc
            except Exception as exc:
                if self._repo is None:
                    _read_model_primary_failed(exc, repo)
                    repo = None
                try:
                    return _customer_timeline_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    return _customer_timeline_unavailable_payload(query, exc)
            finally:
                if self._repo is None:
                    _close_repository(repo)
            return {
                "ok": True,
                "timeline": {
                    "external_userid": query.external_userid or "",
                    "unionid": query.unionid or "",
                    "items": page,
                    "count": len(page),
                    "limit": query.limit,
                    "offset": query.offset,
                    "filters": {"event_type": query.event_type or "", "limit": str(query.limit), "offset": str(query.offset)},
                    "total": total,
                },
                "status_code": 200,
                **_diagnostics(source_status="next_read_model", read_model_status="primary"),
            }

        repo = self._repo or build_customer_read_model_repository()
        try:
            projection_gateway = self._projection_gateway or build_customer_projection_sync_gateway()
            if query.external_userid:
                projection_contract = projection_gateway.update_customer_timeline_projection(
                    external_userid=query.external_userid,
                    sync_cursor=f"offset:{query.offset}:limit:{query.limit}",
                )
            else:
                projection_contract = {"ok": True, "skipped": True, "reason": "unionid_native_query"}
            _, resolved_unionid, resolved_external_userid = resolve_customer_request(
                repo,
                unionid=query.unionid,
                external_userid=query.external_userid,
            )
            resolved_query = query.model_copy(
                update={
                    "unionid": resolved_unionid or None,
                    "external_userid": resolved_external_userid or None,
                }
            )
            page = _repo_list_timeline_by_request(
                repo,
                resolved_query,
                limit=query.limit,
                offset=query.offset,
            )
            counter = getattr(repo, "count_timeline_by_unionid", None)
            if resolved_unionid and callable(counter):
                total = int(counter(resolved_unionid, {"event_type": query.event_type or ""}))
            else:
                total = len(_repo_list_timeline_by_request(repo, resolved_query, limit=None, offset=0))
            return {
                "ok": True,
                "timeline": {
                    "external_userid": query.external_userid or "",
                    "unionid": query.unionid or "",
                    "items": page,
                    "count": len(page),
                    "limit": query.limit,
                    "offset": query.offset,
                    "filters": {"event_type": query.event_type or "", "limit": str(query.limit), "offset": str(query.offset)},
                    "total": total,
                },
                "adapter_contract": {"customer_projection": projection_contract},
                "side_effect_safety": customer_sync_side_effect_safety(),
                **_diagnostics(source_status="local_contract_probe", read_model_status="fixture"),
            }
        finally:
            if self._repo is None:
                _close_repository(repo)

    __call__ = execute


class ListRecentMessagesQuery:
    def __init__(
        self,
        repo: CustomerReadRepository | None = None,
        archive_adapter=None,
        projection_gateway=None,
        live_source_repo: CustomerReadRepository | None = None,
    ) -> None:
        self._repo = repo
        self._archive_adapter = archive_adapter
        self._projection_gateway = projection_gateway
        self._live_source_repo = live_source_repo

    def execute(self, query: RecentMessagesRequest) -> JsonDict:
        if _production_customer_data_required():
            repo: CustomerReadRepository | None = None
            try:
                if not _customer_read_model_next_primary_enabled():
                    raise RuntimeError("customer read model next primary disabled")
                repo = self._repo or build_customer_read_model_repository()
                _, resolved_unionid, resolved_external_userid = resolve_customer_request(
                    repo,
                    unionid=query.unionid,
                    external_userid=query.external_userid,
                )
                messages = _repo_list_recent_messages_by_request(
                    repo,
                    query.model_copy(
                        update={
                            "unionid": resolved_unionid or None,
                            "external_userid": resolved_external_userid or None,
                        }
                    ),
                )
            except NotFoundError as exc:
                if self._repo is None:
                    _close_repository(repo)
                    repo = None
                try:
                    return _recent_messages_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    raise exc
            except Exception as exc:
                if self._repo is None:
                    _read_model_primary_failed(exc, repo)
                    repo = None
                try:
                    return _recent_messages_live_source_payload(query, exc, self._live_source_repo)
                except NotFoundError:
                    raise
                except Exception:
                    return _recent_messages_unavailable_payload(query, exc)
            finally:
                if self._repo is None:
                    _close_repository(repo)
            return {
                "ok": True,
                "messages": messages,
                "items": messages,
                "count": len(messages),
                "external_userid": query.external_userid or "",
                "unionid": query.unionid or "",
                "limit": query.limit,
                "status_code": 200,
                **_diagnostics(source_status="next_read_model", read_model_status="primary"),
            }

        repo = self._repo or build_customer_read_model_repository()
        try:
            archive_adapter = self._archive_adapter or build_archive_sync_adapter()
            projection_gateway = self._projection_gateway or build_customer_projection_sync_gateway()
            if query.external_userid:
                archive_contract = archive_adapter.fetch_recent_messages(
                    external_userid=query.external_userid,
                    limit=query.limit,
                )
                projection_contract = projection_gateway.update_recent_messages_projection(
                    external_userid=query.external_userid,
                    sync_cursor=f"limit:{query.limit}",
                )
            else:
                archive_contract = {"ok": True, "skipped": True, "reason": "unionid_native_query"}
                projection_contract = {"ok": True, "skipped": True, "reason": "unionid_native_query"}
            _, resolved_unionid, resolved_external_userid = resolve_customer_request(
                repo,
                unionid=query.unionid,
                external_userid=query.external_userid,
            )
            messages = _repo_list_recent_messages_by_request(
                repo,
                query.model_copy(
                    update={
                        "unionid": resolved_unionid or None,
                        "external_userid": resolved_external_userid or None,
                    }
                ),
            )
            return {
                "ok": True,
                "messages": messages,
                "items": messages,
                "count": len(messages),
                "external_userid": query.external_userid or "",
                "unionid": query.unionid or "",
                "limit": query.limit,
                "adapter_contract": {
                    "archive_sync": archive_contract,
                    "customer_projection": projection_contract,
                },
                "side_effect_safety": customer_sync_side_effect_safety(),
                **_diagnostics(source_status="local_contract_probe", read_model_status="fixture"),
            }
        finally:
            if self._repo is None:
                _close_repository(repo)

    __call__ = execute


def _identity_binding_summary(customer: JsonDict) -> JsonDict:
    binding = dict(customer.get("binding") or {})
    identity = dict(customer.get("identity") or {})
    mobile = binding.get("mobile") or identity.get("mobile") or customer.get("mobile")
    is_bound = bool(binding.get("is_bound") or mobile)
    return {
        "is_bound": is_bound,
        "binding_status": binding.get("binding_status") or ("bound" if is_bound else "unbound"),
        "person_id": identity.get("person_id") or binding.get("person_id") or customer.get("person_id"),
        "external_userid": customer.get("external_userid") or identity.get("external_userid"),
        "mobile": mobile,
        "third_party_user_id": binding.get("third_party_user_id") or identity.get("third_party_user_id"),
        "owner_userid": customer.get("owner_userid") or binding.get("owner_userid"),
    }


def _customer_context_payload(
    *,
    unionid: str = "",
    external_userid: str,
    customer: JsonDict,
    timeline: JsonDict,
    recent_messages: list[JsonDict],
    source_status: str,
    read_model_status: str = "",
    adapter_contract: JsonDict | None = None,
    warnings: list[str] | None = None,
    fallback_used: bool = False,
    fallback_reason: str = "",
) -> JsonDict:
    return {
        "ok": True,
        "unionid": unionid,
        "external_userid": external_userid,
        "customer": customer,
        "profile": customer,
        "identity_binding_summary": _identity_binding_summary(customer),
        "binding": dict(customer.get("binding") or {}),
        "identity": dict(customer.get("identity") or {}),
        "recent_messages": recent_messages,
        "recent_timeline_events": list(timeline.get("items") or []),
        "timeline": timeline,
        **_diagnostics(
            source_status=source_status,
            read_model_status=read_model_status or source_status,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        ),
        "page_error": "",
        "warnings": warnings or [],
        "adapter_contract": adapter_contract or {},
        "side_effect_safety": customer_sync_side_effect_safety(),
    }


def _production_unavailable_payload(external_userid: str, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "external_userid": external_userid,
        "customer": {},
        "profile": {},
        "identity_binding_summary": {},
        "binding": {},
        "identity": {},
        "recent_messages": [],
        "recent_timeline_events": [],
        "timeline": {"external_userid": external_userid, "items": [], "count": 0, "total": 0},
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "degraded": True,
        "fallback_used": False,
        "page_error": str(exc),
        "error_code": "customer_context_read_unavailable",
        "warnings": ["customer_context_read_failed"],
        "adapter_contract": {},
        "side_effect_safety": customer_sync_side_effect_safety(),
    }


def _admin_profile_input_error(message: str) -> JsonDict:
    return {
        "ok": False,
        "error": message,
        "source_status": "input_error",
        "route_owner": "ai_crm_next",
        "status_code": 400,
    }


def _admin_profile_not_found_error(message: str = "customer not found") -> JsonDict:
    return {
        "ok": False,
        "error": message,
        "source_status": "not_found",
        "read_model_status": "not_found",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 404,
    }


def _admin_profile_payload(
    context: JsonDict,
    *,
    resolved_by: str = "external_userid",
) -> JsonDict:
    profile = dict(context.get("customer") or context.get("profile") or {})
    external_userid = str(profile.get("external_userid") or profile.get("user_id") or "")
    unionid = str(profile.get("unionid") or context.get("unionid") or "")
    normalized_profile = {
        **profile,
        "unionid": unionid,
        "external_userid": external_userid,
        "user_id": profile.get("user_id") or external_userid,
        "tags": list(profile.get("tags") or []),
        "binding": dict(profile.get("binding") or {}),
        "identity": dict(profile.get("identity") or {}),
        "marketing_profile": dict(profile.get("marketing_profile") or {}),
        "sidebar_context": dict(profile.get("sidebar_context") or {}),
    }
    return {
        "ok": True,
        "profile": normalized_profile,
        "customer": normalized_profile,
        "lookup": {"resolved_by": resolved_by, "unionid": unionid, "external_userid": external_userid},
        "source_status": context.get("source_status"),
        "read_model_status": context.get("read_model_status"),
        "route_owner": "ai_crm_next",
        "context": context,
        "identity_binding_summary": dict(context.get("identity_binding_summary") or {}),
        "degraded": bool(context.get("degraded")),
        "fallback_used": bool(context.get("fallback_used")),
        "fallback_reason": context.get("fallback_reason") or "",
        "page_error": context.get("page_error") or "",
        "status_code": 200,
    }


def _admin_profile_tags_payload(customer: JsonDict, *, source_status: str) -> JsonDict:
    tags = _normalized_admin_profile_tags(customer.get("tags") or [])
    return {
        "ok": True,
        "tags": tags,
        "count": len(tags),
        "unionid": str(customer.get("unionid") or ""),
        "external_userid": str(customer.get("external_userid") or ""),
        "source_status": source_status,
        "read_model_status": "fixture" if source_status == "local_contract_probe" else source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "degraded": False,
        "status_code": 200,
    }


def _admin_profile_tag_name(tag: object) -> str:
    if isinstance(tag, dict):
        for key in ("tag_name", "name", "label", "value", "text", "tag_id", "id"):
            value = str(tag.get(key) or "").strip()
            if value and value.lower() not in {"undefined", "null"}:
                return value
        return ""
    value = str(tag or "").strip()
    if value.lower() in {"undefined", "null"}:
        return ""
    return value


def _normalized_admin_profile_tags(tags: object) -> list[str]:
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    for item in tags:
        tag = _admin_profile_tag_name(item)
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


class GetCustomerContextQuery:
    def __init__(
        self,
        repo: CustomerReadRepository | None = None,
        live_source_repo: CustomerReadRepository | None = None,
        owner_scope_verifier: Callable[[str, str], None] | None = None,
    ) -> None:
        self._repo = repo
        self._live_source_repo = live_source_repo
        self._owner_scope_verifier = owner_scope_verifier

    def _assert_owner_scope(self, customer: JsonDict, query: CustomerContextRequest) -> None:
        try:
            _assert_customer_owner_scope(
                customer,
                query.owner_userid,
                require_owner=query.require_owner_scope,
                owner_verified=query.owner_verified,
            )
        except CustomerScopeForbiddenError:
            # Sidebar projections and live-source fallbacks can lag the current
            # WeCom owner relation. Only an injected verifier backed by that
            # current relation may override the projection mismatch; callers
            # without one remain fail-closed.
            if self._owner_scope_verifier is None:
                raise
            external_userid = str(
                customer.get("external_userid")
                or customer.get("user_id")
                or query.external_userid
                or query.user_id
                or ""
            ).strip()
            owner_userid = str(query.owner_userid or "").strip()
            if not external_userid or not owner_userid:
                raise
            self._owner_scope_verifier(external_userid, owner_userid)

    def _resolve_fixture_external_userid(self, query: CustomerContextRequest, repo: CustomerReadRepository) -> str:
        external_userid = str(query.external_userid or query.user_id or "").strip()
        if external_userid:
            return external_userid
        mobile = str(query.mobile or "").strip()
        if not mobile:
            raise NotFoundError("external_userid is required")
        filters = {"mobile": mobile}
        if str(query.owner_userid or "").strip():
            filters["owner_userid"] = str(query.owner_userid or "").strip()
        matches = repo.list_customers(filters, limit=1, offset=0)
        if not matches or not str(matches[0].get("external_userid") or "").strip():
            raise NotFoundError("customer not found")
        return str(matches[0]["external_userid"])

    def _resolve_production_external_userid(self, query: CustomerContextRequest, repo: CustomerReadRepository) -> str:
        external_userid = str(query.external_userid or query.user_id or "").strip()
        if external_userid:
            return external_userid
        mobile = str(query.mobile or "").strip()
        if not mobile:
            raise NotFoundError("external_userid is required")
        payload = ListCustomersQuery(repo, live_source_repo=self._live_source_repo)(
            ListCustomersRequest(
                mobile=mobile,
                owner_userid=str(query.owner_userid or "").strip() or None,
                limit=1,
                offset=0,
            )
        )
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("page_error") or payload.get("error_code") or "customer read model unavailable"))
        rows = list(payload.get("customers") or payload.get("items") or [])
        if not rows or not str(rows[0].get("external_userid") or "").strip():
            raise NotFoundError("customer not found")
        return str(rows[0]["external_userid"])

    def execute(self, query: CustomerContextRequest) -> JsonDict:
        from aicrm_next.platform.shared.runtime import production_data_ready

        if production_data_ready():
            repo: CustomerReadRepository | None = None
            try:
                repo = self._repo or build_customer_read_model_repository()
                unionid = str(query.unionid or "").strip()
                external_userid = "" if unionid else self._resolve_production_external_userid(query, repo)
                detail_request = CustomerDetailRequest(unionid=unionid or None, external_userid=external_userid or None)
                detail = GetCustomerDetailQuery(repo, live_source_repo=self._live_source_repo)(detail_request)
                if not detail.get("ok"):
                    raise RuntimeError(str(detail.get("page_error") or detail.get("error_code") or "customer detail unavailable"))
                customer = dict(detail.get("customer") or {})
                self._assert_owner_scope(customer, query)
                unionid = unionid or str(customer.get("unionid") or "").strip()
                external_userid = external_userid or str(customer.get("external_userid") or customer.get("user_id") or "").strip()
                if query.include_activity:
                    timeline_payload = GetCustomerTimelineQuery(repo, live_source_repo=self._live_source_repo)(
                        CustomerTimelineRequest(unionid=unionid or None, external_userid=external_userid or None, limit=query.timeline_limit)
                    )
                    if not timeline_payload.get("ok"):
                        raise RuntimeError(
                            str(timeline_payload.get("page_error") or timeline_payload.get("error_code") or "customer timeline unavailable")
                        )
                    messages_payload = ListRecentMessagesQuery(repo, live_source_repo=self._live_source_repo)(
                        RecentMessagesRequest(unionid=unionid or None, external_userid=external_userid or None, limit=query.recent_message_limit)
                    )
                    if not messages_payload.get("ok"):
                        raise RuntimeError(
                            str(messages_payload.get("page_error") or messages_payload.get("error_code") or "recent messages unavailable")
                        )
                    timeline = dict(timeline_payload.get("timeline") or {})
                    recent_messages = list(messages_payload.get("messages") or messages_payload.get("items") or [])
                else:
                    timeline_payload = {"source_status": "skipped", "fallback_used": False, "fallback_reason": ""}
                    messages_payload = {"source_status": "skipped", "fallback_used": False, "fallback_reason": ""}
                    timeline = {"external_userid": external_userid, "items": [], "count": 0, "total": 0}
                    recent_messages = []
                return _customer_context_payload(
                    unionid=unionid,
                    external_userid=external_userid,
                    customer=customer,
                    timeline=timeline,
                    recent_messages=recent_messages,
                    source_status=str(detail.get("source_status") or "next_read_model"),
                    read_model_status=str(detail.get("read_model_status") or "primary"),
                    adapter_contract={
                        "detail": {"source_status": detail.get("source_status"), "fallback_used": detail.get("fallback_used")},
                        "timeline": {"source_status": timeline_payload.get("source_status"), "fallback_used": timeline_payload.get("fallback_used")},
                        "recent_messages": {"source_status": messages_payload.get("source_status"), "fallback_used": messages_payload.get("fallback_used")},
                    },
                    fallback_used=bool(detail.get("fallback_used") or timeline_payload.get("fallback_used") or messages_payload.get("fallback_used")),
                    fallback_reason=str(detail.get("fallback_reason") or timeline_payload.get("fallback_reason") or messages_payload.get("fallback_reason") or ""),
                )
            except (NotFoundError, CustomerScopeForbiddenError):
                raise
            except Exception as exc:
                fallback_external_userid = str(query.external_userid or query.user_id or "")
                return _production_unavailable_payload(fallback_external_userid, exc)
            finally:
                if self._repo is None and repo is not None:
                    _close_repository(repo)

        repo = self._repo or build_customer_read_model_repository()
        try:
            unionid = str(query.unionid or "").strip()
            external_userid = "" if unionid else self._resolve_fixture_external_userid(query, repo)
            detail = GetCustomerDetailQuery(repo, live_source_repo=self._live_source_repo)(
                CustomerDetailRequest(unionid=unionid or None, external_userid=external_userid or None)
            )
            customer = dict(detail.get("customer") or {})
            self._assert_owner_scope(customer, query)
            unionid = unionid or str(customer.get("unionid") or "").strip()
            external_userid = external_userid or str(customer.get("external_userid") or customer.get("user_id") or "").strip()
            if query.include_activity:
                timeline = GetCustomerTimelineQuery(repo, live_source_repo=self._live_source_repo)(
                    CustomerTimelineRequest(unionid=unionid or None, external_userid=external_userid or None, limit=query.timeline_limit)
                )
                messages = ListRecentMessagesQuery(repo, live_source_repo=self._live_source_repo)(
                    RecentMessagesRequest(unionid=unionid or None, external_userid=external_userid or None, limit=query.recent_message_limit)
                )
            else:
                timeline = {
                    "timeline": {"external_userid": external_userid, "items": [], "count": 0, "total": 0},
                    "adapter_contract": {"source_status": "skipped", "fallback_used": False},
                }
                messages = {
                    "messages": [],
                    "adapter_contract": {"source_status": "skipped", "fallback_used": False},
                }
            return _customer_context_payload(
                unionid=unionid,
                external_userid=external_userid,
                customer=customer,
                timeline=timeline["timeline"],
                recent_messages=messages["messages"],
                source_status="local_contract_probe",
                read_model_status="fixture",
                adapter_contract={
                    "detail": detail.get("adapter_contract", {}),
                    "timeline": timeline.get("adapter_contract", {}),
                    "recent_messages": messages.get("adapter_contract", {}),
                },
            )
        finally:
            if self._repo is None:
                _close_repository(repo)

    __call__ = execute


class GetAdminCustomerProfileQuery:
    def __init__(self, context_query: GetCustomerContextQuery | None = None) -> None:
        self._context_query = context_query or GetCustomerContextQuery()

    def execute(
        self,
        *,
        unionid: str | None = None,
        external_userid: str | None = None,
        mobile: str | None = None,
        user_id: str | None = None,
        owner_userid: str | None = None,
        require_owner_scope: bool = False,
    ) -> JsonDict:
        resolved_unionid = str(unionid or "").strip()
        resolved_external_userid = str(external_userid or user_id or "").strip()
        resolved_mobile = str(mobile or "").strip()
        if not resolved_unionid and not resolved_external_userid and not resolved_mobile:
            return _admin_profile_input_error("unionid is required")

        request = CustomerContextRequest(
            unionid=resolved_unionid or None,
            external_userid=resolved_external_userid or None,
            mobile=resolved_mobile or None,
            user_id=str(user_id or "").strip() or None,
            owner_userid=str(owner_userid or "").strip() or None,
            require_owner_scope=bool(require_owner_scope),
        )
        try:
            context = self._context_query(request)
        except NotFoundError:
            return _admin_profile_not_found_error()
        except Exception as exc:
            fallback_external_userid = resolved_external_userid
            context = _production_unavailable_payload(fallback_external_userid, exc)

        if not context.get("ok"):
            payload = dict(context)
            payload.setdefault("route_owner", "ai_crm_next")
            payload["status_code"] = 503 if payload.get("degraded") else 400
            return payload

        customer = dict(context.get("customer") or {})
        if not customer:
            return _admin_profile_not_found_error()

        if resolved_unionid:
            resolved_by = "unionid"
        elif resolved_mobile and not resolved_external_userid:
            resolved_by = "mobile"
        else:
            resolved_by = (
                "user_id_fallback_external_userid"
                if user_id and not external_userid
                else "external_userid"
            )
        return _admin_profile_payload(context, resolved_by=resolved_by)

    __call__ = execute


class GetAdminCustomerProfileTagsQuery:
    def __init__(self, context_query: GetCustomerContextQuery | None = None) -> None:
        self._context_query = context_query or GetCustomerContextQuery()

    def execute(
        self,
        *,
        unionid: str | None = None,
        external_userid: str | None = None,
        user_id: str | None = None,
        owner_userid: str | None = None,
        require_owner_scope: bool = False,
    ) -> JsonDict:
        resolved_unionid = str(unionid or "").strip()
        resolved_external_userid = str(external_userid or user_id or "").strip()
        if not resolved_unionid and not resolved_external_userid:
            return _admin_profile_input_error("unionid is required")

        try:
            context = self._context_query(
                CustomerContextRequest(
                    unionid=resolved_unionid or None,
                    external_userid=resolved_external_userid,
                    user_id=str(user_id or "").strip() or None,
                    owner_userid=str(owner_userid or "").strip() or None,
                    require_owner_scope=bool(require_owner_scope),
                )
            )
        except NotFoundError:
            return _admin_profile_not_found_error()
        except Exception as exc:
            context = _production_unavailable_payload(resolved_external_userid, exc)

        if not context.get("ok"):
            payload = dict(context)
            payload.setdefault("route_owner", "ai_crm_next")
            payload["status_code"] = 503 if payload.get("degraded") else 400
            return payload

        customer = dict(context.get("customer") or {})
        if not customer:
            return _admin_profile_not_found_error()
        return _admin_profile_tags_payload(
            customer,
            source_status=str(context.get("source_status") or ""),
        )

    __call__ = execute


class GetCustomer360ProfileQuery:
    def __init__(self, context_query: GetCustomerContextQuery | None = None) -> None:
        self._context_query = context_query or GetCustomerContextQuery()

    def execute(self, unionid: str) -> JsonDict:
        resolved_unionid = str(unionid or "").strip()
        if not resolved_unionid:
            return _admin_profile_input_error("unionid is required")

        try:
            context = self._context_query(
                CustomerContextRequest(
                    unionid=resolved_unionid,
                    recent_message_limit=20,
                    timeline_limit=20,
                )
            )
        except NotFoundError:
            return _admin_profile_input_error("customer not found")
        except Exception as exc:
            context = _production_unavailable_payload("", exc)

        if not context.get("ok"):
            payload = dict(context)
            payload.setdefault("route_owner", "ai_crm_next")
            payload["status_code"] = 503 if payload.get("degraded") else 400
            return payload

        profile = dict(context.get("customer") or context.get("profile") or {})
        if not profile:
            return _admin_profile_input_error("customer not found")
        identity = _customer_360_identity(context, profile, resolved_unionid)
        messages = list(context.get("recent_messages") or [])
        timeline_items = list(dict(context.get("timeline") or {}).get("items") or context.get("recent_timeline_events") or [])
        return {
            "ok": True,
            "unionid": resolved_unionid,
            "identity": identity,
            "orders_summary": _customer_360_orders_summary(profile),
            "questionnaire_summary": _customer_360_questionnaire_summary(profile),
            "message_summary": _customer_360_message_summary(profile, messages),
            "tags": _normalized_admin_profile_tags(profile.get("tags") or []),
            "user_ops_status": _customer_360_user_ops_status(profile),
            "automation_status": _customer_360_automation_status(profile),
            "recent_touchpoints": _customer_360_touchpoints(timeline_items),
            "risk_flags": _customer_360_risk_flags(profile, identity, messages),
            "source_status": context.get("source_status"),
            "read_model_status": context.get("read_model_status"),
            "route_owner": "ai_crm_next",
            "fallback_used": bool(context.get("fallback_used")),
            "degraded": bool(context.get("degraded")),
            "status_code": 200,
        }

    __call__ = execute


from .application_customer360_support import (
    _customer_360_automation_status,
    _customer_360_identity,
    _customer_360_message_summary,
    _customer_360_orders_summary,
    _customer_360_questionnaire_answers,
    _customer_360_questionnaire_summary,
    _customer_360_risk_flags,
    _customer_360_touchpoints,
    _customer_360_user_ops_status,
)


GetCustomerChatContextQuery = GetCustomerContextQuery
