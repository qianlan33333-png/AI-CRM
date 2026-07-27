from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.automation_engine import channels_api
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
)
from aicrm_next.platform_foundation.external_effects.repo import reset_external_effect_fixture_state
from aicrm_next.platform_foundation.push_center.capability_status import PushCapabilityStatusReadService
from aicrm_next.extensions.forms.questionnaire.admin_write import reset_questionnaire_admin_write_fixture_state
from aicrm_next.extensions.forms.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_questionnaire_fixture_state()
    reset_questionnaire_admin_write_fixture_state()
    reset_questionnaire_h5_write_fixture_state()
    reset_external_effect_fixture_state()
    channels_api._FIXTURE_CHANNELS.clear()
    channels_api._FIXTURE_CHANNELS.update(
        {
            701: {
                "id": 701,
                "channel_name": "问卷交付渠道",
                "channel_type": "qrcode",
                "carrier_type": "qrcode",
                "status": "active",
                "scene_value": "questionnaire-delivery",
                "qr_url": "https://cdn.example.com/questionnaire-delivery.png",
                "active_qrcode_asset_id": 9701,
                "qrcode_status": "active",
            },
            702: {
                "id": 702,
                "channel_name": "无二维码渠道",
                "channel_type": "qrcode",
                "carrier_type": "qrcode",
                "status": "active",
                "scene_value": "missing-qr",
                "qr_url": "",
                "active_qrcode_asset_id": 0,
                "qrcode_status": "not_generated",
            },
        }
    )
    client = TestClient(create_app())
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def _content_payload(title: str = "专项增强问卷") -> dict:
    return {
        "title": title,
        "slug": "questionnaire-operations-test",
        "description": "只包含问卷内容字段",
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "title": "是否继续？",
                "required": True,
                "options": [{"id": "yes", "label": "是", "value": "yes"}],
            }
        ],
    }


def _create_questionnaire(client: TestClient) -> int:
    response = client.post("/api/admin/questionnaires", json=_content_payload())
    assert response.status_code == 200, response.text
    return int(response.json()["questionnaire_id"])


def test_platform_push_capability_projection_applies_runtime_gates() -> None:
    class CapabilitySettings:
        def __init__(self, values):
            self.values = values

        def get_values(self, keys):
            return {key: self.values.get(key, "") for key in keys}

    enabled = PushCapabilityStatusReadService(
        CapabilitySettings(
            {
                "AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED": "true",
                "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
                "AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE": "true",
            }
        )
    ).get_capability_status("questionnaire_external_push")
    assert enabled["enabled"] is True

    missing_allowlist = PushCapabilityStatusReadService(
        CapabilitySettings(
            {
                "AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED": "true",
                "AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE": "true",
            }
        )
    ).get_capability_status("questionnaire_external_push")
    assert missing_allowlist["enabled"] is False
    assert missing_allowlist["reason"] == "effect_type_allowlist_missing"


def test_completion_operations_are_independent_and_mutually_exclusive(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)

    external = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push",
        json={"enabled": True, "webhook_url": "https://hooks.example.com/independent"},
    )
    assert external.status_code == 200, external.text
    assert "completion" not in external.json()

    lead = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 701},
    )
    assert lead.status_code == 200, lead.text
    completion = lead.json()["completion"]
    assert completion["mode"] == "lead_qr"
    assert completion["lead_channel_id"] == 701
    assert completion["lead_channel"]["qr_url"].endswith("questionnaire-delivery.png")
    assert completion["completion_target"]["enabled"] is False
    assert "external_push" not in lead.json()
    assert client.get(f"/api/admin/questionnaires/{questionnaire_id}/operations").json()["external_push"]["webhook_url"].endswith("independent")

    redirect = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={
            "enabled": True,
            "action_type": "redirect",
            "completion_target": {
                "enabled": True,
                "target_type": "h5",
                "open_strategy": "h5_redirect",
                "h5_url": "https://example.com/questionnaire-finished",
            },
        },
    )
    assert redirect.status_code == 200, redirect.text
    completion = redirect.json()["completion"]
    assert completion["mode"] == "redirect"
    assert completion["lead_channel_id"] is None
    assert completion["completion_target"]["h5_url"].endswith("questionnaire-finished")

    url_link = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={
            "enabled": True,
            "action_type": "redirect",
            "completion_target": {
                "enabled": True,
                "target_type": "url_link",
                "open_strategy": "url_link",
                "url_link": {
                    "enabled": True,
                    "source_url": "https://example.com/api/dynamic-url-link",
                    "response_url_key": "url_link",
                },
            },
        },
    )
    assert url_link.status_code == 200, url_link.text
    assert url_link.json()["completion"]["completion_target"]["target_type"] == "url_link"

    disabled = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["completion"]["enabled"] is False
    assert disabled.json()["completion"]["lead_channel_id"] is None
    assert disabled.json()["completion"]["completion_target"]["enabled"] is False


def test_invalid_operations_json_does_not_clear_existing_configuration(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)
    assert client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 701},
    ).status_code == 200

    malformed = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        content=b"{broken-json",
        headers={"content-type": "application/json"},
    )

    assert malformed.status_code == 422
    current = client.get(f"/api/admin/questionnaires/{questionnaire_id}/operations").json()
    assert current["completion"]["lead_channel_id"] == 701


def test_operations_missing_resources_return_404_and_next_owner_headers(client: TestClient) -> None:
    missing_questionnaire = client.get("/api/admin/questionnaires/987654/operations")
    assert missing_questionnaire.status_code == 404
    assert missing_questionnaire.json()["fallback_used"] is False
    assert missing_questionnaire.headers.get("x-aicrm-route-owner") == "ai_crm_next"
    assert missing_questionnaire.headers.get("x-aicrm-fallback-used") == "false"

    questionnaire_id = _create_questionnaire(client)
    missing_channel = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 987654},
    )
    assert missing_channel.status_code == 404


def test_unavailable_push_capability_returns_503_without_blocking_dimension_saves(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questionnaire_id = _create_questionnaire(client)

    class UnavailableCapabilityReader:
        def get_capability_status(self, _key):
            raise RuntimeError("push capability source unavailable")

    monkeypatch.setattr(
        "aicrm_next.extensions.forms.questionnaire.operations.build_push_capability_reader",
        lambda: UnavailableCapabilityReader(),
    )

    unavailable = client.get(f"/api/admin/questionnaires/{questionnaire_id}/operations")
    assert unavailable.status_code == 503
    assert unavailable.json()["source_status"] == "production_unavailable"

    completion = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 701},
    )
    external = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push",
        json={"enabled": True, "webhook_url": "https://hooks.example.com/source-independent"},
    )
    assert completion.status_code == 200, completion.text
    assert external.status_code == 200, external.text


def test_completion_operations_reject_unusable_channels_and_native_miniprogram(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)

    no_qr = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 702},
    )
    assert no_qr.status_code == 422

    native_miniprogram = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={
            "enabled": True,
            "action_type": "redirect",
            "completion_target": {
                "enabled": True,
                "target_type": "mini_program",
                "mini_program": {"username": "gh_xxx", "path": "/pages/index"},
            },
        },
    )
    assert native_miniprogram.status_code == 422


def test_legacy_native_miniprogram_is_readonly_until_explicit_conversion(client: TestClient) -> None:
    payload = _content_payload()
    payload["slug"] = "legacy-native-miniprogram"
    payload["completion_target"] = {
        "enabled": True,
        "target_type": "mini_program",
        "open_strategy": "wechat_open_tag",
        "mini_program": {"username": "gh_legacy", "path": "/pages/result"},
    }
    created = client.post("/api/admin/questionnaires", json=payload)
    assert created.status_code == 200, created.text
    questionnaire_id = int(created.json()["questionnaire_id"])

    legacy = client.get(f"/api/admin/questionnaires/{questionnaire_id}/operations")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["completion"]["legacy_target_readonly"] is True
    assert legacy.json()["completion"]["completion_target"]["target_type"] == "mini_program"

    converted = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={
            "enabled": True,
            "action_type": "redirect",
            "completion_target": {
                "enabled": True,
                "target_type": "h5",
                "h5_url": "https://example.com/converted",
            },
        },
    )
    assert converted.status_code == 200, converted.text
    assert converted.json()["completion"]["legacy_target_readonly"] is False


def test_content_update_preserves_operations_and_duplicate_resets_them(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)
    assert client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 701},
    ).status_code == 200
    assert client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push",
        json={
            "enabled": True,
            "webhook_url": "https://hooks.example.com/questionnaire",
            "type": "subscription",
            "day": 14,
            "frequency": 2,
            "remark": "运营配置",
            "custom_params": [{"name": "source", "value": "operations"}],
        },
    ).status_code == 200

    update = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}",
        json=_content_payload("内容更新后仍保留运营配置"),
    )
    assert update.status_code == 200, update.text
    operations = client.get(f"/api/admin/questionnaires/{questionnaire_id}/operations").json()
    assert operations["completion"]["lead_channel_id"] == 701
    assert operations["external_push"]["enabled"] is True
    assert operations["external_push"]["day"] == 14

    duplicate = client.post(f"/api/admin/questionnaires/{questionnaire_id}/duplicate", json={})
    assert duplicate.status_code == 200, duplicate.text
    duplicate_id = duplicate.json()["questionnaire_id"]
    copied = client.get(f"/api/admin/questionnaires/{duplicate_id}/operations").json()
    assert copied["questionnaire"]["is_disabled"] is True
    assert copied["completion"]["enabled"] is False
    assert copied["completion"]["lead_channel_id"] is None
    assert copied["external_push"]["enabled"] is False
    assert copied["external_push"]["webhook_url"] == ""


def test_external_push_test_only_queues_synthetic_effect(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    questionnaire_id = _create_questionnaire(client)
    saved = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push",
        json={"enabled": True, "webhook_url": "https://hooks.example.com/questionnaire-test"},
    )
    assert saved.status_code == 200, saved.text

    class EnabledCapabilityReader:
        def get_capability_status(self, _key):
            return {
                "key": "questionnaire_external_push",
                "enabled": True,
                "configured_enabled": True,
                "readonly_reason": "",
            }

    monkeypatch.setattr(
        "aicrm_next.extensions.forms.questionnaire.operations.build_push_capability_reader",
        lambda: EnabledCapabilityReader(),
    )
    queued = client.post(f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push/test")
    assert queued.status_code == 200, queued.text
    assert queued.json()["real_external_call_executed"] is False
    assert queued.json()["test_run_id"]

    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH})
    assert total == 1
    assert jobs[0].status == "queued"
    assert jobs[0].payload_json["body"]["user_id"] == "questionnaire_test"
    assert jobs[0].payload_json["body"]["phone_number"] == "NULL"
    assert jobs[0].payload_json["body"]["answers"] == []


def test_external_push_test_rejects_disabled_capability_without_queueing(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)
    assert client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push",
        json={"enabled": True, "webhook_url": "https://hooks.example.com/questionnaire-test"},
    ).status_code == 200

    response = client.post(f"/api/admin/questionnaires/{questionnaire_id}/operations/external-push/test")

    assert response.status_code == 409
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH})
    assert jobs == []
    assert total == 0


def test_lead_qr_is_exposed_only_after_submission_and_degrades_when_channel_is_invalid(client: TestClient) -> None:
    questionnaire_id = _create_questionnaire(client)
    configured = client.put(
        f"/api/admin/questionnaires/{questionnaire_id}/operations/completion",
        json={"enabled": True, "action_type": "lead_qr", "lead_channel_id": 701},
    )
    assert configured.status_code == 200

    direct_success_page = client.get("/s/questionnaire-operations-test/submitted")
    assert direct_success_page.status_code == 200
    assert "questionnaire-delivery.png" not in direct_success_page.text
    assert "问卷交付渠道" not in direct_success_page.text

    submit = client.post(
        "/api/h5/questionnaires/questionnaire-operations-test/submit",
        json={"answers": {"q1": "yes"}, "identity": {"respondent_key": "lead-qr-user"}},
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["completion_action"]["type"] == "lead_qr"
    assert submit.json()["lead_qr"]["channel_id"] == 701

    recognized_success_page = client.get(
        "/s/questionnaire-operations-test/submitted",
        params={"respondent_key": "lead-qr-user"},
    )
    assert recognized_success_page.status_code == 200
    assert "questionnaire-delivery.png" in recognized_success_page.text
    assert "问卷交付渠道" in recognized_success_page.text

    channels_api._FIXTURE_CHANNELS[701]["status"] = "disabled"
    repeat = client.get(
        "/api/h5/questionnaires/questionnaire-operations-test",
        params={"respondent_key": "lead-qr-user"},
    )
    assert repeat.status_code == 409
    assert repeat.json()["completion_action"]["type"] == "default"
    assert repeat.json().get("lead_qr") is None
