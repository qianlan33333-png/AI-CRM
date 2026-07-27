from __future__ import annotations

from copy import deepcopy
from typing import Any

from aicrm_next.platform.shared.errors import ContractError


AGENT_RUN_ROUTE_FAMILY = "/api/admin/automation-conversion/agent-runs*"
MAX_AGENT_RUN_PAGE_SIZE = 100
ALLOWED_AGENT_RUN_STATUSES = {"completed", "failed", "queued", "running", "cancelled"}
ALLOWED_AGENT_RUN_TRIGGERS = {"manual", "fixture", "system", "replay", "ai_audience_ops"}
ALLOWED_AGENT_RUN_VISIBILITY = {"masked", "console"}
DANGEROUS_AGENT_RUN_FIELDS = {
    "run_creation",
    "run_execution",
    "execute",
    "execution",
    "replay",
    "orchestration",
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


def agent_run_side_effect_safety() -> dict[str, bool]:
    return {
        "real_external_call_executed": False,
        "real_run_created": False,
        "real_run_executed": False,
        "real_replay_executed": False,
        "real_orchestration_executed": False,
        "real_agent_output_generated": False,
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


def reject_dangerous_agent_run_fields(payload: dict[str, Any]) -> None:
    for key_path in _walk_keys(payload):
        normalized = key_path.lower().replace("-", "_")
        for field in DANGEROUS_AGENT_RUN_FIELDS:
            if field in normalized:
                raise ContractError(f"dangerous agent run field is not allowed: {key_path}")


def normalize_agent_run_filters(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    source = deepcopy(filters or {})
    reject_dangerous_agent_run_fields(source)
    page = _bounded_int(source.get("page"), default=1, minimum=1, maximum=10000, field="page")
    page_size = _bounded_int(
        source.get("page_size", source.get("limit")),
        default=50,
        minimum=1,
        maximum=MAX_AGENT_RUN_PAGE_SIZE,
        field="page_size",
    )
    run_status = _text(source.get("run_status")).lower()
    if run_status and run_status not in ALLOWED_AGENT_RUN_STATUSES:
        raise ContractError(f"run_status must be one of: {', '.join(sorted(ALLOWED_AGENT_RUN_STATUSES))}")
    trigger_source = _text(source.get("trigger_source")).lower()
    if trigger_source and trigger_source not in ALLOWED_AGENT_RUN_TRIGGERS:
        raise ContractError(f"trigger_source must be one of: {', '.join(sorted(ALLOWED_AGENT_RUN_TRIGGERS))}")
    visibility = _text(source.get("visibility") or "masked").lower()
    if visibility not in ALLOWED_AGENT_RUN_VISIBILITY:
        raise ContractError("visibility must be masked or console")
    return {
        "page": page,
        "page_size": page_size,
        "offset": (page - 1) * page_size,
        "request_id": _text(source.get("request_id")),
        "run_id": _text(source.get("run_id")),
        "agent_code": _text(source.get("agent_code")),
        "run_status": run_status,
        "trigger_source": trigger_source,
        "unionid": _text(source.get("unionid")),
        "userid": _text(source.get("userid")),
        "started_after": _text(source.get("started_after")),
        "started_before": _text(source.get("started_before")),
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


def agent_run_projection(run: dict[str, Any], *, visibility: str = "masked") -> dict[str, Any]:
    item = deepcopy(run or {})
    normalized_visibility = _text(visibility or "masked").lower()
    if normalized_visibility not in ALLOWED_AGENT_RUN_VISIBILITY:
        raise ContractError("visibility must be masked or console")
    unionid = _text(item.get("unionid"))
    userid = _text(item.get("userid"))
    return {
        "id": _text(item.get("id") or item.get("run_id")),
        "run_id": _text(item.get("run_id") or item.get("id")),
        "request_id": _text(item.get("request_id")),
        "agent_code": _text(item.get("agent_code")),
        "run_status": _text(item.get("run_status") or "completed"),
        "trigger_source": _text(item.get("trigger_source") or "fixture"),
        "unionid": unionid if normalized_visibility == "console" else _mask_identifier(unionid),
        "userid": userid if normalized_visibility == "console" else _mask_identifier(userid),
        "started_at": _text(item.get("started_at")),
        "finished_at": _text(item.get("finished_at")),
        "duration_ms": int(item.get("duration_ms") or 0),
        "error_code": _text(item.get("error_code")),
        "error_message": _text(item.get("error_message")),
        "output_count": int(item.get("output_count") or 0),
        "metadata": deepcopy(item.get("metadata") or {}),
        "created_at": _text(item.get("created_at")),
        "updated_at": _text(item.get("updated_at")),
        "visibility": normalized_visibility,
    }
