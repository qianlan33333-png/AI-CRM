from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import create_engine, text

from aicrm_next.commerce.repo import PostgresCommerceRepository
from aicrm_next.customer_read_model import api as customer_read_model_api
from aicrm_next.customer_read_model import sidebar_v2
from aicrm_next.customer_read_model.application import GetCustomerContextQuery
from aicrm_next.customer_read_model.dto import CustomerContextRequest
from aicrm_next.customer_read_model.sidebar_v2 import (
    SidebarCommerceReadModel,
    SidebarQuestionnaireReadModel,
    SidebarV2SqlRepository,
    SidebarWorkbenchReadModel,
)
from aicrm_next.main import create_app
from aicrm_next.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.shared.errors import NotFoundError
from tests.sidebar_auth_test_helpers import install_sidebar_auth


def _client(
    monkeypatch,
    *,
    external_userid: str = "wx_ext_001",
    viewer_userid: str = "ZhaoYanFang",
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.headers.update(
        install_sidebar_auth(
            client,
            viewer_userid=viewer_userid,
            external_userid=external_userid,
        )
    )
    return client


def _unauthenticated_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_next(payload: dict) -> None:
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False


def test_sidebar_owner_scope_returns_resolved_context_without_loading_activity() -> None:
    requests: list[CustomerContextRequest] = []

    class ContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            requests.append(request)
            return {
                "ok": True,
                "external_userid": "wm-canonical",
                "unionid": "union-canonical",
            }

    context = customer_read_model_api._verify_sidebar_owner_scope(
        ContextQuery(),
        external_userid="wm-alias",
        owner_userid="owner-a",
        corp_id="ww-test",
        owner_verified=True,
    )

    assert context.external_userid == "wm-canonical"
    assert context.unionid == "union-canonical"
    assert context.owner_userid == "owner-a"
    assert context.corp_id == "ww-test"
    assert context.owner_verified is True
    assert len(requests) == 1
    assert requests[0].external_userid == "wm-alias"
    assert requests[0].include_activity is False


def test_sidebar_workflow_title_uses_preserved_channel_link_tables_after_retirement() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _sqlite_jsonb_exists(dbapi_connection, _connection_record):
        dbapi_connection.create_function("jsonb_exists", 2, lambda _payload, _value: 0)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE crm_user_identity (
                    unionid TEXT PRIMARY KEY,
                    primary_external_userid TEXT NOT NULL,
                    external_userids_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE automation_channel_contact (
                    id INTEGER PRIMARY KEY,
                    unionid TEXT NOT NULL,
                    channel_id INTEGER,
                    updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE automation_channel (
                    id INTEGER PRIMARY KEY,
                    channel_code TEXT,
                    channel_name TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE wecom_customer_acquisition_links (
                    id INTEGER PRIMARY KEY,
                    automation_channel_id INTEGER,
                    link_name TEXT,
                    initial_audience_code TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO crm_user_identity (unionid, primary_external_userid, external_userids_json)
                VALUES ('union_sidebar_retired', 'wx_ext_retired', '["wx_ext_retired"]')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO automation_channel_contact (id, unionid, channel_id, updated_at)
                VALUES (1, 'union_sidebar_retired', 11, '2026-06-25 10:00:00')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO automation_channel (id, channel_code, channel_name)
                VALUES (11, 'channel-11', 'Fallback Channel')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO wecom_customer_acquisition_links (
                    id, automation_channel_id, link_name, initial_audience_code, updated_at
                )
                VALUES (21, 11, 'Preserved Link Name', 'audience-21', '2026-06-25 10:01:00')
                """
            )
        )

    assert (
        SidebarV2SqlRepository(engine=engine).get_workflow_title_for_customer("wx_ext_retired")
        == "Preserved Link Name"
    )
    source = inspect.getsource(SidebarV2SqlRepository.get_workflow_title_for_customer)
    assert "automation_member" not in source
    assert "automation_channel_contact" in source
    assert "_resolve_identity" in source


def test_sidebar_user_visible_read_paths_do_not_join_retired_automation_tables() -> None:
    sidebar_source = inspect.getsource(SidebarV2SqlRepository.get_workflow_title_for_customer)
    commerce_source = inspect.getsource(PostgresCommerceRepository.list_lead_channels)

    assert "automation_workflow" not in sidebar_source
    assert "automation_program" not in sidebar_source
    assert "automation_program" not in commerce_source


def test_sidebar_v2_workbench_and_read_panels_are_next_owned(monkeypatch):
    client = _client(monkeypatch)

    owner_query = "external_userid=wx_ext_001&owner_userid=ZhaoYanFang"
    workbench = client.get(f"/api/sidebar/v2/workbench?{owner_query}")
    questionnaires = client.get(f"/api/sidebar/v2/questionnaires?{owner_query}")
    products = client.get(f"/api/sidebar/v2/products?{owner_query}")
    orders = client.get(f"/api/sidebar/v2/orders?{owner_query}")
    periodic_orders = client.get(f"/api/sidebar/v2/periodic-orders?{owner_query}")

    for response in (workbench, questionnaires, products, orders, periodic_orders):
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.headers["Cache-Control"] == "no-store, max-age=0"
        payload = response.json()
        _assert_next(payload)
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        assert payload["source_status"] in {"next_read_model", "production_unavailable"}


def test_sidebar_v2_rejects_query_owner_impersonation(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "sidebar-v2-owner-token")
    client = _client(monkeypatch)

    response = client.get(
        "/api/sidebar/v2/products?external_userid=wx_ext_001&owner_userid=LiuXiao",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "sidebar owner scope forbidden"


def test_sidebar_v2_workbench_rejects_missing_viewer_without_returning_customer(monkeypatch):
    class FakeSidebarRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "只读客户", "owner_userid": ""}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": ""}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": False, "external_userid": external_userid}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return set()

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    monkeypatch.setattr("aicrm_next.customer_read_model.api.SidebarV2SqlRepository", FakeSidebarRepo)
    monkeypatch.setattr("aicrm_next.customer_read_model.sidebar_v2.SidebarV2SqlRepository", FakeSidebarRepo)
    client = _unauthenticated_client(monkeypatch)

    response = client.get("/api/sidebar/v2/workbench?external_userid=wx_ext_001")

    assert response.status_code == 403
    payload = response.json()
    assert payload == {"detail": "sidebar context required"}
    assert "customer" not in payload


def test_sidebar_v2_empty_owner_scope_rejects_all_panels_without_pii(monkeypatch):
    class EmptySidebarRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": False, "external_userid": external_userid}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return set()

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

        def list_questionnaire_answers(self, *, external_userid: str, mobile: str = "") -> list[dict]:
            return []

        def list_customer_wechat_pay_orders(self, *, external_userid: str, mobile: str = "", limit: int = 20) -> list[dict]:
            return []

        def list_customer_service_period_orders(self, *, external_userid: str, mobile: str = "", limit: int = 20) -> list[dict]:
            return []

    class FakeCommerceRepo:
        def list_sidebar_active_products(self, *, limit: int = 100, offset: int = 0) -> dict:
            return {
                "items": [
                    {
                        "id": 1,
                        "product_code": "trial",
                        "title": "体验月",
                        "price_cents": 990,
                        "enabled": True,
                        "status": "active",
                    }
                ]
            }

    monkeypatch.setattr("aicrm_next.customer_read_model.api.SidebarV2SqlRepository", EmptySidebarRepo)
    monkeypatch.setattr("aicrm_next.customer_read_model.sidebar_v2.SidebarV2SqlRepository", EmptySidebarRepo)
    monkeypatch.setattr("aicrm_next.customer_read_model.sidebar_v2.build_commerce_repository", lambda: FakeCommerceRepo())
    client = _unauthenticated_client(monkeypatch)

    workbench = client.get("/api/sidebar/v2/workbench?external_userid=wx_ext_missing")
    questionnaires = client.get("/api/sidebar/v2/questionnaires?external_userid=wx_ext_missing")
    orders = client.get("/api/sidebar/v2/orders?external_userid=wx_ext_missing")
    periodic_orders = client.get("/api/sidebar/v2/periodic-orders?external_userid=wx_ext_missing")
    products = client.get("/api/sidebar/v2/products?external_userid=wx_ext_missing")

    for response in (workbench, questionnaires, orders, periodic_orders, products):
        assert response.status_code == 403
        payload = response.json()
        assert payload == {"detail": "sidebar context required"}
        assert "customer" not in payload


def test_sidebar_products_include_service_period_products_with_signed_links(monkeypatch) -> None:
    class FakeCommerceRepo:
        def list_sidebar_active_products(self, *, limit: int = 100, offset: int = 0) -> dict:
            return {
                "items": [
                    {
                        "id": "trade_regular",
                        "product_code": "regular_001",
                        "title": "普通商品",
                        "price_cents": 19900,
                        "enabled": True,
                        "status": "active",
                    }
                ]
            }

    class FakeListServicePeriodProductsQuery:
        def __call__(self, *, limit: int = 100, offset: int = 0) -> dict:
            return {
                "items": [
                    {
                        "id": "sp_active",
                        "trade_product_id": "trade_periodic",
                        "title": "季度周期商品",
                        "price_cents": 99900,
                        "duration_days": 90,
                        "link_slug": "periodic-quarter",
                        "enabled": True,
                        "status": "active",
                    },
                    {
                        "id": "sp_disabled",
                        "trade_product_id": "trade_disabled",
                        "title": "禁用周期商品",
                        "duration_days": 30,
                        "link_slug": "periodic-disabled",
                        "enabled": False,
                        "status": "active",
                    },
                    {
                        "id": "sp_draft",
                        "trade_product_id": "trade_draft",
                        "title": "草稿周期商品",
                        "duration_days": 30,
                        "link_slug": "periodic-draft",
                        "enabled": True,
                        "status": "draft",
                    },
                ]
            }

    monkeypatch.setattr(sidebar_v2, "build_commerce_repository", lambda: FakeCommerceRepo())
    monkeypatch.setattr(sidebar_v2, "ListServicePeriodProductsQuery", lambda: FakeListServicePeriodProductsQuery())

    payload = SidebarCommerceReadModel().products(
        external_userid="wx_periodic_product",
        owner_userid="HuangYouCan",
        bind_by_userid="HuangYouCan",
    )

    assert [item["id"] for item in payload["products"]] == ["regular_001"]
    assert len(payload["service_period_products"]) == 1
    periodic = payload["service_period_products"][0]
    assert periodic["id"] == "sp_active"
    assert periodic["service_product_id"] == "sp_active"
    assert periodic["trade_product_id"] == "trade_periodic"
    assert periodic["title"] == "季度周期商品"
    assert periodic["price_label"] == "¥999"
    assert periodic["duration_days"] == 90
    assert periodic["link_slug"] == "periodic-quarter"
    assert periodic["product_url"].startswith("/s/periodic-quarter#aicrm_ctx=")
    assert periodic["context_status"] == "signed"


def test_sidebar_v2_profile_context_and_binding_status_use_next_read_models(monkeypatch):
    client = _client(monkeypatch)

    context = client.get("/api/sidebar/customer-context?external_userid=wx_ext_001&owner_userid=ZhaoYanFang").json()
    profile = client.get("/api/sidebar/profile?external_userid=wx_ext_001&owner_userid=ZhaoYanFang").json()
    binding = client.get("/api/sidebar/contact-binding-status?external_userid=wx_ext_001&owner_userid=ZhaoYanFang").json()

    for payload in (context, profile, binding):
        assert payload["ok"] is True
        _assert_next(payload)
    assert context["context"]["customer"]["external_userid"] == "wx_ext_001"
    assert profile["profile"]["external_userid"] == "wx_ext_001"
    assert binding["is_bound"] is True
    assert binding["mobile"] == "13800138000"


def test_sidebar_jssdk_config_is_fake_safe(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/api/sidebar/jssdk-config?url=https://example.com/sidebar/bind-mobile")
    payload = response.json()

    assert response.status_code == 200
    _assert_next(payload)
    assert payload["source_status"] == "next_jssdk_adapter"
    assert payload["real_external_call_executed"] is False
    assert "getCurExternalContact" in payload["jsApiList"]


def test_sidebar_bind_mobile_command_stays_local_only(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": "wx_ext_001", "mobile": "13800138000", "owner_userid": "ZhaoYanFang"},
    )
    payload = response.json()

    assert response.status_code == 200
    _assert_next(payload)
    assert payload["real_external_call_executed"] is False
    assert payload["source_status"] == "next_command"


def test_sidebar_orders_expose_wechat_shop_channel_fields() -> None:
    class FakeContextQuery:
        def __init__(self) -> None:
            self.requests = []

        def __call__(self, request: CustomerContextRequest) -> dict:
            self.requests.append(request)
            return {
                "ok": True,
                "source_status": "fixture",
                "customer": {
                    "external_userid": request.external_userid,
                    "customer_name": "微信小店客户",
                    "owner_userid": "HuangYouCan",
                    "mobile": "18028720840",
                    "binding": {},
                },
            }

    class FakeRepo:
        def __init__(self) -> None:
            self.order_calls = []
            self.unexpected_workbench_calls = []

        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "微信小店客户"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            self.unexpected_workbench_calls.append(("profile", external_userid))
            return {}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": False, "external_userid": external_userid}

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict:
            return {"mobile_snapshot": "18028720840", "order_count": 1}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            self.unexpected_workbench_calls.append(("workflow", external_userid))
            return ""

        def list_customer_wechat_pay_orders(self, *, external_userid: str, mobile: str = "", limit: int = 20) -> list[dict]:
            self.order_calls.append({"external_userid": external_userid, "mobile": mobile, "limit": limit})
            return [
                {
                    "provider": "wechat_shop",
                    "channel": "wechat_shop",
                    "channel_label": "微信小店",
                    "id": "3737077448554214400",
                    "out_trade_no": "3737077448554214400",
                    "transaction_id": "4600000181202606148304750608",
                    "product_code": "subscription_trial_month",
                    "product_name": "黄小璨会员体验月",
                    "amount_total": 990,
                    "currency": "CNY",
                    "mobile_snapshot": "18028720840",
                    "status": "paid",
                    "trade_state": "SUCCESS",
                    "refunded_amount_total": 0,
                    "refund_status": "",
                    "paid_at": "2026-06-14 17:28:57+08:00",
                    "created_at": "2026-06-14 17:28:30+08:00",
                }
            ]

    repo = FakeRepo()
    context_query = FakeContextQuery()
    payload = SidebarCommerceReadModel(repo=repo, context_query=context_query).orders(
        external_userid="wmbNXyCwAAdv14187FTFLDTKp9UUGrbw",
        owner_userid="HuangYouCan",
    )

    assert payload["ok"] is True
    assert payload["diagnostics"]["orders_context"] == "workbench_customer_overlay"
    assert [(request.include_activity, request.recent_message_limit, request.timeline_limit) for request in context_query.requests] == [
        (False, 20, 20)
    ]
    assert repo.unexpected_workbench_calls == []
    assert repo.order_calls == [
        {
            "external_userid": "wmbNXyCwAAdv14187FTFLDTKp9UUGrbw",
            "mobile": "18028720840",
            "limit": 20,
        }
    ]
    item = payload["orders"][0]
    assert item["provider"] == "wechat_shop"
    assert item["channel"] == "wechat_shop"
    assert item["channel_label"] == "微信小店"
    assert item["detail_url"] == "/admin/wechat-shop/transactions/3737077448554214400"
    assert item["status_label"] == "已支付"


def test_sidebar_context_query_can_skip_unused_activity_reads(monkeypatch) -> None:
    monkeypatch.setattr("aicrm_next.shared.runtime.production_data_ready", lambda: False)

    class ActivityTrackingRepo:
        def __init__(self) -> None:
            self.calls = []

        def get_customer(self, external_userid: str) -> dict:
            self.calls.append("get_customer")
            return {
                "external_userid": external_userid,
                "unionid": "union_lightweight_sidebar",
                "owner_userid": "HuangYouCan",
                "follow_users": [{"userid": "HuangYouCan", "is_primary": True}],
            }

        def list_timeline(self, *args, **kwargs):
            self.calls.append("list_timeline")
            raise AssertionError("sidebar lightweight context must not load timeline")

        def list_recent_messages(self, *args, **kwargs):
            self.calls.append("list_recent_messages")
            raise AssertionError("sidebar lightweight context must not load recent messages")

    repo = ActivityTrackingRepo()

    payload = GetCustomerContextQuery(repo=repo, live_source_repo=repo)(
        CustomerContextRequest(
            external_userid="wx_lightweight_sidebar",
            owner_userid="HuangYouCan",
            require_owner_scope=True,
            include_activity=False,
        )
    )

    assert payload["ok"] is True
    assert payload["timeline"]["items"] == []
    assert payload["recent_messages"] == []
    assert repo.calls == ["get_customer"]
    assert payload["adapter_contract"]["timeline"]["source_status"] == "skipped"
    assert payload["adapter_contract"]["recent_messages"]["source_status"] == "skipped"


def test_sidebar_context_query_skips_unused_activity_reads_in_production(monkeypatch) -> None:
    monkeypatch.setattr("aicrm_next.shared.runtime.production_data_ready", lambda: True)

    class ProductionActivityTrackingRepo:
        def __init__(self) -> None:
            self.calls = []

        def get_customer(self, external_userid: str) -> dict:
            self.calls.append("get_customer")
            return {
                "external_userid": external_userid,
                "unionid": "union_lightweight_sidebar_prod",
                "owner_userid": "HuangYouCan",
                "follow_users": [{"userid": "HuangYouCan", "is_primary": True}],
            }

        def list_timeline(self, *args, **kwargs):
            self.calls.append("list_timeline")
            raise AssertionError("production sidebar lightweight context must not load timeline")

        def list_recent_messages(self, *args, **kwargs):
            self.calls.append("list_recent_messages")
            raise AssertionError("production sidebar lightweight context must not load recent messages")

    repo = ProductionActivityTrackingRepo()

    payload = GetCustomerContextQuery(repo=repo, live_source_repo=repo)(
        CustomerContextRequest(
            external_userid="wx_lightweight_sidebar_prod",
            owner_userid="HuangYouCan",
            require_owner_scope=True,
            include_activity=False,
        )
    )

    assert payload["ok"] is True
    assert payload["source_status"] == "next_read_model"
    assert payload["timeline"]["items"] == []
    assert payload["recent_messages"] == []
    assert repo.calls == ["get_customer"]
    assert payload["adapter_contract"]["timeline"]["source_status"] == "skipped"
    assert payload["adapter_contract"]["recent_messages"]["source_status"] == "skipped"


def test_sidebar_periodic_orders_include_active_and_expired_with_member_remark() -> None:
    future = datetime.now(timezone.utc) + timedelta(days=8, hours=2)
    past = datetime.now(timezone.utc) - timedelta(days=2)

    class FakeContextQuery:
        def __init__(self) -> None:
            self.requests = []

        def __call__(self, request: CustomerContextRequest) -> dict:
            self.requests.append(request)
            return {
                "ok": True,
                "source_status": "fixture",
                "customer": {
                    "external_userid": request.external_userid,
                    "customer_name": "周期客户",
                    "owner_userid": "HuangYouCan",
                    "mobile": "13900001111",
                    "is_bound": True,
                    "binding": {},
                },
            }

    class FakeRepo:
        def __init__(self) -> None:
            self.calls = []

        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "周期客户", "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "13900001111", "owner_userid": "HuangYouCan"}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def list_customer_service_period_orders(self, *, external_userid: str, mobile: str = "", limit: int = 20) -> list[dict]:
            self.calls.append({"external_userid": external_userid, "mobile": mobile, "limit": limit})
            return [
                {
                    "entitlement_id": "ent_active",
                    "service_product_id": "sp_active",
                    "trade_product_id": "trade_active",
                    "product_code": "sp_every_quarter",
                    "product_name": "老黄的 AI+ 进化同行圈/每季度",
                    "status": "active",
                    "end_at": future,
                    "duration_days": 90,
                    "product_amount_total": 99900,
                    "last_order_id": "order_active",
                    "last_out_trade_no": "WXP260708234704E31147891A00",
                    "last_order_paid_at": "2026-07-09 07:47:00+08:00",
                    "remark": "高意向，提醒续费",
                    "unionid": "union_periodic",
                    "huangyoucan_match_status": "matched_unionid",
                    "huangyoucan_formally_logged_in": True,
                    "huangyoucan_has_token_usage": True,
                    "huangyoucan_learning_plan_current": 4,
                    "huangyoucan_learning_plan_total": 8,
                    "huangyoucan_open_count_7d": 6,
                    "huangyoucan_last_open_at": "2026-07-13T01:30:00+00:00",
                    "huangyoucan_data_refreshed_at": "2026-07-13T01:00:00+00:00",
                },
                {
                    "entitlement_id": "ent_expired",
                    "service_product_id": "sp_expired",
                    "trade_product_id": "trade_expired",
                    "product_code": "sp_private_trial",
                    "product_name": "老黄的 AI+进化同行圈体验-私域",
                    "status": "active",
                    "end_at": past,
                    "duration_days": 14,
                    "product_amount_total": 990,
                    "last_order_id": "order_expired",
                    "last_out_trade_no": "WXP2607081103375882F3604D6C",
                    "remark": "已过期，待跟进",
                    "unionid": "union_periodic",
                },
                {
                    "entitlement_id": "ent_disabled",
                    "service_product_id": "sp_disabled",
                    "product_name": "已禁用服务期",
                    "status": "disabled",
                    "end_at": future,
                    "unionid": "union_periodic",
                },
            ]

    repo = FakeRepo()
    payload = SidebarCommerceReadModel(repo=repo, context_query=FakeContextQuery()).periodic_orders(
        external_userid="wmbNXyCwAAdv14187FTFLDTKp9UUGrbw",
        owner_userid="HuangYouCan",
    )

    assert payload["ok"] is True
    assert payload["diagnostics"]["periodic_orders_context"] == "workbench_customer_overlay"
    assert repo.calls == [
        {
            "external_userid": "wmbNXyCwAAdv14187FTFLDTKp9UUGrbw",
            "mobile": "13900001111",
            "limit": 20,
        }
    ]
    assert [item["id"] for item in payload["periodic_orders"]] == ["ent_active", "ent_expired"]
    active, expired = payload["periodic_orders"]
    assert active["status_label"] == "使用中"
    assert active["remaining_days"] > 0
    assert active["remark"] == "高意向，提醒续费"
    assert active["detail_url"] == "/admin/wechat-pay/transactions/order_active"
    assert active["huangyoucan_formally_logged_in"] is True
    assert active["huangyoucan_has_token_usage"] is True
    assert active["huangyoucan_learning_plan_progress"] == {"current": 4, "total": 8}
    assert active["huangyoucan_open_count_7d"] == 6
    assert active["huangyoucan_last_open_at"] == "2026-07-13T01:30:00+00:00"
    assert active["huangyoucan_match_status"] == "matched_unionid"
    assert expired["status_label"] == "已过期"
    assert expired["remaining_days"] == 0
    assert expired["remark"] == "已过期，待跟进"
    assert expired["huangyoucan_match_status"] == "not_found"
    assert expired["huangyoucan_open_count_7d"] is None


def test_sidebar_periodic_order_remark_target_keeps_customer_scope() -> None:
    class FakeContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            return {
                "ok": True,
                "source_status": "fixture",
                "customer": {
                    "external_userid": request.external_userid,
                    "owner_userid": "HuangYouCan",
                    "mobile": "13900001111",
                    "is_bound": True,
                    "binding": {},
                },
            }

    class FakeRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "13900001111", "owner_userid": "HuangYouCan"}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_customer_service_period_order(self, *, external_userid: str, entitlement_id: str, mobile: str = "") -> dict | None:
            if entitlement_id == "ent_foreign":
                return None
            if entitlement_id == "ent_disabled":
                return {
                    "entitlement_id": "ent_disabled",
                    "service_product_id": "sp_disabled",
                    "status": "disabled",
                    "end_at": datetime.now(timezone.utc) + timedelta(days=7),
                    "unionid": "union_periodic",
                }
            return {
                "entitlement_id": "ent_active",
                "service_product_id": "sp_active",
                "product_name": "周期商品",
                "status": "active",
                "end_at": datetime.now(timezone.utc) + timedelta(days=7),
                "unionid": "union_periodic",
                "remark": "可编辑",
            }

    read_model = SidebarCommerceReadModel(repo=FakeRepo(), context_query=FakeContextQuery())
    target = read_model.periodic_order_remark_target(
        external_userid="wx_periodic",
        owner_userid="HuangYouCan",
        entitlement_id="ent_active",
    )
    assert target["service_product_id"] == "sp_active"
    assert target["unionid"] == "union_periodic"
    assert target["periodic_order"]["remark"] == "可编辑"

    with pytest.raises(NotFoundError):
        read_model.periodic_order_remark_target(
            external_userid="wx_periodic",
            owner_userid="HuangYouCan",
            entitlement_id="ent_foreign",
        )
    with pytest.raises(NotFoundError):
        read_model.periodic_order_remark_target(
            external_userid="wx_periodic",
            owner_userid="HuangYouCan",
            entitlement_id="ent_disabled",
        )


def test_sidebar_periodic_order_sql_reuses_service_period_member_remark_contract() -> None:
    source = inspect.getsource(SidebarV2SqlRepository.list_customer_service_period_orders)
    target_source = inspect.getsource(SidebarV2SqlRepository.get_customer_service_period_order)

    assert "service_period_entitlements e" in source
    assert "service_period_products sp" in source
    assert "wechat_pay_products p" in source
    assert "e.status IN ('active', 'expired')" in source
    assert "metadata_json->>'admin_remark'" in source
    assert "metadata_json->>'remark'" in source
    assert "metadata_json->>'admin_remark'" in target_source
    assert "e.mobile_snapshot" not in source
    assert "e.mobile_snapshot" not in target_source


def test_sidebar_periodic_order_remark_route_writes_service_period_member_remark(monkeypatch) -> None:
    calls = []

    class FakeCommerceReadModel:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("read_model_init", bool(kwargs.get("context_query"))))

        def periodic_order_remark_target(self, *, external_userid: str, owner_userid: str, owner_verified: bool, entitlement_id: str) -> dict:
            calls.append(("target", external_userid, owner_userid, owner_verified, entitlement_id))
            return {
                "service_product_id": "sp_active",
                "unionid": "union_periodic",
                "periodic_order": {
                    "id": entitlement_id,
                    "service_product_id": "sp_active",
                    "title": "周期商品",
                    "status": "active",
                    "status_label": "使用中",
                    "remark": "旧备注",
                },
            }

    class FakeRemarkCommand:
        def __call__(self, service_product_id: str, unionid: str, *, remark: str) -> dict:
            calls.append(("remark", service_product_id, unionid, remark))
            return {"ok": True, "member": {"remark": remark}}

    monkeypatch.setattr("aicrm_next.customer_read_model.api.SidebarCommerceReadModel", FakeCommerceReadModel)
    monkeypatch.setattr("aicrm_next.customer_read_model.api.UpdateServicePeriodMemberRemarkCommand", lambda: FakeRemarkCommand())
    monkeypatch.setattr("aicrm_next.customer_read_model.api._verify_sidebar_owner_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr("aicrm_next.customer_read_model.api._request_scoped_customer_context_query", lambda _db: (object(), None))
    client = _client(
        monkeypatch,
        external_userid="wx_periodic",
        viewer_userid="HuangYouCan",
    )

    response = client.put(
        "/api/sidebar/v2/periodic-orders/ent_active/remark?owner_userid=HuangYouCan",
        json={"external_userid": "wx_periodic", "remark": "新备注"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_next(payload)
    assert payload["source_status"] == "next_command"
    assert payload["periodic_order"]["remark"] == "新备注"
    assert ("target", "wx_periodic", "HuangYouCan", True, "ent_active") in calls
    assert ("remark", "sp_active", "union_periodic", "新备注") in calls


def test_sidebar_orders_fall_back_to_identity_snapshot_when_context_flaps() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class FakeRepo:
        def __init__(self) -> None:
            self.order_calls = []

        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "快照客户", "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan", "name": "身份客户"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "13391962579", "owner_userid": "HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

        def list_customer_wechat_pay_orders(self, *, external_userid: str, mobile: str = "", limit: int = 20) -> list[dict]:
            self.order_calls.append({"external_userid": external_userid, "mobile": mobile, "limit": limit})
            return []

    repo = FakeRepo()
    payload = SidebarCommerceReadModel(repo=repo, context_query=FailingContextQuery()).orders(
        external_userid="wmbNXyCwAAncAArq9MSmXQq6yUo8fC8g",
        owner_userid="HuangYouCan",
        owner_verified=True,
    )

    assert payload["ok"] is True
    assert payload["orders"] == []
    assert payload["customer"]["display_name"] == "快照客户"
    assert payload["customer"]["mobile"] == "13391962579"
    assert payload["diagnostics"]["orders_context"] == "identity_snapshot_fallback"
    assert repo.order_calls == [
        {
            "external_userid": "wmbNXyCwAAncAArq9MSmXQq6yUo8fC8g",
            "mobile": "13391962579",
            "limit": 20,
        }
    ]


def test_sidebar_orders_identity_snapshot_fallback_keeps_owner_scope() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class FakeRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "快照客户", "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "13391962579", "owner_userid": "HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    with pytest.raises(sidebar_v2.CustomerScopeForbiddenError):
        SidebarCommerceReadModel(repo=FakeRepo(), context_query=FailingContextQuery()).orders(
            external_userid="wmbNXyCwAAncAArq9MSmXQq6yUo8fC8g",
            owner_userid="OtherOwner",
            owner_verified=True,
        )


def test_sidebar_workbench_snapshot_values_win_over_placeholder_live_source() -> None:
    class PlaceholderContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            return {
                "ok": True,
                "source_status": "live_source_fallback",
                "customer": {
                    "external_userid": request.external_userid,
                    "owner_userid": "HuangYouCan",
                    "display_name": "remark",
                    "customer_name": "customer_name",
                    "remark": "remark",
                    "mobile": "mobile",
                    "binding": {"is_bound": True, "mobile": "mobile"},
                    "contact": {"remark": "remark"},
                },
            }

    class FakeRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "Wayne", "owner_userid": "HuangYouCan", "remark": "Wayne"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan", "name": "Wayne"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "18086851909", "owner_userid": "QianLan"}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return {"HuangYouCan", "QianLan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    payload = SidebarWorkbenchReadModel(repo=FakeRepo(), context_query=PlaceholderContextQuery())(
        external_userid="wmbNXyCwAAdmxZosevw5DHT5K2_MKIyQ",
        owner_userid="HuangYouCan",
        owner_verified=True,
    )

    assert payload["customer"]["display_name"] == "Wayne"
    assert payload["customer"]["mobile"] == "18086851909"
    assert payload["customer"]["is_bound"] is True


def test_sidebar_workbench_preserves_profile_only_fallback_without_duplicate_profile_read() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class ProfileOnlyRepo:
        def __init__(self) -> None:
            self.profile_calls = 0

        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": False, "external_userid": external_userid}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return {"HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            self.profile_calls += 1
            return {"source": "profile-only", "industry": "教育"}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    repo = ProfileOnlyRepo()

    payload = SidebarWorkbenchReadModel(repo=repo, context_query=FailingContextQuery())(
        external_userid="wx_profile_only",
        owner_userid="HuangYouCan",
        owner_verified=True,
    )

    assert payload["ok"] is True
    assert payload["profile"]["source"] == "profile-only"
    assert payload["profile"]["industry"] == "教育"
    assert repo.profile_calls == 1


def test_sidebar_questionnaires_fall_back_to_identity_snapshot_when_context_flaps() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class FakeRepo:
        def __init__(self) -> None:
            self.questionnaire_calls = []

        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "Wayne", "owner_userid": "HuangYouCan", "remark": "Wayne"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan", "name": "Wayne"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "18086851909", "owner_userid": "HuangYouCan"}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return {"HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

        def list_questionnaire_answers(self, *, external_userid: str, mobile: str = "") -> list[dict]:
            self.questionnaire_calls.append({"external_userid": external_userid, "mobile": mobile})
            return [
                {
                    "submission_id": "1414",
                    "questionnaire_id": "21",
                    "questionnaire_title": "填写问卷激活黄小璨AI",
                    "submitted_at": "2026-06-24 18:04:31+08:00",
                    "question_id": "596",
                    "question": "请输入手机号",
                    "selected_option_texts_snapshot": [],
                    "text_value": "18086851909",
                },
                {
                    "submission_id": "1414",
                    "questionnaire_id": "21",
                    "questionnaire_title": "questionnaire_title",
                    "submitted_at": "2026-06-24 18:04:31+08:00",
                    "question_id": "placeholder",
                    "question": "question",
                    "selected_option_texts_snapshot": [],
                    "text_value": "text_value",
                },
            ]

    repo = FakeRepo()
    payload = SidebarQuestionnaireReadModel(repo=repo, context_query=FailingContextQuery())(
        external_userid="wmbNXyCwAAdmxZosevw5DHT5K2_MKIyQ",
        owner_userid="HuangYouCan",
        owner_verified=True,
    )

    assert repo.questionnaire_calls == [
        {
            "external_userid": "wmbNXyCwAAdmxZosevw5DHT5K2_MKIyQ",
            "mobile": "18086851909",
        }
    ]
    assert payload["diagnostics"]["context_source_status"] == "identity_snapshot_fallback"
    assert payload["questionnaires"] == [
        {
            "id": "1414",
            "submission_id": "1414",
            "questionnaire_id": "21",
            "title": "填写问卷激活黄小璨AI",
            "submitted_at": "2026-06-24 18:04",
            "answer_count": 1,
            "total_count": 1,
            "answers": [{"question": "请输入手机号", "answer": "18086851909"}],
        }
    ]


def test_sidebar_workbench_identity_snapshot_fallback_keeps_owner_scope() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class FakeRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "Jane初芯", "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan", "name": "Jane初芯"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "18201398887", "owner_userid": "HuangYouCan"}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return {"HuangYouCan"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    with pytest.raises(sidebar_v2.CustomerScopeForbiddenError):
        SidebarWorkbenchReadModel(repo=FakeRepo(), context_query=FailingContextQuery())(
            external_userid="wmbNXyCwAABrzB-3rPps07AecwEGRGMA",
            owner_userid="OtherOwner",
            owner_verified=True,
        )


def test_sidebar_workbench_allows_current_viewer_from_contact_owner_candidates() -> None:
    class FailingContextQuery:
        def __call__(self, request: CustomerContextRequest) -> dict:
            raise NotFoundError("customer not found")

    class FakeRepo:
        def get_contact_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "customer_name": "共同客户", "owner_userid": "HuangYouCan"}

        def get_external_identity_snapshot(self, external_userid: str) -> dict:
            return {"external_userid": external_userid, "follow_user_userid": "HuangYouCan", "name": "共同客户"}

        def get_contact_binding_status(self, external_userid: str) -> dict:
            return {"is_bound": True, "external_userid": external_userid, "mobile": "15950551623", "owner_userid": "HuangYouCan"}

        def get_contact_owner_userids(self, external_userid: str) -> set[str]:
            return {"HuangYouCan", "ZhaoYanFang"}

        def get_profile_fields(self, external_userid: str) -> dict:
            return {}

        def get_workflow_title_for_customer(self, external_userid: str) -> str:
            return ""

        def get_bindable_wechat_pay_order_mobile(self, external_userid: str) -> dict | None:
            return None

    payload = SidebarWorkbenchReadModel(repo=FakeRepo(), context_query=FailingContextQuery())(
        external_userid="wmbNXyCwAARg-umkvY19AtDXGOMknjww",
        owner_userid="ZhaoYanFang",
        owner_verified=True,
    )

    assert payload["ok"] is True
    assert payload["customer"]["display_name"] == "共同客户"
    assert payload["customer"]["owner_userid"] == "ZhaoYanFang"
    assert payload["diagnostics"]["context_source_status"] == "identity_snapshot_fallback"


def test_sidebar_order_sql_includes_wechat_shop_identity_matching() -> None:
    source = inspect.getsource(sidebar_v2.SidebarV2SqlRepository.list_customer_wechat_pay_orders)

    assert "wechat_shop_orders" in source
    assert "wechat_shop_unionid_orders" in source
    assert "微信小店" in source
    assert "DISTINCT ON (provider, id)" in source


def test_sidebar_commerce_and_material_paths_avoid_heavy_list_queries() -> None:
    commerce_source = inspect.getsource(PostgresCommerceRepository.list_sidebar_active_products)
    material_source = inspect.getsource(PostgresMediaLibraryRepository._select_list)

    assert "SELECT p.*" not in commerce_source
    assert "WHERE p.enabled = TRUE" in commerce_source
    assert "p.status = 'active'" in commerce_source
    assert "metadata_json->>'aicrm_product_owner'" in commerce_source
    assert "service_period_products sp" in commerce_source
    assert "SELECT * FROM {table}" not in material_source
    assert "self._table_columns(cur, table)" in material_source
    assert "self._list_columns(kind, available_columns)" in material_source


def test_sidebar_material_image_list_columns_tolerate_legacy_schema() -> None:
    repo = PostgresMediaLibraryRepository("postgresql://example.invalid/db")
    production_columns_without_dimensions = {
        "id",
        "name",
        "file_name",
        "source",
        "source_url",
        "data_base64",
        "mime_type",
        "file_size",
        "thumb_media_id",
        "thumb_media_id_expires_at",
        "enabled",
        "created_at",
        "updated_at",
        "description",
        "tags",
        "category",
        "ai_metadata",
    }

    columns = repo._list_columns("image", production_columns_without_dimensions)

    assert "0 AS width" in columns
    assert "0 AS height" in columns
    assert "ai_metadata" in columns
    assert "width, height" not in columns


def test_sidebar_commerce_orders_default_context_query_is_initialized() -> None:
    model = SidebarCommerceReadModel()

    assert model._context_query is not None
