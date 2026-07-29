from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from aicrm_next.platform.shared.errors import ContractError


AGENT_ROUTE_FAMILY = "/api/admin/automation-conversion/agents*"
MAX_AGENT_NAME_LENGTH = 160
MAX_JSON_LENGTH = 20000
ALLOWED_AGENT_STATUSES = {"draft", "disabled", "inactive", "pending"}
ALLOWED_AGENT_TYPES = {"metadata", "assistant", "reviewer", "classifier", "followup"}
DANGEROUS_AGENT_FIELDS = {
    "agent_run",
    "run_creation",
    "run_execution",
    "execute",
    "execution",
    "workflow_execution",
    "task_execution",
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


def agent_side_effect_safety() -> dict[str, bool]:
    return {
        "real_external_call_executed": False,
        "real_agent_run_executed": False,
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return slug or "agent"


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


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


def reject_dangerous_agent_fields(payload: dict[str, Any]) -> None:
    for key_path in _walk_keys(payload):
        normalized = key_path.lower().replace("-", "_")
        for field in DANGEROUS_AGENT_FIELDS:
            if field in normalized:
                raise ContractError(f"dangerous agent field is not allowed: {key_path}")


def _json_object(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be a JSON object")
    if _json_size(value) > MAX_JSON_LENGTH:
        raise ContractError(f"{field} payload is too large")
    reject_dangerous_agent_fields(value)
    return deepcopy(value)


def normalize_agent_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(payload or {})
    reject_dangerous_agent_fields(source)

    agent_name = _text(source.get("agent_name") or source.get("name"))
    if not agent_name:
        raise ContractError("agent_name is required")
    if len(agent_name) > MAX_AGENT_NAME_LENGTH:
        raise ContractError(f"agent_name must be at most {MAX_AGENT_NAME_LENGTH} characters")

    ints: dict[str, int] = {}
    for field in ("sort_order",):
        try:
            value = int(source.get(field) or 0)
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{field} must be an integer") from exc
        if value < 0:
            raise ContractError(f"{field} must be non-negative")
        ints[field] = value

    agent_type = _text(source.get("agent_type") or "metadata").lower()
    if agent_type not in ALLOWED_AGENT_TYPES:
        raise ContractError(f"agent type must be one of: {', '.join(sorted(ALLOWED_AGENT_TYPES))}")

    status = _text(source.get("status") or "draft").lower()
    if status not in ALLOWED_AGENT_STATUSES:
        raise ContractError(f"agent status must be one of: {', '.join(sorted(ALLOWED_AGENT_STATUSES))}")

    agent_code = _text(source.get("agent_code") or source.get("code")) or _slugify(agent_name)
    return {
        **ints,
        "agent_code": agent_code,
        "code": agent_code,
        "agent_name": agent_name,
        "name": agent_name,
        "agent_type": agent_type,
        "status": status,
        "metadata": _json_object(source.get("metadata"), field="metadata"),
        "config": _json_object(source.get("config"), field="config"),
        "enabled": False,
        "created_by": _text(source.get("operator") or source.get("created_by") or "system"),
        "updated_by": _text(source.get("operator") or source.get("updated_by") or "system"),
    }


def agent_projection(agent: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(agent or {})
    agent_id = int(item.get("id") or item.get("agent_id") or 0)
    code = _text(item.get("agent_code") or item.get("code")) or _slugify(_text(item.get("agent_name") or item.get("name")))
    name = _text(item.get("agent_name") or item.get("name"))
    now = utc_now_iso()
    return {
        "id": agent_id,
        "agent_id": agent_id,
        "agent_code": code,
        "code": code,
        "agent_name": name,
        "name": name,
        "agent_type": _text(item.get("agent_type") or "metadata"),
        "status": _text(item.get("status") or "draft"),
        "sort_order": int(item.get("sort_order") or 0),
        "metadata": deepcopy(item.get("metadata") or {}),
        "config": deepcopy(item.get("config") or {}),
        "enabled": bool(item.get("enabled", False)),
        "created_by": _text(item.get("created_by")),
        "updated_by": _text(item.get("updated_by")),
        "created_at": _text(item.get("created_at")) or now,
        "updated_at": _text(item.get("updated_at")) or now,
        "archived_at": _text(item.get("archived_at")),
    }
