from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.radar.radar_links.domain import sign_viewer_session
from aicrm_next.extensions.radar.radar_links.repo import PostgresRadarLinksRepository, build_radar_links_repository


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "radar-links-test-secret")
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "fake")
    client = TestClient(create_app(), raise_server_exceptions=False, base_url="https://testserver")
    client.headers["User-Agent"] = "Mozilla/5.0 MicroMessenger/8.0"
    return client


def _create_link(client: TestClient, **overrides):
    payload = {
        "title": "直播报名页",
        "original_url": "https://example.com/landing",
        "enabled": True,
        "auth_required": False,
        "source_channel": "wechat_group",
        "campaign_id": "campaign_001",
        "staff_id": "staff_001",
    }
    payload.update(overrides)
    response = client.post("/api/admin/radar-links", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["radar_link"]


def _state_from_oauth_start_location(location: str) -> str:
    parsed = urlparse(location)
    values = parse_qs(parsed.query)
    return values["state"][0]


def _decode_state_payload(state: str) -> dict:
    body = state.split(".", 1)[0]
    padding = "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode((body + padding).encode("ascii")).decode("utf-8"))


def test_create_radar_link_returns_wrapper_url(client):
    link = _create_link(client)

    assert link["id"] >= 1
    assert link["code"]
    assert link["wrapper_url"].endswith(f"/r/{link['code']}")
    assert link["original_url"] == "https://example.com/landing"


def test_admin_radar_links_page_is_in_operations_nav(client):
    response = client.get("/admin/radar-links")

    assert response.status_code == 200
    assert "内容雷达" in response.text
    assert "/api/admin/radar-links" in response.text
    assert "<th>PV</th>" in response.text
    assert "<th>UV</th>" in response.text
    assert "<th>雷达链接</th>" not in response.text


@pytest.mark.parametrize("original_url", ["javascript:alert(1)", "data:text/plain,hello", "file:///tmp/a", "ftp://example.com/a"])
def test_rejects_illegal_url_scheme(client, original_url):
    response = client.post(
        "/api/admin/radar-links",
        json={"title": "bad", "original_url": original_url},
    )

    assert response.status_code == 400
    assert "http or https" in response.text


@pytest.mark.parametrize(
    "original_url", ["http://localhost/a", "http://127.0.0.1/a", "http://10.0.0.1/a", "http://172.16.1.2/a", "http://192.168.1.3/a", "http://[::1]/a"]
)
def test_rejects_localhost_and_private_ip_targets(client, original_url):
    response = client.post(
        "/api/admin/radar-links",
        json={"title": "bad", "original_url": original_url},
    )

    assert response.status_code == 400
    assert "host is not allowed" in response.text


def test_disabled_link_access_returns_404(client):
    link = _create_link(client)
    disable_response = client.post(f"/api/admin/radar-links/{link['id']}/disable")
    assert disable_response.status_code == 200

    response = client.get(f"/r/{link['code']}", follow_redirects=False)

    assert response.status_code == 404


def test_public_radar_redirect_records_landing(client):
    link = _create_link(client)

    response = client.get(f"/r/{link['code']}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/landing"
    events = client.get(f"/api/admin/radar-links/{link['id']}/events").json()["events"]
    assert events == []
    raw_events, _ = build_radar_links_repository().list_click_events(link["id"])
    assert [event["stage"] for event in raw_events] == ["redirect", "landing"]
    assert raw_events[1]["ip_hash"]
    assert not raw_events[1].get("ip")


def test_public_radar_ignores_forged_query_and_plain_identity_cookies(client):
    link = _create_link(client, auth_required=True)
    client.cookies.set("openid", "openid_forged_cookie")
    client.cookies.set("unionid", "unionid_forged_cookie")
    client.cookies.set("external_userid", "wm_forged_cookie")

    response = client.get(
        f"/r/{link['code']}?openid=openid_forged_query&unionid=unionid_forged_query&external_userid=wm_forged_query&OpenID=openid_forged_mixed_case",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/api/h5/radar/oauth/start?state=")
    events = client.get(f"/api/admin/radar-links/{link['id']}/events").json()["events"]
    assert events == []
    raw_events, _ = build_radar_links_repository().list_click_events(link["id"])
    assert [event["stage"] for event in raw_events] == ["oauth_start", "landing"]
    assert all(not str(event.get("openid") or "") for event in raw_events)
    assert all(not str(event.get("unionid") or "") for event in raw_events)
    assert all(not str(event.get("external_userid") or "") for event in raw_events)
    assert all(not ({"openid", "unionid", "external_userid"} & {key.lower() for key in event["query_params_json"]}) for event in raw_events)


def test_protected_radar_requires_wechat_browser_before_oauth(client):
    link = _create_link(client, auth_required=True)

    response = client.get(
        f"/r/{link['code']}",
        headers={"User-Agent": "Mozilla/5.0 Safari/605.1.15"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "请在微信中打开" in response.text
    assert "open.weixin.qq.com" not in response.text


def test_protected_radar_reauthenticates_openid_only_viewer_session(client):
    link = _create_link(client, auth_required=True)
    client.cookies.set(
        "aicrm_radar_viewer",
        sign_viewer_session(
            code=link["code"],
            openid="openid_only_viewer",
            secret_key="radar-links-test-secret",
        ),
    )

    response = client.get(f"/r/{link['code']}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/api/h5/radar/oauth/start?state=")


def test_fake_oauth_callback_with_unionid_records_authorized_click_and_redirects(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    assert landing_response.status_code == 302
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    state_payload = _decode_state_payload(state)
    assert set(state_payload) == {"code", "nonce", "exp"}
    assert state_payload["code"] == link["code"]

    callback_response = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_from_fake_callback"},
        follow_redirects=False,
    )

    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == "https://example.com/landing"
    events = client.get(f"/api/admin/radar-links/{link['id']}/events").json()["events"]
    stages = [event["stage"] for event in events]
    assert stages == ["redirect", "authorized", "oauth_callback"]
    assert events[1]["unionid_masked"] == "unioni...back"
    assert "unionid" not in events[1]
    raw_events, _ = build_radar_links_repository().list_click_events(link["id"])
    assert [event["stage"] for event in raw_events] == ["redirect", "authorized", "oauth_callback", "oauth_start", "landing"]


def test_admin_radar_records_hide_missing_unionid_and_enrich_external_userid(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_001"},
        follow_redirects=False,
    )

    response = client.get(f"/api/admin/radar-links/{link['id']}/events")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert [event["stage"] for event in payload["events"]] == ["redirect", "authorized", "oauth_callback"]
    assert all(event["unionid_masked"] for event in payload["events"])
    assert {event["external_userid"] for event in payload["events"]} == {"wx_ext_001"}


def test_admin_radar_export_excludes_missing_unionid_and_uses_resolved_external_userid(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_001"},
        follow_redirects=False,
    )

    response = client.get(f"/api/admin/radar-links/{link['id']}/events/export")

    assert response.status_code == 200
    lines = response.text.lstrip("\ufeff").splitlines()
    assert len(lines) == 4
    assert all("unionid_001,wx_ext_001," in line for line in lines[1:])


def test_postgres_admin_radar_records_use_one_identity_join_and_unionid_filter(monkeypatch):
    class Result:
        def __init__(self, *, one=None, rows=None) -> None:
            self._one = one
            self._rows = rows or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._rows

    class Connection:
        def __init__(self) -> None:
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "COUNT(*) AS total" in sql:
                return Result(one={"total": 1})
            return Result(
                rows=[
                    {
                        "event_id": 9,
                        "unionid": "unionid_001",
                        "external_userid": "wx_ext_001",
                        "created_at": "2026-07-16T11:21:17+08:00",
                    }
                ]
            )

    connection = Connection()
    repository = PostgresRadarLinksRepository("postgresql://fixture")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    rows, total = repository.list_click_events(11, require_unionid=True, enrich_external_userid=True)

    assert total == 1
    assert rows[0]["external_userid"] == "wx_ext_001"
    assert len(connection.calls) == 2
    page_sql = connection.calls[1][0]
    assert "JOIN crm_user_identity" in page_sql
    assert "NULLIF(event.unionid, '') IS NOT NULL" in page_sql
    assert "primary_external_userid" in page_sql


def test_real_radar_oauth_start_builds_wechat_authorize_url_under_explicit_flag(client, monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-radar-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "radar-secret")
    monkeypatch.setenv("WECHAT_MP_OAUTH_SCOPE", "snsapi_userinfo")
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://crm.example.com")
    link = _create_link(client, auth_required=True)

    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    assert landing_response.status_code == 302
    start_response = client.get(landing_response.headers["location"], follow_redirects=False)

    assert start_response.status_code == 302
    parsed = urlparse(start_response.headers["location"])
    values = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "open.weixin.qq.com"
    assert parsed.path == "/connect/oauth2/authorize"
    assert parsed.fragment == "wechat_redirect"
    assert values["appid"] == ["wx-radar-app"]
    assert values["scope"] == ["snsapi_userinfo"]
    assert values["state"][0] == _state_from_oauth_start_location(landing_response.headers["location"])
    assert values["redirect_uri"][0].startswith("https://crm.example.com/api/h5/radar/oauth/callback?state=")


def test_real_radar_oauth_callback_exchanges_code_and_records_unionid(client, monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-radar-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "radar-secret")
    monkeypatch.setenv("WECHAT_MP_OAUTH_SCOPE", "snsapi_userinfo")

    from aicrm_next.channels.integration_gateway import questionnaire_adapters

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            assert app_id == "wx-radar-app"
            assert app_secret == "radar-secret"
            assert code == "real-code"
            return {"openid": "openid_real", "access_token": "access-token-real"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            assert access_token == "access-token-real"
            assert openid == "openid_real"
            return {"openid": openid, "unionid": "unionid_real"}

    monkeypatch.setattr(questionnaire_adapters, "build_wechat_oauth_client", lambda: FakeOAuthClient())
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])

    callback_response = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "code": "real-code"},
        follow_redirects=False,
    )

    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == "https://example.com/landing"
    events = client.get(f"/api/admin/radar-links/{link['id']}/events").json()["events"]
    stages = [event["stage"] for event in events]
    assert stages == ["redirect", "authorized", "oauth_callback"]
    assert events[1]["openid_masked"] == "openid...real"
    assert events[1]["unionid_masked"] == "unioni...real"
    assert "openid" not in events[1]
    assert "unionid" not in events[1]
    raw_events, _ = build_radar_links_repository().list_click_events(link["id"])
    assert [event["stage"] for event in raw_events] == ["redirect", "authorized", "oauth_callback", "oauth_start", "landing"]


def test_real_radar_oauth_callback_ignores_forged_identity_and_requires_unionid(client, monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-radar-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "radar-secret")
    monkeypatch.setenv("WECHAT_MP_OAUTH_SCOPE", "snsapi_base")

    from aicrm_next.channels.integration_gateway import questionnaire_adapters

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            assert code == "trusted-code"
            return {"openid": "openid_from_provider", "access_token": "provider-token"}

    monkeypatch.setattr(questionnaire_adapters, "build_wechat_oauth_client", lambda: FakeOAuthClient())
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])

    callback_response = client.get(
        "/api/h5/radar/oauth/callback",
        params={
            "state": state,
            "code": "trusted-code",
            "openid": "openid_forged",
            "unionid": "unionid_forged",
            "external_userid": "wm_forged",
        },
        follow_redirects=False,
    )

    assert callback_response.status_code == 409
    assert callback_response.headers["content-type"].startswith("text/html")
    assert "微信未返回 UnionID" in callback_response.text
    assert "forged" not in callback_response.text
    events = client.get(f"/api/admin/radar-links/{link['id']}/events?stage=authorized").json()["events"]
    assert events == []
    raw_events, _ = build_radar_links_repository().list_click_events(link["id"], stage="authorized")
    assert raw_events == []


def test_real_radar_oauth_requires_explicit_flag(client, monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "production")
    monkeypatch.delenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", raising=False)
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-radar-app")
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)

    response = client.get(landing_response.headers["location"], follow_redirects=False)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "当前微信授权配置未完成，请联系管理员" in response.text
    assert "production mode is not enabled" not in response.text
    assert '{"ok":' not in response.text


def test_radar_oauth_callback_error_returns_readable_html(client):
    response = client.get(
        "/api/h5/radar/oauth/callback?state=bad-state&code=fake-code",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "内容授权未完成" in response.text
    assert '{"ok":' not in response.text
    assert "state=bad-state" not in response.text


def test_stats_returns_required_click_fields(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_stats"}, follow_redirects=False)

    response = client.get(f"/api/admin/radar-links/{link['id']}/stats")

    assert response.status_code == 200
    stats = response.json()["stats"]
    assert {
        "total_clicks",
        "authorized_clicks",
        "unique_users",
        "today_clicks",
        "last_clicked_at",
        "total_landings",
        "authorized_users",
        "viewer_opens",
        "view_opens",
        "last_viewed_at",
    } <= set(stats)
    assert stats["total_clicks"] == 1
    assert stats["authorized_clicks"] == 1
    assert stats["unique_users"] == 1
    assert stats["today_clicks"] == 1
    assert stats["last_clicked_at"]


def test_list_radar_links_includes_list_summary_fields(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_list"}, follow_redirects=False)

    response = client.get("/api/admin/radar-links")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert {
        "total_landings",
        "authorized_users",
        "view_count",
        "last_viewed_at",
        "created_at",
        "updated_at",
    } <= set(item)
    assert item["total_landings"] == 1
    assert item["authorized_users"] == 1


def test_radar_link_new_options_and_admin_subpages_render(client):
    options = client.get("/api/admin/radar-links/new/options")
    assert options.status_code == 200
    assert options.json()["defaults"]["source_channel"] == "manual"
    assert options.json()["defaults"]["staff_id"] == "HuangYouCan"

    link = _create_link(client)
    for path in ["/admin/radar-links/new", f"/admin/radar-links/{link['id']}/edit", f"/admin/radar-links/{link['id']}/detail"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "内容雷达" in response.text

    detail_response = client.get(f"/admin/radar-links/{link['id']}/detail")
    assert "unionid" in detail_response.text
    assert "外部联系人ID" in detail_response.text
    assert "导出 CSV" in detail_response.text
    assert "只展示已授权并取得 unionid 的访问记录" in detail_response.text
    assert "暂无已授权访问记录" in detail_response.text
    assert 'titleEl.textContent = link.title || "未命名内容"' in detail_response.text
    assert 'titleEl.textContent = "点击记录"' not in detail_response.text
    assert "user_agent" not in detail_response.text
    assert "openid" not in detail_response.text


def test_radar_link_form_hides_internal_tracking_fields_and_type_sections(client):
    link = _create_link(client, target_type="pdf", media_item_id="attachment_masked_001", original_url="")

    response = client.get(f"/admin/radar-links/{link['id']}/edit")

    assert response.status_code == 200
    assert "来源渠道" not in response.text
    assert "员工归属" not in response.text
    assert "活动 ID" not in response.text
    assert 'name="source_channel" type="hidden"' in response.text
    assert 'name="staff_id" type="hidden"' in response.text
    assert 'name="campaign_id" type="hidden"' in response.text
    assert ".radar-field[hidden]" in response.text
    assert "data-link-config" in response.text
    assert "data-media-config hidden" in response.text


def test_radar_link_share_returns_full_url_and_base64_jpeg_qr(client):
    link = _create_link(client, title="课程介绍 PDF")

    response = client.get(f"/api/admin/radar-links/{link['id']}/share")

    assert response.status_code == 200
    share = response.json()["share"]
    assert share["title"] == "课程介绍 PDF"
    assert share["url"] == f"https://testserver/r/{link['code']}"
    assert share["path"] == f"/r/{link['code']}"
    assert share["qr_data_url"].startswith("data:image/jpeg;base64,")
    assert share["download_filename"] == "课程介绍 PDF二维码.jpg"


def test_radar_events_support_stage_filter_and_mask_identity(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_events"}, follow_redirects=False)

    response = client.get(f"/api/admin/radar-links/{link['id']}/events?stage=authorized")

    assert response.status_code == 200
    payload = response.json()
    assert payload["link"]["wrapper_url"] == f"https://testserver/r/{link['code']}"
    assert payload["pagination"]["has_more"] is False
    assert [event["stage"] for event in payload["events"]] == ["authorized"]
    assert payload["events"][0]["unionid_masked"] == "unioni...ents"
    assert "unionid" not in payload["events"][0]


def test_radar_events_export_returns_identity_time_only(client):
    link = _create_link(client, auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_export", "external_userid": "wm_external_001"},
        follow_redirects=False,
    )

    response = client.get(f"/api/admin/radar-links/{link['id']}/events/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    lines = response.text.lstrip("\ufeff").splitlines()
    assert lines[0] == "unionid,external_userid,created_at"
    assert "unionid_export,wm_external_001," in response.text
    assert "openid" not in response.text
    assert "user_agent" not in response.text


def test_public_redirect_query_cannot_override_original_url(client):
    link = _create_link(client, original_url="https://example.com/fixed")

    response = client.get(f"/r/{link['code']}?redirect=https://evil.example/phish", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/fixed"


def test_create_image_radar_link_and_authorized_view_records_content_events(client):
    link = _create_link(
        client,
        target_type="image",
        media_item_id="image_masked_001",
        original_url="",
        auth_required=True,
    )
    assert link["target_type"] == "image"
    assert link["original_url"] == ""
    assert link["media_item_id"] == "image_masked_001"
    assert link["file_name_snapshot"] == "image_masked_001.png"

    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    callback_response = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_image_viewer"},
        follow_redirects=False,
    )

    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == f"/radar/view/{link['code']}"
    assert "aicrm_radar_viewer" in callback_response.headers.get("set-cookie", "")

    viewer_response = client.get(callback_response.headers["location"])
    assert viewer_response.status_code == 200
    assert "/api/h5/radar-contents/" in viewer_response.text
    assert "下载" not in viewer_response.text

    event_response = client.post(f"/api/h5/radar-contents/{link['code']}/events", json={"stage": "viewer_open", "page": 1})
    assert event_response.status_code == 200

    image_response = client.get(f"/api/h5/radar-contents/{link['code']}/image")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"].startswith("image/png")

    stats = client.get(f"/api/admin/radar-links/{link['id']}/stats").json()["stats"]
    assert stats["total_landings"] == 1
    assert stats["authorized_users"] == 1
    assert stats["viewer_opens"] == 2
    assert stats["image_loaded"] == 1


def test_signed_viewer_session_is_the_only_identity_source_for_content_events(client):
    link = _create_link(
        client,
        target_type="image",
        media_item_id="image_masked_001",
        original_url="",
        auth_required=True,
    )
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    callback = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_signed_viewer"},
        follow_redirects=False,
    )
    assert callback.status_code == 302

    viewer = client.get(callback.headers["location"])
    assert viewer.status_code == 200
    events = client.get(f"/api/admin/radar-links/{link['id']}/events?stage=viewer_open").json()["events"]
    assert events[0]["unionid_masked"] == "unioni...ewer"

    viewer_cookie = client.cookies.get("aicrm_radar_viewer")
    # The callback cookie is scoped to the test-server domain. Adding an
    # unscoped cookie with the same name leaves two candidates in httpx's jar,
    # whose header order differs across Python versions. Send exactly one
    # tampered credential so this remains an identity-boundary test.
    client.cookies.clear()
    client.cookies.set("aicrm_radar_viewer", f"{viewer_cookie}tampered")
    rejected = client.get(f"/radar/view/{link['code']}")
    assert rejected.status_code == 400
    assert "viewer session" in rejected.text


def test_pdf_radar_content_requires_viewer_session_and_streams_inline_pdf(client):
    upload_response = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("brief.pdf", b"%PDF-1.4\n% radar test\n", "application/pdf")},
    )
    assert upload_response.status_code == 200, upload_response.text
    media_id = upload_response.json()["item"]["id"]
    link = _create_link(
        client,
        target_type="pdf",
        media_item_id=media_id,
        original_url="",
        auth_required=True,
    )

    no_session_response = client.get(f"/radar/view/{link['code']}")
    assert no_session_response.status_code == 400
    assert "viewer session" in no_session_response.text

    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    callback_response = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": state, "unionid": "unionid_pdf_viewer"},
        follow_redirects=False,
    )
    assert callback_response.headers["location"] == f"/radar/view/{link['code']}"

    viewer_response = client.get(callback_response.headers["location"])
    assert viewer_response.status_code == 200
    assert "pdfjs-dist" not in viewer_response.text
    assert "cdn.jsdelivr" not in viewer_response.text
    assert f"/api/h5/radar-contents/{link['code']}/manifest" in viewer_response.text
    assert "IntersectionObserver" in viewer_response.text
    assert "<iframe" not in viewer_response.text
    assert "下载" not in viewer_response.text

    pdf_response = client.get(f"/api/h5/radar-contents/{link['code']}/pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"].startswith("application/pdf")
    assert pdf_response.headers["content-disposition"].startswith('inline; filename="brief.pdf"')

    stats = client.get(f"/api/admin/radar-links/{link['id']}/stats").json()["stats"]
    assert stats["pdf_opened"] == 1


def test_radar_pdf_upload_accepts_3mb_10mb_and_octet_stream_pdf(client):
    for file_name, size, content_type in [
        ("three-mb.pdf", 3 * 1024 * 1024, "application/pdf"),
        ("ten-mb.pdf", 10 * 1024 * 1024, "application/pdf"),
        ("octet.pdf", 3 * 1024 * 1024, "application/octet-stream"),
    ]:
        payload = b"%PDF-" + b"0" * (size - 5)
        response = client.post(
            "/api/admin/radar-links/upload-pdf",
            files={"pdf": (file_name, payload, content_type)},
        )
        assert response.status_code == 200, response.text
        item = response.json()["item"]
        assert item["mime_type"] == "application/pdf"
        assert item["file_size"] == size


def test_radar_pdf_upload_rejects_too_large_and_fake_pdf(client):
    too_large = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("too-large.pdf", b"%PDF-" + b"0" * (50 * 1024 * 1024), "application/pdf")},
    )
    assert too_large.status_code == 400
    assert "request_body_too_large" in too_large.text

    fake = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert fake.status_code == 400
    assert "invalid_pdf" in fake.text


def test_radar_pdf_chunk_upload_supports_out_of_order_and_retries(client):
    payload = b"%PDF-" + b"a" * (3 * 1024 * 1024 - 5)
    initiate = client.post(
        "/api/admin/radar-links/pdf-uploads/initiate",
        json={"file_name": "chunked.pdf", "file_size": len(payload), "mime_type": "application/pdf"},
    )
    assert initiate.status_code == 200, initiate.text
    upload = initiate.json()
    part_size = upload["part_size"]
    upload_id = upload["upload_id"]
    parts = [payload[index : index + part_size] for index in range(0, len(payload), part_size)]

    missing_complete = client.post(f"/api/admin/radar-links/pdf-uploads/{upload_id}/complete", json={"name": "chunked"})
    assert missing_complete.status_code == 400
    assert "missing_upload_part" in missing_complete.text

    assert client.put(f"/api/admin/radar-links/pdf-uploads/{upload_id}/parts/2", content=parts[1]).status_code == 200
    assert client.put(f"/api/admin/radar-links/pdf-uploads/{upload_id}/parts/1", content=parts[0]).status_code == 200
    assert client.put(f"/api/admin/radar-links/pdf-uploads/{upload_id}/parts/2", content=parts[1]).status_code == 200
    assert client.put(f"/api/admin/radar-links/pdf-uploads/{upload_id}/parts/3", content=parts[2]).status_code == 200

    complete = client.post(f"/api/admin/radar-links/pdf-uploads/{upload_id}/complete", json={"name": "chunked", "tags": "radar_content"})
    assert complete.status_code == 200, complete.text
    assert complete.json()["media_item_id"]
    assert complete.json()["item"]["file_size"] == len(payload)


def test_radar_pdf_manifest_page_images_and_range(client, monkeypatch):
    monkeypatch.setattr("aicrm_next.extensions.radar.radar_links.application._render_pdf_preview_assets_with_pymupdf", lambda *args, **kwargs: [])
    upload_response = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("manifest.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n", "application/pdf")},
    )
    media_id = upload_response.json()["item"]["id"]
    link = _create_link(client, target_type="pdf", media_item_id=media_id, original_url="", auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_manifest"}, follow_redirects=False)

    manifest = client.get(f"/api/h5/radar-contents/{link['code']}/manifest")
    assert manifest.status_code == 200, manifest.text
    data = manifest.json()
    assert data["preview_mode"] == "page_image"
    assert data["processing_status"] == "ready"
    assert data["page_count"] == 1
    assert data["pages"][0]["file_size"] < 2 * 1024 * 1024
    assert "storage.invalid" not in manifest.text

    page = client.get(f"/api/h5/radar-contents/{link['code']}/pdf/pages/1")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/")
    assert page.headers["cache-control"].startswith("private")

    ranged = client.get(f"/api/h5/radar-contents/{link['code']}/pdf", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"].startswith("bytes 0-9/")
    assert ranged.headers["content-type"].startswith("application/pdf")
    assert ranged.headers["content-disposition"].startswith("inline;")

    invalid = client.get(f"/api/h5/radar-contents/{link['code']}/pdf", headers={"Range": "bytes=999999-1000000"})
    assert invalid.status_code == 416

    events = client.get(f"/api/admin/radar-links/{link['id']}/events?stage=pdf_page_loaded").json()["events"]
    assert events[0]["query_params_json"]["page_no"] == 1


def test_radar_pdf_manifest_requires_authorized_viewer_session(client):
    upload_response = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("locked.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    media_id = upload_response.json()["item"]["id"]
    link = _create_link(client, target_type="pdf", media_item_id=media_id, original_url="", auth_required=True)

    response = client.get(f"/api/h5/radar-contents/{link['code']}/manifest")

    assert response.status_code == 400
    assert "viewer session" in response.text


def test_radar_pdf_wechat_viewer_uses_page_image_without_external_cdn(client):
    upload_response = client.post(
        "/api/admin/radar-links/upload-pdf",
        files={"pdf": ("wechat.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    media_id = upload_response.json()["item"]["id"]
    link = _create_link(client, target_type="pdf", media_item_id=media_id, original_url="", auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_wechat"}, follow_redirects=False)

    response = client.get(
        f"/radar/view/{link['code']}",
        headers={"User-Agent": "Mozilla/5.0 MicroMessenger/8.0.47"},
    )

    assert response.status_code == 200
    assert f"/api/h5/radar-contents/{link['code']}/manifest" in response.text
    assert "pdfjs-dist" not in response.text
    assert "cdn.jsdelivr" not in response.text
    assert "arrayBuffer" not in response.text


def test_radar_image_manifest_and_octet_stream_upload(client):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+b5f0AAAAASUVORK5CYII=")
    upload = client.post(
        "/api/admin/radar-links/upload-image",
        files={"image": ("mobile.png", png, "application/octet-stream")},
    )
    assert upload.status_code == 200, upload.text
    media_id = upload.json()["item"]["id"]
    link = _create_link(client, target_type="image", media_item_id=media_id, original_url="", auth_required=True)
    landing_response = client.get(f"/r/{link['code']}", follow_redirects=False)
    state = _state_from_oauth_start_location(landing_response.headers["location"])
    client.get("/api/h5/radar/oauth/callback", params={"state": state, "unionid": "unionid_image_manifest"}, follow_redirects=False)

    manifest = client.get(f"/api/h5/radar-contents/{link['code']}/image/manifest")
    assert manifest.status_code == 200
    assert "mobile_1080" in manifest.json()["variants"]

    variant = client.get(f"/api/h5/radar-contents/{link['code']}/image/variants/mobile_1080")
    assert variant.status_code == 200
    assert variant.headers["content-type"].startswith("image/")
    assert "attachment" not in variant.headers.get("content-disposition", "")


def test_radar_links_uses_postgres_repo_when_production_data_ready(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    repo = build_radar_links_repository()

    assert repo.__class__.__name__ == "PostgresRadarLinksRepository"


def test_radar_links_api_does_not_return_fixture_success_when_production_data_ready(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    response = TestClient(create_app(), raise_server_exceptions=False).get("/api/admin/radar-links")

    assert response.status_code == 503
    assert "production_unavailable" in response.text
    assert "fixture_repository_blocked_in_production" not in response.text


def test_alembic_schema_includes_radar_pdf_preview_assets():
    migration = (ROOT / "migrations" / "versions" / "0025_radar_pdf_preview_assets.py").read_text(encoding="utf-8")

    assert "ALTER TABLE IF EXISTS radar_links" in migration
    assert "CREATE TABLE IF NOT EXISTS radar_pdf_preview_assets" in migration
    assert "media_item_id TEXT NOT NULL" in migration
    assert "pdf_processing_status TEXT NOT NULL DEFAULT ''" in migration
    assert "pdf_preview_error_code TEXT NOT NULL DEFAULT ''" in migration
    assert "pdf_preview_error_message TEXT NOT NULL DEFAULT ''" in migration
    assert "idx_radar_pdf_preview_assets_link" in migration
