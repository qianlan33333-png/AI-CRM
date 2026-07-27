from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aicrm_next.automation_engine import channels_api
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_TEMPLATES = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console"
FRONTEND_STATIC = ROOT / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console"
AUTOMATION_TEMPLATES = ROOT / "aicrm_next" / "automation_engine" / "templates" / "admin_console"
AUTOMATION_STATIC = ROOT / "aicrm_next" / "automation_engine" / "static" / "admin_console"
CUSTOMER_TAGS_TEMPLATES = ROOT / "aicrm_next" / "crm" / "customer_tags" / "templates" / "admin_console"
CUSTOMER_TAGS_STATIC = ROOT / "aicrm_next" / "crm" / "customer_tags" / "static" / "admin_console"
RADAR_TEMPLATES = ROOT / "aicrm_next" / "extensions" / "radar" / "radar_links" / "templates" / "admin_console"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SECRET_KEY", "channel-radar-tag-pages-native-test")
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    channels_api._FIXTURE_CHANNELS.clear()
    channels_api._FIXTURE_CHANNEL_ASSIGNEES.clear()
    channels_api._FIXTURE_ASSIGNMENT_EVENTS.clear()
    channels_api._NEXT_ID = 1
    channels_api._NEXT_ASSIGNEE_ID = 1
    channels_api._NEXT_ASSIGNMENT_EVENT_ID = 1
    return TestClient(create_app(), raise_server_exceptions=False)


def test_channel_radar_and_tag_pages_are_served_by_next_native_routers(monkeypatch) -> None:
    app = create_app()
    route_modules = {
        route.name: route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.name
        in {
            "api.admin_channels_page",
            "api.admin_channel_new_page",
            "api.admin_channel_edit_page",
            "api.admin_radar_links",
            "api.admin_radar_link_new",
            "api.admin_radar_link_edit",
            "api.admin_radar_link_detail",
            "api.admin_wecom_tags_page",
        }
    }

    assert route_modules == {
        "api.admin_channels_page": "aicrm_next.automation_engine.channel_admin_pages",
        "api.admin_channel_new_page": "aicrm_next.automation_engine.channel_admin_pages",
        "api.admin_channel_edit_page": "aicrm_next.automation_engine.channel_admin_pages",
        "api.admin_radar_links": "aicrm_next.extensions.radar.radar_links.admin_pages",
        "api.admin_radar_link_new": "aicrm_next.extensions.radar.radar_links.admin_pages",
        "api.admin_radar_link_edit": "aicrm_next.extensions.radar.radar_links.admin_pages",
        "api.admin_radar_link_detail": "aicrm_next.extensions.radar.radar_links.admin_pages",
        "api.admin_wecom_tags_page": "aicrm_next.crm.customer_tags.admin_pages",
    }

    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={"channel_name": "Native ownership smoke", "channel_type": "qrcode", "owner_staff_id": "sales_01"},
    )
    assert created.status_code == 201
    assert re.fullmatch(r"channel_[0-9a-f]{32}", created.json()["channel"]["channel_code"])
    channel_id = created.json()["channel"]["id"]

    for url in [
        "/admin/channels",
        "/admin/channels/new",
        f"/admin/channels/{channel_id}/edit",
        "/admin/radar-links",
        "/admin/radar-links/new",
        "/admin/radar-links/1/edit",
        "/admin/radar-links/1/detail",
        "/admin/wecom-tags",
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers.get("X-AICRM-Compatibility-Facade") is None, url
        assert "客户管理后台" in response.text, url
        assert "Not Found" not in response.text, url


def test_channel_radar_and_tag_pages_keep_existing_static_and_api_contracts(monkeypatch) -> None:
    client = _client(monkeypatch)

    channels = client.get("/admin/channels")
    channel_new = client.get("/admin/channels/new")
    radar_list = client.get("/admin/radar-links")
    radar_new = client.get("/admin/radar-links/new")
    radar_detail = client.get("/admin/radar-links/1/detail")
    wecom_tags = client.get("/admin/wecom-tags")

    assert "/api/admin/channels?limit=300" in channels.text
    assert "/static/automation-engine/admin_console/channel_admission_pages.css" in channels.text
    assert "/static/automation-engine/admin_console/channel_code_center_next.js" in channels.text
    assert "新建渠道" in channels.text
    assert "自动化运营" in channels.text

    assert "/static/automation-engine/admin_console/channel_admission_pages.css" in channel_new.text
    assert "/static/automation-engine/admin_console/channel_admission_pages.js" in channel_new.text
    assert "/static/admin_console/material_picker.js" in channel_new.text
    assert "/static/admin_console/send_content_composer.js" in channel_new.text
    assert '"/api/admin/wecom/tags"' in channel_new.text
    assert "留空将自动生成唯一编码" in channel_new.text

    assert "/api/admin/radar-links" in radar_list.text
    assert "/admin/radar-links/new" in radar_list.text
    assert "/api/admin/radar-links/new/options" in radar_new.text
    assert "/static/admin_console/material_picker.js" in radar_new.text
    assert "AICRMMaterialPicker.open" in radar_new.text
    assert "/api/admin/radar-links/${linkId}/events" in radar_detail.text

    assert "/static/customer-tags/admin_console/wecom_tag_management.js" in wecom_tags.text
    assert 'data-api-tags="/api/admin/wecom/tags"' in wecom_tags.text
    assert 'data-api-groups="/api/admin/wecom/tag-groups"' in wecom_tags.text
    assert 'data-api-sync="/api/admin/wecom/tags/sync"' in wecom_tags.text


def test_channel_center_page_routes_keep_form_contract(monkeypatch) -> None:
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={"channel_name": "二级编辑页渠道", "channel_code": "edit-contract", "owner_staff_id": "sales_01"},
    )
    assert created.status_code == 201
    channel_id = created.json()["channel"]["id"]

    list_page = client.get("/admin/channels")
    new_page = client.get("/admin/channels/new")
    edit_page = client.get(f"/admin/channels/{channel_id}/edit")

    assert list_page.status_code == 200
    assert "渠道码中心" in list_page.text
    assert "渠道总数" in list_page.text
    assert "渠道资产" in list_page.text
    assert "企微获客助手链接" in list_page.text
    assert "渠道用户" in list_page.text

    assert new_page.status_code == 200
    assert 'data-channel-admission-page="channel-form"' in new_page.text
    assert 'data-is-edit="0"' in new_page.text
    assert 'data-api-create="/api/admin/channels"' in new_page.text
    assert "channel_code_form.html" not in new_page.text

    assert edit_page.status_code == 200
    for token in [
        'data-channel-admission-page="channel-form"',
        'data-is-edit="1"',
        f'data-api-detail="/api/admin/channels/{channel_id}"',
        "渠道码中心",
        "基础配置",
        "渠道载体",
        "客服分配",
        "欢迎语素材",
        "入渠标签",
    ]:
        assert token in edit_page.text


def test_channel_center_api_routes_keep_unified_contract(monkeypatch) -> None:
    def fake_generate(command):
        return {
            "ok": True,
            "channel_id": command.channel_id,
            "scene_value": "aqr_contract",
            "qr_url": "https://wework.qpic.cn/contract",
            "qrcode_asset_id": 1,
            "source": "test",
        }

    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.generate_channel_qrcode", fake_generate)
    client = _client(monkeypatch)

    created = client.post("/api/admin/channels", json={"channel_name": "API 契约", "channel_code": "api-contract"})
    assert created.status_code == 201
    channel_id = created.json()["channel"]["id"]

    checks = [
        client.get("/api/admin/channels?limit=300"),
        client.get(f"/api/admin/channels/{channel_id}"),
        client.patch(f"/api/admin/channels/{channel_id}", json={"status": "inactive"}),
        client.post(f"/api/admin/channels/{channel_id}/qrcode/generate", json={}),
        client.get(f"/api/admin/channels/{channel_id}/contacts?limit=20"),
        client.get("/api/admin/wecom/tags"),
        client.get("/api/admin/common/operation-members?scope=channel_code&page_size=100"),
    ]
    assert [response.status_code for response in checks] == [200, 200, 200, 200, 200, 200, 200]
    assert checks[0].json()["channels"]
    assert checks[2].json()["channel"]["status"] == "inactive"
    assert checks[3].json()["ok"] is True
    assert checks[4].json()["contacts"] == []


def test_channel_create_without_code_generates_distinct_codes(monkeypatch) -> None:
    client = _client(monkeypatch)

    first = client.post("/api/admin/channels", json={"channel_name": "城市会员 8.8 发布会"})
    second = client.post("/api/admin/channels", json={"channel_name": "第二个未填写编码的渠道"})

    assert first.status_code == second.status_code == 201
    first_code = first.json()["channel"]["channel_code"]
    second_code = second.json()["channel"]["channel_code"]
    assert re.fullmatch(r"channel_[0-9a-f]{32}", first_code)
    assert re.fullmatch(r"channel_[0-9a-f]{32}", second_code)
    assert first_code != second_code


def test_channel_update_repairs_legacy_blank_code(monkeypatch) -> None:
    client = _client(monkeypatch)
    channels_api._FIXTURE_CHANNELS[6] = {
        "id": 6,
        "channel_name": "历史空编码渠道",
        "channel_code": "",
        "channel_type": "qrcode",
        "carrier_type": "qrcode",
        "status": "active",
    }

    updated = client.patch("/api/admin/channels/6", json={"entry_tag_name": "发布会"})

    assert updated.status_code == 200
    assert re.fullmatch(r"channel_[0-9a-f]{32}", updated.json()["channel"]["channel_code"])
    assert updated.json()["channel"]["entry_tag_name"] == "发布会"


def test_channel_duplicate_code_error_does_not_expose_database_detail(monkeypatch) -> None:
    client = _client(monkeypatch)

    def raise_duplicate(_payload):
        raise RuntimeError(
            'duplicate key value violates unique constraint "automation_channel_channel_code_key" '
            "DETAIL: Key (channel_code)=() already exists."
        )

    monkeypatch.setattr(channels_api, "_save_postgres_channel", raise_duplicate)
    response = client.post("/api/admin/channels", json={"channel_name": "重复编码渠道"})

    assert response.status_code == 400
    assert response.json()["detail"] == "渠道编码已存在，请更换后重试"
    assert "duplicate key" not in response.text


def test_channel_radar_and_tag_pages_are_removed_from_frontend_compat_inventory() -> None:
    assert not (ROOT / "aicrm_next/app/admin_console/legacy_routes.py").exists()


def test_channel_radar_and_tag_templates_and_static_are_native_owned() -> None:
    native_files = [
        AUTOMATION_TEMPLATES / "channel_code_center.html",
        AUTOMATION_TEMPLATES / "channel_code_form.html",
        AUTOMATION_STATIC / "channel_admission_pages.css",
        AUTOMATION_STATIC / "channel_admission_pages.js",
        AUTOMATION_STATIC / "channel_code_center_next.js",
        CUSTOMER_TAGS_TEMPLATES / "config_wecom_tags.html",
        CUSTOMER_TAGS_STATIC / "wecom_tag_management.js",
        RADAR_TEMPLATES / "radar_links.html",
        RADAR_TEMPLATES / "radar_link_form.html",
        RADAR_TEMPLATES / "radar_link_detail.html",
    ]
    removed_frontend_compat_files = [
        FRONTEND_TEMPLATES / "channel_code_center.html",
        FRONTEND_TEMPLATES / "channel_code_form.html",
        FRONTEND_STATIC / "channel_admission_pages.css",
        FRONTEND_STATIC / "channel_admission_pages.js",
        FRONTEND_STATIC / "channel_code_center_next.js",
        FRONTEND_TEMPLATES / "config_wecom_tags.html",
        FRONTEND_STATIC / "wecom_tag_management.js",
        FRONTEND_TEMPLATES / "radar_links.html",
        FRONTEND_TEMPLATES / "radar_link_form.html",
        FRONTEND_TEMPLATES / "radar_link_detail.html",
    ]

    for path in native_files:
        assert path.exists(), path
    for path in removed_frontend_compat_files:
        assert not path.exists(), path


def test_native_static_mounts_serve_moved_page_assets(monkeypatch) -> None:
    client = _client(monkeypatch)

    channel_css = client.get("/static/automation-engine/admin_console/channel_admission_pages.css")
    channel_js = client.get("/static/automation-engine/admin_console/channel_admission_pages.js")
    tag_js = client.get("/static/customer-tags/admin_console/wecom_tag_management.js")

    assert channel_css.status_code == 200
    assert ".channel-action-cell" in channel_css.text
    assert channel_js.status_code == 200
    assert "AICRMChannelWelcomeAdapter" in channel_js.text
    assert tag_js.status_code == 200
    assert "/api/admin/wecom/tags" in tag_js.text
