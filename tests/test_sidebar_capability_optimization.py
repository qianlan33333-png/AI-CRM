from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from aicrm_next.extensions.commerce.commerce.coupons.application import CouponSidebarApplication
from aicrm_next.customer_read_model.sidebar_timeline import (
    ResolvedSidebarCustomerContext,
    SidebarCustomerTimelineQuery,
)
from aicrm_next.main import create_app
from aicrm_next.extensions.radar.radar_links.application import ListSidebarRadarLinksQuery
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.public_url import canonical_public_base_url
from tests.sidebar_auth_test_helpers import install_sidebar_auth


class CouponRepository:
    def __init__(self) -> None:
        self.filters = []

    def list_coupons(self, *, limit: int, offset: int, q: str, status: str):
        self.filters.append({"limit": limit, "offset": offset, "q": q, "status": status})
        return {
            "items": [
                {
                    "id": 7,
                    "name": "新人券",
                    "discount_amount_total": 990,
                    "claim_ends_at": datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc),
                    "public_slug": "new-user",
                    "products": [{"title": "入门课", "product_type": "standard_product", "tenant_id": "secret"}],
                    "tenant_id": "secret",
                    "created_by": "operator-secret",
                }
            ],
            "total": 1,
        }


def test_coupon_sidebar_projection_requests_only_active_claimable_rows_and_is_safe() -> None:
    repo = CouponRepository()
    payload = CouponSidebarApplication(repo).list_claimable(
        public_base_url="https://id-dev.youcangogogo.com",
    )

    assert repo.filters == [{"limit": 200, "offset": 0, "q": "", "status": "active"}]
    assert payload == {
        "ok": True,
        "items": [
            {
                "id": 7,
                "name": "新人券",
                "discount_amount_total": 990,
                "discount_label": "立减 ¥9.9",
                "products": [{"title": "入门课", "product_type": "standard_product"}],
                "claim_ends_at": "2026-08-01 00:00",
                "url": "https://id-dev.youcangogogo.com/c/new-user",
            }
        ],
        "total": 1,
    }
    assert "tenant_id" not in str(payload)
    assert "created_by" not in str(payload)


def test_radar_sidebar_projection_paginates_repository_and_returns_enabled_wrappers_only() -> None:
    rows = [
        {
            "id": index,
            "code": f"code-{index}",
            "title": f"雷达 {index}",
            "target_type": "pdf" if index % 2 else "image",
            "enabled": index not in {2, 204},
            "original_url": f"https://secret.example/{index}",
            "unionid": f"secret-{index}",
        }
        for index in range(1, 206)
    ]

    class Repository:
        def __init__(self) -> None:
            self.calls = []

        def list_links(self, *, limit: int, offset: int):
            self.calls.append((limit, offset))
            return rows[offset : offset + limit], len(rows)

    repo = Repository()
    payload = ListSidebarRadarLinksQuery(repo)(
        base_url="https://id-dev.youcangogogo.com",
    )

    assert repo.calls == [(200, 0), (200, 200)]
    assert payload["total"] == 203
    assert len(payload["items"]) == 203
    assert payload["items"][0]["url"] == "https://id-dev.youcangogogo.com/r/code-1"
    assert {item["target_type"] for item in payload["items"]} == {"image", "pdf"}
    assert "original_url" not in str(payload)
    assert "unionid" not in str(payload)


def test_timeline_projection_is_paged_filtered_and_strips_identity_and_raw_payload() -> None:
    rows = [
        {
            "event_time": "2026-07-17T10:00:00Z",
            "event_type": "radar_opened",
            "title": "打开雷达 · 白皮书",
            "summary": "已打开追踪链接",
            "metadata": {
                "radar_id": "9",
                "radar_title": "白皮书",
                "target_type": "pdf",
                "unionid": "secret-union",
                "raw_payload": {"openid": "secret-openid"},
            },
            "unionid": "secret-union",
            "source_id": "secret-source",
        },
        {
            "event_time": "2026-07-16T10:00:00Z",
            "event_type": "message",
            "title": "聊天记录不应出现",
            "summary": "secret",
            "metadata": {},
        },
    ]

    class Repository:
        def get_customer(self, external_userid):
            assert external_userid == "wm-signed"
            return {"external_userid": external_userid, "unionid": "union-signed"}

        def list_timeline_by_unionid(self, unionid, filters, *, limit=None, offset=0):
            assert unionid == "union-signed"
            assert filters["event_types"] == [
                "channel_entry",
                "questionnaire_submitted",
                "product_enrolled",
                "radar_opened",
            ]
            allowed = [item for item in rows if item["event_type"] in filters["event_types"]]
            return allowed[offset : offset + limit]

        def count_timeline_by_unionid(self, unionid, filters):
            assert unionid == "union-signed"
            return sum(item["event_type"] in filters["event_types"] for item in rows)

    payload = SidebarCustomerTimelineQuery(Repository())(
        external_userid="wm-signed",
        limit=20,
        offset=0,
    )

    assert payload["total"] == 1
    assert payload["has_more"] is False
    assert payload["next_offset"] == 1
    assert payload["items"] == [
        {
            "event_time": "2026-07-17T10:00:00Z",
            "event_type": "radar_opened",
            "title": "打开雷达 · 白皮书",
            "summary": "已打开追踪链接",
            "metadata": {"radar_id": "9", "radar_title": "白皮书", "target_type": "pdf"},
        }
    ]
    for secret in ("secret-union", "secret-openid", "secret-source", "raw_payload"):
        assert secret not in str(payload)


def test_timeline_source_actions_are_allowlisted_and_internal_only() -> None:
    rows = [
        {
            "event_id": "questionnaire:submission-42",
            "event_time": "2026-07-17T11:26:29+08:00",
            "event_type": "questionnaire_submitted",
            "title": "提交问卷 · 激活问卷",
            "summary": "已完成问卷提交",
            "source_table": "questionnaire_submissions",
            "source_id": "submission-42",
            "metadata": {"questionnaire_id": "21", "questionnaire_title": "激活问卷"},
        },
        {
            "event_id": "product:payment:wx/order 1",
            "event_time": "2026-07-17T11:20:00+08:00",
            "event_type": "product_enrolled",
            "title": "报名或支付成功 · 年度版",
            "summary": "已完成商品报名或支付",
            "source_table": "wechat_pay_orders",
            "source_id": "wx/order 1",
            "metadata": {"product_id": "annual", "product_title": "年度版", "product_type": "standard_product"},
        },
        {
            "event_id": "product:wechat_shop:shop/order 2",
            "event_time": "2026-07-17T11:10:00+08:00",
            "event_type": "product_enrolled",
            "title": "报名或支付成功 · 小店商品",
            "summary": "已完成商品报名或支付",
            "source_table": "wechat_shop_orders",
            "source_id": "shop/order 2",
            "metadata": {"product_id": "shop", "product_title": "小店商品", "product_type": "wechat_shop"},
        },
        {
            "event_id": "channel_entry:8",
            "event_time": "2026-07-17T11:00:00+08:00",
            "event_type": "channel_entry",
            "title": "扫码进入渠道",
            "summary": "直播间",
            "source_table": "automation_channel_entry_effect_log",
            "source_id": "8",
            "metadata": {"channel_name": "直播间"},
        },
        {
            "event_id": "radar:9",
            "event_time": "2026-07-17T10:50:00+08:00",
            "event_type": "radar_opened",
            "title": "打开雷达 · 白皮书",
            "summary": "已打开追踪链接",
            "source_table": "radar_click_events",
            "source_id": "9",
            "metadata": {"radar_id": "3", "radar_title": "白皮书", "target_type": "pdf"},
        },
        {
            "event_id": "product:service_period:enrollment-only",
            "event_time": "2026-07-17T10:40:00+08:00",
            "event_type": "product_enrolled",
            "title": "报名成功 · 无订单报名",
            "summary": "已完成商品报名或支付",
            "source_table": "commerce_enrollment",
            "source_id": "enrollment-only",
            "metadata": {"product_id": "offline", "product_title": "无订单报名", "product_type": "service_period"},
        },
    ]

    class Repository:
        def get_customer(self, external_userid):
            return {"external_userid": external_userid, "unionid": "union-signed"}

        def list_timeline_by_unionid(self, _unionid, _filters, *, limit=None, offset=0):
            return rows[offset : offset + limit]

        def count_timeline_by_unionid(self, _unionid, _filters):
            return len(rows)

    payload = SidebarCustomerTimelineQuery(Repository())(external_userid="wm-signed")
    items = payload["items"]

    assert items[0]["source_action"] == {
        "kind": "questionnaire_submission",
        "label": "查看原纪录",
        "submission_id": "submission-42",
        "questionnaire_id": "21",
    }
    assert items[1]["source_action"] == {
        "kind": "order_detail",
        "label": "查看原纪录",
        "detail_url": "/admin/wechat-pay/transactions/wx%2Forder%201",
    }
    assert items[2]["source_action"] == {
        "kind": "order_detail",
        "label": "查看原纪录",
        "detail_url": "/admin/wechat-shop/transactions/shop%2Forder%202",
    }
    assert all("source_action" not in items[index] for index in (3, 4, 5))
    serialized = str(payload)
    for hidden in ("source_table", "source_id", "questionnaire_submissions", "automation_channel_entry_effect_log", "radar_click_events"):
        assert hidden not in serialized


def test_service_period_payment_event_reuses_payment_number_for_order_detail() -> None:
    safe_item = SidebarCustomerTimelineQuery._safe_item(
        {
            "event_id": "product:payment:period-pay-9",
            "event_time": "2026-07-17T10:00:00Z",
            "event_type": "product_enrolled",
            "title": "报名或支付成功 · 周期课",
            "summary": "已确认周期商品报名或激活",
            "source_table": "service_period_events",
            "source_id": "period-event-1",
            "metadata": {"product_id": "9", "product_title": "周期课", "product_type": "service_period"},
        }
    )

    assert safe_item["source_action"]["detail_url"] == "/admin/wechat-pay/transactions/period-pay-9"


def _sidebar_client(monkeypatch, *, external_userid: str = "wm-signed") -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.headers.update(
        install_sidebar_auth(
            client,
            viewer_userid="sales-1",
            external_userid=external_userid,
        )
    )
    return client


def test_new_sidebar_apis_require_a_signed_grant(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path in ("/api/sidebar/v2/coupons", "/api/sidebar/v2/radar-links", "/api/sidebar/v2/timeline"):
        response = client.get(path)
        assert response.status_code in {401, 403}


def test_timeline_api_uses_grant_customer_and_ignores_query_identity(monkeypatch) -> None:
    requested_unionids = []

    class Repository:
        def get_customer(self, _external_userid):
            raise AssertionError("timeline must reuse the request-scoped resolved context")

        def list_timeline_by_unionid(self, unionid, _filters, *, limit=None, offset=0):
            requested_unionids.append(unionid)
            return []

        def count_timeline_by_unionid(self, _unionid, _filters):
            return 0

    repo = Repository()
    monkeypatch.setattr(
        "aicrm_next.customer_read_model.api._request_scoped_customer_repositories",
        lambda _db: (repo, repo),
    )
    monkeypatch.setattr(
        "aicrm_next.customer_read_model.api._verify_sidebar_owner_scope",
        lambda *_args, **_kwargs: ResolvedSidebarCustomerContext(
            external_userid="wm-signed",
            unionid="union-signed",
            owner_userid="sales-1",
            owner_verified=True,
        ),
    )
    client = _sidebar_client(monkeypatch)

    response = client.get("/api/sidebar/v2/timeline?external_userid=wm-other&unionid=union-other&limit=20&offset=0")

    assert response.status_code == 200
    assert requested_unionids == ["union-signed"]
    assert response.json() == {
        "ok": True,
        "items": [],
        "total": 0,
        "has_more": False,
        "next_offset": 0,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
    }


def test_sidebar_read_failures_return_503_without_fixture_fallback(monkeypatch) -> None:
    client = _sidebar_client(monkeypatch)

    def fail_coupons(*_args, **_kwargs):
        raise RuntimeError("coupon database unavailable")

    def fail_radar(*_args, **_kwargs):
        raise RuntimeError("radar database unavailable")

    monkeypatch.setattr("aicrm_next.extensions.commerce.commerce.coupons.sidebar_api.CouponSidebarApplication.list_claimable", fail_coupons)
    monkeypatch.setattr("aicrm_next.extensions.radar.radar_links.api.ListSidebarRadarLinksQuery.execute", fail_radar)

    coupon = client.get("/api/sidebar/v2/coupons")
    radar = client.get("/api/sidebar/v2/radar-links")

    assert coupon.status_code == radar.status_code == 503
    assert coupon.json()["fallback_used"] is False
    assert coupon.json()["source_status"] == "production_unavailable"
    assert radar.json()["detail"]["source_status"] == "production_unavailable"


def test_canonical_public_base_url_uses_configured_https_origin_in_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id-dev.youcangogogo.com")
    monkeypatch.delenv("AICRM_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "method": "GET",
            "path": "/api/sidebar/v2/coupons",
            "raw_path": b"/api/sidebar/v2/coupons",
            "query_string": b"",
            "headers": [(b"host", b"attacker.example")],
            "server": ("attacker.example", 80),
            "client": ("127.0.0.1", 1),
        }
    )

    assert canonical_public_base_url(request) == "https://id-dev.youcangogogo.com"

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://id-dev.youcangogogo.com")
    with pytest.raises(ContractError, match="https"):
        canonical_public_base_url(request)
