from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.runtime import production_environment, raw_database_url


CLOUD_CAMPAIGN_RUN_DUE_PATH = "/api/admin/cloud-orchestrator/campaigns/run-due"
CLOUD_CAMPAIGN_RUN_DUE_PREVIEW_PATH = "/api/admin/cloud-orchestrator/campaigns/run-due/preview"

INTERNAL_TIMER_PATHS = frozenset(
    {
        CLOUD_CAMPAIGN_RUN_DUE_PATH,
        CLOUD_CAMPAIGN_RUN_DUE_PREVIEW_PATH,
    }
)
RUN_DUE_EXECUTION_PATHS = frozenset({CLOUD_CAMPAIGN_RUN_DUE_PATH})
PREVIEW_PATHS = frozenset({CLOUD_CAMPAIGN_RUN_DUE_PREVIEW_PATH})

CAMPAIGN_ALLOWLIST_FIELDS = ("allow_campaign_ids",)

_SAFE_LOCAL_DB_SENTINELS = ("127.0.0.1:1", "localhost:1")

_GUARD_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-Automation-Runtime-Executed": "false",
    "X-AICRM-Campaign-Runtime-Executed": "false",
    "X-AICRM-WeCom-Send-Executed": "false",
}


def parse_truthy(value: Any) -> bool:
    if value is True:
        return True
    if value in (False, None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_production_runtime() -> bool:
    if production_environment():
        return True
    database_url = raw_database_url()
    if not database_url:
        return False
    return not any(sentinel in database_url for sentinel in _SAFE_LOCAL_DB_SENTINELS)


def scheduled_safe_mode_requested(payload: dict[str, Any]) -> bool:
    return parse_truthy(payload.get("scheduled_safe_mode"))


def has_allowlist_for_path(path: str, payload: dict[str, Any]) -> bool:
    return any(_has_nonempty_value(payload.get(field)) for field in _allowlist_fields(path))


def build_scheduled_safe_mode_response(path: str, payload: dict[str, Any], *, idle: bool) -> JSONResponse:
    if idle:
        body = {
            "ok": True,
            "status": "idle",
            "scheduled_safe_mode": True,
            "side_effect_executed": False,
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "automation_runtime_executed": False,
            "campaign_runtime_executed": False,
            "wecom_send_executed": False,
            "preview": _scheduled_preview(path, payload, candidate_status="explicit_none"),
        }
        return _json_response(body, status_code=200)

    body = {
        "ok": True,
        "status": "blocked_not_executed",
        "scheduled_safe_mode": True,
        "manual_action_required": True,
        "error_code": "active_automation_due_candidates_require_allowlist",
        "side_effect_executed": False,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "automation_runtime_executed": False,
        "campaign_runtime_executed": False,
        "wecom_send_executed": False,
        "preview": _scheduled_preview(path, payload, candidate_status=_candidate_status(payload)),
    }
    return _json_response(body, status_code=409)


def build_allowlist_required_response(path: str) -> JSONResponse:
    is_campaign = path == CLOUD_CAMPAIGN_RUN_DUE_PATH
    error = "campaign_run_due_allowlist_required" if is_campaign else "automation_run_due_allowlist_required"
    body = {
        "ok": False,
        "error": error,
        "error_code": error,
        "side_effect_executed": False,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "automation_runtime_executed": False,
        "campaign_runtime_executed": False,
        "wecom_send_executed": False,
        "required_allowlists": list(_allowlist_fields(path)),
        "preflight_summary": {
            "allowlist_present": False,
            "external_call_allowed": False,
        },
    }
    return _json_response(body, status_code=409)


def maybe_guard_internal_run_due_request(
    *,
    request: Request,
    payload: dict[str, Any] | None,
    source_route: str = "",
    route_kind: str = "",
) -> JSONResponse | None:
    path = source_route or request.url.path
    if request.method.upper() != "POST" or path not in INTERNAL_TIMER_PATHS:
        return None

    payload = dict(payload or {})
    if _is_dry_run_or_preview(request, payload, path):
        return None

    if path not in RUN_DUE_EXECUTION_PATHS or not is_production_runtime():
        return None

    if scheduled_safe_mode_requested(payload):
        if has_allowlist_for_path(path, payload):
            return None
        return build_scheduled_safe_mode_response(path, payload, idle=_explicit_due_count_is_zero(payload))

    if not has_allowlist_for_path(path, payload):
        return build_allowlist_required_response(path)
    return None


def _is_dry_run_or_preview(request: Request, payload: dict[str, Any], path: str) -> bool:
    if path in PREVIEW_PATHS or parse_truthy(payload.get("preview")):
        return True
    if parse_truthy(request.headers.get("x-aicrm-dry-run")):
        return True
    if parse_truthy(request.query_params.get("dry_run")):
        return True
    return parse_truthy(payload.get("dry_run"))


def _allowlist_fields(path: str) -> tuple[str, ...]:
    if path == CLOUD_CAMPAIGN_RUN_DUE_PATH:
        return CAMPAIGN_ALLOWLIST_FIELDS
    return ()


def _has_nonempty_value(value: Any) -> bool:
    if value in (None, "", [], (), {}, set()):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_nonempty_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_nonempty_value(item) for item in value)
    return True


def _explicit_due_count_is_zero(payload: dict[str, Any]) -> bool:
    for key in ("expected_due_count", "due_count", "candidate_count", "total_due_count"):
        if key not in payload:
            continue
        try:
            return int(payload.get(key) or 0) == 0
        except (TypeError, ValueError):
            return False
    candidates = payload.get("candidates")
    if isinstance(candidates, (list, tuple)):
        return len(candidates) == 0
    return False


def _candidate_status(payload: dict[str, Any]) -> str:
    if _explicit_due_count_is_zero(payload):
        return "explicit_none"
    for key in ("expected_due_count", "due_count", "candidate_count", "total_due_count"):
        if key in payload:
            return "explicit_present"
    if isinstance(payload.get("candidates"), (list, tuple)):
        return "explicit_present"
    return "unknown_guarded"


def _scheduled_preview(path: str, payload: dict[str, Any], *, candidate_status: str) -> dict[str, Any]:
    due_count = 0
    for key in ("expected_due_count", "due_count", "candidate_count", "total_due_count"):
        try:
            due_count = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            due_count = 0
        if key in payload:
            break
    return {
        "path": path,
        "route_kind": "cloud_campaign_run_due" if path == CLOUD_CAMPAIGN_RUN_DUE_PATH else "automation_jobs_run_due",
        "candidate_status": candidate_status,
        "due_count": due_count,
        "candidate_count": due_count,
        "source_status": "next_internal_run_due_guard",
        "real_external_call_executed": False,
    }


def _json_response(payload: dict[str, Any], *, status_code: int) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=_GUARD_HEADERS)
