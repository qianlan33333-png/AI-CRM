from __future__ import annotations

from copy import deepcopy
from typing import Any

from aicrm_next.platform.shared.errors import ContractError


AGENT_OUTPUT_ROUTE_FAMILY = "/api/admin/automation-conversion/agent-outputs*"
MAX_AGENT_OUTPUT_PAGE_SIZE = 100
ALLOWED_AGENT_OUTPUT_TYPES = {"reply_draft", "route_decision", "summary", "metadata"}
ALLOWED_AGENT_OUTPUT_STATUSES = {"draft", "pending_review", "applied", "rejected", "failed"}
ALLOWED_AGENT_OUTPUT_VISIBILITY = {"masked", "console"}
DANGEROUS_AGENT_OUTPUT_FIELDS = {
    "agent_run_execution",
    "run_execution",
    "workflow_execution",
    "task_execution",
    "execute",
    "execution",
    "export",
    "download",
    "file_download",
    "file_export",
    "generate",
    "generation",
    "llm",
    "deepseek",
    "openclaw",
    "mcp",
    "external_call",
    "send",
    "wecom",
    "timer",
    "outbound",
    "production_owner",
    "fallback_removal",
}


def agent_output_side_effect_safety() -> dict[str, bool]:
    return {
        "real_external_call_executed": False,
        "real_agent_run_executed": False,
        "real_agent_output_generated": False,
        "real_export_job_created": False,
        "real_file_download_executed": False,
        "real_llm_call_executed": False,
        "real_deepseek_call_executed": False,
        "real_openclaw_call_executed": False,
        "real_mcp_call_executed": False,
        "real_wecom_call_executed": False,
        "real_timer_executed": False,
        "real_outbound_send_executed": False,
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, field: str) -> int:
    try:
        number = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field} must be an integer") from exc
    if number < minimum:
        raise ContractError(f"{field} must be at least {minimum}")
    if number > maximum:
        raise ContractError(f"{field} must be at most {maximum}")
    return number


def _optional_float(value: Any, *, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field} must be a number") from exc


def _walk_keys(value: Any, *, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            full = f"{prefix}.{key_text}" if prefix else key_text
            keys.append(full)
            keys.extend(_walk_keys(child, prefix=full))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            keys.extend(_walk_keys(child, prefix=f"{prefix}[{index}]"))
    return keys


def reject_dangerous_agent_output_fields(payload: dict[str, Any]) -> None:
    for key_path in _walk_keys(payload):
        normalized = key_path.lower().replace("-", "_")
        for field in DANGEROUS_AGENT_OUTPUT_FIELDS:
            if field in normalized:
                raise ContractError(f"dangerous agent output field is not allowed: {key_path}")


def normalize_agent_output_filters(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    source = deepcopy(filters or {})
    reject_dangerous_agent_output_fields(source)
    page = _bounded_int(source.get("page"), default=1, minimum=1, maximum=10000, field="page")
    page_size = _bounded_int(
        source.get("page_size", source.get("limit")),
        default=50,
        minimum=1,
        maximum=MAX_AGENT_OUTPUT_PAGE_SIZE,
        field="page_size",
    )
    min_confidence = _optional_float(source.get("min_confidence"), field="min_confidence")
    max_confidence = _optional_float(source.get("max_confidence"), field="max_confidence")
    if min_confidence is not None and not 0 <= min_confidence <= 1:
        raise ContractError("min_confidence must be between 0 and 1")
    if max_confidence is not None and not 0 <= max_confidence <= 1:
        raise ContractError("max_confidence must be between 0 and 1")
    if min_confidence is not None and max_confidence is not None and min_confidence > max_confidence:
        raise ContractError("min_confidence must be less than or equal to max_confidence")

    output_type = _text(source.get("output_type")).lower()
    if output_type and output_type not in ALLOWED_AGENT_OUTPUT_TYPES:
        raise ContractError(f"output_type must be one of: {', '.join(sorted(ALLOWED_AGENT_OUTPUT_TYPES))}")
    applied_status = _text(source.get("applied_status")).lower()
    if applied_status and applied_status not in ALLOWED_AGENT_OUTPUT_STATUSES:
        raise ContractError(f"applied_status must be one of: {', '.join(sorted(ALLOWED_AGENT_OUTPUT_STATUSES))}")

    visibility = _text(source.get("visibility") or "masked").lower()
    if visibility not in ALLOWED_AGENT_OUTPUT_VISIBILITY:
        raise ContractError("visibility must be masked or console")

    return {
        "page": page,
        "page_size": page_size,
        "offset": (page - 1) * page_size,
        "request_id": _text(source.get("request_id")),
        "unionid": _text(source.get("unionid")),
        "userid": _text(source.get("userid")),
        "agent_code": _text(source.get("agent_code")),
        "output_type": output_type,
        "applied_status": applied_status,
        "min_confidence": min_confidence,
        "max_confidence": max_confidence,
        "has_error": source.get("has_error"),
        "visibility": visibility,
    }


def _mask_identifier(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    if len(text) <= 4:
        return "***"
    return f"***{text[-4:]}"


def agent_output_projection(output: dict[str, Any], *, visibility: str = "masked") -> dict[str, Any]:
    item = deepcopy(output or {})
    normalized_visibility = _text(visibility or "masked").lower()
    if normalized_visibility not in ALLOWED_AGENT_OUTPUT_VISIBILITY:
        raise ContractError("visibility must be masked or console")
    unionid = _text(item.get("unionid"))
    userid = _text(item.get("userid"))
    return {
        "id": _text(item.get("id") or item.get("output_id")),
        "output_id": _text(item.get("output_id") or item.get("id")),
        "run_id": _text(item.get("run_id")),
        "request_id": _text(item.get("request_id")),
        "userid": userid if normalized_visibility == "console" else _mask_identifier(userid),
        "unionid": unionid if normalized_visibility == "console" else _mask_identifier(unionid),
        "agent_code": _text(item.get("agent_code")),
        "output_type": _text(item.get("output_type") or "metadata"),
        "rendered_output_text": _text(item.get("rendered_output_text")),
        "target_agent_code": _text(item.get("target_agent_code")),
        "target_pool": _text(item.get("target_pool")),
        "confidence": float(item.get("confidence") or 0),
        "reason": _text(item.get("reason")),
        "need_human_review": bool(item.get("need_human_review", False)),
        "applied_status": _text(item.get("applied_status") or "draft"),
        "error_code": _text(item.get("error_code")),
        "error_message": _text(item.get("error_message")),
        "created_at": _text(item.get("created_at")),
        "visibility": normalized_visibility,
    }


def agent_output_run_projection(output: dict[str, Any], *, visibility: str = "masked") -> dict[str, Any]:
    projected = agent_output_projection(output, visibility=visibility)
    return {
        "run_id": projected["run_id"],
        "request_id": projected["request_id"],
        "agent_code": projected["agent_code"],
        "source_status": "fixture_local_contract",
        "execution_enabled": False,
        "generation_enabled": False,
        "replay_enabled": False,
        "orchestration_enabled": False,
    }
