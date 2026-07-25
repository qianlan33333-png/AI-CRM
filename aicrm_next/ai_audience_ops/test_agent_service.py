from __future__ import annotations

from typing import Any

from aicrm_next.shared.runtime_settings import runtime_bool, runtime_csv, runtime_setting

from .repository import AudienceRepository, build_audience_repository, _json_dumps, _text
from .webhook_service import AudienceInboundWebhookService


TEST_AGENT_MESSAGE_TEXT = "【AI人群包生产真实测试】你好，这是一条由 AI 人群包自测 Agent 生成的测试消息。"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS",
        "AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS",
    }
)


class AudienceTestAgentService:
    def __init__(
        self,
        repository: AudienceRepository | None = None,
        inbound_service: AudienceInboundWebhookService | None = None,
    ):
        self._repo = repository or build_audience_repository()
        self._inbound = inbound_service or AudienceInboundWebhookService(repository=self._repo)

    def handle(self, payload: Any, *, headers: dict[str, Any] | None = None) -> dict[str, Any]:
        if not runtime_bool("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED"):
            return {"ok": False, "error": "test_agent_disabled", "status_code": 404, "real_external_call_executed": False}
        if isinstance(payload, list):
            return self._handle_run_payload(payload, headers=headers or {})
        if not isinstance(payload, dict):
            return {"ok": False, "error": "invalid_test_agent_payload", "status_code": 400, "real_external_call_executed": False}

        package_key = _text(payload.get("package_key"))
        member_event_id = int(payload.get("member_event_id") or 0)
        event_type = _event_type(payload)
        member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
        external_userid = _text(member.get("external_userid") or member.get("identity_value"))

        if not package_key or not member_event_id or not event_type or not external_userid:
            return {"ok": False, "error": "invalid_test_agent_payload", "status_code": 400, "real_external_call_executed": False}
        if not _package_key_allowed(package_key):
            return {"ok": False, "error": "package_key_not_allowed", "status_code": 403, "real_external_call_executed": False}
        if not _allowed("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", external_userid):
            return {"ok": False, "error": "external_userid_not_allowed", "status_code": 403, "real_external_call_executed": False}

        package = self._repo.get_package_by_key(package_key)
        if not package:
            return {"ok": False, "error": "package_not_found", "status_code": 404, "real_external_call_executed": False}
        subscription = self._matching_subscription(
            int(package["id"]),
            event_type=event_type,
        )
        if not subscription:
            return {"ok": False, "error": "subscription_not_found", "status_code": 404, "real_external_call_executed": False}

        sender = _text(runtime_setting("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", "HuangYouCan")) or "HuangYouCan"
        callback_payload = {
            "external_event_id": f"self_agent:{package_key}:{member_event_id}",
            "member_event_id": member_event_id,
            "status": "generated",
            "message": {"text": TEST_AGENT_MESSAGE_TEXT},
            "action": {
                "type": "send_private_message",
                "target_external_userid": external_userid,
                "sender_userid": sender,
            },
        }
        raw_body = _json_dumps(callback_payload).encode("utf-8")
        result = self._inbound.handle(package_key, callback_payload, raw_body=raw_body)
        status_code = 200 if result.get("ok") else 400
        return {
            "ok": bool(result.get("ok")),
            "status_code": status_code,
            "package_key": package_key,
            "member_event_id": member_event_id,
            "external_userid": external_userid,
            "sender_userid": sender,
            "simulated_message": TEST_AGENT_MESSAGE_TEXT,
            "inbound_result": result,
            "external_effect_job_id": result.get("external_effect_job_id"),
            "record_only": result.get("record_only"),
            "real_external_call_executed": False,
        }

    def _handle_run_payload(self, payload: list[Any], *, headers: dict[str, Any]) -> dict[str, Any]:
        package_key = _header(headers, "X-AICRM-Package-Key")
        refresh_run_id = _header(headers, "X-AICRM-Refresh-Run-Id")
        event_type = _header(headers, "X-AICRM-Event-Type")
        external_userids = [_text(item) for item in payload if _text(item)]
        if event_type != "audience.incremental.entered":
            return {"ok": False, "error": "event_type_not_allowed", "status_code": 400, "real_external_call_executed": False}
        if not package_key or not refresh_run_id or len(external_userids) != 1:
            return {"ok": False, "error": "invalid_test_agent_payload", "status_code": 400, "real_external_call_executed": False}
        external_userid = external_userids[0]
        if not _package_key_allowed(package_key):
            return {"ok": False, "error": "package_key_not_allowed", "status_code": 403, "real_external_call_executed": False}
        if not _allowed("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", external_userid):
            return {"ok": False, "error": "external_userid_not_allowed", "status_code": 403, "real_external_call_executed": False}

        package = self._repo.get_package_by_key(package_key)
        if not package:
            return {"ok": False, "error": "package_not_found", "status_code": 404, "real_external_call_executed": False}
        subscription = self._matching_subscription(
            int(package["id"]),
            event_type="entered",
        )
        if not subscription:
            return {"ok": False, "error": "subscription_not_found", "status_code": 404, "real_external_call_executed": False}

        sender = _text(runtime_setting("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", "HuangYouCan")) or "HuangYouCan"
        message_text = _message_text(package_key, refresh_run_id)
        external_event_id = f"self_agent_run:{package_key}:{refresh_run_id}:{external_userid}"
        callback_payload = {
            "external_event_id": external_event_id,
            "status": "generated",
            "message": {"text": message_text},
            "action": {
                "type": "send_private_message",
                "target_external_userid": external_userid,
                "sender_userid": sender,
            },
        }
        raw_body = _json_dumps(callback_payload).encode("utf-8")
        result = self._inbound.handle(package_key, callback_payload, raw_body=raw_body)
        status_code = 200 if result.get("ok") else 400
        return {
            "ok": bool(result.get("ok")),
            "status_code": status_code,
            "package_key": package_key,
            "refresh_run_id": refresh_run_id,
            "external_userid": external_userid,
            "sender_userid": sender,
            "simulated_message": message_text,
            "inbound_result": result,
            "external_event_id": external_event_id,
            "external_effect_job_id": result.get("external_effect_job_id"),
            "record_only": result.get("record_only"),
            "real_external_call_executed": False,
        }

    def _matching_subscription(self, package_id: int, *, event_type: str) -> dict[str, Any] | None:
        for subscription in self._repo.list_subscriptions(package_id, active_only=True, trigger_event_type=event_type):
            return subscription
        return None


def _event_type(payload: dict[str, Any]) -> str:
    raw = _text(payload.get("event_type"))
    if raw.startswith("audience.member."):
        return raw.rsplit(".", 1)[-1]
    return raw


def _allowed(setting_name: str, value: str) -> bool:
    allowed = runtime_csv(setting_name)
    return bool(value and value in allowed)


def _package_key_allowed(package_key: str) -> bool:
    if _allowed("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", package_key):
        return True
    return runtime_bool("AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED") and package_key.startswith("prod_e2e_")


def _header(headers: dict[str, Any], name: str) -> str:
    target = name.lower()
    for key, value in dict(headers or {}).items():
        if str(key or "").lower() == target:
            return _text(value)
    return ""


def _message_text(package_key: str, run_id: str) -> str:
    if "questionnaire" in package_key:
        return f"【E2E-问卷】run_id={run_id}：AI Audience 自动化真实链路测试。"
    if "payment" in package_key:
        return f"【E2E-支付】run_id={run_id}：支付命中后自动发送测试。"
    if "channel_entry" in package_key or "channel" in package_key:
        return f"【E2E-渠道】run_id={run_id}：渠道进入后自动发送测试。"
    return f"【E2E-AI人群包】run_id={run_id}：AI Audience 自动化真实链路测试。"
