from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aicrm_next.platform.admin_config.repository import AdminConfigRepository
from aicrm_next.platform.admin_config.application import AdminConfigReadService
from aicrm_next.platform.admin_config.application_support import _validate_known_setting
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    FEISHU_WEBHOOK_NOTIFY,
    MEDIA_STORAGE_UPLOAD,
    OPENCLAW_CONTEXT_PUSH,
    PAYMENT_WECHAT_ORDER_QUERY,
    WECOM_CONTACT_TAG_MARK,
    WECOM_MESSAGE_PRIVATE_SEND,
    WECOM_MEDIA_UPLOAD,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    ExternalEffectDispatchResult,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform.platform_foundation.external_effects.jobs import SCHEDULER_BATCH_SIZE_KEY, SCHEDULER_ENABLED_KEY, SCHEDULER_INTERVAL_SECONDS_KEY
from aicrm_next.platform.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform.platform_foundation.external_effects.adapters import WECOM_EFFECT_TYPES
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.platform_foundation.push_center.capability_registry import PUSH_CAPABILITIES
from aicrm_next.platform.platform_foundation.push_center.section_mapper import all_sections, effect_types_for_section, label_for_section
from aicrm_next.platform.shared.wecom_runtime import WECOM_ENABLED_EFFECT_TYPES_KEY, WECOM_EXECUTION_MODE_KEY
from tests.admin_auth_test_helpers import install_admin_action_tokens


class _SucceedingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, job):
        self.calls += 1
        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary={"effect_type": job.effect_type},
            response_summary={"ok": True, "status_code": 200, "real_external_call_executed": True},
            real_external_call_executed=True,
            provider_result_received=True,
        )


_SETTINGS: dict[str, str] = {}
_AUDIT_LOGS: list[dict[str, Any]] = []
_ENV_SETTING_KEYS: set[str] = set()


@pytest.fixture(autouse=True)
def _patch_admin_config_repository(monkeypatch: pytest.MonkeyPatch):
    for key in list(_ENV_SETTING_KEYS):
        monkeypatch.delenv(key, raising=False)
    _ENV_SETTING_KEYS.clear()
    _SETTINGS.clear()
    _AUDIT_LOGS.clear()

    def list_app_settings(self) -> list[dict[str, Any]]:
        return [{"key": key, "value": value, "updated_at": ""} for key, value in sorted(_SETTINGS.items())]

    def get_app_setting(self, key: str) -> dict[str, Any] | None:
        normalized = str(key or "").strip()
        if normalized not in _SETTINGS:
            return None
        return {"key": normalized, "value": _SETTINGS[normalized], "updated_at": ""}

    def upsert_app_setting(self, *, key: str, value: str) -> dict[str, Any]:
        normalized = str(key or "").strip()
        _SETTINGS[normalized] = str(value)
        os.environ[normalized] = str(value)
        _ENV_SETTING_KEYS.add(normalized)
        return {"key": normalized, "value": str(value), "updated_at": ""}

    def insert_audit_log(
        self,
        *,
        operator: str,
        action_type: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        _AUDIT_LOGS.append(
            {
                "operator": operator,
                "action_type": action_type,
                "target_type": target_type,
                "target_id": target_id,
                "before": before or {},
                "after": after or {},
            }
        )

    monkeypatch.setattr(AdminConfigRepository, "list_app_settings", list_app_settings)
    monkeypatch.setattr(AdminConfigRepository, "get_app_setting", get_app_setting)
    monkeypatch.setattr(AdminConfigRepository, "upsert_app_setting", upsert_app_setting)
    monkeypatch.setattr(AdminConfigRepository, "insert_audit_log", insert_audit_log)
    yield
    for key in list(_ENV_SETTING_KEYS):
        os.environ.pop(key, None)
    _ENV_SETTING_KEYS.clear()


def _set_setting(key: str, value: str) -> None:
    AdminConfigRepository().upsert_app_setting(key=key, value=value)
    os.environ[str(key or "").strip()] = str(value)
    _ENV_SETTING_KEYS.add(str(key or "").strip())


def _context(trace_id: str) -> CommandContext:
    return CommandContext(actor_id="pytest", actor_type="system", request_id=trace_id, trace_id=trace_id, source_route="/pytest/push-capabilities")


def _plan_job(
    *,
    effect_type: str,
    business_type: str,
    business_id: str,
    adapter_name: str = "outbound_webhook",
    target_type: str = "questionnaire_submission",
    target_id: str = "sub-1",
    idempotency_key: str,
) -> dict:
    return ExternalEffectService().plan_effect(
        effect_type=effect_type,
        adapter_name=adapter_name,
        operation="send" if effect_type == WECOM_MESSAGE_PRIVATE_SEND else "post",
        target_type=target_type,
        target_id=target_id,
        business_type=business_type,
        business_id=business_id,
        payload={"owner_userid": "HuangYouCan", "external_userids": [target_id], "channel": "wecom_private", "content_text": "hello"},
        context=_context(f"trace-{idempotency_key}"),
        source_module="pytest.push_capabilities",
        source_event_id=business_id,
        idempotency_key=idempotency_key,
        status="queued",
        execution_mode="execute",
    )


def _registry(adapter: _SucceedingAdapter) -> ExternalEffectAdapterRegistry:
    registry = ExternalEffectAdapterRegistry()
    registry._adapters["outbound_webhook"] = adapter  # type: ignore[attr-defined]
    registry._adapters["wecom_private_message"] = adapter  # type: ignore[attr-defined]
    registry._adapters["wecom_tag"] = adapter  # type: ignore[attr-defined]
    return registry


def test_push_capabilities_get_hides_raw_engineering_settings_and_sensitive_values(next_client: TestClient) -> None:
    _set_setting("OPENCLAW_FOCUS_MESSAGE_WEBHOOK_TOKEN", "super-secret-openclaw")
    _set_setting("QUESTIONNAIRE_SUBMIT_WEBHOOK_TOKEN", "super-secret-questionnaire")
    _set_setting("AICRM_EXTERNAL_EFFECT_WEBHOOK_SIGNING_SECRET", "super-secret-signing")

    response = next_client.get("/api/admin/config/push-capabilities")
    body = response.json()
    text = response.text

    assert response.status_code == 200
    assert body["ok"] is True
    keys = {item["key"] for item in body["capabilities"]}
    assert {
        "questionnaire_external_push",
        "order_paid_push",
        "ai_assist_push",
        "private_broadcast",
        "group_ops_push",
        "group_broadcast",
        "customer_webhook",
        "tags",
        "welcome_message",
        "payment_query",
        "integrations",
        "test_receiver",
    } <= keys
    assert body["summary"]["total"] == len(PUSH_CAPABILITIES)
    assert all("push_center_href" not in item for item in body["capabilities"])
    assert all("queue_counts" not in item for item in body["capabilities"])
    assert "abnormal_count" not in body["summary"]
    assert "super-secret" not in text
    assert "Authorization" not in text
    assert "access_token" not in text
    assert "OPENCLAW_FOCUS_MESSAGE_WEBHOOK_TIMEOUT_SECONDS" not in text
    assert "OUTBOUND_WEBHOOK_RETRY_MAX_ATTEMPTS" not in text
    assert "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES" not in text


def test_push_capabilities_read_is_configuration_only() -> None:
    payload = AdminConfigReadService().get_push_capabilities()

    assert payload["ok"] is True
    assert "abnormal_count" not in payload["summary"]
    assert all("queue_counts" not in item for item in payload["capabilities"])
    assert all("last_error_code" not in item for item in payload["capabilities"])


def test_push_capabilities_derive_missing_wecom_toggles_from_typed_runtime() -> None:
    _set_setting("WECOM_CORP_ID", "corp-test")
    _set_setting("WECOM_CONTACT_SECRET", "secret-test")
    _set_setting(WECOM_EXECUTION_MODE_KEY, "execute")
    _set_setting(
        WECOM_ENABLED_EFFECT_TYPES_KEY,
        ",".join(WECOM_EFFECT_TYPES),
    )

    payload = AdminConfigReadService().get_push_capabilities()
    by_key = {item["key"]: item for item in payload["capabilities"]}

    assert by_key["tags"]["enabled"] is True
    assert by_key["welcome_message"]["enabled"] is True


def test_push_capability_non_wecom_toggle_preserves_typed_wecom_runtime(next_client: TestClient) -> None:
    _set_setting("WECOM_CORP_ID", "corp-test")
    _set_setting("WECOM_CONTACT_SECRET", "secret-test")
    _set_setting(WECOM_EXECUTION_MODE_KEY, "execute")
    _set_setting(
        WECOM_ENABLED_EFFECT_TYPES_KEY,
        ",".join(WECOM_EFFECT_TYPES),
    )
    token = install_admin_action_tokens(
        next_client,
        ("PATCH", "/api/admin/config/push-capabilities/{capability_key}"),
    )[("PATCH", "/api/admin/config/push-capabilities/{capability_key}")]

    response = next_client.patch(
        "/api/admin/config/push-capabilities/questionnaire_external_push",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert AdminConfigRepository().get_app_setting(WECOM_EXECUTION_MODE_KEY)["value"] == "execute"
    persisted_types = set(AdminConfigRepository().get_app_setting(WECOM_ENABLED_EFFECT_TYPES_KEY)["value"].split(","))
    assert set(WECOM_EFFECT_TYPES) <= persisted_types


def test_push_capability_wecom_toggle_removes_only_explicitly_disabled_effects(next_client: TestClient) -> None:
    _set_setting("WECOM_CORP_ID", "corp-test")
    _set_setting("WECOM_CONTACT_SECRET", "secret-test")
    _set_setting(WECOM_EXECUTION_MODE_KEY, "execute")
    _set_setting(WECOM_ENABLED_EFFECT_TYPES_KEY, ",".join(WECOM_EFFECT_TYPES))
    token = install_admin_action_tokens(
        next_client,
        ("PATCH", "/api/admin/config/push-capabilities/{capability_key}"),
    )[("PATCH", "/api/admin/config/push-capabilities/{capability_key}")]

    response = next_client.patch(
        "/api/admin/config/push-capabilities/tags",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": False},
    )

    assert response.status_code == 200
    persisted_types = set(AdminConfigRepository().get_app_setting(WECOM_ENABLED_EFFECT_TYPES_KEY)["value"].split(","))
    assert {"wecom.contact.tag.mark", "wecom.contact.tag.unmark", "wecom.profile.update"}.isdisjoint(persisted_types)
    assert {"wecom.welcome_message.send", "wecom.message.private.send", "wecom.message.group.send", "wecom.media.upload"} <= persisted_types


def test_wecom_effect_type_validator_accepts_media_upload() -> None:
    value = _validate_known_setting(
        WECOM_ENABLED_EFFECT_TYPES_KEY,
        f"{WECOM_CONTACT_TAG_MARK},wecom.media.upload",
    )

    assert value == f"{WECOM_CONTACT_TAG_MARK},wecom.media.upload"


def test_push_capability_toggle_updates_business_setting_and_derived_gates(next_client: TestClient) -> None:
    _set_setting("WECOM_CORP_ID", "corp-test")
    _set_setting("WECOM_CONTACT_SECRET", "secret-test")
    token = install_admin_action_tokens(
        next_client,
        ("PATCH", "/api/admin/config/push-capabilities/{capability_key}"),
    )[("PATCH", "/api/admin/config/push-capabilities/{capability_key}")]
    disabled = next_client.patch(
        "/api/admin/config/push-capabilities/questionnaire_external_push",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["capability"]["enabled"] is False
    assert WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH not in disabled.json()["derived_gates"]["allowed_effect_types"]
    assert AdminConfigRepository().get_app_setting("AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED")["value"] == "false"
    assert WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH not in (AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")["value"] or "")

    enabled = next_client.patch(
        "/api/admin/config/push-capabilities/questionnaire_external_push",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["capability"]["enabled"] is True
    assert WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH in enabled.json()["derived_gates"]["allowed_effect_types"]

    group_broadcast = next_client.patch(
        "/api/admin/config/push-capabilities/group_broadcast",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    rejected = next_client.patch("/api/admin/config/push-capabilities/order_paid_push", json={"enabled": True})

    assert group_broadcast.status_code == 200
    assert group_broadcast.json()["capability"]["enabled"] is True
    assert rejected.status_code == 401


def test_push_capability_toggle_derives_all_external_effect_execution_gates(next_client: TestClient) -> None:
    token = install_admin_action_tokens(
        next_client,
        ("PATCH", "/api/admin/config/push-capabilities/{capability_key}"),
    )[("PATCH", "/api/admin/config/push-capabilities/{capability_key}")]

    payment = next_client.patch(
        "/api/admin/config/push-capabilities/payment_query",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    assert payment.status_code == 200
    assert payment.json()["derived_gates"]["payment_execute"] is True
    assert PAYMENT_WECHAT_ORDER_QUERY in payment.json()["derived_gates"]["allowed_effect_types"]
    assert AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE")["value"] == "true"

    integrations = next_client.patch(
        "/api/admin/config/push-capabilities/integrations",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    assert integrations.status_code == 200
    derived = integrations.json()["derived_gates"]
    assert derived["feishu_execute"] is True
    assert derived["openclaw_execute"] is True
    assert derived["media_upload_execute"] is True
    assert {FEISHU_WEBHOOK_NOTIFY, OPENCLAW_CONTEXT_PUSH, MEDIA_STORAGE_UPLOAD, WECOM_MEDIA_UPLOAD} <= set(derived["allowed_effect_types"])
    assert AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_FEISHU_EXECUTE")["value"] == "true"
    assert AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_OPENCLAW_EXECUTE")["value"] == "true"
    assert AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_MEDIA_UPLOAD_EXECUTE")["value"] == "true"

    receiver = next_client.patch(
        "/api/admin/config/push-capabilities/test_receiver",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    assert receiver.status_code == 200
    assert receiver.json()["derived_gates"]["test_receiver_enabled"] is True
    assert AdminConfigRepository().get_app_setting("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED")["value"] == "true"

    disable_integrations = next_client.patch(
        "/api/admin/config/push-capabilities/integrations",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": False},
    )
    assert disable_integrations.status_code == 200
    disabled = disable_integrations.json()["derived_gates"]
    assert disabled["feishu_execute"] is False
    assert disabled["openclaw_execute"] is False
    assert disabled["media_upload_execute"] is False


def test_push_capability_scheduler_toggle_is_global_not_per_capability(next_client: TestClient) -> None:
    token = install_admin_action_tokens(
        next_client,
        ("PATCH", "/api/admin/config/push-capabilities/scheduler"),
    )[("PATCH", "/api/admin/config/push-capabilities/scheduler")]
    AdminConfigRepository().upsert_app_setting(key=SCHEDULER_ENABLED_KEY, value="false")
    initial = next_client.get("/api/admin/config/push-capabilities")
    assert initial.status_code == 200
    assert initial.json()["scheduler"]["enabled"] is False
    assert initial.json()["scheduler"]["interval_seconds"] == 60

    enabled = next_client.patch(
        "/api/admin/config/push-capabilities/scheduler",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    body = enabled.json()
    assert body["scheduler"]["enabled"] is True
    assert body["scheduler"]["interval_seconds"] == 60
    assert body["scheduler"]["batch_size"] == 20
    assert AdminConfigRepository().get_app_setting(SCHEDULER_ENABLED_KEY)["value"] == "true"
    assert AdminConfigRepository().get_app_setting(SCHEDULER_INTERVAL_SECONDS_KEY)["value"] == "60"
    assert AdminConfigRepository().get_app_setting(SCHEDULER_BATCH_SIZE_KEY)["value"] == "20"

    disabled = next_client.patch(
        "/api/admin/config/push-capabilities/scheduler",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["scheduler"]["enabled"] is False


def test_external_effect_worker_blocks_disabled_capability_before_adapter_and_allows_enabled() -> None:
    reset_external_effect_fixture_state()
    _set_setting("AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED", "false")
    job = _plan_job(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        business_type="questionnaire",
        business_id="q-disabled",
        idempotency_key="capability-disabled-questionnaire",
    )
    adapter = _SucceedingAdapter()

    blocked = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH],
    )

    assert blocked["counts"]["blocked_count"] == 1
    assert blocked["items"][0]["attempt"]["error_code"] == "push_capability_disabled"
    assert blocked["real_external_call_executed"] is False
    assert adapter.calls == 0

    _set_setting("AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED", "true")
    _plan_job(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        business_type="questionnaire",
        business_id="q-enabled",
        idempotency_key="capability-enabled-questionnaire",
    )
    executed = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH],
    )

    assert executed["counts"]["succeeded_count"] == 1
    assert adapter.calls == 1
    assert job["id"] != executed["items"][0]["job"]["id"]


def test_shared_wecom_effect_type_is_gated_by_business_section() -> None:
    reset_external_effect_fixture_state()
    _set_setting("AICRM_WECOM_EXECUTION_MODE", "execute")
    _set_setting("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_MESSAGE_PRIVATE_SEND)
    _set_setting("AICRM_PUSH_CAPABILITY_AI_ASSIST_PUSH_ENABLED", "true")
    _set_setting("AICRM_PUSH_CAPABILITY_PRIVATE_BROADCAST_ENABLED", "false")
    adapter = _SucceedingAdapter()

    _plan_job(
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        business_type="ai_assist_campaign",
        business_id="camp-1",
        target_type="external_user",
        target_id="wm-ai",
        idempotency_key="wecom-ai-assist-enabled",
    )
    _plan_job(
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        business_type="private_broadcast",
        business_id="broadcast-1",
        target_type="external_user",
        target_id="wm-private",
        idempotency_key="wecom-private-disabled",
    )

    ai_result = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_MESSAGE_PRIVATE_SEND],
    )
    private_result = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_MESSAGE_PRIVATE_SEND],
    )

    assert ai_result["items"][0]["job"]["business_type"] == "ai_assist_campaign"
    assert ai_result["counts"]["succeeded_count"] == 1
    assert private_result["items"][0]["job"]["business_type"] == "private_broadcast"
    assert private_result["items"][0]["attempt"]["error_code"] == "push_capability_disabled"
    assert private_result["counts"]["blocked_count"] == 1
    assert adapter.calls == 1


def test_wecom_tag_effect_honors_tags_capability_unless_explicitly_bypassed() -> None:
    reset_external_effect_fixture_state()
    _set_setting("AICRM_WECOM_EXECUTION_MODE", "execute")
    _set_setting("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_CONTACT_TAG_MARK)
    _set_setting("AICRM_PUSH_CAPABILITY_TAGS_ENABLED", "false")
    adapter = _SucceedingAdapter()

    _plan_job(
        effect_type=WECOM_CONTACT_TAG_MARK,
        adapter_name="wecom_tag",
        business_type="wecom_tag",
        business_id="tag-disabled",
        target_type="external_user",
        target_id="wx-tag-disabled",
        idempotency_key="wecom-tag-disabled-capability",
    )
    blocked = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_CONTACT_TAG_MARK],
    )

    assert blocked["items"][0]["attempt"]["error_code"] == "push_capability_disabled"
    assert blocked["counts"]["blocked_count"] == 1
    assert adapter.calls == 0

    ExternalEffectService().plan_effect(
        effect_type=WECOM_CONTACT_TAG_MARK,
        adapter_name="wecom_tag",
        operation="tag_mark",
        target_type="external_user",
        target_id="wx-tag-bypass",
        business_type="wecom_tag",
        business_id="tag-bypass",
        payload={
            "external_userid": "wx-tag-bypass",
            "tag_ids": ["tag_a"],
            "follow_user_userid": "owner-a",
            "bypass_push_capability": True,
        },
        context=_context("trace-wecom-tag-bypass"),
        source_module="pytest.push_capabilities",
        idempotency_key="wecom-tag-bypass-capability",
        status="queued",
        execution_mode="execute",
    )
    bypassed = ExternalEffectWorker(adapter_registry=_registry(adapter)).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_CONTACT_TAG_MARK],
    )

    assert bypassed["counts"]["succeeded_count"] == 1
    assert adapter.calls == 1


def test_webhooks_push_page_is_push_capability_entry(next_client: TestClient) -> None:
    response = next_client.get("/admin/config/detail/webhooks_push")

    assert response.status_code == 200
    assert "推送能力配置" in response.text
    assert "统一队列自动调度" in response.text
    assert "已开启能力" in response.text
    assert "异常任务" not in response.text
    assert "业务推送能力" in response.text
    assert "capabilityTbody" in response.text
    assert "advancedPanel" in response.text
    assert "暂无推送能力数据" in response.text
    assert "缺少操作令牌" in response.text
    assert "data-action=\"toggle\"" in response.text
    assert "readonly_reason" in response.text
    assert "push_center_href" not in response.text
    assert "timeout" not in response.text.lower()
    assert "retry" not in response.text.lower()
    assert "allowed_types" not in response.text
    assert "raw token" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "Authorization" not in response.text
    assert "access_token" not in response.text
    assert "/api/admin/config/push-capabilities" in response.text
    assert "/api/admin/push-center/stats" not in response.text
    assert "/api/admin/push-center/legacy-deprecations" not in response.text
    assert "/api/admin/push-center?section=questionnaire" not in response.text


def test_push_center_sections_match_capability_registry_metadata() -> None:
    sections = {item["key"]: item for item in all_sections()}
    for capability in PUSH_CAPABILITIES:
        section = sections[capability.section]
        assert section["label"] == capability.section_label
        assert label_for_section(capability.section) == capability.section_label
        assert set(effect_types_for_section(capability.section)) == set(capability.effect_types)
