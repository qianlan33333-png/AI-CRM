from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_read_model.application import GetCustomerContextQuery
from aicrm_next.crm.customer_read_model.dto import CustomerContextRequest
from aicrm_next.integration_gateway.dispatch import McpToolDispatcher
from tests.sidebar_auth_test_helpers import install_sidebar_auth


def _context_payload(external_userid: str = "wx_ext_001") -> dict:
    return {
        "ok": True,
        "external_userid": external_userid,
        "customer": {"external_userid": external_userid, "tags": ["重点跟进"], "binding": {"is_bound": True}},
        "profile": {"external_userid": external_userid},
        "identity_binding_summary": {"is_bound": True, "external_userid": external_userid},
        "binding": {"is_bound": True},
        "identity": {"external_userid": external_userid},
        "recent_messages": [{"msgid": "msg_001"}],
        "recent_timeline_events": [{"event_type": "message"}],
        "timeline": {"external_userid": external_userid, "items": [{"event_type": "message"}], "count": 1},
        "source_status": "local_contract_probe",
        "degraded": False,
        "page_error": "",
        "warnings": [],
        "adapter_contract": {},
        "side_effect_safety": {"real_external_call_executed": False},
    }


def test_customer_context_query_unifies_detail_timeline_messages_and_binding(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "development")

    result = GetCustomerContextQuery()(CustomerContextRequest(external_userid="wx_ext_001", recent_message_limit=2, timeline_limit=2))

    assert result["ok"] is True
    assert result["source_status"] == "local_contract_probe"
    assert result["customer"]["external_userid"] == "wx_ext_001"
    assert result["profile"] == result["customer"]
    assert result["timeline"]["external_userid"] == "wx_ext_001"
    assert result["recent_timeline_events"] == result["timeline"]["items"]
    assert isinstance(result["recent_messages"], list)
    assert result["identity_binding_summary"]["is_bound"] is True
    assert result["degraded"] is False
    assert result["page_error"] == ""


def test_customer_context_query_does_not_return_fixture_success_when_production_ready(monkeypatch):
    from aicrm_next.crm.customer_read_model import application

    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ctx:ctx@127.0.0.1:1/aicrm_ctx")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setattr(
        application,
        "build_customer_read_model_repository",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres unavailable")),
    )

    result = GetCustomerContextQuery()(CustomerContextRequest(external_userid="wx_ext_001"))

    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["source_status"] == "production_unavailable"
    assert result["customer"] == {}
    assert result["recent_messages"] == []
    assert "local_contract" not in str(result)


def test_sidebar_and_profile_readonly_routes_reuse_customer_context_query(monkeypatch):
    from aicrm_next.crm.customer_read_model import application as customer_application
    from aicrm_next.crm.customer_read_model import api as customer_api
    from aicrm_next.main import create_app

    calls: list[CustomerContextRequest] = []

    class FakeGetCustomerContextQuery:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, request: CustomerContextRequest):
            calls.append(request)
            return _context_payload(request.external_userid)

    monkeypatch.setattr(customer_api, "GetCustomerContextQuery", FakeGetCustomerContextQuery)
    monkeypatch.setattr(customer_application, "GetCustomerContextQuery", FakeGetCustomerContextQuery)
    client = TestClient(create_app())
    client.headers.update(
        install_sidebar_auth(
            client,
            viewer_userid="ZhaoYanFang",
            external_userid="wx_ext_001",
        )
    )

    sidebar_response = client.get("/api/sidebar/customer-context?external_userid=wx_ext_001&owner_userid=ZhaoYanFang")
    profile_response = client.get("/api/admin/customers/profile?external_userid=wx_ext_001")
    tags_response = client.get("/api/admin/customers/profile/tags?external_userid=wx_ext_001")

    assert sidebar_response.status_code == 200
    assert sidebar_response.json()["context"]["recent_messages"] == [{"msgid": "msg_001"}]
    assert profile_response.status_code == 200
    assert profile_response.json()["context"]["timeline"]["count"] == 1
    assert tags_response.status_code == 200
    assert tags_response.json()["tags"] == ["重点跟进"]
    assert [call.external_userid for call in calls] == ["wx_ext_001", "wx_ext_001", "wx_ext_001"]


def test_admin_customer_profile_route_json_encodes_legacy_scalar_values(monkeypatch):
    from aicrm_next.crm.customer_read_model import application as customer_application
    from aicrm_next.crm.customer_read_model import api as customer_api
    from aicrm_next.main import create_app

    class FakeGetCustomerContextQuery:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, request: CustomerContextRequest):
            payload = _context_payload(request.external_userid)
            payload["customer"]["updated_at"] = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
            payload["customer"]["marketing_profile"] = {"score": Decimal("9.5")}
            payload["timeline"]["items"][0]["event_time"] = datetime(2026, 5, 26, 9, 1, tzinfo=timezone.utc)
            return payload

    monkeypatch.setattr(customer_api, "GetCustomerContextQuery", FakeGetCustomerContextQuery)
    monkeypatch.setattr(customer_application, "GetCustomerContextQuery", FakeGetCustomerContextQuery)
    client = TestClient(create_app())

    response = client.get("/api/admin/customers/profile?external_userid=wx_ext_001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["updated_at"] == "2026-05-26T09:00:00+00:00"
    assert payload["profile"]["marketing_profile"]["score"] == 9.5
    assert payload["context"]["timeline"]["items"][0]["event_time"] == "2026-05-26T09:01:00+00:00"


def test_mcp_get_customer_context_reuses_customer_context_query(monkeypatch):
    calls: list[CustomerContextRequest] = []

    class FakeGetCustomerContextQuery:
        def __call__(self, request: CustomerContextRequest):
            calls.append(request)
            return _context_payload(request.external_userid)

    payload = McpToolDispatcher(
        customer_context_query=lambda external_userid, recent_limit, timeline_limit: FakeGetCustomerContextQuery()(
            CustomerContextRequest(
                external_userid=external_userid,
                recent_message_limit=recent_limit,
                timeline_limit=timeline_limit,
            )
        )
    ).dispatch("get_customer_context", {"external_userid": "wx_ext_001", "request_id": "req_001"})

    assert payload["customer"]["external_userid"] == "wx_ext_001"
    assert payload["recent_messages"] == [{"msgid": "msg_001"}]
    assert calls and calls[0].external_userid == "wx_ext_001"


def test_admin_customer_detail_page_uses_context_backed_profile_route():
    from aicrm_next.main import create_app

    response = TestClient(create_app()).get("/admin/customers/wx_ext_001", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers.get("location", "") == ""
    assert "客户档案" in response.text
    assert "/api/admin/customers/profile/tags?unionid=union_customer_001" in response.text
