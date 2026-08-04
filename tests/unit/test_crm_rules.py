from __future__ import annotations

import pytest

from aicrm_next.crm.customer_tags.mutation_commands import PlanWeComTagMarkCommand
from aicrm_next.crm.identity_contact.domain import (
    normalize_identity_request,
    normalize_mainland_mobile,
    normalize_mobile_binding_request,
    resolve_single_corp_id,
)
from aicrm_next.crm.identity_contact.dto import BindMobileToExternalContactRequest, ResolvePersonIdentityRequest
from aicrm_next.crm.sidebar_write.commands import BindMobileCommand
from aicrm_next.platform.shared.errors import ContractError


pytestmark = pytest.mark.unit


def test_identity_request_normalizes_current_alias_inputs() -> None:
    request = normalize_identity_request(
        ResolvePersonIdentityRequest(
            external_userid=" ext-1 ",
            mobile="+86 138-0013-8000",
            openid=" open-1 ",
            unionid=" union-1 ",
        )
    )
    assert request.model_dump() == {
        "external_userid": "ext-1",
        "mobile": "13800138000",
        "openid": "open-1",
        "unionid": "union-1",
    }


def test_mobile_binding_requires_customer_and_valid_mobile() -> None:
    normalized = normalize_mobile_binding_request(
        BindMobileToExternalContactRequest(
            external_userid=" ext-1 ",
            mobile="138 0013 8000",
            owner_userid=" owner-1 ",
        )
    )
    assert normalized.external_userid == "ext-1"
    assert normalized.mobile == "13800138000"
    with pytest.raises(ContractError, match="external_userid is required"):
        normalize_mobile_binding_request(BindMobileToExternalContactRequest(external_userid=" ", mobile="13800138000"))
    with pytest.raises(ContractError, match="valid mainland"):
        normalize_mainland_mobile("10086")


def test_single_corp_request_cannot_override_runtime_corp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECOM_CORP_ID", "corp-current")
    assert resolve_single_corp_id("") == "corp-current"
    with pytest.raises(ContractError, match="corp_id_mismatch"):
        resolve_single_corp_id("corp-other")


def test_commands_keep_business_name_out_of_provider_payload() -> None:
    sidebar = BindMobileCommand(idempotency_key="bind:1", external_userid="ext-1", payload={"mobile": "13800138000"})
    tag = PlanWeComTagMarkCommand(idempotency_key="tag:1", external_userid="ext-1", tag_ids=["tag-a"])
    assert sidebar.command_name == "sidebar.bind_mobile"
    assert tag.command_name == "wecom.tag.mark"
    assert "command_name" not in sidebar.to_payload()
    assert "command_name" not in tag.to_payload()
