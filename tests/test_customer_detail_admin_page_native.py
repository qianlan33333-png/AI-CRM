from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _read_frontend(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _profile_result(
    *,
    external_userid: str = "ext_test_001",
    customer_name: str = "测试客户",
    mobile: str = "13800000000",
    owner: str = "HuangYouCan",
    owner_userid: str = "HuangYouCan",
    unionid: str = "union_test_001",
) -> dict:
    return {
        "ok": True,
        "profile": {
            "external_userid": external_userid,
            "user_id": external_userid,
            "customer_name": customer_name,
            "mobile": mobile,
            "owner": owner,
            "owner_userid": owner_userid,
            "unionid": unionid,
        },
        "lookup": {"resolved_by": "external_userid"},
    }


def _patch_profile_query(monkeypatch, result: dict) -> None:
    from aicrm_next.crm.customer_read_model import admin_pages

    class FakeGetAdminCustomerProfileQuery:
        def __call__(self, *, unionid=None, external_userid=None, mobile=None, user_id=None):
            return dict(result)

    monkeypatch.setattr(admin_pages, "GetAdminCustomerProfileQuery", FakeGetAdminCustomerProfileQuery)


def test_customer_list_profile_link_uses_detail_route_unionid(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import admin_pages

    class FakeListCustomersQuery:
        def __call__(self, query):
            return {
                "ok": True,
                "customers": [
                    {
                        "unionid": "union_test_001",
                        "external_userid": "",
                        "customer_name": "测试客户",
                        "owner_display_name": "HuangYouCan",
                        "mobile": "13800000000",
                    }
                ],
                "total": 1,
            }

    monkeypatch.setattr(admin_pages, "ListCustomersQuery", FakeListCustomersQuery)
    _patch_profile_query(monkeypatch, _profile_result())
    client = TestClient(create_app())
    response = client.get("/admin/customers")

    assert response.status_code == 200
    assert 'href="/admin/customers/union_test_001"' in response.text
    assert "/admin/customers?external_userid=" not in response.text

    detail_response = client.get("/admin/customers/union_test_001")
    assert detail_response.status_code == 200
    assert "测试客户" in detail_response.text


def test_customer_list_projection_preserves_unionid() -> None:
    from aicrm_next.crm.customer_read_model.projections import list_item_projection

    item = list_item_projection(
        {
            "identity": {"unionid": "union_test_001"},
            "external_userid": "",
            "customer_name": "测试客户",
        }
    )

    assert item["unionid"] == "union_test_001"


def test_customer_detail_page_renders_from_native_shell(monkeypatch) -> None:
    _patch_profile_query(monkeypatch, _profile_result())
    client = TestClient(create_app())

    response = client.get("/admin/customers/ext_test_001")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "测试客户" in response.text
    assert "13800000000" in response.text
    assert "HuangYouCan" in response.text
    assert "union_test_001" in response.text
    assert "data-customer-profile-root" in response.text
    assert 'data-external-userid="ext_test_001"' in response.text
    assert "data-tags-url=" in response.text
    assert "data-questionnaire-url=" in response.text
    assert "data-messages-url=" in response.text
    assert "data-automation-put-in-pool-url=" not in response.text
    assert "data-automation-remove-from-pool-url=" not in response.text
    assert "data-automation-set-focus-url=" not in response.text
    assert "data-automation-set-normal-url=" not in response.text
    assert "data-automation-mark-won-url=" not in response.text
    assert "data-automation-unmark-won-url=" not in response.text
    assert "data-automation-push-openclaw-url=" not in response.text
    assert "customer-automation-sidebar" not in response.text
    assert response.headers.get("x-aicrm-route-owner") == "ai_crm_next"


def test_customer_detail_page_tab_mapping_is_preserved(monkeypatch) -> None:
    _patch_profile_query(monkeypatch, _profile_result())
    client = TestClient(create_app())
    expected = {
        "tags": "customer-live-tags",
        "questionnaire": "customer-questionnaire-answers",
        "questionnaires": "customer-questionnaire-answers",
        "messages": "customer-message-records",
        "automation": "",
    }

    for tab, section in expected.items():
        response = client.get(f"/admin/customers/ext_test_001?tab={tab}")

        assert response.status_code == 200
        assert f'data-initial-section="{section}"' in response.text


def test_customer_detail_page_url_contract_is_preserved(monkeypatch) -> None:
    _patch_profile_query(monkeypatch, _profile_result())
    client = TestClient(create_app())

    response = client.get("/admin/customers/ext_test_001")

    assert response.status_code == 200
    for marker in (
        "/api/admin/customers/profile?unionid=union_test_001",
        "/api/admin/customers/profile/tags?unionid=union_test_001",
        "/api/admin/customers/profile/questionnaire-answers?unionid=union_test_001",
        "/api/admin/customers/profile/messages?unionid=union_test_001",
    ):
        assert marker in response.text
    for marker in (
        "/api/admin/automation-conversion/member?external_contact_id=ext_test_001",
        "/api/admin/automation-conversion/member/put-in-pool",
        "/api/admin/automation-conversion/member/remove-from-pool",
        "/api/admin/automation-conversion/member/set-focus",
        "/api/admin/automation-conversion/member/set-normal",
        "/api/admin/automation-conversion/member/mark-won",
        "/api/admin/automation-conversion/member/unmark-won",
        "/api/admin/automation-conversion/member/push-openclaw",
    ):
        assert marker not in response.text


def test_customer_detail_live_tags_frontend_accepts_string_tags() -> None:
    source = _read_frontend("aicrm_next/frontend_compat/static/admin_console/customer_profile_sections.js")
    css = _read_frontend("aicrm_next/frontend_compat/static/admin_console/admin_console.css")

    assert "function liveTagName(tag)" in source
    assert "tag.tag_name || tag.name || tag.label || tag.value || tag.text || tag.tag_id || tag.id" in source
    assert ".map(liveTagName)" in source
    assert 'normalized.toLowerCase() === "undefined"' in source
    assert "escapeHtml(tag)" in source
    assert "[hidden]" in css
    assert "display: none !important" in css


def test_customer_detail_live_tag_assets_are_cache_busted(monkeypatch) -> None:
    _patch_profile_query(monkeypatch, _profile_result())
    client = TestClient(create_app())

    response = client.get("/admin/customers/ext_test_001")

    assert response.status_code == 200
    assert "customer_profile_sections.js?v=customer-profile-live-tags-20260629" in response.text
    assert "customer_profile_core.js?v=customer-profile-live-tags-20260629" in response.text


def test_customer_detail_live_tag_payload_normalizes_object_tags() -> None:
    from aicrm_next.crm.customer_read_model.application import _normalized_admin_profile_tags

    assert _normalized_admin_profile_tags(
        [
            {"name": "大健康"},
            {"tag_name": "创始人/老板/合伙人"},
            {"label": "行业交流社群"},
            {"tag_name": "undefined", "tag_id": "et_fallback"},
            "undefined",
            "大健康",
            None,
        ]
    ) == ["大健康", "创始人/老板/合伙人", "行业交流社群", "et_fallback"]


def test_customer_detail_page_not_found_state_is_preserved(monkeypatch) -> None:
    _patch_profile_query(monkeypatch, {"ok": False, "status_code": 404, "error": "未找到客户"})
    client = TestClient(create_app())

    response = client.get("/admin/customers/not_found_ext")

    assert response.status_code == 404
    assert "客户不存在" in response.text
    assert "返回客户列表" in response.text
    assert "/admin/customers" in response.text


def test_customer_detail_page_normalizes_fallback_profile_fields(monkeypatch) -> None:
    _patch_profile_query(
        monkeypatch,
        {
            "ok": True,
            "profile": {
                "user_id": "ext_fallback_001",
                "remark": "备注客户",
                "identity": {
                    "mobile": "13900000000",
                    "unionid": "union_from_identity",
                },
            },
            "lookup": {"resolved_by": "external_userid"},
        },
    )
    client = TestClient(create_app())

    response = client.get("/admin/customers/ext_fallback_001")

    assert response.status_code == 200
    assert 'data-external-userid="ext_fallback_001"' in response.text
    assert "备注客户" in response.text
    assert "13900000000" in response.text
    assert "union_from_identity" in response.text


def test_customer_360_page_renders_from_native_shell(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import admin_pages

    class FakeCustomer360Query:
        def __call__(self, unionid):
            assert unionid == "union_test_001"
            return {
                "ok": True,
                "unionid": "union_test_001",
                "identity": {
                    "unionid": "union_test_001",
                    "external_userid": "ext_test_001",
                    "mobile": "13800000000",
                    "owner_userid": "HuangYouCan",
                },
                "orders_summary": {"paid_order_count": 2, "source_status": "test"},
                "questionnaire_summary": {"answer_count": 1},
                "message_summary": {"recent_message_count": 3},
                "tags": ["高意向"],
                "user_ops_status": {"current_status": "activated", "activation_bucket": "activated"},
                "automation_status": {"stage_key": "activated/high", "recommended_action": "继续跟进"},
                "recent_touchpoints": [
                    {
                        "touchpoint_key": "evt-1",
                        "touchpoint_type": "message",
                        "summary": "客户回复",
                        "source_table": "archived_messages",
                        "source_id": "m-1",
                        "occurred_at": "2026-07-02T09:30:00+08:00",
                    }
                ],
                "risk_flags": [],
            }

    monkeypatch.setattr(admin_pages, "GetCustomer360ProfileQuery", FakeCustomer360Query)
    response = TestClient(create_app()).get("/admin/customer-360/union_test_001")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "Customer 360" in response.text
    assert 'data-customer-360-root' in response.text
    assert "union_test_001" in response.text
    assert "ext_test_001" in response.text
    assert "高意向" in response.text
    assert "archived_messages #m-1" in response.text


def test_customer_360_page_not_found_state(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model import admin_pages

    class MissingCustomer360Query:
        def __call__(self, unionid):
            return {"ok": False, "status_code": 404, "error": "customer not found"}

    monkeypatch.setattr(admin_pages, "GetCustomer360ProfileQuery", MissingCustomer360Query)
    response = TestClient(create_app()).get("/admin/customer-360/missing_union")

    assert response.status_code == 404
    assert "Customer 360 不可用" in response.text
    assert "返回客户列表" in response.text
