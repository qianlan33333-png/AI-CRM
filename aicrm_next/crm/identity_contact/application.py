from __future__ import annotations

from aicrm_next.integration_ports import build_identity_mapping_adapter
from aicrm_next.platform.platform_foundation.internal_events.customer_identity import emit_customer_phone_bound_event
from aicrm_next.platform.platform_foundation.internal_events.shadow import safe_emit
from aicrm_next.platform.shared.runtime import production_data_ready
from aicrm_next.platform.shared.typing import JsonDict

from .domain import normalize_identity_request, normalize_mobile_binding_request, resolve_single_corp_id
from .dto import BindMobileToExternalContactRequest, IdentityResolution, IdentityResolveResult, ResolvePersonIdentityRequest
from .repo import FixtureIdentityRepository, IdentityBindingRepository, PostgresIdentityRepository, build_identity_binding_repository
from .resolver import resolved_identity_or_none


class ResolvePersonIdentityQuery:
    def __init__(
        self,
        repo: FixtureIdentityRepository | None = None,
        identity_adapter=None,
        postgres_repo: PostgresIdentityRepository | None = None,
    ) -> None:
        self._repo = repo or FixtureIdentityRepository()
        self._postgres_repo = postgres_repo or PostgresIdentityRepository()
        self._identity_adapter = identity_adapter or build_identity_mapping_adapter()

    def execute_result(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        normalized = normalize_identity_request(query)
        if production_data_ready():
            resolver = getattr(self._postgres_repo, "resolve_result", None)
            if callable(resolver):
                return resolver(normalized)
            identity = self._postgres_repo.resolve(normalized)
            return IdentityResolveResult(
                status="resolved" if identity is not None else "not_found",
                identity=identity,
                reason="" if identity is not None else "identity_not_found",
                matched_fields=[
                    field
                    for field in ("unionid", "external_userid", "openid", "mobile")
                    if getattr(normalized, field)
                ],
                candidate_count=1 if identity is not None else 0,
            )
        self._identity_adapter.resolve_person_identity(
            external_userid=normalized.external_userid or "",
            openid=normalized.openid or "",
            unionid=normalized.unionid or "",
            mobile=normalized.mobile or "",
        )
        resolver = getattr(self._repo, "resolve_result", None)
        if callable(resolver):
            return resolver(normalized)
        identity = self._repo.resolve(normalized)
        return IdentityResolveResult(
            status="resolved" if identity is not None else "not_found",
            identity=identity,
            reason="" if identity is not None else "identity_not_found",
            candidate_count=1 if identity is not None else 0,
        )

    def execute(self, query: ResolvePersonIdentityRequest) -> IdentityResolution | None:
        return resolved_identity_or_none(self.execute_result(query))

    __call__ = execute


class ListExternalContactOwnerCandidatesQuery:
    def __init__(
        self,
        repo: FixtureIdentityRepository | None = None,
        postgres_repo: PostgresIdentityRepository | None = None,
    ) -> None:
        self._repo = repo or FixtureIdentityRepository()
        self._postgres_repo = postgres_repo or PostgresIdentityRepository()

    def execute(self, *, external_userid: str | None = None) -> set[str]:
        normalized_external = str(external_userid or "").strip()
        if not normalized_external:
            return set()
        if production_data_ready():
            return self._postgres_repo.list_external_contact_owner_userids(normalized_external)
        return self._repo.list_external_contact_owner_userids(normalized_external)

    __call__ = execute


def _empty_binding_status_payload(
    *,
    external_userid: str,
    owner_userid: str = "",
    source_status: str,
) -> JsonDict:
    return {
        "ok": True,
        "is_bound": False,
        "external_userid": external_userid,
        "owner_userid": owner_userid,
        "customer_name": "",
        "remark": "",
        "display_name": f"客户 {external_userid[-6:]}",
        "person_id": None,
        "mobile": None,
        "third_party_user_id": None,
        "detail_url": f"/admin/customers/{external_userid}",
        "source_status": source_status,
        "read_model_status": source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "degraded": False,
        "status_code": 200,
    }


def _production_unavailable_payload(external_userid: str, exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "is_bound": False,
        "external_userid": external_userid,
        "owner_userid": "",
        "customer_name": "",
        "remark": "",
        "display_name": "",
        "person_id": None,
        "mobile": None,
        "third_party_user_id": None,
        "detail_url": f"/admin/customers/{external_userid}" if external_userid else "",
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "error_code": "contact_binding_status_unavailable",
        "page_error": str(exc),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 503,
    }


def _customer_read_model_binding_status_payload(
    customer: JsonDict,
    *,
    external_userid: str,
    owner_userid: str = "",
    source_status: str = "next_read_model",
) -> JsonDict:
    binding = dict(customer.get("binding") or {})
    identity = dict(customer.get("identity") or {})
    mobile = binding.get("mobile") or identity.get("mobile") or customer.get("mobile")
    is_bound = bool(binding.get("is_bound") or mobile)
    return {
        "ok": True,
        "is_bound": is_bound,
        "external_userid": external_userid,
        "owner_userid": owner_userid or customer.get("owner_userid") or "",
        "customer_name": customer.get("customer_name") or "",
        "remark": customer.get("remark") or "",
        "display_name": customer.get("customer_name")
        or customer.get("remark")
        or f"客户 {external_userid[-6:]}",
        "person_id": identity.get("person_id") or binding.get("person_id"),
        "mobile": mobile,
        "third_party_user_id": binding.get("third_party_user_id") or identity.get("third_party_user_id"),
        "detail_url": customer.get("sidebar_context", {}).get("customer_profile_url")
        or f"/admin/customers/{external_userid}",
        "source_status": source_status,
        "read_model_status": source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "degraded": False,
        "status_code": 200,
    }


def _owner_candidates_from_customer(customer: JsonDict) -> set[str]:
    candidates = {str(customer.get("owner_userid") or "").strip()}
    for field in ("binding", "identity", "contact"):
        value = customer.get(field)
        if isinstance(value, dict):
            candidates.update(
                {
                    str(value.get("owner_userid") or "").strip(),
                    str(value.get("last_owner_userid") or "").strip(),
                    str(value.get("first_owner_userid") or "").strip(),
                    str(value.get("primary_owner_userid") or "").strip(),
                    str(value.get("follow_user_userid") or "").strip(),
                }
            )
    follow_users = customer.get("follow_users")
    if isinstance(follow_users, list):
        for item in follow_users:
            if isinstance(item, dict):
                candidates.update(
                    {
                        str(item.get("userid") or "").strip(),
                        str(item.get("user_id") or "").strip(),
                        str(item.get("owner_userid") or "").strip(),
                    }
                )
    return {item for item in candidates if item}


def _owner_scope_not_found_payload(external_userid: str) -> JsonDict:
    return {
        "ok": False,
        "error": "customer not found",
        "source_status": "not_found",
        "read_model_status": "not_found",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "degraded": False,
        "status_code": 404,
        "external_userid": external_userid,
    }


def _identity_binding_status_payload(
    result: IdentityResolution,
    *,
    external_userid: str,
    owner_userid: str = "",
) -> JsonDict:
    return {
        "ok": True,
        "is_bound": bool(result.mobile),
        "external_userid": external_userid,
        "owner_userid": owner_userid or result.owner_userid or "",
        "customer_name": "",
        "remark": "",
        "display_name": f"客户 {external_userid[-6:]}",
        "person_id": result.person_id,
        "mobile": result.mobile,
        "third_party_user_id": None,
        "detail_url": f"/admin/customers/{external_userid}",
        "source_status": "identity_contact",
        "read_model_status": "identity_contact",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "degraded": False,
        "status_code": 200,
    }


class GetSidebarContactBindingStatusQuery:
    def __init__(
        self,
        identity_query: ResolvePersonIdentityQuery | None = None,
        customer_detail_query=None,
    ) -> None:
        self._identity_query = identity_query or ResolvePersonIdentityQuery()
        self._customer_detail_query = customer_detail_query

    def execute(
        self,
        *,
        external_userid: str | None = None,
        owner_userid: str | None = None,
        require_owner_scope: bool = False,
    ) -> JsonDict:
        resolved_external_userid = str(external_userid or "").strip()
        resolved_owner_userid = str(owner_userid or "").strip()
        if not resolved_external_userid:
            return {
                "ok": False,
                "error": "external_userid is required",
                "source_status": "input_error",
                "read_model_status": "input_error",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
                "degraded": False,
                "status_code": 400,
            }
        if require_owner_scope and not resolved_owner_userid:
            return {
                "ok": False,
                "error": "owner_userid is required",
                "source_status": "input_error",
                "read_model_status": "input_error",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
                "degraded": False,
                "status_code": 400,
            }

        if production_data_ready():
            try:
                if self._customer_detail_query is None:
                    raise RuntimeError("customer detail composition unavailable")
                payload = self._customer_detail_query(resolved_external_userid)
                if not payload.get("ok"):
                    raise RuntimeError(str(payload.get("page_error") or payload.get("error_code") or "customer detail unavailable"))
                customer = dict(payload.get("customer") or {})
                if resolved_owner_userid and resolved_owner_userid not in _owner_candidates_from_customer(customer):
                    return _owner_scope_not_found_payload(resolved_external_userid)
            except Exception as exc:
                result = self._identity_query(
                    ResolvePersonIdentityRequest(
                        external_userid=resolved_external_userid,
                    )
                )
                if result is not None:
                    if resolved_owner_userid and resolved_owner_userid != str(result.owner_userid or "").strip():
                        return _owner_scope_not_found_payload(resolved_external_userid)
                    payload = _identity_binding_status_payload(
                        result,
                        external_userid=resolved_external_userid,
                        owner_userid=resolved_owner_userid,
                    )
                    payload["source_status"] = "identity_contact_fallback"
                    payload["read_model_status"] = "identity_contact_fallback"
                    payload["customer_detail_error"] = str(exc)
                    return payload
                return _production_unavailable_payload(resolved_external_userid, exc)
            return _customer_read_model_binding_status_payload(
                customer,
                external_userid=resolved_external_userid,
                owner_userid=resolved_owner_userid,
                source_status=str(payload.get("source_status") or "next_read_model"),
            )

        result = self._identity_query(
            ResolvePersonIdentityRequest(
                external_userid=resolved_external_userid,
            )
        )
        if result is None:
            return _empty_binding_status_payload(
                external_userid=resolved_external_userid,
                owner_userid=resolved_owner_userid,
                source_status="identity_contact",
            )
        if resolved_owner_userid and resolved_owner_userid != str(result.owner_userid or "").strip():
            return _owner_scope_not_found_payload(resolved_external_userid)
        return _identity_binding_status_payload(
            result,
            external_userid=resolved_external_userid,
            owner_userid=resolved_owner_userid,
        )

    __call__ = execute


class UpsertIdentityMappingCommand:
    def __init__(self, identity_adapter=None) -> None:
        self._identity_adapter = identity_adapter or build_identity_mapping_adapter()

    def execute(
        self,
        *,
        external_userid: str = "",
        openid: str = "",
        unionid: str = "",
        mobile: str = "",
        person_id: str = "",
        corp_id: str = "",
        idempotency_key: str | None = None,
    ) -> JsonDict:
        return self._identity_adapter.upsert_identity_mapping(
            external_userid=external_userid,
            openid=openid,
            unionid=unionid,
            mobile=mobile,
            person_id=person_id,
            corp_id=resolve_single_corp_id(corp_id),
            idempotency_key=idempotency_key,
        )

    __call__ = execute


class BindMobileToExternalContactCommand:
    def __init__(self, repo: IdentityBindingRepository | None = None) -> None:
        self._repo = repo or build_identity_binding_repository()

    def execute(self, request: BindMobileToExternalContactRequest) -> JsonDict:
        normalized = normalize_mobile_binding_request(request)
        result = dict(
            self._repo.bind_mobile_to_external_contact(
                external_userid=normalized.external_userid,
                mobile=normalized.mobile,
                owner_userid=normalized.owner_userid or "",
                bind_by_userid=normalized.bind_by_userid or "",
                customer_name=normalized.customer_name or "",
                force_rebind=normalized.force_rebind,
            )
        )
        internal_event = safe_emit(
            "customer.phone_bound",
            emit_customer_phone_bound_event,
            request=normalized,
            binding_result=result,
        )
        result["internal_event_status"] = str(internal_event.get("status") or "")
        result["internal_event_id"] = str(internal_event.get("event_id") or "")
        if internal_event.get("reason"):
            result["internal_event_reason"] = str(internal_event.get("reason") or "")
        if internal_event.get("error"):
            result["internal_event_error"] = str(internal_event.get("error") or "")
        if internal_event.get("consumer_run_count") is not None:
            result["internal_event_consumer_run_count"] = int(internal_event.get("consumer_run_count") or 0)
        return result

    __call__ = execute


class LinkOpenidUnionidExternalUseridCommand:
    def __init__(self, identity_adapter=None) -> None:
        self._identity_adapter = identity_adapter or build_identity_mapping_adapter()

    def execute(
        self,
        *,
        external_userid: str,
        openid: str = "",
        unionid: str = "",
        corp_id: str = "",
        idempotency_key: str | None = None,
    ) -> JsonDict:
        return self._identity_adapter.link_openid_unionid_external_userid(
            external_userid=external_userid,
            openid=openid,
            unionid=unionid,
            corp_id=resolve_single_corp_id(corp_id),
            idempotency_key=idempotency_key,
        )

    __call__ = execute
