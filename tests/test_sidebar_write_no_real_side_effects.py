from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.sidebar_write import get_sidebar_write_side_effect_plans
from tests.sidebar_auth_test_helpers import install_sidebar_auth


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


def test_sidebar_write_external_effects_create_plans_only(client: TestClient) -> None:
    client.headers.update(
        install_sidebar_auth(
            client,
            viewer_userid="ZhaoYanFang",
            external_userid="wx_ext_001",
        )
    )
    calls = [
        (
            "post",
            "/api/sidebar/signup-tags/mark",
            {"external_userid": "wx_ext_001", "tag_name": "trial-active"},
            "wecom.tag.update",
            "medium",
        ),
        (
            "post",
            "/api/sidebar/marketing-status/set-followup-segment",
            {"external_userid": "wx_ext_001", "segment": "high_intent"},
            "automation.followup_segment_changed",
            "medium",
        ),
        (
            "put",
            "/api/sidebar/v2/profile",
            {"external_userid": "wx_ext_001", "remark": "profile plan only"},
            "wecom.profile.update",
            "medium",
        ),
        (
            "post",
            "/api/sidebar/v2/materials/send",
            {"external_userid": "wx_ext_001", "material_id": "mat-001"},
            "wecom.material.send",
            "high",
        ),
    ]

    for method, path, payload, effect_type, risk_level in calls:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 200
        body = response.json()
        plan = body["side_effect_plan"]
        assert body["real_external_call_executed"] is False
        assert plan["effect_type"] == effect_type
        assert plan["adapter_mode"] == "real_blocked"
        assert plan["risk_level"] == risk_level
        assert plan["requires_approval"] is True
        assert plan["real_external_call_executed"] is False

    plans = get_sidebar_write_side_effect_plans()
    assert [plan["effect_type"] for plan in plans] == [call[3] for call in calls]
    assert all(plan["adapter_mode"] == "real_blocked" for plan in plans)
    assert all(plan["real_external_call_executed"] is False for plan in plans)


def test_sidebar_write_internal_mutations_do_not_create_side_effect_plans(client: TestClient) -> None:
    client.headers.update(
        install_sidebar_auth(
            client,
            viewer_userid="LiuXiao",
            external_userid="wx_ext_002",
        )
    )
    response = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": "wx_ext_002", "mobile": "13800138123"},
    )

    assert response.status_code == 200
    assert "side_effect_plan" not in response.json()
    assert get_sidebar_write_side_effect_plans() == []


def test_sidebar_write_module_does_not_import_real_external_adapters() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("aicrm_next/crm/sidebar_write").glob("*.py")
    )

    forbidden = [
        "forward_to_legacy_flask",
        "legacy_sidebar",
        "wecom_ability" + "_service",
        "httpx.",
        "requests.",
        "send_message",
        "dispatch_private_message",
        "upload_media",
    ]
    assert [token for token in forbidden if token in combined] == []
