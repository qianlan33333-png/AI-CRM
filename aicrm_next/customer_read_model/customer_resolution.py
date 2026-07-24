from __future__ import annotations

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.typing import JsonDict

from .dto import CustomerDetailRequest
from .repo import CustomerReadRepository


def get_customer_by_request(
    repo: CustomerReadRepository,
    query: CustomerDetailRequest,
) -> JsonDict | None:
    unionid = str(query.unionid or "").strip()
    if unionid:
        getter = getattr(repo, "get_customer_by_unionid", None)
        return getter(unionid) if callable(getter) else None
    external_userid = str(query.external_userid or "").strip()
    return repo.get_customer(external_userid) if external_userid else None


def resolve_customer_request(
    repo: CustomerReadRepository,
    *,
    unionid: str | None,
    external_userid: str | None,
) -> tuple[JsonDict, str, str]:
    customer = get_customer_by_request(
        repo,
        CustomerDetailRequest(unionid=unionid, external_userid=external_userid),
    )
    if not customer:
        raise NotFoundError("customer not found")
    resolved_unionid = str(
        unionid
        or customer.get("unionid")
        or dict(customer.get("identity") or {}).get("unionid")
        or ""
    ).strip()
    resolved_external_userid = str(
        external_userid
        or customer.get("external_userid")
        or customer.get("user_id")
        or ""
    ).strip()
    return customer, resolved_unionid, resolved_external_userid
