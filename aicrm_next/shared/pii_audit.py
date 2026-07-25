from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .route_policy import RoutePolicy
from .runtime import production_environment
from .runtime_settings import managed_runtime_setting
from .sensitive_data import redact_sensitive_text, stable_hmac_identifier


LOGGER = logging.getLogger(__name__)
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DEFAULT_PURPOSES = {
    "customer": "customer_record_access",
    "sensitive": "sensitive_record_access",
    "financial": "financial_record_access",
}
PII_LEVELS = frozenset(_DEFAULT_PURPOSES)
_EXPORT_ROUTE_NAMES = {
    "class_user_management_export",
    "create_admin_export",
    "export_questionnaire",
    "export_radar_link_events",
    "export_wechat_admin_orders",
    "get_admin_export",
    "user_ops_export_preview",
    "user_ops_export_stub",
}
_REPAIR_ROUTE_NAMES = {
    "api_admin_jobs_order_identity_repair_run",
    "repair_entry",
}
_PREFIX_RULES = (
    ("/api/identity", "identity_resolution", True),
    ("/api/messages", "message_content_access", True),
    ("/api/archive", "message_archive_access", True),
    ("/api/admin/questionnaires", "questionnaire_response_access", True),
    ("/api/h5/questionnaires", "questionnaire_self_service_access", False),
    ("/api/admin/radar-links", "radar_event_access", True),
)


@dataclass(frozen=True)
class PiiAuditRule:
    purpose: str
    fail_closed: bool


@dataclass(frozen=True)
class PiiAuditEvent:
    actor_type: str
    actor_fingerprint: str
    purpose: str
    policy_scope: str
    pii_level: str
    result_count: int
    route_name: str
    status_code: int
    request_id: str
    resource_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_type": self.actor_type,
            "actor_fingerprint": self.actor_fingerprint,
            "purpose": self.purpose,
            "policy_scope": self.policy_scope,
            "pii_level": self.pii_level,
            "result_count": self.result_count,
            "route_name": self.route_name,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "resource_fingerprint": self.resource_fingerprint,
        }


class PiiAuditRepository(Protocol):
    def record_pii_access(self, event: PiiAuditEvent) -> None: ...


def pii_audit_enabled() -> bool:
    configured = managed_runtime_setting("AICRM_PII_AUDIT_ENABLED").lower()
    if configured in _TRUE_VALUES:
        return True
    if configured in _FALSE_VALUES:
        return production_environment()
    return production_environment()


def pii_audit_rule(policy: RoutePolicy) -> PiiAuditRule:
    if policy.route_name in _EXPORT_ROUTE_NAMES:
        return PiiAuditRule(purpose="pii_export", fail_closed=True)
    if policy.route_name in _REPAIR_ROUTE_NAMES:
        return PiiAuditRule(purpose="identity_forced_repair", fail_closed=True)
    for prefix, purpose, fail_closed in _PREFIX_RULES:
        if policy.path.startswith(prefix):
            return PiiAuditRule(purpose=purpose, fail_closed=fail_closed)
    return PiiAuditRule(
        purpose=_DEFAULT_PURPOSES.get(policy.pii_level, "pii_record_access"),
        fail_closed=False,
    )


def set_pii_audit_result_count(request: Request, count: Any) -> None:
    try:
        normalized = max(0, int(count or 0))
    except (TypeError, ValueError):
        normalized = 0
    request.state.pii_result_count = normalized


def infer_pii_result_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    export_download = payload.get("export_download")
    if isinstance(export_download, dict) and isinstance(export_download.get("rows"), list):
        return len(export_download["rows"])
    for key in ("items", "rows", "records", "payments", "refunds"):
        if isinstance(payload.get(key), list):
            return len(payload[key])
    for key in ("count", "total", "result_count"):
        try:
            if payload.get(key) is not None:
                return max(0, int(payload[key]))
        except (TypeError, ValueError):
            continue
    return 0


def _request_id(request: Request, *, fingerprint_secret: bytes) -> str:
    candidate = str(request.headers.get("x-request-id") or "").strip()
    if candidate and _SAFE_REQUEST_ID.fullmatch(candidate):
        if redact_sensitive_text(candidate) == candidate:
            return candidate
    if candidate:
        return stable_hmac_identifier(candidate, secret=fingerprint_secret, namespace="request-id")
    return uuid4().hex


def _event(
    *,
    request: Request,
    response: Response,
    policy: RoutePolicy,
    fingerprint_secret: bytes,
) -> PiiAuditEvent:
    actor_type = str(getattr(request.state, "pii_actor_type", "anonymous") or "anonymous").strip()
    actor_id = str(getattr(request.state, "pii_actor_id", "anonymous") or "anonymous").strip()
    policy_scope = str(getattr(request.state, "pii_policy_scope", policy.access_scope) or policy.access_scope).strip()
    resource_material = json.dumps(
        {
            "method": str(request.method or "").upper(),
            "path": str(request.url.path or ""),
            "query": sorted((str(key), str(value)) for key, value in request.query_params.multi_items()),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    result_count = getattr(request.state, "pii_result_count", 0)
    try:
        normalized_result_count = max(0, int(result_count or 0))
    except (TypeError, ValueError):
        normalized_result_count = 0
    rule = pii_audit_rule(policy)
    return PiiAuditEvent(
        actor_type=actor_type,
        actor_fingerprint=stable_hmac_identifier(actor_id, secret=fingerprint_secret, namespace=f"actor:{actor_type}"),
        purpose=rule.purpose,
        policy_scope=policy_scope,
        pii_level=policy.pii_level,
        result_count=normalized_result_count,
        route_name=policy.route_name,
        status_code=int(response.status_code),
        request_id=_request_id(request, fingerprint_secret=fingerprint_secret),
        resource_fingerprint=stable_hmac_identifier(
            resource_material,
            secret=fingerprint_secret,
            namespace=f"resource:{policy.route_name}",
        ),
    )


def apply_pii_audit(
    *,
    request: Request,
    response: Response,
    repository: PiiAuditRepository,
    fingerprint_secret: bytes,
) -> Response:
    policy = getattr(request.state, "route_policy", None)
    if request.method.upper() == "OPTIONS" or not isinstance(policy, RoutePolicy) or policy.pii_level not in PII_LEVELS:
        return response
    rule = pii_audit_rule(policy)
    try:
        repository.record_pii_access(
            _event(
                request=request,
                response=response,
                policy=policy,
                fingerprint_secret=fingerprint_secret,
            )
        )
    except Exception as exc:
        LOGGER.warning(
            "PII audit write failed route=%s error_type=%s",
            policy.route_name,
            type(exc).__name__,
        )
        if rule.fail_closed and int(response.status_code) < 400:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "pii_audit_unavailable",
                    "route_owner": "ai_crm_next",
                    "real_external_call_executed": False,
                },
                status_code=503,
            )
    return response
