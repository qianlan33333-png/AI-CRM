from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import Request

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.shared.runtime_settings import runtime_bool, runtime_csv

from .models import (
    AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
    GROUP_OPS_MESSAGE_LOOPBACK,
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    WEBHOOK_ORDER_PAID_PUSH,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    utcnow,
    public_datetime,
)
from .repo import ExternalEffectRepository, _payload_summary
from .service import ExternalEffectService

TEST_RECEIVER_PATH_PREFIX = "/api/external-effects/test-receiver"
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "testserver"}
_SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "set-cookie", "x-admin-action-token", "x-api-key"}
_ALLOWED_RESPONSE_STATUSES = {200, 400, 500}
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_EXTERNAL_EFFECT_ALLOWED_BASE_HOSTS",
        "AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY",
        "AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED",
    }
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "questionnaire_submission_push_success": {
        "effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        "target_type": "questionnaire_submission",
        "target_id_prefix": "synthetic_questionnaire_submission",
        "business_type": "questionnaire",
        "business_id_prefix": "synthetic_questionnaire",
        "default_response_status": 200,
    },
    "order_paid_push_success": {
        "effect_type": WEBHOOK_ORDER_PAID_PUSH,
        "target_type": "wechat_pay_order",
        "target_id_prefix": "synthetic_wechat_pay_order",
        "business_type": "commerce_order",
        "business_id_prefix": "synthetic_order",
        "default_response_status": 200,
    },
    "questionnaire_submission_push_retry_500": {
        "effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        "target_type": "questionnaire_submission",
        "target_id_prefix": "synthetic_questionnaire_submission_retry",
        "business_type": "questionnaire",
        "business_id_prefix": "synthetic_questionnaire_retry",
        "default_response_status": 500,
    },
    "order_paid_push_terminal_400": {
        "effect_type": WEBHOOK_ORDER_PAID_PUSH,
        "target_type": "wechat_pay_order",
        "target_id_prefix": "synthetic_wechat_pay_order_terminal",
        "business_type": "commerce_order",
        "business_id_prefix": "synthetic_order_terminal",
        "default_response_status": 400,
    },
    "group_ops_message_loopback_success": {
        "effect_type": GROUP_OPS_MESSAGE_LOOPBACK,
        "target_type": "group_ops_node",
        "target_id_prefix": "synthetic_group_ops_node",
        "business_type": "group_ops_plan",
        "business_id_prefix": "synthetic_group_ops_plan",
        "default_response_status": 200,
    },
    "ai_assist_campaign_message_loopback_success": {
        "effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
        "target_type": "campaign_member",
        "target_id_prefix": "synthetic_campaign_member",
        "business_type": "ai_assist_campaign",
        "business_id_prefix": "synthetic_ai_assist_campaign",
        "default_response_status": 200,
    },
    "group_ops_webhook_action_loopback_success": {
        "effect_type": GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
        "target_type": "group_ops_trigger_event",
        "target_id_prefix": "synthetic_group_ops_trigger_event",
        "business_type": "group_ops_plan",
        "business_id_prefix": "synthetic_group_ops_plan",
        "default_response_status": 200,
    },
}


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def test_receiver_enabled() -> bool:
    return _enabled("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED")


def test_execution_only_enabled() -> bool:
    return _enabled("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY")


def allowed_base_hosts() -> set[str]:
    return {_host_without_port(item.strip().lower()) for item in runtime_csv("AICRM_EXTERNAL_EFFECT_ALLOWED_BASE_HOSTS") if item.strip()}


def detect_current_base_url(request: Request) -> str:
    proto = str(request.headers.get("X-Forwarded-Proto") or request.url.scheme or "").split(",", 1)[0].strip().lower()
    host = str(request.headers.get("X-Forwarded-Host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    if proto not in {"http", "https"}:
        raise ValueError("invalid_forwarded_proto")
    if not _valid_host(host):
        raise ValueError("invalid_host")
    if not _host_allowed_by_env(host):
        raise ValueError("host_not_allowed")
    return f"{proto}://{host}"


def safe_current_base_url(request: Request) -> str:
    try:
        return detect_current_base_url(request)
    except ValueError:
        return ""


def _valid_host(host: str) -> bool:
    normalized = host.strip().lower()
    without_port = _host_without_port(normalized)
    return bool(host and _HOST_PATTERN.match(host) and without_port not in _BLOCKED_HOSTS and not without_port.startswith("127."))


def _host_without_port(host: str) -> str:
    normalized = host.strip().lower()
    return normalized.rsplit(":", 1)[0] if ":" in normalized and not normalized.startswith("[") else normalized


def _host_allowed_by_env(host: str) -> bool:
    allowed = allowed_base_hosts()
    if not allowed:
        return True
    return _host_without_port(host) in allowed


def canonical_payload_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_loopback_job(
    *,
    request: Request,
    service: ExternalEffectService,
    scenario: str,
    response_status: int | None = None,
) -> dict[str, Any]:
    scenario_config = SCENARIOS.get(str(scenario or "").strip())
    if not scenario_config:
        raise ValueError("unknown_test_loopback_scenario")
    status = int(response_status or scenario_config["default_response_status"])
    if status not in _ALLOWED_RESPONSE_STATUSES:
        raise ValueError("unsupported_test_receiver_response_status")

    base_url = detect_current_base_url(request)
    receiver_url = f"{base_url}{TEST_RECEIVER_PATH_PREFIX}"
    suffix = uuid4().hex[:12]
    target_id = f"{scenario_config['target_id_prefix']}_{suffix}"
    business_id = f"{scenario_config['business_id_prefix']}_{suffix}"
    trace_id = f"trace_test_loopback_{suffix}"
    idempotency_key = f"test-loopback:{scenario}:evt_{uuid4().hex}"
    body = {
        "synthetic": True,
        "scenario": scenario,
        "effect_type": scenario_config["effect_type"],
        "target_type": scenario_config["target_type"],
        "target_id": target_id,
        "business_type": scenario_config["business_type"],
        "business_id": business_id,
        "trace_id": trace_id,
        "idempotency_key": idempotency_key,
        "test_only": True,
    }
    payload_hash = canonical_payload_hash(body)
    job = service.plan_effect(
        effect_type=scenario_config["effect_type"],
        adapter_name="outbound_webhook",
        operation="post",
        target_type=scenario_config["target_type"],
        target_id=target_id,
        business_type=scenario_config["business_type"],
        business_id=business_id,
        payload={
            "webhook_url": receiver_url,
            "body": body,
            "receiver_response_status": status,
            "test_receiver_expires_at": public_datetime(utcnow() + timedelta(hours=12)),
            "execution_scope": "test_loopback",
            "is_test": True,
            "expected_payload_hash": payload_hash,
        },
        payload_summary={
            "scenario": scenario,
            "event_id": idempotency_key,
            "receiver_response_status": status,
            "execution_scope": "test_loopback",
            "expected_payload_hash": payload_hash,
        },
        context=CommandContext(
            actor_id="external-effect-test-loopback",
            actor_type="system",
            request_id="req_" + suffix,
            trace_id=trace_id,
            source_route="/api/admin/external-effects/test-loopback/jobs",
        ),
        source_module="platform_foundation.external_effects.test_receiver",
        risk_level="low",
        execution_mode="execute",
        status="queued",
        idempotency_key=idempotency_key,
    )
    return {
        "ok": True,
        "scenario": scenario,
        "job": job,
        "receiver_url": receiver_url,
        "runbook_next_steps": [
            "POST /api/admin/external-effects/run-due/preview with test_only=true",
            "POST /api/admin/external-effects/run-due with dry_run=true and test_only=true",
            "POST /api/admin/external-effects/run-due with dry_run=false, batch_size=1, test_only=true",
            "GET /api/admin/external-effects/test-receipts?job_id={job_id}",
        ],
    }


async def record_test_receiver_request(*, request: Request, repository: ExternalEffectRepository) -> tuple[int, dict[str, Any]]:
    if not test_receiver_enabled():
        return 404, {"ok": False, "error": "test_receiver_disabled"}
    event_id = str(request.headers.get("X-AICRM-Event-Id") or "").strip()
    job = repository.get_job_by_event_id(event_id)
    if not job:
        return 404, {"ok": False, "error": "test_receiver_event_not_found"}
    payload = dict(job.payload_json or {})
    expires_at = str(payload.get("test_receiver_expires_at") or "").strip()
    if expires_at and public_datetime(expires_at) < public_datetime(utcnow()):
        return 403, {"ok": False, "error": "test_receiver_event_expired"}

    try:
        body = await request.json()
    except Exception:
        body = {}
    body_json = dict(body or {}) if isinstance(body, dict) else {"_non_object": body}
    payload_hash = canonical_payload_hash(body_json)
    signature_valid = isinstance(getattr(request.state, "auth_context", None), AuthContext)
    response_status = int(payload.get("receiver_response_status") or 200)
    receipt = repository.create_test_receipt(
        event_id=event_id,
        job=job,
        request_method=request.method,
        request_path=request.url.path,
        headers_summary=_headers_summary(request.headers),
        payload_summary=_payload_summary(body_json),
        payload_hash=payload_hash,
        body_json=body_json,
        signature_valid=signature_valid,
        response_status=response_status,
    )
    return response_status, {"ok": True, "receipt_id": receipt.receipt_id, "received": True}


def _headers_summary(headers: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered in _SENSITIVE_HEADER_NAMES or any(token in lowered for token in ("token", "secret", "password", "authorization")):
            summary[key] = "[redacted]"
        elif lowered in {"content-type", "user-agent", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}:
            summary[key] = str(value)[:200]
    return summary
