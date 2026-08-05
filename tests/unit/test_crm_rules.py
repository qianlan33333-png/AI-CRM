from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import time

import pytest

from aicrm_next.crm.customer_read_model import admin_business_profile, api as customer_read_api
from aicrm_next.crm.customer_read_model.application import _assert_customer_owner_scope
from aicrm_next.crm.customer_read_model.errors import CustomerScopeForbiddenError
from aicrm_next.crm.customer_tags.mutation_commands import PlanWeComTagMarkCommand
from aicrm_next.crm.identity_contact.domain import (
    normalize_identity_request,
    normalize_mainland_mobile,
    normalize_mobile_binding_request,
    resolve_single_corp_id,
)
from aicrm_next.crm.identity_contact.dto import BindMobileToExternalContactRequest, ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.sidebar_authorization import SidebarAuthorizationService
from aicrm_next.crm.customer_read_model.sidebar_v2 import SidebarQuestionnaireReadModel, SidebarWorkbenchReadModel, _assert_snapshot_owner_scope
from aicrm_next.crm.sidebar_write.commands import BindMobileCommand
from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from aicrm_next.platform.shared.signed_context import build_sidebar_owner_context_token, validate_sidebar_owner_context
from aicrm_next.platform.shared.signed_session import sign_session_payload


pytestmark = pytest.mark.unit


def test_sidebar_questionnaire_other_option_keeps_respondent_detail() -> None:
    read_model = SidebarQuestionnaireReadModel()

    grouped = read_model._group(
        [
            {
                "submission_id": "submission-1",
                "questionnaire_id": "questionnaire-1",
                "questionnaire_title": "来源问卷",
                "submitted_at": "2026-08-05T10:00:00+08:00",
                "question": "通过什么渠道了解？",
                "selected_option_texts_snapshot": ["其他"],
                "text_value": "线下社群推荐",
            }
        ]
    )
    assert grouped[0]["answers"] == [{"question": "通过什么渠道了解？", "answer": "其他（线下社群推荐）"}]
    assert read_model._answer_text(
        {
            "selected_option_texts_snapshot": ["公开课", "其他"],
            "text_value": "朋友转发",
        }
    ) == "公开课、其他（朋友转发）"
    assert read_model._answer_text({"selected_option_texts_snapshot": [], "text_value": "自由填写内容"}) == "自由填写内容"
    fallback_row = {"selected_option_texts_snapshot": ["其他"], "text_value": "同学推荐"}
    assert customer_read_api._questionnaire_answer_text(fallback_row) == "其他（同学推荐）"
    assert admin_business_profile._questionnaire_answer_text(fallback_row) == "其他（同学推荐）"


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


class RecordingRelationRepository:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0
        self.lock = Lock()
        self.started = Event()
        self.release = Event()
        self.release.set()

    def has_active_follow_relation(self, *, corp_id: str, user_id: str, external_userid: str) -> bool:
        with self.lock:
            self.calls += 1
            self.started.set()
        self.release.wait(timeout=2)
        return self.allowed


def test_sidebar_relationship_cache_uses_short_negative_ttl_and_singleflight() -> None:
    now = [100.0]
    allowed_repo = RecordingRelationRepository(True)
    denied_repo = RecordingRelationRepository(False)
    allowed = SidebarAuthorizationService(allowed_repo, clock=lambda: now[0])
    denied = SidebarAuthorizationService(denied_repo, clock=lambda: now[0])
    key = {"corp_id": "corp", "user_id": "staff", "external_userid": "customer"}
    assert allowed.authorize(**key) is True
    assert denied.authorize(**key) is False
    now[0] += 31
    assert allowed.authorize(**key) is True
    assert denied.authorize(**key) is False
    assert allowed_repo.calls == 1
    assert denied_repo.calls == 2

    allowed.invalidate(**key)
    allowed_repo.release.clear()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(allowed.authorize, **key) for _ in range(8)]
        assert allowed_repo.started.wait(timeout=1)
        allowed_repo.release.set()
        assert [future.result(timeout=2) for future in futures] == [True] * 8
    assert allowed_repo.calls == 2


def test_sidebar_relationship_repository_errors_fail_closed() -> None:
    class BrokenRepository:
        def has_active_follow_relation(self, **_kwargs) -> bool:
            raise RuntimeError("database unavailable")

    service = SidebarAuthorizationService(BrokenRepository())
    assert service.authorize(corp_id="corp", user_id="staff", external_userid="customer") is False
    assert service.snapshot()["repository_error"] == 1


def test_viewer_session_can_switch_customers_without_cross_customer_token_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-context-switch")
    session = sign_session_payload(
        {
            "auth_source": "wecom_sidebar_oauth",
            "wecom_userid": "owner-a",
            "session_id": "session-a",
            "corp_id": "corp-a",
            "iat": int(time()),
        }
    )
    token_a = build_sidebar_owner_context_token(
        viewer_userid="owner-a", external_userid="external-a", session_id="session-a", corp_id="corp-a"
    )
    token_b = build_sidebar_owner_context_token(
        viewer_userid="owner-a", external_userid="external-b", session_id="session-a", corp_id="corp-a"
    )
    assert validate_sidebar_owner_context(token=token_a, viewer_session_cookie=session, external_userid="external-a")["ok"]
    assert validate_sidebar_owner_context(token=token_b, viewer_session_cookie=session, external_userid="external-b")["ok"]
    replay = validate_sidebar_owner_context(token=token_a, viewer_session_cookie=session, external_userid="external-b")
    assert replay["ok"] is False
    assert replay["status"].endswith("scope_forbidden")
    assert replay["context"] == {}


def test_trusted_sidebar_context_skips_lagging_follow_relation_projections() -> None:
    customer = {"identity": {"follow_user_userid": "staff-from-stale-projection"}}
    with pytest.raises(CustomerScopeForbiddenError):
        _assert_customer_owner_scope(customer, "staff-in-oauth-session", require_owner=True)
    _assert_customer_owner_scope(
        customer,
        "staff-in-oauth-session",
        require_owner=True,
        owner_verified=True,
    )

    class SnapshotRepository:
        def get_contact_owner_userids(self, _external_userid: str) -> list[str]:
            return ["staff-from-stale-projection"]

    with pytest.raises(CustomerScopeForbiddenError):
        _assert_snapshot_owner_scope(
            repo=SnapshotRepository(),
            external_userid="external-a",
            owner_userid="staff-in-oauth-session",
            owner_verified=False,
            contact={},
            identity={},
            binding={},
        )
    _assert_snapshot_owner_scope(
        repo=SnapshotRepository(),
        external_userid="external-a",
        owner_userid="staff-in-oauth-session",
        owner_verified=True,
        contact={},
        identity={},
        binding={},
    )


def test_trusted_sidebar_context_can_use_live_customer_source_when_projection_is_missing() -> None:
    def missing_projection(_request: object) -> dict:
        raise NotFoundError("customer not found")

    class LiveSource:
        def get_customer(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "当前企微客户"}

    model = SidebarWorkbenchReadModel(context_query=missing_projection, live_source_repo=LiveSource())
    context, diagnostics = model._context(
        "external-a",
        owner_userid="staff-in-oauth-session",
        owner_verified=True,
    )
    assert context["customer"]["external_userid"] == "external-a"
    assert diagnostics["context_source_status"] == "live_source"

    with pytest.raises(NotFoundError):
        model._context(
            "external-a",
            owner_userid="staff-in-oauth-session",
            owner_verified=False,
        )
