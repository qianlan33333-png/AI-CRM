from __future__ import annotations

import pytest

from aicrm_next.crm.identity_contact.application import (
    GetSidebarContactBindingStatusQuery,
    ListExternalContactOwnerCandidatesQuery,
    ResolvePersonIdentityQuery,
    UpsertIdentityMappingCommand,
)
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.crm.identity_contact.dto import IdentityResolution, ResolvePersonIdentityRequest


def test_identity_resolution_query_uses_next_fixture_repository():
    result = ResolvePersonIdentityQuery().execute(ResolvePersonIdentityRequest(external_userid="wx_ext_001"))

    assert result is not None
    assert result.person_id == "person_001"
    assert result.mobile == "13800138000"
    assert result.owner_userid == "ZhaoYanFang"


def test_identity_resolution_query_uses_postgres_repository_when_production_ready(monkeypatch):
    class FakeFixtureRepository:
        def resolve(self, query):  # pragma: no cover - should not be used in this test
            raise AssertionError("fixture repository should not resolve production identity")

    class FakePostgresRepository:
        def resolve(self, query):
            assert query.unionid == "unionid_live_001"
            return IdentityResolution(
                person_id=None,
                external_userid="wm_live_001",
                mobile=None,
                openid="openid_live_001",
                unionid="unionid_live_001",
                binding_status="bound",
                owner_userid="owner_live_001",
                identity_map_id=75550,
                follow_user_userid="owner_live_001",
                matched_by="unionid",
            )

    monkeypatch.setattr("aicrm_next.crm.identity_contact.application.production_data_ready", lambda: True)

    result = ResolvePersonIdentityQuery(
        repo=FakeFixtureRepository(),
        postgres_repo=FakePostgresRepository(),
    ).execute(ResolvePersonIdentityRequest(unionid=" unionid_live_001 "))

    assert result is not None
    assert result.external_userid == "wm_live_001"
    assert result.identity_map_id == 75550
    assert result.follow_user_userid == "owner_live_001"
    assert result.matched_by == "unionid"


def test_external_contact_owner_candidates_use_fixture_repository():
    owners = ListExternalContactOwnerCandidatesQuery().execute(external_userid="wx_ext_001")

    assert owners == {"ZhaoYanFang"}


def test_external_contact_owner_candidates_use_postgres_repository_when_production_ready(monkeypatch):
    class FakeFixtureRepository:
        def list_external_contact_owner_userids(self, external_userid):  # pragma: no cover - should not be used
            raise AssertionError("fixture repository should not resolve production owners")

    class FakePostgresRepository:
        def list_external_contact_owner_userids(self, external_userid):
            assert external_userid == "wx_ext_live"
            return {"owner-a", "owner-b"}

    monkeypatch.setattr("aicrm_next.crm.identity_contact.application.production_data_ready", lambda: True)

    owners = ListExternalContactOwnerCandidatesQuery(
        repo=FakeFixtureRepository(),
        postgres_repo=FakePostgresRepository(),
    ).execute(external_userid=" wx_ext_live ")

    assert owners == {"owner-a", "owner-b"}


def test_sidebar_contact_binding_status_query_is_next_owned():
    payload = GetSidebarContactBindingStatusQuery().execute(external_userid="wx_ext_001")

    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["source_status"] == "identity_contact"
    assert payload["is_bound"] is True


def test_identity_mapping_command_rejects_corp_override_before_adapter_call(monkeypatch) -> None:
    calls: list[dict] = []

    class Adapter:
        def upsert_identity_mapping(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    monkeypatch.setenv("WECOM_CORP_ID", "corp-configured")

    with pytest.raises(ContractError, match="corp_id_mismatch"):
        UpsertIdentityMappingCommand(identity_adapter=Adapter())(
            external_userid="external-r03-corp",
            unionid="union-r03-corp",
            corp_id="corp-request-override",
        )

    assert calls == []
