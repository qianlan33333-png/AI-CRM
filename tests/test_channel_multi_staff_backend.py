from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine import channels_api
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "channel-multi-staff-backend-test")
    channels_api._FIXTURE_CHANNELS.clear()
    channels_api._FIXTURE_CHANNEL_ASSIGNEES.clear()
    channels_api._FIXTURE_ASSIGNMENT_EVENTS.clear()
    channels_api._NEXT_ID = 1
    channels_api._NEXT_ASSIGNEE_ID = 1
    channels_api._NEXT_ASSIGNMENT_EVENT_ID = 1
    return TestClient(create_app(), raise_server_exceptions=False)


def _ratio_assignees(first: int = 50, second: int = 50) -> list[dict]:
    return [
        {
            "staff_id": "staff-a",
            "display_name": "Staff A",
            "priority": 1,
            "ratio_percent": first,
            "max_scans_24h": None,
            "status": "active",
        },
        {
            "staff_id": "staff-b",
            "display_name": "Staff B",
            "priority": 2,
            "ratio_percent": second,
            "max_scans_24h": None,
            "status": "active",
        },
    ]


def _cap_assignees(first: int = 1, second: int = 1) -> list[dict]:
    return [
        {
            "staff_id": "staff-a",
            "display_name": "Staff A",
            "priority": 1,
            "ratio_percent": None,
            "max_scans_24h": first,
            "status": "active",
        },
        {
            "staff_id": "staff-b",
            "display_name": "Staff B",
            "priority": 2,
            "ratio_percent": None,
            "max_scans_24h": second,
            "status": "active",
        },
    ]


def test_channel_multi_staff_migration_contains_required_schema():
    source = Path("migrations/versions/0036_channel_multi_staff_assignment.py").read_text()

    for token in (
        "assignment_mode",
        "assignment_strategy",
        "overflow_policy",
        "assignment_config_json",
        "automation_channel_assignee",
        "automation_channel_assignment_event",
        "idx_channel_assignee_active",
        "idx_channel_assignment_24h",
        "idx_channel_assignment_unionid",
    ):
        assert token in source


def test_assignees_api_validates_and_saves_ratio_and_cap_switch(monkeypatch):
    client = _client(monkeypatch)
    channel_id = client.post("/api/admin/channels", json={"channel_name": "多客服渠道", "channel_code": "multi"}).json()["channel"]["id"]

    loaded_empty = client.get(f"/api/admin/channels/{channel_id}/assignees")
    assert loaded_empty.status_code == 200
    assert loaded_empty.json()["assignees"] == []

    saved = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "ratio", "assignees": _ratio_assignees(50, 50)},
    )
    assert saved.status_code == 200
    assert saved.json()["assignment_mode"] == "multi_staff"
    assert saved.json()["assignment_strategy"] == "ratio"
    assert [item["ratio_percent"] for item in saved.json()["assignees"]] == [50, 50]

    invalid_90 = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "ratio", "assignees": _ratio_assignees(60, 30)},
    )
    assert invalid_90.status_code == 400
    assert invalid_90.json()["reason"] == "ratio_percent_total_must_equal_100"

    invalid_negative = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "ratio", "assignees": _ratio_assignees(110, -10)},
    )
    assert invalid_negative.status_code == 400
    assert invalid_negative.json()["reason"] == "ratio_percent_must_be_positive"

    six_assignees = [
        {
            "staff_id": f"staff-{index}",
            "display_name": f"Staff {index}",
            "priority": index,
            "ratio_percent": 20 if index <= 5 else 0,
            "status": "active",
        }
        for index in range(1, 7)
    ]
    too_many = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "ratio", "assignees": six_assignees},
    )
    assert too_many.status_code == 400
    assert too_many.json()["reason"] == "active_assignees_count_must_be_1_to_5"

    cap_saved = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "cap_switch", "assignees": _cap_assignees(1, 1)},
    )
    assert cap_saved.status_code == 200
    assert cap_saved.json()["assignment_strategy"] == "cap_switch"
    assert [item["max_scans_24h"] for item in cap_saved.json()["assignees"]] == [1, 1]

    invalid_cap = client.put(
        f"/api/admin/channels/{channel_id}/assignees",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "cap_switch", "assignees": _cap_assignees(0, 1)},
    )
    assert invalid_cap.status_code == 400
    assert invalid_cap.json()["reason"] == "max_scans_24h_must_be_positive"


def test_channel_crud_saves_multi_staff_and_list_detail_serialize(monkeypatch):
    client = _client(monkeypatch)

    created = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "多客服创建",
            "channel_code": "multi-create",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(50, 50),
        },
    )

    assert created.status_code == 201
    channel = created.json()["channel"]
    assert channel["assignment_mode"] == "multi_staff"
    assert channel["assignment_strategy"] == "ratio"
    assert len(channel["assignees"]) == 2

    detail = client.get(f"/api/admin/channels/{channel['id']}").json()["channel"]
    assert detail["assignment_mode"] == "multi_staff"
    assert detail["assignment_strategy"] == "ratio"
    assert len(detail["assignees"]) == 2

    listed = client.get("/api/admin/channels").json()["channels"][0]
    assert listed["assignment_mode"] == "multi_staff"
    assert listed["assignment_strategy"] == "ratio"
    assert listed["assignee_count"] == 2


def test_patch_status_only_preserves_link_welcome_tag_material_and_assignees(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={
            "channel_type": "wecom_customer_acquisition",
            "carrier_type": "link",
            "channel_name": "Link 多客服",
            "channel_code": "link-multi",
            "customer_channel": "wca_link_multi",
            "link_url": "https://work.weixin.qq.com/ca/link-multi",
            "final_url": "https://work.weixin.qq.com/ca/link-multi?customer_channel=wca_link_multi",
            "welcome_message": "欢迎语",
            "welcome_image_library_ids": [1, 2],
            "welcome_miniprogram_library_ids": [3],
            "welcome_attachment_library_ids": [4],
            "entry_tag_id": "tag-1",
            "entry_tag_name": "标签一",
            "entry_tag_group_name": "标签组",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(50, 50),
        },
    ).json()["channel"]

    patched = client.patch(f"/api/admin/channels/{created['id']}", json={"status": "archived"})

    assert patched.status_code == 200
    channel = patched.json()["channel"]
    assert channel["status"] == "archived"
    assert channel["channel_type"] == "wecom_customer_acquisition"
    assert channel["carrier_type"] == "link"
    assert channel["customer_channel"] == "wca_link_multi"
    assert channel["link_url"] == "https://work.weixin.qq.com/ca/link-multi"
    assert channel["final_url"] == "https://work.weixin.qq.com/ca/link-multi?customer_channel=wca_link_multi"
    assert channel["welcome_message"] == "欢迎语"
    assert channel["welcome_image_library_ids"] == [1, 2]
    assert channel["welcome_miniprogram_library_ids"] == [3]
    assert channel["welcome_attachment_library_ids"] == [4]
    assert channel["entry_tag_id"] == "tag-1"
    assert channel["entry_tag_name"] == "标签一"
    assert channel["entry_tag_group_name"] == "标签组"
    assert len(channel["assignees"]) == 2

    share = client.get(f"/api/admin/channels/{created['id']}/share-link")
    assert share.status_code == 200
    assert share.json()["share_url"] == "https://work.weixin.qq.com/ca/link-multi?customer_channel=wca_link_multi"


def test_patch_status_only_preserves_qrcode_fields(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={"channel_name": "二维码渠道", "channel_code": "qr-status", "auto_accept_friend": True},
    ).json()["channel"]
    channel_id = int(created["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_generated"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/generated"

    patched = client.patch(f"/api/admin/channels/{channel_id}", json={"status": "archived"})

    assert patched.status_code == 200
    channel = patched.json()["channel"]
    assert channel["status"] == "archived"
    assert channel["channel_type"] == "qrcode"
    assert channel["carrier_type"] == "qrcode"
    assert channel["scene_value"] == "aqr_generated"
    assert channel["qr_url"] == "https://wework.qpic.cn/generated"
    assert channel["auto_accept_friend"] is True


def test_unified_create_and_patch_qrcode_payload_boundary(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={
            "channel_type": "qrcode",
            "carrier_type": "qrcode",
            "channel_name": "二维码保存边界",
            "channel_code": "qr-boundary",
            "auto_accept_friend": True,
            "customer_channel": "should-not-save",
            "link_url": "https://work.weixin.qq.com/ca/should-not-save",
            "final_url": "https://work.weixin.qq.com/ca/should-not-save?customer_channel=bad",
        },
    )
    assert created.status_code == 201
    channel = created.json()["channel"]
    channel_id = int(channel["id"])
    assert channel["channel_type"] == "qrcode"
    assert channel["carrier_type"] == "qrcode"
    assert channel["auto_accept_friend"] is True
    assert channel["customer_channel"] == ""
    assert channel["link_url"] == ""
    assert channel["final_url"] == ""
    assert channel["scene_value"] == ""
    assert channel["qr_url"] == ""

    patched = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={
            "channel_type": "qrcode",
            "carrier_type": "qrcode",
            "customer_channel": "still-ignored",
            "link_url": "https://work.weixin.qq.com/ca/still-ignored",
            "final_url": "https://work.weixin.qq.com/ca/still-ignored?customer_channel=bad",
            "auto_accept_friend": False,
        },
    )
    assert patched.status_code == 200
    channel = patched.json()["channel"]
    assert channel["auto_accept_friend"] is False
    assert channel["customer_channel"] == ""
    assert channel["link_url"] == ""
    assert channel["final_url"] == ""

    rejected_scene = client.patch(f"/api/admin/channels/{channel_id}", json={"scene_value": "frontend_scene"})
    rejected_qr = client.patch(f"/api/admin/channels/{channel_id}", json={"qr_url": "https://wework.qpic.cn/frontend"})
    assert rejected_scene.status_code == 400
    assert rejected_scene.json()["detail"] == "scene_value_is_system_managed"
    assert rejected_qr.status_code == 400
    assert rejected_qr.json()["detail"] == "qr_url_is_system_managed"


def test_unified_create_and_patch_wecom_link_payload_boundary(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={
            "channel_type": "wecom_customer_acquisition",
            "carrier_type": "link",
            "channel_name": "获客链接保存边界",
            "channel_code": "link-boundary",
            "customer_channel": "wca_boundary",
            "link_url": "https://work.weixin.qq.com/ca/boundary",
            "final_url": "https://work.weixin.qq.com/ca/boundary?customer_channel=wca_boundary",
            "auto_accept_friend": True,
        },
    )
    assert created.status_code == 201
    channel = created.json()["channel"]
    channel_id = int(channel["id"])
    assert channel["channel_type"] == "wecom_customer_acquisition"
    assert channel["carrier_type"] == "link"
    assert channel["customer_channel"] == "wca_boundary"
    assert channel["link_url"] == "https://work.weixin.qq.com/ca/boundary"
    assert channel["final_url"] == "https://work.weixin.qq.com/ca/boundary?customer_channel=wca_boundary"
    assert channel["auto_accept_friend"] is False

    patched = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={
            "channel_type": "wecom_customer_acquisition",
            "carrier_type": "link",
            "customer_channel": "wca_boundary_next",
            "link_url": "https://work.weixin.qq.com/ca/boundary-next",
            "final_url": "https://work.weixin.qq.com/ca/boundary-next?customer_channel=wca_boundary_next",
            "auto_accept_friend": True,
        },
    )
    assert patched.status_code == 200
    channel = patched.json()["channel"]
    assert channel["customer_channel"] == "wca_boundary_next"
    assert channel["link_url"] == "https://work.weixin.qq.com/ca/boundary-next"
    assert channel["final_url"] == "https://work.weixin.qq.com/ca/boundary-next?customer_channel=wca_boundary_next"
    assert channel["auto_accept_friend"] is False


def test_unified_create_and_patch_validate_assignment_contract(monkeypatch):
    client = _client(monkeypatch)

    one_ratio = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "单客服比例",
            "channel_code": "ratio-one",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": [_ratio_assignees(100, 0)[0]],
        },
    )
    assert one_ratio.status_code == 201
    assert [item["ratio_percent"] for item in one_ratio.json()["channel"]["assignees"]] == [100]

    two_ratio = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "双客服比例",
            "channel_code": "ratio-two",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(50, 50),
        },
    )
    assert two_ratio.status_code == 201
    channel_id = int(two_ratio.json()["channel"]["id"])

    invalid_ratio = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "ratio", "assignees": _ratio_assignees(60, 30)},
    )
    assert invalid_ratio.status_code == 400
    assert invalid_ratio.json()["detail"] == "ratio_percent_total_must_equal_100"

    too_many = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": [
                {"staff_id": f"staff-{index}", "display_name": f"Staff {index}", "priority": index, "ratio_percent": 20, "status": "active"}
                for index in range(1, 6)
            ]
            + [{"staff_id": "staff-6", "display_name": "Staff 6", "priority": 6, "ratio_percent": 0, "status": "active"}],
        },
    )
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "active_assignees_count_must_be_1_to_5"

    cap_saved = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "cap_switch", "assignees": _cap_assignees(1, 2)},
    )
    assert cap_saved.status_code == 200
    assert [item["max_scans_24h"] for item in cap_saved.json()["channel"]["assignees"]] == [1, 2]

    invalid_cap = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={"assignment_mode": "multi_staff", "assignment_strategy": "cap_switch", "assignees": _cap_assignees(0, 1)},
    )
    assert invalid_cap.status_code == 400
    assert invalid_cap.json()["detail"] == "max_scans_24h_must_be_positive"

    invalid_strategy = client.patch(f"/api/admin/channels/{channel_id}", json={"assignment_strategy": "weighted_random"})
    assert invalid_strategy.status_code == 400
    assert invalid_strategy.json()["detail"] == "invalid_assignment_strategy"


def test_channel_status_only_patch_supports_list_page_lifecycle(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "列表状态操作",
            "channel_code": "list-status",
            "welcome_message": "欢迎语",
            "welcome_image_library_ids": [1],
            "entry_tag_id": "tag-list",
            "entry_tag_name": "列表标签",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(50, 50),
        },
    ).json()["channel"]
    channel_id = int(created["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_list_status"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/list-status"

    inactive = client.patch(f"/api/admin/channels/{channel_id}", json={"status": "inactive"})
    assert inactive.status_code == 200
    assert inactive.json()["channel"]["status"] == "inactive"

    active = client.patch(f"/api/admin/channels/{channel_id}", json={"status": "active"})
    assert active.status_code == 200
    assert active.json()["channel"]["status"] == "active"

    archived = client.patch(f"/api/admin/channels/{channel_id}", json={"status": "archived"})
    assert archived.status_code == 200
    channel = archived.json()["channel"]
    assert channel["status"] == "archived"
    assert channel["scene_value"] == "aqr_list_status"
    assert channel["qr_url"] == "https://wework.qpic.cn/list-status"
    assert channel["welcome_message"] == "欢迎语"
    assert channel["welcome_image_library_ids"] == [1]
    assert channel["entry_tag_id"] == "tag-list"
    assert channel["entry_tag_name"] == "列表标签"
    assert channel["assignment_mode"] == "multi_staff"
    assert channel["assignment_strategy"] == "ratio"
    assert len(channel["assignees"]) == 2
    listed = client.get("/api/admin/channels").json()["channels"]
    assert all(item["id"] != channel_id for item in listed)

    archived_listed = client.get("/api/admin/channels?status=archived").json()["channels"]
    assert any(item["id"] == channel_id and item["status"] == "archived" for item in archived_listed)

    include_archived = client.get("/api/admin/channels?include_archived=true").json()["channels"]
    assert any(item["id"] == channel_id and item["status"] == "archived" for item in include_archived)

    invalid = client.patch(f"/api/admin/channels/{channel_id}", json={"status": "deleted"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid_channel_status"


def test_ratio_assignment_distribution_and_events(monkeypatch):
    client = _client(monkeypatch)
    channel_id = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "Ratio 50",
            "channel_code": "ratio-50",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(50, 50),
        },
    ).json()["channel"]["id"]

    assigned = [
        client.post(
            f"/api/admin/channels/{channel_id}/assignment/preview",
            json={"external_contact_id": f"external-{index}", "write_event": True},
        ).json()["assignee_staff_id"]
        for index in range(10)
    ]
    assert assigned.count("staff-a") == 5
    assert assigned.count("staff-b") == 5

    events = client.get(f"/api/admin/channels/{channel_id}/assignment-events?limit=10")
    assert events.status_code == 200
    assert len(events.json()["events"]) == 10

    channel_60 = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "Ratio 60",
            "channel_code": "ratio-60",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "assignees": _ratio_assignees(60, 40),
        },
    ).json()["channel"]
    assigned_60 = [
        client.post(
            f"/api/admin/channels/{channel_60['id']}/assignment/preview",
            json={"external_contact_id": f"external-60-{index}", "write_event": True},
        ).json()["assignee_staff_id"]
        for index in range(10)
    ]
    assert assigned_60.count("staff-a") == 6
    assert assigned_60.count("staff-b") == 4


def test_cap_switch_assignment_and_full_cap(monkeypatch):
    client = _client(monkeypatch)
    channel_id = client.post(
        "/api/admin/channels",
        json={
            "channel_name": "Cap switch",
            "channel_code": "cap-switch",
            "assignment_mode": "multi_staff",
            "assignment_strategy": "cap_switch",
            "assignees": _cap_assignees(1, 1),
        },
    ).json()["channel"]["id"]

    first = client.post(f"/api/admin/channels/{channel_id}/assignment/preview", json={"external_contact_id": "external-1", "write_event": True})
    second = client.post(f"/api/admin/channels/{channel_id}/assignment/preview", json={"external_contact_id": "external-2", "write_event": True})
    third = client.post(f"/api/admin/channels/{channel_id}/assignment/preview", json={"external_contact_id": "external-3", "write_event": True})

    assert first.status_code == 200
    assert first.json()["assignee_staff_id"] == "staff-a"
    assert second.status_code == 200
    assert second.json()["assignee_staff_id"] == "staff-b"
    assert third.status_code == 409
    assert third.json()["reason"] == "all_assignees_reached_24h_cap"
