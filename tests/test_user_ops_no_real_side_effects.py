from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def test_user_ops_module_has_no_legacy_forward_or_real_external_markers() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in Path("aicrm_next/automation/ops_enrollment").glob("*.py"))

    forbidden = [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        '"fallback_used": True',
        "'fallback_used': True",
        '"real_external_call_executed": True',
        "'real_external_call_executed': True",
        "real_enabled",
        "wecom_client",
        "requests.post(",
        "httpx.post(",
        "upload_media",
    ]
    assert [token for token in forbidden if token in combined] == []


def test_user_ops_preview_routes_do_not_execute_external_calls() -> None:
    client = TestClient(create_app())
    broadcast = client.post(
        "/api/admin/user-ops/broadcast/preview",
        json={"filters": {"tag": "黄小璨"}, "message": {"text": "测试预览，不发送"}},
    ).json()
    export = client.post(
        "/api/admin/user-ops/export/preview",
        json={"filters": {"tag": "黄小璨"}, "fields": ["mobile"]},
    ).json()

    for body in [broadcast, export]:
        assert body["real_external_call_executed"] is False
        assert body["side_effect_plan"]["real_external_call_executed"] is False
        assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
