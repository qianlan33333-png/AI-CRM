from __future__ import annotations

import json

from aicrm_next.channels.integration_gateway.wecom_group_adapter import WeComGroupMessageAdapter
from aicrm_next.channels.integration_gateway.wecom_channel_entry_client import WeComApiError
from aicrm_next.platform.platform_foundation.external_effects import adapters as effect_adapters
from aicrm_next.platform.platform_foundation.external_effects.models import (
    ExternalEffectJob,
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    WECOM_MESSAGE_GROUP_SEND,
)
from aicrm_next.platform.shared.wecom_runtime import (
    classify_wecom_provider_error,
    classify_wecom_provider_outcome,
)


def _adapter_payload() -> dict:
    return {
        "sender": "owner_canary",
        "chat_ids": ["chat_canary"],
        "text": {"content": "safe canary"},
    }


def test_group_provider_error_keeps_safe_errcode_and_classification(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE", "true")

    class _Client:
        def create_group_message_task(self, payload):
            assert payload["chat_id_list"] == ["chat_canary"]
            return {"errcode": 48002, "errmsg": "api forbidden"}

    result = WeComGroupMessageAdapter(
        mode="production",
        client_factory=lambda: _Client(),
    ).create_group_message_task(_adapter_payload(), idempotency_key="canary-group-error")

    assert result["ok"] is False
    assert result["error_code"] == "permission_denied"
    assert result["provider_errcode"] == 48002
    assert result["provider_error_classification"] == "terminal"
    assert result["retryable"] is False


def test_provider_error_60020_is_terminal_ip_trust_configuration() -> None:
    assert classify_wecom_provider_error(provider_errcode=60020) == ("ip_not_trusted", "terminal")


def test_external_contact_relationship_absence_is_visible_non_retryable_business_outcome() -> None:
    assert classify_wecom_provider_error(provider_errcode=84061) == (
        "external_contact_relationship_absent",
        "terminal",
    )

    class _Client:
        def get_external_contact_detail(self, external_userid):
            raise WeComApiError(
                "not external contact",
                payload={"errcode": 84061, "errmsg": "not external contact"},
            )

    job = ExternalEffectJob(
        id=84061,
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        adapter_name="wecom_external_contact_detail",
        operation="get_external_contact_detail",
        target_type="external_user",
        target_id="wm_relationship_absent",
        execution_mode="execute",
        payload_json={"external_userid": "wm_relationship_absent", "queue_id": 7},
    )
    result = effect_adapters.WeComExternalContactDetailAdapter(
        adapter_factory=lambda: _Client(),
    ).dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "external_contact_relationship_absent"
    assert result.real_external_call_executed is True
    assert result.provider_result_received is True
    assert result.response_summary["errcode"] == 84061
    assert result.response_summary["provider_result_received"] is True


def test_miniprogram_title_limit_is_business_rejection_not_system_failure(
    monkeypatch,
) -> None:
    provider_message = "attachments.miniprogram.title exceed max length 64"
    assert classify_wecom_provider_outcome(
        provider_errcode=40058,
        errmsg=provider_message,
    ) == ("business_rejection", "miniprogram_title_exceeds_64_bytes")
    assert classify_wecom_provider_outcome(
        provider_errcode=40058,
        errmsg="invalid request parameter",
    ) == ("system_failure", "")
    assert classify_wecom_provider_outcome(
        provider_errcode=40014,
        errmsg="invalid access token",
    ) == ("system_failure", "")

    monkeypatch.setenv("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE", "true")

    class _Client:
        def create_group_message_task(self, payload):
            assert payload["chat_id_list"] == ["chat_canary"]
            return {"errcode": 40058, "errmsg": provider_message}

    result = WeComGroupMessageAdapter(
        mode="production",
        client_factory=lambda: _Client(),
    ).create_group_message_task(
        _adapter_payload(),
        idempotency_key="group-title-business-rejection",
    )

    assert result["ok"] is False
    assert result["error_code"] == "wecom_error_40058"
    assert result["provider_error_classification"] == "terminal"
    assert result["provider_outcome_classification"] == "business_rejection"
    assert result["business_reason_code"] == "miniprogram_title_exceeds_64_bytes"
    assert result["retryable"] is False


def test_external_effect_summary_preserves_business_rejection_without_raw_errmsg(
    monkeypatch,
) -> None:
    provider_message = "attachments.miniprogram.title exceed max length 64"

    class _Client:
        def create_group_message_task(self, payload):
            return {"errcode": 40058, "errmsg": provider_message}

    monkeypatch.setenv("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE", "true")
    monkeypatch.setattr(effect_adapters, "wecom_canary_job_gate_error", lambda job: "")
    job = ExternalEffectJob(
        id=40058,
        effect_type=WECOM_MESSAGE_GROUP_SEND,
        adapter_name="wecom_group_message",
        operation="send_group_message",
        target_type="group_chat",
        target_id="chat_canary",
        idempotency_key="group-title-business-rejection-summary",
        execution_mode="execute",
        payload_json={
            "owner_userid": "owner_canary",
            "chat_ids": ["chat_canary"],
            "content_payload": {
                "attachments": [
                    {
                        "msgtype": "miniprogram",
                        "miniprogram": {
                            "appid": "wx_test",
                            "page": "pages/test",
                            "title": "超" * 22,
                            "pic_media_id": "media_test",
                        },
                    }
                ]
            },
        },
    )

    result = effect_adapters.WeComGroupMessageExternalEffectAdapter(
        adapter_factory=lambda: WeComGroupMessageAdapter(
            mode="production",
            client_factory=lambda: _Client(),
        ),
    ).dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "wecom_error_40058"
    assert result.response_summary["provider_outcome_classification"] == ("business_rejection")
    assert result.response_summary["business_reason_code"] == ("miniprogram_title_exceeds_64_bytes")
    assert provider_message not in json.dumps(result.response_summary)


def test_group_provider_malformed_diagnostics_do_not_raise_after_boundary(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE", "true")

    class _Client:
        def create_group_message_task(self, payload):
            return {"errcode": "not-a-number", "errmsg": "malformed", "fail_list": "not-a-list"}

    result = WeComGroupMessageAdapter(
        mode="production",
        client_factory=lambda: _Client(),
    ).create_group_message_task(_adapter_payload(), idempotency_key="canary-group-malformed")

    assert result["ok"] is False
    assert result["error_code"] == "wecom_group_exact_target_not_verified"
    assert result["side_effect_executed"] is True


def test_external_effect_group_summary_redacts_errmsg_and_preserves_retryability(monkeypatch) -> None:
    class _Adapter:
        def create_group_message_task(self, payload, *, idempotency_key=""):
            return {
                "ok": False,
                "adapter": "WeComGroupMessageAdapter",
                "mode": "production",
                "operation": "create_group_message_task",
                "audit_id": "audit_canary",
                "side_effect_executed": True,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": ["chat_canary"],
                "requested_chat_count": 1,
                "result": {"errcode": 45009, "errmsg": "raw provider detail"},
                "provider_errcode": 45009,
                "provider_error_classification": "retryable",
                "retryable": True,
                "error_code": "rate_limited",
                "error_message": "raw provider detail",
            }

    monkeypatch.setattr(effect_adapters, "wecom_canary_job_gate_error", lambda job: "")
    job = ExternalEffectJob(
        id=91,
        effect_type=WECOM_MESSAGE_GROUP_SEND,
        adapter_name="wecom_group_message",
        operation="send_group_message",
        target_type="group_chat",
        target_id="chat_canary",
        idempotency_key="canary-group-rate-limit",
        execution_mode="execute",
        payload_json={
            "owner_userid": "owner_canary",
            "chat_ids": ["chat_canary"],
            "content_payload": {"text": {"content": "safe canary"}, "attachments": []},
        },
    )

    result = effect_adapters.WeComGroupMessageExternalEffectAdapter(
        adapter_factory=lambda: _Adapter(),
    ).dispatch(job)

    assert result.status == "failed_retryable"
    assert result.error_code == "rate_limited"
    assert result.response_summary["errcode"] == 45009
    assert result.response_summary["errmsg_present"] is True
    assert result.response_summary["provider_error_classification"] == "retryable"
    assert "raw provider detail" not in json.dumps(result.response_summary)


def test_external_effect_group_summary_tolerates_malformed_provider_diagnostics(monkeypatch) -> None:
    class _Adapter:
        def create_group_message_task(self, payload, *, idempotency_key=""):
            return {
                "ok": False,
                "side_effect_executed": True,
                "exact_target_required": True,
                "requested_chat_ids": "not-a-list",
                "requested_chat_count": "not-a-number",
                "result": {"errcode": "not-a-number", "fail_list": "not-a-list"},
                "failed_chat_count": "not-a-number",
                "error_code": "provider_response_invalid",
            }

    monkeypatch.setattr(effect_adapters, "wecom_canary_job_gate_error", lambda job: "")
    job = ExternalEffectJob(
        id=92,
        effect_type=WECOM_MESSAGE_GROUP_SEND,
        adapter_name="wecom_group_message",
        operation="send_group_message",
        target_type="group_chat",
        target_id="chat_canary",
        idempotency_key="canary-group-malformed-summary",
        execution_mode="execute",
        payload_json={
            "owner_userid": "owner_canary",
            "chat_ids": ["chat_canary"],
            "content_payload": {"text": {"content": "safe canary"}, "attachments": []},
        },
    )

    result = effect_adapters.WeComGroupMessageExternalEffectAdapter(
        adapter_factory=lambda: _Adapter(),
    ).dispatch(job)

    assert result.status == "failed_terminal"
    assert result.response_summary["errcode"] == 0
    assert result.response_summary["requested_chat_count"] == 0
    assert result.response_summary["failed_chat_count"] == 0
