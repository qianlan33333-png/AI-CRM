from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_read_model.application import GetCustomer360ProfileQuery
from aicrm_next.crm.customer_read_model.dto import CustomerContextRequest
from aicrm_next.main import create_app


class FakeCustomerContextQuery:
    def __call__(self, request: CustomerContextRequest) -> dict:
        assert request.unionid == "union_customer_001"
        assert not request.external_userid
        return {
            "ok": True,
            "unionid": request.unionid,
            "source_status": "test_context",
            "read_model_status": "fixture",
            "customer": {
                "unionid": "union_customer_001",
                "external_userid": "wx_ext_001",
                "owner_userid": "owner-1",
                "updated_at": "2026-07-02T10:00:00+08:00",
                "last_message_at": "2026-07-02T09:30:00+08:00",
                "tags": ["高意向"],
                "identity": {
                    "unionid": "union_customer_001",
                    "external_userid": "wx_ext_001",
                    "openid": "openid-1",
                    "mobile": "13800138000",
                },
                "class_user_status": {"current_status": "activated", "activation_bucket": "activated"},
                "marketing_summary": {"value_segment": "high"},
                "marketing_profile": {
                    "stage_key": "activated/high",
                    "recommended_action": "继续跟进",
                    "signals": ["paid_intent"],
                    "matched_questions": [
                        {
                            "questionnaire_id": "q-1",
                            "submission_id": "s-1",
                            "submitted_at": "2026-07-01T10:00:00+08:00",
                            "question": "目标",
                            "answer": "提升表达",
                        }
                    ],
                },
            },
            "identity_binding_summary": {"binding_status": "bound", "owner_userid": "owner-1"},
            "recent_messages": [{"msgid": "m-1", "send_time": "2026-07-02T09:30:00+08:00"}],
            "timeline": {
                "items": [
                    {
                        "event_id": "evt-1",
                        "event_type": "message",
                        "event_time": "2026-07-02T09:30:00+08:00",
                        "summary": "客户回复",
                        "source_table": "archived_messages",
                        "source_id": "m-1",
                    }
                ]
            },
        }


def test_customer_360_profile_query_uses_unionid_only_and_shapes_sections() -> None:
    payload = GetCustomer360ProfileQuery(FakeCustomerContextQuery())("union_customer_001")

    assert payload["ok"] is True
    assert payload["unionid"] == "union_customer_001"
    assert payload["identity"]["unionid"] == "union_customer_001"
    assert payload["identity"]["external_userid"] == "wx_ext_001"
    assert payload["orders_summary"]["source_status"] == "not_connected"
    assert payload["questionnaire_summary"]["answer_count"] == 1
    assert payload["message_summary"]["recent_message_count"] == 1
    assert payload["tags"] == ["高意向"]
    assert payload["user_ops_status"]["current_status"] == "activated"
    assert payload["automation_status"]["stage_key"] == "activated/high"
    assert payload["recent_touchpoints"][0]["source_table"] == "archived_messages"
    assert payload["risk_flags"] == []


def test_customer_360_admin_api_returns_fixture_profile_without_external_join(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    response = TestClient(create_app(), raise_server_exceptions=False).get("/api/admin/customer-360/union_customer_001")
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["unionid"] == "union_customer_001"
    assert set(payload) >= {
        "identity",
        "orders_summary",
        "questionnaire_summary",
        "message_summary",
        "tags",
        "user_ops_status",
        "automation_status",
        "recent_touchpoints",
        "risk_flags",
    }
    assert "external_userid" in payload["identity"]


def test_customer_360_sources_do_not_reference_retired_automation_tables() -> None:
    import inspect

    from aicrm_next.crm.customer_read_model import application

    source = inspect.getsource(application.GetCustomer360ProfileQuery) + inspect.getsource(application._customer_360_automation_status)

    assert "automation_membership_v2" not in source
    assert "automation_task_plan_v2" not in source
