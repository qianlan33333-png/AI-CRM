from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

import requests

from aicrm_next.platform.shared.wecom_runtime import load_wecom_execution_config
from aicrm_next.platform.platform_foundation.auth_platform.webhook_hmac import (
    WebhookHmacSigner,
    runtime_outbound_webhook_signer,
)

from aicrm_next.platform.shared.outbound_https.transport import (
    CallableHttpsTransport,
    HttpsTransport,
    HttpsTransportError,
    HttpsTransportTimeout,
    PinnedHttpsTransport,
)
from aicrm_next.platform.shared.outbound_https.security import Resolver, WebhookUrlValidationError, resolve_and_validate_public_https_target
from aicrm_next.platform.shared.runtime_settings import runtime_bool, runtime_csv, runtime_setting
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text

from .models import (
    AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
    GROUP_OPS_MESSAGE_LOOPBACK,
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    PAYMENT_WECHAT_REFUND_REQUEST,
    WEBHOOK_ORDER_PAID_PUSH,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    WEBHOOK_GENERIC_PUSH,
    WECOM_CONTACT_TAG_MARK,
    WECOM_CONTACT_TAG_UNMARK,
    WECOM_MEDIA_UPLOAD,
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
    WECOM_WELCOME_MESSAGE_SEND,
    WECOM_PROFILE_UPDATE,
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from .retry_policy import http_error_code
from .refund_outcomes import classify_refund_business_rejection
from .wecom_canary_policy import (
    WECOM_PROVIDER_TARGET_POLICY_KEY,
    wecom_canary_job_gate_error,
    wecom_canary_policy_snapshot,
)
from .wecom_attachment_contract import wecom_provider_attachment_ready

LOW_RISK_WEBHOOK_EFFECT_TYPES = frozenset(
    {
        WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        WEBHOOK_ORDER_PAID_PUSH,
        WEBHOOK_GENERIC_PUSH,
        AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
        GROUP_OPS_MESSAGE_LOOPBACK,
        GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    }
)
WECOM_EFFECT_TYPES = (
    WECOM_CONTACT_TAG_MARK,
    WECOM_CONTACT_TAG_UNMARK,
    WECOM_WELCOME_MESSAGE_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_PROFILE_UPDATE,
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    WECOM_MEDIA_UPLOAD,
)
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES",
        "AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE",
        "AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE",
        WECOM_PROVIDER_TARGET_POLICY_KEY,
    }
)


class ExternalEffectAdapter(Protocol):
    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult: ...


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _csv_env(name: str) -> set[str]:
    return runtime_csv(name)


def _normalized_wecom_execution_mode() -> tuple[str, str]:
    config = load_wecom_execution_config()
    return config.execution_mode, config.execution_mode_source


def _enabled_wecom_effect_types() -> tuple[list[str], str]:
    config = load_wecom_execution_config()
    supported = set(WECOM_EFFECT_TYPES)
    return sorted(item for item in config.enabled_effect_types if item in supported), config.enabled_effect_types_source


def _configured_wecom_sender(fallback: str = "") -> str:
    return load_wecom_execution_config().default_sender_userid or str(fallback or "").strip()


def _safe_response_json_summary(response: Any) -> dict[str, Any]:
    parsed: Any = None
    parser = getattr(response, "json", None)
    if callable(parser):
        try:
            parsed = parser()
        except Exception:
            parsed = None
    if parsed is None:
        raw_text = str(getattr(response, "text", "") or "").strip()
        if raw_text.startswith("{") and raw_text.endswith("}"):
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                parsed = None
    if not isinstance(parsed, dict):
        return {}
    allowed_keys = {
        "ok",
        "mode",
        "batch_id",
        "received_count",
        "deduped_count",
        "accepted_count",
        "error",
        "detail",
    }
    summary = {key: parsed.get(key) for key in allowed_keys if key in parsed}
    batch_id = str(parsed.get("batch_id") or "").strip()
    if batch_id.startswith("agent_batch_"):
        summary["automation_agent_batch_id"] = batch_id
    return redact_sensitive_data(summary)


def _safe_error_message(value: Any, *, limit: int = 500) -> str:
    return redact_sensitive_text(value)[: max(0, int(limit))]


def _safe_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _configured_wecom_provider_gate(job: ExternalEffectJob) -> str:
    """Apply the explicit target policy without changing legacy prod defaults."""

    if not runtime_setting(WECOM_PROVIDER_TARGET_POLICY_KEY, "").strip():
        return ""
    return wecom_canary_job_gate_error(job)


def _wecom_provider_failure(
    exc: Exception,
    *,
    default_error_code: str,
    executed_key: str,
) -> tuple[str, str, bool, dict[str, Any]]:
    error_code = default_error_code
    error_message = _safe_error_message(exc)
    retryable = False
    response_summary: dict[str, Any] = {"real_external_call_executed": True, executed_key: False}
    try:
        if hasattr(exc, "classification") and hasattr(exc, "error_code"):
            payload = dict(getattr(exc, "payload", {}) or {})
            response_summary.update(
                {
                    "errcode": int(payload.get("errcode") or getattr(exc, "provider_errcode", 0) or 0),
                    "errmsg_present": bool(str(payload.get("errmsg") or "").strip()),
                    "provider_error_classification": getattr(exc, "classification", ""),
                    "http_status": getattr(exc, "status_code", None),
                    "retry_after_seconds": getattr(exc, "retry_after_seconds", None),
                    "real_external_call_executed": bool(getattr(exc, "real_external_call_executed", True)),
                }
            )
            error_code = str(getattr(exc, "error_code", default_error_code) or default_error_code)
            error_message = _safe_error_message(payload.get("errmsg") or getattr(exc, "message", "") or exc)
            retryable = bool(getattr(exc, "retryable", False))
    except Exception:
        pass
    if error_message.startswith("missing_wecom_config:"):
        error_code = "config_missing"
        retryable = False
        response_summary["real_external_call_executed"] = False
    elif error_message.endswith("_adapter_composition_missing"):
        error_code = "adapter_composition_missing"
        retryable = False
        response_summary["real_external_call_executed"] = False
    return error_code, error_message, retryable, response_summary


def _target_unionid(payload: dict[str, Any]) -> str:
    return str(payload.get("target_unionid") or payload.get("unionid") or "").strip()


def _wecom_target_mismatch(job: ExternalEffectJob, payload: dict[str, Any], external_userid: str) -> bool:
    target_unionid = _target_unionid(payload)
    target_id = str(job.target_id or "").strip()
    if target_unionid:
        return target_id != target_unionid
    return not external_userid or target_id != external_userid


def webhook_execution_settings() -> dict[str, Any]:
    return {
        "enabled": _enabled("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE"),
        "allowed_types": sorted(_csv_env("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")),
        "supported_types": sorted(LOW_RISK_WEBHOOK_EFFECT_TYPES),
    }


def wecom_execution_settings() -> dict[str, Any]:
    config = load_wecom_execution_config()
    execution_mode, mode_source = config.execution_mode, config.execution_mode_source
    enabled_types, enabled_types_source = _enabled_wecom_effect_types()
    default_sender = _configured_wecom_sender()
    deprecated_settings_present = list(config.deprecated_settings_present)
    explicit_provider_policy = runtime_setting(WECOM_PROVIDER_TARGET_POLICY_KEY, "").strip()
    provider_policy = wecom_canary_policy_snapshot() if explicit_provider_policy else None
    blocking_reasons: list[str] = list(config.blocking_reasons)
    if execution_mode == "execute" and not enabled_types:
        blocking_reasons.append("wecom_enabled_effect_types_empty")
    if execution_mode == "execute" and not default_sender:
        blocking_reasons.append("default_sender_userid_missing")
    if execution_mode == "execute" and provider_policy is not None:
        blocking_reasons.extend(provider_policy["blocking_reasons"])
    blocking_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "enabled": execution_mode == "execute" and not blocking_reasons,
        "execution_mode": execution_mode,
        "execution_mode_source": mode_source,
        "allowed_types": enabled_types,
        "enabled_effect_types": enabled_types,
        "enabled_effect_types_source": enabled_types_source,
        **(
            {
                "provider_target_policy": provider_policy["provider_target_policy"],
                "required_execution_scope": provider_policy["required_execution_scope"],
                "allowlisted_canary_enabled": provider_policy["allowlisted_canary_enabled"],
                "allowlist_counts": provider_policy["allowlist_counts"],
            }
            if provider_policy is not None
            else {
                "allowed_target_external_userids": "all",
                "allowed_group_ops_webhook_keys": "all",
                "allowed_owner_userids": [default_sender] if default_sender else [],
                "allowed_group_chat_ids": "all",
            }
        ),
        "supported_types": list(WECOM_EFFECT_TYPES),
        "corp_id_present": bool(config.corp_id),
        "contact_secret_present": bool(config.contact_secret),
        "default_sender_userid_present": bool(default_sender),
        "deprecated_settings_present": deprecated_settings_present,
        "deprecated_settings_owner": "integration_gateway",
        "deprecated_settings_delete_after": "2026-10-01",
        "config_conflict": config.conflict,
        "blocking_reasons": blocking_reasons,
    }


def payment_execution_settings() -> dict[str, Any]:
    return {
        "enabled": _enabled("AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE"),
        "allowed_types": sorted(_csv_env("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")),
        "supported_types": [PAYMENT_WECHAT_REFUND_REQUEST],
    }


class DisabledAdapter:
    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        return ExternalEffectDispatchResult(
            status="failed_terminal",
            adapter_mode=job.execution_mode or "disabled",
            request_summary={"effect_type": job.effect_type, "target_type": job.target_type, "target_id": job.target_id},
            response_summary={"blocked": True, "real_external_call_executed": False},
            error_code="adapter_not_implemented",
            error_message="No External Effect Queue adapter is registered for this adapter_name.",
            real_external_call_executed=False,
        )


class WebhookAdapter:
    def __init__(
        self,
        http_post=None,
        *,
        transport: HttpsTransport | None = None,
        resolver: Resolver | None = None,
        signer: WebhookHmacSigner | None = None,
    ) -> None:
        if http_post is not None and transport is not None:
            raise ValueError("provide either http_post or transport, not both")
        self._transport = transport or (CallableHttpsTransport(http_post) if http_post is not None else PinnedHttpsTransport())
        self._resolver = resolver or (self._injected_test_resolver if http_post is not None else None)
        self._signer = signer

    @staticmethod
    def _injected_test_resolver(_hostname: str, _port: int) -> list[str]:
        return ["8.8.8.8"]

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        gate_error = self._execution_gate_error(job)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "shadow",
                request_summary={
                    "effect_type": job.effect_type,
                    "operation": job.operation,
                    "target_type": job.target_type,
                    "target_id": job.target_id,
                },
                response_summary={"blocked": True, "execution_gate": gate_error, "real_external_call_executed": False},
                error_code=gate_error,
                error_message="Webhook adapter execution is blocked by external effect execution gates.",
                real_external_call_executed=False,
            )

        payload = dict(job.payload_json or {})
        url = str(payload.get("webhook_url") or payload.get("target_url") or "").strip()
        body = self._request_body(payload)
        if not url:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary={"target_url_present": False, "effect_type": job.effect_type},
                response_summary={"real_external_call_executed": False},
                error_code="config_missing",
                error_message="webhook_url is required",
                real_external_call_executed=False,
            )
        if body is None:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary={"target_url_present": True, "effect_type": job.effect_type},
                response_summary={"real_external_call_executed": False},
                error_code="payload_invalid",
                error_message="webhook payload body must be a JSON object or array",
                real_external_call_executed=False,
            )
        timeout = float(runtime_setting("AICRM_EXTERNAL_EFFECT_WEBHOOK_TIMEOUT_SECONDS", "5") or "5")
        signer = self._signer or runtime_outbound_webhook_signer()
        request_summary = {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_url_present": True,
            "timeout_seconds": timeout,
            "body_type": type(body).__name__,
            "signature_configured": signer is not None,
            "signature_scheme": "aicrm_hmac_sha256",
            "redirect_policy": "deny",
        }
        try:
            target = resolve_and_validate_public_https_target(url, resolver=self._resolver)
        except WebhookUrlValidationError:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"blocked": True, "real_external_call_executed": False},
                error_code="ssrf_blocked",
                error_message="webhook target failed public HTTPS validation",
                real_external_call_executed=False,
            )
        if signer is None:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"blocked": True, "real_external_call_executed": False},
                error_code="auth_signature_config_missing",
                error_message="registered outbound webhook HMAC credentials are required",
                real_external_call_executed=False,
            )
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = self._headers(
            payload=payload,
            body=body_bytes,
            signer=signer,
            event_id=_webhook_event_id(job.idempotency_key or f"external-effect-{job.id}"),
        )
        request_summary["resolved_ip_count"] = len(target.ip_addresses)
        try:
            response = self._transport.post(
                target,
                body=body_bytes,
                headers=headers,
                timeout=timeout,
            )
        except (HttpsTransportTimeout, requests.Timeout):
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": True},
                error_code="timeout",
                error_message="webhook request timed out",
                real_external_call_executed=True,
            )
        except (HttpsTransportError, requests.RequestException) as exc:
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": True},
                error_code="network_error",
                error_message=_safe_error_message(exc),
                real_external_call_executed=True,
            )

        status_code = int(response.status_code)
        if 300 <= status_code < 400:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"status_code": status_code, "redirect_blocked": True, "real_external_call_executed": True},
                error_code="redirect_blocked",
                error_message="webhook redirects are not allowed",
                real_external_call_executed=True,
            )
        if 200 <= status_code < 300:
            status = "succeeded"
        elif status_code in {408, 429} or status_code >= 500:
            status = "failed_retryable"
        else:
            status = "failed_terminal"
        retry_after_seconds = None
        if status_code == 429:
            try:
                retry_after_seconds = max(
                    0,
                    min(int(float(str(response.headers.get("Retry-After") or "").strip())), 86400),
                )
            except (TypeError, ValueError):
                retry_after_seconds = None
        response_summary = {
            "status_code": status_code,
            "real_external_call_executed": True,
            **({"retry_after_seconds": retry_after_seconds} if retry_after_seconds is not None else {}),
        }
        response_json_summary = _safe_response_json_summary(response)
        if response_json_summary:
            response_summary["response_json"] = response_json_summary
            if response_json_summary.get("automation_agent_batch_id"):
                response_summary["automation_agent_batch_id"] = response_json_summary["automation_agent_batch_id"]
        return ExternalEffectDispatchResult(
            status=status,
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code="" if status == "succeeded" else http_error_code(status_code),
            error_message="" if status == "succeeded" else _safe_error_message(response.text),
            retry_after_seconds=retry_after_seconds,
            real_external_call_executed=True,
            provider_result_received=True,
        )

    def _execution_gate_error(self, job: ExternalEffectJob) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type == GROUP_OPS_MESSAGE_LOOPBACK:
            payload = dict(job.payload_json or {})
            if str(payload.get("execution_scope") or "").strip() != "test_loopback" or not payload.get("webhook_url"):
                return "group_ops_loopback_requires_test_receiver"
        if not _enabled("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE"):
            return "execution_disabled"
        if job.effect_type not in LOW_RISK_WEBHOOK_EFFECT_TYPES:
            return "unsupported_effect_type"
        allowed = _csv_env("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")
        if job.effect_type not in allowed:
            return "effect_type_not_allowed"
        return ""

    def _request_body(self, payload: dict[str, Any]) -> dict[str, Any] | list[Any] | None:
        if "body" in payload:
            body = payload.get("body")
        elif "payload" in payload:
            body = payload.get("payload")
        else:
            body = {key: value for key, value in payload.items() if key not in {"webhook_url", "target_url"}}
        return body if isinstance(body, (dict, list)) else None

    def _headers(
        self,
        *,
        payload: dict[str, Any],
        body: bytes,
        signer: WebhookHmacSigner,
        event_id: str,
    ) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        extra_headers = payload.get("headers")
        if isinstance(extra_headers, dict):
            for key, value in extra_headers.items():
                header_name = str(key or "").strip()
                if not header_name or any(sensitive in header_name.lower() for sensitive in ("authorization", "token", "secret", "cookie")):
                    continue
                headers[header_name] = str(value or "")
        headers.update(signer.sign_headers(body=body, event_id=event_id))
        return headers


def _webhook_event_id(value: str) -> str:
    normalized = str(value or "").strip()
    if 16 <= len(normalized) <= 256:
        return normalized
    return f"evt_{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


class WeComPrivateMessageAdapter:
    def __init__(self, adapter_factory=None) -> None:
        self._adapter_factory = adapter_factory

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        external_userids = [str(item or "").strip() for item in list(payload.get("external_userids") or []) if str(item or "").strip()]
        owner_userid = str(payload.get("owner_userid") or payload.get("sender") or "").strip()
        sender_userid = _configured_wecom_sender(owner_userid)
        content_text = str(payload.get("content_text") or "").strip()
        gate_error = self._execution_gate_error(job=job, payload=payload, external_userids=external_userids, owner_userid=sender_userid)
        request_summary = {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "owner_userid": owner_userid,
            "sender_userid": sender_userid,
            "sender_binding_applied": bool(sender_userid and sender_userid != owner_userid),
            "target_unionid": _target_unionid(payload),
            "external_userid_count": len(external_userids),
            "content_text_length": len(content_text),
            "attachment_count": len(payload.get("attachments") or []) if isinstance(payload.get("attachments"), list) else 0,
            "business_type": job.business_type,
            "source": str(payload.get("source") or ""),
        }
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={"blocked": True, "execution_gate": gate_error, "real_external_call_executed": False, "wecom_send_executed": False},
                error_code=gate_error,
                error_message="WeCom private-message adapter execution is blocked by payload validation.",
                real_external_call_executed=False,
            )
        adapter_payload: dict[str, Any] = {
            "sender": sender_userid,
            "external_userids": external_userids,
        }
        if content_text:
            adapter_payload["text"] = {"content": content_text}
        attachments = payload.get("attachments")
        if isinstance(attachments, list) and attachments:
            adapter_payload["attachments"] = attachments
        try:
            if self._adapter_factory is None:
                raise RuntimeError("wecom_private_adapter_composition_missing")
            result = self._adapter_factory().create_private_message_task(
                adapter_payload,
                idempotency_key=job.idempotency_key or str(job.id),
            )
        except Exception as exc:
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": True, "wecom_send_executed": False},
                error_code="adapter_exception",
                error_message=_safe_error_message(exc),
                real_external_call_executed=True,
            )
        side_effect_executed = bool(result.get("side_effect_executed"))
        ok = bool(result.get("ok"))
        error_code = str(result.get("error_code") or "").strip()
        provider_result = dict(result.get("result") or {}) if isinstance(result.get("result"), dict) else {}
        provider_errcode = _safe_int(result.get("provider_errcode") or provider_result.get("errcode"))
        provider_result_received = bool(side_effect_executed and provider_result)
        response_summary = {
            "real_external_call_executed": side_effect_executed,
            "wecom_send_executed": side_effect_executed,
            "adapter_mode": str(result.get("mode") or ""),
            "exact_target_verified": bool(result.get("exact_target_verified")),
            "requested_external_userid_count": len(result.get("requested_external_userids") or external_userids),
            "wecom_msgid_present": bool(str(result.get("wecom_msgid") or "").strip()),
            "errcode": provider_errcode,
            "errmsg_present": bool(str(provider_result.get("errmsg") or result.get("error_message") or "").strip()),
            "provider_error_classification": str(result.get("provider_error_classification") or ""),
            **({"retry_after_seconds": _safe_int(result.get("retry_after_seconds"))} if _safe_int(result.get("retry_after_seconds")) > 0 else {}),
            "failed_external_userid_count": _safe_int(
                result.get("failed_external_userid_count"),
                default=_safe_list_count(provider_result.get("fail_list")),
            ),
            "provider_result_received": provider_result_received,
        }
        for classification_key in (
            "provider_outcome_classification",
            "business_reason_code",
        ):
            classification_value = str(result.get(classification_key) or "").strip()
            if classification_value:
                response_summary[classification_key] = classification_value
        if ok:
            return ExternalEffectDispatchResult(
                status="succeeded",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary=response_summary,
                real_external_call_executed=side_effect_executed,
                provider_result_received=bool(side_effect_executed and response_summary.get("wecom_msgid_present")),
            )
        if not side_effect_executed:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary=response_summary,
                error_code=error_code or "adapter_blocked",
                error_message=_safe_error_message(result.get("error_message") or "WeCom private-message adapter blocked before external call."),
                real_external_call_executed=False,
            )
        retryable = error_code in {"external_call_unknown", "adapter_exception", "network_error", "timeout", "rate_limited"}
        return ExternalEffectDispatchResult(
            status="failed_retryable" if result.get("retryable") is True or retryable else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code or "wecom_private_send_failed",
            error_message=_safe_error_message(result.get("error_message") or "WeCom private-message send failed."),
            retry_after_seconds=_safe_int(result.get("retry_after_seconds")) or None,
            real_external_call_executed=True,
            provider_result_received=provider_result_received,
        )

    def _execution_gate_error(
        self,
        *,
        job: ExternalEffectJob,
        payload: dict[str, Any],
        external_userids: list[str],
        owner_userid: str,
    ) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != WECOM_MESSAGE_PRIVATE_SEND:
            return "unsupported_effect_type"
        if len(external_userids) != 1:
            return "single_target_required"
        if _wecom_target_mismatch(job, payload, external_userids[0]):
            return "target_mismatch"
        if str(payload.get("channel") or "").strip() != "wecom_private":
            return "channel_not_allowed"
        has_text = bool(str(payload.get("content_text") or "").strip())
        has_attachments = isinstance(payload.get("attachments"), list) and bool(payload.get("attachments"))
        if not has_text and not has_attachments:
            return "payload_invalid"
        if has_attachments and any(not wecom_provider_attachment_ready(item) for item in payload.get("attachments") or []):
            return "unresolved_material_dependency"
        return _configured_wecom_provider_gate(job)


class WeComGroupMessageExternalEffectAdapter:
    def __init__(self, adapter_factory=None) -> None:
        self._adapter_factory = adapter_factory

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        gate_error = self._execution_gate_error(job, payload)
        request_summary = self._request_summary(job, payload)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "wecom_send_executed": False,
                },
                error_code=gate_error,
                error_message="WeCom group message adapter execution is blocked by payload validation.",
                real_external_call_executed=False,
            )

        wecom_payload = self._wecom_payload(payload)
        try:
            if self._adapter_factory is None:
                raise RuntimeError("wecom_group_adapter_composition_missing")
            result = self._adapter_factory().create_group_message_task(
                wecom_payload,
                idempotency_key=job.idempotency_key or job.trace_id or str(job.id),
            )
        except Exception as exc:
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": True, "wecom_send_executed": False},
                error_code="network_error",
                error_message=_safe_error_message(exc),
                real_external_call_executed=True,
            )

        response_summary = {
            "adapter": result.get("adapter"),
            "mode": result.get("mode"),
            "operation": result.get("operation"),
            "audit_id": result.get("audit_id"),
            "requested_chat_count": _safe_int(
                result.get("requested_chat_count"),
                default=_safe_list_count(result.get("requested_chat_ids")),
            ),
            "exact_target_required": bool(result.get("exact_target_required")),
            "exact_target_verified": bool(result.get("exact_target_verified")),
            "wecom_msgid_present": bool(str(result.get("wecom_msgid") or "").strip()),
            "real_external_call_executed": bool(result.get("side_effect_executed")),
            "wecom_send_executed": bool(result.get("side_effect_executed")),
        }
        provider_result = dict(result.get("result") or {}) if isinstance(result.get("result"), dict) else {}
        response_summary.update(
            {
                "errcode": _safe_int(result.get("provider_errcode") or provider_result.get("errcode")),
                "errmsg_present": bool(str(provider_result.get("errmsg") or result.get("error_message") or "").strip()),
                "provider_error_classification": str(result.get("provider_error_classification") or ""),
                "failed_chat_count": _safe_int(
                    result.get("failed_chat_count"),
                    default=_safe_list_count(provider_result.get("fail_list")),
                ),
            }
        )
        for classification_key in (
            "provider_outcome_classification",
            "business_reason_code",
        ):
            classification_value = str(result.get(classification_key) or "").strip()
            if classification_value:
                response_summary[classification_key] = classification_value
        if result.get("ok") and result.get("exact_target_verified") is True:
            return ExternalEffectDispatchResult(
                status="succeeded",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary=response_summary,
                real_external_call_executed=bool(result.get("side_effect_executed")),
                provider_result_received=bool(
                    result.get("side_effect_executed") and (response_summary.get("wecom_msgid_present") or response_summary.get("audit_id"))
                ),
            )
        error_code = str(result.get("error_code") or "wecom_group_message_failed").strip()
        return ExternalEffectDispatchResult(
            status="failed_retryable" if result.get("retryable") is True else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code,
            error_message=_safe_error_message(result.get("error_message") or error_code),
            real_external_call_executed=bool(result.get("side_effect_executed")),
        )

    def _request_summary(self, job: ExternalEffectJob, payload: dict[str, Any]) -> dict[str, Any]:
        chat_ids = self._chat_ids(payload)
        return {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "webhook_key": str(payload.get("webhook_key") or ""),
            "owner_userid": str(payload.get("owner_userid") or payload.get("sender") or ""),
            "chat_count": len(chat_ids),
            "mention_all": bool(payload.get("mention_all") or payload.get("is_mention_all")),
            "content_text_length": len(str(((payload.get("content_payload") or {}).get("text") or {}).get("content") or "")),
        }

    def _execution_gate_error(self, job: ExternalEffectJob, payload: dict[str, Any]) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != WECOM_MESSAGE_GROUP_SEND:
            return "unsupported_effect_type"
        owner = _configured_wecom_sender(str(payload.get("owner_userid") or payload.get("sender") or "").strip())
        if not owner:
            return "owner_userid_missing"
        chat_ids = self._chat_ids(payload)
        if not chat_ids:
            return "group_chat_id_missing"
        content_payload = payload.get("content_payload")
        if not isinstance(content_payload, dict):
            return "payload_invalid"
        text = content_payload.get("text") if isinstance(content_payload.get("text"), dict) else {}
        attachments = content_payload.get("attachments") if isinstance(content_payload.get("attachments"), list) else []
        if not str(text.get("content") or "").strip() and not attachments:
            return "payload_invalid"
        return _configured_wecom_provider_gate(job)

    def _chat_ids(self, payload: dict[str, Any]) -> list[str]:
        return [str(item or "").strip() for item in list(payload.get("chat_ids") or []) if str(item or "").strip()]

    def _wecom_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        content_payload = dict(payload.get("content_payload") or {})
        result = dict(content_payload)
        result["sender"] = _configured_wecom_sender(str(payload.get("owner_userid") or payload.get("sender") or content_payload.get("sender") or "").strip())
        result["chat_ids"] = self._chat_ids(payload)
        return result


class WeComWelcomeMessageAdapter:
    def __init__(self, adapter_factory=None, material_resolver=None) -> None:
        self._adapter_factory = adapter_factory
        # Compatibility-only argument: media dependencies must already be
        # resolved before the single provider dispatch boundary.
        del material_resolver

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        request_summary = self._request_summary(job, payload)
        gate_error = self._execution_gate_error(job, payload)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "wecom_send_executed": False,
                },
                error_code=gate_error,
                error_message="WeCom welcome-message adapter execution is blocked by payload validation.",
                real_external_call_executed=False,
            )

        try:
            wecom_payload = self._wecom_payload(payload)
            adapter = self._build_adapter()
            result = adapter.send_welcome_msg(wecom_payload)
        except Exception as exc:
            return self._failure_result(exc, request_summary=request_summary)

        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "errcode": int(result.get("errcode") or 0) if isinstance(result, dict) else 0,
                "errmsg_present": bool(str((result or {}).get("errmsg") or "").strip()) if isinstance(result, dict) else False,
                "real_external_call_executed": True,
                "wecom_send_executed": True,
            },
            real_external_call_executed=True,
            provider_result_received=True,
        )

    def _request_summary(self, job: ExternalEffectJob, payload: dict[str, Any]) -> dict[str, Any]:
        text_payload = payload.get("text") if isinstance(payload.get("text"), dict) else {}
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        return {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "target_unionid": _target_unionid(payload),
            "external_userid": str(payload.get("external_userid") or ""),
            "follow_user_userid": str(payload.get("follow_user_userid") or ""),
            "welcome_code_present": bool(str(payload.get("welcome_code") or "").strip()),
            "text_length": len(str(text_payload.get("content") or "")),
            "attachment_count": len(attachments),
        }

    def _execution_gate_error(self, job: ExternalEffectJob, payload: dict[str, Any]) -> str:
        from .deadlines import provider_deadline_elapsed

        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != WECOM_WELCOME_MESSAGE_SEND:
            return "unsupported_effect_type"
        if provider_deadline_elapsed(payload):
            return "provider_deadline_elapsed"
        external_userid = str(payload.get("external_userid") or "").strip()
        if _wecom_target_mismatch(job, payload, external_userid):
            return "target_mismatch"
        follow_user_userid = str(payload.get("follow_user_userid") or "").strip()
        if not follow_user_userid:
            return "owner_userid_missing"
        if not str(payload.get("welcome_code") or "").strip():
            return "welcome_code_missing"
        has_text = isinstance(payload.get("text"), dict) and bool(str((payload.get("text") or {}).get("content") or "").strip())
        has_attachments = isinstance(payload.get("attachments"), list) and bool(payload.get("attachments"))
        if not has_text and not has_attachments:
            return "payload_invalid"
        if has_attachments and any(not wecom_provider_attachment_ready(item) for item in payload.get("attachments") or []):
            return "unresolved_material_dependency"
        return _configured_wecom_provider_gate(job)

    def _wecom_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"welcome_code": str(payload.get("welcome_code") or "").strip()}
        if isinstance(payload.get("text"), dict):
            result["text"] = dict(payload.get("text") or {})
        attachments = list(payload.get("attachments") or []) if isinstance(payload.get("attachments"), list) else []
        if attachments:
            result["attachments"] = attachments
        return result

    def _build_adapter(self):
        if self._adapter_factory is None:
            raise RuntimeError("wecom_welcome_adapter_composition_missing")
        return self._adapter_factory()

    def _failure_result(self, exc: Exception, *, request_summary: dict[str, Any]) -> ExternalEffectDispatchResult:
        error_code, error_message, retryable, response_summary = _wecom_provider_failure(
            exc,
            default_error_code="wecom_welcome_send_failed",
            executed_key="wecom_send_executed",
        )
        return ExternalEffectDispatchResult(
            status="failed_retryable" if retryable else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code,
            error_message=error_message,
            real_external_call_executed=bool(response_summary.get("real_external_call_executed")),
        )


class WeComContactTagAdapter:
    def __init__(self, adapter_factory=None) -> None:
        self._adapter_factory = adapter_factory

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        request_summary = self._request_summary(job, payload)
        gate_error = self._execution_gate_error(job, payload)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "wecom_tag_executed": False,
                },
                error_code=gate_error,
                error_message="WeCom contact-tag adapter execution is blocked by external effect gates.",
                real_external_call_executed=False,
            )

        try:
            adapter = self._build_adapter()
            result = adapter.mark_external_contact_tags(
                external_userid=str(payload.get("external_userid") or "").strip(),
                follow_user_userid=str(payload.get("follow_user_userid") or payload.get("userid") or "").strip(),
                add_tags=self._add_tags(job, payload),
                remove_tags=self._remove_tags(job, payload),
            )
        except Exception as exc:
            return self._failure_result(exc, request_summary=request_summary)

        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "errcode": int(result.get("errcode") or 0) if isinstance(result, dict) else 0,
                "errmsg_present": bool(str((result or {}).get("errmsg") or "").strip()) if isinstance(result, dict) else False,
                "real_external_call_executed": True,
                "wecom_tag_executed": True,
            },
            real_external_call_executed=True,
            provider_result_received=True,
        )

    def _request_summary(self, job: ExternalEffectJob, payload: dict[str, Any]) -> dict[str, Any]:
        add_tags = self._add_tags(job, payload)
        remove_tags = self._remove_tags(job, payload)
        return {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "target_unionid": _target_unionid(payload),
            "external_userid": str(payload.get("external_userid") or ""),
            "follow_user_userid": str(payload.get("follow_user_userid") or payload.get("userid") or ""),
            "add_tag_count": len(add_tags),
            "remove_tag_count": len(remove_tags),
        }

    def _execution_gate_error(self, job: ExternalEffectJob, payload: dict[str, Any]) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type not in {WECOM_CONTACT_TAG_MARK, WECOM_CONTACT_TAG_UNMARK}:
            return "unsupported_effect_type"
        external_userid = str(payload.get("external_userid") or "").strip()
        if _wecom_target_mismatch(job, payload, external_userid):
            return "target_mismatch"
        follow_user_userid = str(payload.get("follow_user_userid") or payload.get("userid") or "").strip()
        if not follow_user_userid:
            return "owner_userid_missing"
        add_tags = self._add_tags(job, payload)
        remove_tags = self._remove_tags(job, payload)
        if not add_tags and not remove_tags:
            return "tag_ids_missing"
        if job.effect_type == WECOM_CONTACT_TAG_MARK and not add_tags:
            return "add_tags_missing"
        if job.effect_type == WECOM_CONTACT_TAG_UNMARK and not remove_tags:
            return "remove_tags_missing"
        return _configured_wecom_provider_gate(job)

    def _build_adapter(self):
        if self._adapter_factory is None:
            raise RuntimeError("wecom_tag_adapter_composition_missing")
        return self._adapter_factory()

    def _failure_result(self, exc: Exception, *, request_summary: dict[str, Any]) -> ExternalEffectDispatchResult:
        error_code, error_message, retryable, response_summary = _wecom_provider_failure(
            exc,
            default_error_code="wecom_tag_mark_failed",
            executed_key="wecom_tag_executed",
        )
        return ExternalEffectDispatchResult(
            status="failed_retryable" if retryable else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code,
            error_message=error_message,
            real_external_call_executed=bool(response_summary.get("real_external_call_executed")),
        )

    def _tags(self, value: Any) -> list[str]:
        return [str(item or "").strip() for item in list(value or []) if str(item or "").strip()]

    def _add_tags(self, job: ExternalEffectJob, payload: dict[str, Any]) -> list[str]:
        explicit = self._tags(payload.get("add_tags"))
        if explicit or job.effect_type != WECOM_CONTACT_TAG_MARK:
            return explicit
        return self._tags(payload.get("tag_ids"))

    def _remove_tags(self, job: ExternalEffectJob, payload: dict[str, Any]) -> list[str]:
        explicit = self._tags(payload.get("remove_tags"))
        if explicit or job.effect_type != WECOM_CONTACT_TAG_UNMARK:
            return explicit
        return self._tags(payload.get("tag_ids"))


class WeComProfileUpdateAdapter:
    def __init__(self, adapter_factory=None) -> None:
        self._adapter_factory = adapter_factory

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        request_summary = self._request_summary(job, payload)
        gate_error = self._execution_gate_error(job, payload)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "wecom_profile_update_executed": False,
                },
                error_code=gate_error,
                error_message="WeCom profile-update adapter execution is blocked by external effect gates.",
                real_external_call_executed=False,
            )

        wecom_payload = {
            "userid": str(payload.get("follow_user_userid") or payload.get("userid") or "").strip(),
            "external_userid": str(payload.get("external_userid") or "").strip(),
        }
        for key in ("remark", "description", "remark_company"):
            value = str(payload.get(key) or "").strip()
            if value:
                wecom_payload[key] = value
        remark_mobiles = [str(item or "").strip() for item in list(payload.get("remark_mobiles") or []) if str(item or "").strip()]
        if remark_mobiles:
            wecom_payload["remark_mobiles"] = remark_mobiles
        try:
            result = self._build_adapter().update_external_contact_remark(wecom_payload)
        except Exception as exc:
            return self._failure_result(exc, request_summary=request_summary)

        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "errcode": int(result.get("errcode") or 0) if isinstance(result, dict) else 0,
                "errmsg_present": bool(str((result or {}).get("errmsg") or "").strip()) if isinstance(result, dict) else False,
                "real_external_call_executed": True,
                "wecom_profile_update_executed": True,
            },
            real_external_call_executed=True,
            provider_result_received=True,
        )

    def _request_summary(self, job: ExternalEffectJob, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "target_unionid": _target_unionid(payload),
            "external_userid": str(payload.get("external_userid") or ""),
            "follow_user_userid": str(payload.get("follow_user_userid") or payload.get("userid") or ""),
            "remark_present": bool(str(payload.get("remark") or "").strip()),
            "description_present": bool(str(payload.get("description") or "").strip()),
        }

    def _execution_gate_error(self, job: ExternalEffectJob, payload: dict[str, Any]) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != WECOM_PROFILE_UPDATE:
            return "unsupported_effect_type"
        external_userid = str(payload.get("external_userid") or "").strip()
        if _wecom_target_mismatch(job, payload, external_userid):
            return "target_mismatch"
        follow_user_userid = str(payload.get("follow_user_userid") or payload.get("userid") or "").strip()
        if not follow_user_userid:
            return "owner_userid_missing"
        if not any(str(payload.get(key) or "").strip() for key in ("remark", "description", "remark_company")) and not payload.get("remark_mobiles"):
            return "profile_update_payload_missing"
        return _configured_wecom_provider_gate(job)

    def _build_adapter(self):
        if self._adapter_factory is None:
            raise RuntimeError("wecom_profile_adapter_composition_missing")
        return self._adapter_factory()

    def _failure_result(self, exc: Exception, *, request_summary: dict[str, Any]) -> ExternalEffectDispatchResult:
        error_code, error_message, retryable, response_summary = _wecom_provider_failure(
            exc,
            default_error_code="wecom_profile_update_failed",
            executed_key="wecom_profile_update_executed",
        )
        return ExternalEffectDispatchResult(
            status="failed_retryable" if retryable else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code,
            error_message=error_message,
            real_external_call_executed=bool(response_summary.get("real_external_call_executed")),
        )


class WeComExternalContactDetailAdapter:
    """Canonical provider-read boundary for identity resolution."""

    def __init__(self, adapter_factory=None) -> None:
        self._adapter_factory = adapter_factory

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        external_userid = str(payload.get("external_userid") or "").strip()
        request_summary = {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_hash": "sha256:" + hashlib.sha256(external_userid.encode("utf-8")).hexdigest() if external_userid else "",
            "external_userid_present": bool(external_userid),
            "queue_link_present": int(payload.get("queue_id") or 0) > 0,
            "event_link_present": int(payload.get("event_log_id") or 0) > 0,
        }
        gate_error = self._execution_gate_error(job, payload=payload, external_userid=external_userid)
        if gate_error:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "provider_result_received": False,
                },
                error_code=gate_error,
                error_message="WeCom external-contact detail fetch is blocked before provider dispatch.",
                real_external_call_executed=False,
                provider_result_received=False,
            )
        try:
            if self._adapter_factory is None:
                raise RuntimeError("wecom_external_contact_detail_adapter_composition_missing")
            result = self._adapter_factory().get_external_contact_detail(external_userid)
        except Exception as exc:
            error_code, error_message, retryable, response_summary = _wecom_provider_failure(
                exc,
                default_error_code="wecom_external_contact_detail_failed",
                executed_key="wecom_external_contact_detail_executed",
            )
            provider_result_received = int(response_summary.get("errcode") or 0) != 0
            response_summary["provider_result_received"] = provider_result_received
            return ExternalEffectDispatchResult(
                status="failed_retryable" if retryable else "failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary=response_summary,
                error_code=error_code,
                error_message=error_message,
                real_external_call_executed=bool(response_summary.get("real_external_call_executed")),
                provider_result_received=provider_result_received,
            )
        detail = dict(result or {})
        errcode = int(detail.get("errcode") or 0)
        if errcode != 0:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={
                    "errcode": errcode,
                    "errmsg_present": bool(str(detail.get("errmsg") or "").strip()),
                    "real_external_call_executed": True,
                    "provider_result_received": True,
                },
                error_code=f"wecom_errcode_{errcode}",
                error_message=_safe_error_message(detail.get("errmsg") or "WeCom external-contact detail fetch failed."),
                real_external_call_executed=True,
                provider_result_received=True,
            )
        provider_detail = {
            "external_contact": dict(detail.get("external_contact") or {}),
            "follow_user": [dict(item or {}) for item in list(detail.get("follow_user") or []) if isinstance(item, dict)],
        }
        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "errcode": 0,
                "provider_detail_present": bool(provider_detail["external_contact"]),
                "follow_user_count": len(provider_detail["follow_user"]),
                "real_external_call_executed": True,
                "provider_result_received": True,
            },
            provider_result=provider_detail,
            real_external_call_executed=True,
            provider_result_received=True,
        )

    @staticmethod
    def _execution_gate_error(
        job: ExternalEffectJob,
        *,
        payload: dict[str, Any],
        external_userid: str,
    ) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != WECOM_EXTERNAL_CONTACT_DETAIL_FETCH:
            return "unsupported_effect_type"
        if job.target_type != "external_user" or not external_userid or job.target_id != external_userid:
            return "target_mismatch"
        if job.operation != "get_external_contact_detail":
            return "operation_not_allowed"
        return _configured_wecom_provider_gate(job)


class WeChatPaymentAdapter:
    def __init__(self, client_factory=None, refund_result_sync=None, refund_failure_sync=None) -> None:
        self._client_factory = client_factory
        self._refund_result_sync = refund_result_sync
        self._refund_failure_sync = refund_failure_sync

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        request_payload = dict(payload.get("request_payload") or {})
        request_summary = self._request_summary(job, payload, request_payload)
        out_refund_no = str(request_payload.get("out_refund_no") or payload.get("out_refund_no") or job.target_id or "").strip()
        gate_error = self._execution_gate_error(job, payload, request_payload)
        if gate_error:
            sync_result = self._mark_refund_failed(
                out_refund_no,
                error_code=gate_error,
                error_message="WeChat payment refund execution is blocked by external effect gates.",
                response_payload={"blocked": True, "execution_gate": gate_error, "real_external_call_executed": False},
            )
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode=job.execution_mode or "execute",
                request_summary=request_summary,
                response_summary={
                    "blocked": True,
                    "execution_gate": gate_error,
                    "real_external_call_executed": False,
                    "wechat_refund_executed": False,
                    "refund_failure_synced": bool(sync_result.get("ok")),
                },
                error_code=gate_error,
                error_message="WeChat payment refund execution is blocked by external effect gates.",
                real_external_call_executed=False,
            )

        try:
            provider_payload = self._build_client().create_refund(request_payload)
        except Exception as exc:
            return self._failure_result(exc, request_summary=request_summary, out_refund_no=out_refund_no)

        refund_payload = {
            **dict(provider_payload or {}),
            "out_trade_no": str(payload.get("out_trade_no") or request_payload.get("out_trade_no") or ""),
            "transaction_id": str(payload.get("transaction_id") or request_payload.get("transaction_id") or ""),
            "out_refund_no": str((provider_payload or {}).get("out_refund_no") or out_refund_no),
            "refund_status": str((provider_payload or {}).get("status") or (provider_payload or {}).get("refund_status") or "PROCESSING"),
            "amount": dict(request_payload.get("amount") or {}),
        }
        try:
            sync_result = self._apply_refund_result(refund_payload)
        except Exception as exc:
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={
                    "real_external_call_executed": True,
                    "wechat_refund_executed": True,
                    "refund_result_sync_failed": True,
                    "provider_status": str(refund_payload.get("refund_status") or ""),
                    "refund_id_present": bool(str(refund_payload.get("refund_id") or "").strip()),
                },
                error_code="network_error",
                error_message=_safe_error_message(f"wechat refund created but local result sync failed: {exc}"),
                real_external_call_executed=True,
            )

        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "real_external_call_executed": True,
                "wechat_refund_executed": True,
                "refund_result_synced": True,
                "provider_status": str(refund_payload.get("refund_status") or ""),
                "refund_id_present": bool(str(refund_payload.get("refund_id") or "").strip()),
                "order_refund_status": str(sync_result.get("order_refund_status") or "") if isinstance(sync_result, dict) else "",
            },
            real_external_call_executed=True,
            provider_result_received=True,
        )

    def _request_summary(self, job: ExternalEffectJob, payload: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
        amount = request_payload.get("amount") if isinstance(request_payload.get("amount"), dict) else {}
        return {
            "effect_type": job.effect_type,
            "operation": job.operation,
            "target_type": job.target_type,
            "target_id": job.target_id,
            "out_trade_no": str(payload.get("out_trade_no") or request_payload.get("out_trade_no") or ""),
            "out_refund_no": str(request_payload.get("out_refund_no") or payload.get("out_refund_no") or ""),
            "transaction_id_present": bool(str(payload.get("transaction_id") or request_payload.get("transaction_id") or "").strip()),
            "refund_amount_total": _safe_int(amount.get("refund")),
            "order_amount_total": _safe_int(amount.get("total")),
            "notify_url_present": bool(str(request_payload.get("notify_url") or "").strip()),
        }

    def _execution_gate_error(self, job: ExternalEffectJob, payload: dict[str, Any], request_payload: dict[str, Any]) -> str:
        if job.execution_mode in {"disabled", "shadow", "plan_only", "execute_dryrun"}:
            return "shadow_only"
        if job.effect_type != PAYMENT_WECHAT_REFUND_REQUEST or job.operation != "refund_request":
            return "unsupported_effect_type"
        if not _enabled("AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE"):
            return "payment_execution_disabled"
        allowed_types = _csv_env("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")
        if job.effect_type not in allowed_types:
            return "effect_type_not_allowed"
        out_refund_no = str(request_payload.get("out_refund_no") or payload.get("out_refund_no") or "").strip()
        if not out_refund_no or out_refund_no != str(job.target_id or "").strip():
            return "target_mismatch"
        if not str(request_payload.get("transaction_id") or payload.get("transaction_id") or "").strip():
            return "transaction_id_missing"
        amount = request_payload.get("amount") if isinstance(request_payload.get("amount"), dict) else {}
        try:
            refund_amount = int(amount.get("refund") or 0)
            order_amount = int(amount.get("total") or 0)
        except (TypeError, ValueError):
            return "payload_invalid"
        if refund_amount <= 0 or order_amount <= 0 or refund_amount > order_amount:
            return "payload_invalid"
        return ""

    def _build_client(self):
        if self._client_factory is None:
            raise RuntimeError("wechat_pay_adapter_composition_missing")
        return self._client_factory()

    def _apply_refund_result(self, refund_payload: dict[str, Any]) -> dict[str, Any]:
        if self._refund_result_sync is None:
            raise RuntimeError("refund_result_sync_composition_missing")
        return dict(self._refund_result_sync(refund_payload) or {})

    def _mark_refund_failed(self, out_refund_no: str, *, error_code: str, error_message: str, response_payload: dict[str, Any]) -> dict[str, Any]:
        if not out_refund_no:
            return {"ok": False, "reason": "out_refund_no_missing"}
        try:
            if self._refund_failure_sync is None:
                return {"ok": False, "reason": "refund_failure_sync_composition_missing"}
            return dict(
                self._refund_failure_sync(
                    out_refund_no,
                    error_code=error_code,
                    error_message=error_message,
                    response_payload=response_payload,
                )
                or {}
            )
        except Exception as exc:
            return {"ok": False, "reason": "refund_failure_sync_failed", "error": _safe_error_message(exc, limit=200)}

    def _failure_result(self, exc: Exception, *, request_summary: dict[str, Any], out_refund_no: str) -> ExternalEffectDispatchResult:
        status_code = getattr(exc, "status_code", None)
        provider_payload = dict(getattr(exc, "payload", {}) or {})
        business_rejection = classify_refund_business_rejection(provider_payload)
        error_message = _safe_error_message(exc)
        if status_code is None and ("required" in error_message or "failed to load WeChat Pay" in error_message):
            error_code = "config_missing"
            real_external_call_executed = False
        else:
            error_code = http_error_code(status_code)
            real_external_call_executed = True
        provider_result_received = status_code is not None or bool(provider_payload)
        retryable = error_code in {"network_error", "timeout", "http_408", "http_429", "http_5xx"}
        sync_result: dict[str, Any] = {}
        if not retryable:
            sync_result = self._mark_refund_failed(
                out_refund_no,
                error_code=error_code,
                error_message=error_message,
                response_payload={
                    "provider_payload": provider_payload,
                    "real_external_call_executed": real_external_call_executed,
                    **business_rejection,
                },
            )
        return ExternalEffectDispatchResult(
            status="failed_retryable" if retryable else "failed_terminal",
            adapter_mode="execute",
            request_summary=request_summary,
            response_summary={
                "status_code": status_code,
                "provider_payload_present": bool(provider_payload),
                "real_external_call_executed": real_external_call_executed,
                "wechat_refund_executed": False,
                "refund_failure_synced": bool(sync_result.get("ok")) if sync_result else False,
                "provider_result_received": provider_result_received,
                **business_rejection,
            },
            error_code=error_code,
            error_message=error_message,
            real_external_call_executed=real_external_call_executed,
            provider_result_received=provider_result_received,
        )


class ExternalEffectAdapterRegistry:
    def __init__(self, adapters: dict[str, ExternalEffectAdapter] | None = None) -> None:
        self._adapters: dict[str, ExternalEffectAdapter] = adapters or {
            "outbound_webhook": WebhookAdapter(),
            "webhook": WebhookAdapter(),
            "wechat_payment": WeChatPaymentAdapter(),
            "wecom_private_message": WeComPrivateMessageAdapter(),
            "wecom_group_message": WeComGroupMessageExternalEffectAdapter(),
            "wecom_welcome_message": WeComWelcomeMessageAdapter(),
            "wecom_tag": WeComContactTagAdapter(),
            "wecom_profile": WeComProfileUpdateAdapter(),
        }
        self._disabled = DisabledAdapter()

    def get(self, adapter_name: str) -> ExternalEffectAdapter:
        return self._adapters.get(str(adapter_name or "").strip(), self._disabled)


DEFAULT_ADAPTER_REGISTRY = ExternalEffectAdapterRegistry()
