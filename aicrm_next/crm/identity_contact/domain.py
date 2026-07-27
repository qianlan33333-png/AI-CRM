from __future__ import annotations

import re

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime_settings import managed_runtime_setting

from .dto import BindMobileToExternalContactRequest, ResolvePersonIdentityRequest


def resolve_single_corp_id(requested_corp_id: str | None) -> str:
    configured = str(managed_runtime_setting("WECOM_CORP_ID") or "").strip()
    requested = str(requested_corp_id or "").strip()
    if configured and requested and configured != requested:
        raise ContractError("corp_id_mismatch")
    return configured or requested


def normalize_identity_request(query: ResolvePersonIdentityRequest) -> ResolvePersonIdentityRequest:
    mobile = (query.mobile or "").strip()
    return ResolvePersonIdentityRequest(
        external_userid=(query.external_userid or "").strip() or None,
        mobile=normalize_mainland_mobile(mobile) if mobile else None,
        openid=(query.openid or "").strip() or None,
        unionid=(query.unionid or "").strip() or None,
    )


def normalize_mainland_mobile(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    if not re.fullmatch(r"1\d{10}", digits):
        raise ContractError("mobile must be a valid mainland China mobile number")
    return digits


def normalize_mobile_binding_request(request: BindMobileToExternalContactRequest) -> BindMobileToExternalContactRequest:
    external_userid = str(request.external_userid or "").strip()
    if not external_userid:
        raise ContractError("external_userid is required")
    return BindMobileToExternalContactRequest(
        external_userid=external_userid,
        mobile=normalize_mainland_mobile(request.mobile),
        owner_userid=str(request.owner_userid or "").strip() or None,
        bind_by_userid=str(request.bind_by_userid or "").strip() or None,
        customer_name=str(request.customer_name or "").strip() or None,
        force_rebind=bool(request.force_rebind),
    )
