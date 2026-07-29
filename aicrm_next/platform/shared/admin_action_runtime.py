from __future__ import annotations

from typing import Any, Callable

from fastapi import Request


def ensure_admin_action_token() -> str:
    """Compatibility placeholder; route-bound grants are emitted by the shell."""

    return ""


def admin_action_token_bundle(request: Request) -> dict[str, str]:
    builder = getattr(request.app.state, "admin_action_token_bundle_builder", None)
    if not callable(builder):
        return {}
    payload = builder(request)
    return dict(payload or {}) if isinstance(payload, dict) else {}


def validate_admin_action_token(token: str, *, request: Request | None = None) -> str:
    normalized_token = str(token or "").strip()
    if request is not None and _non_human_principal(request):
        return ""
    if not normalized_token:
        return "缺少 admin_action_token"
    if request is None:
        return "admin auth context is required"
    validator: Callable[[Request, str], Any] | None = getattr(
        request.app.state,
        "admin_action_token_validator",
        None,
    )
    if not callable(validator):
        return "admin auth context is required"
    result = validator(request, normalized_token)
    if bool(getattr(result, "ok", False)):
        return ""
    return (
        "admin_action_token 已过期"
        if str(getattr(result, "error", "") or "") == "expired"
        else "admin_action_token 无效或与当前动作不匹配"
    )


def _non_human_principal(request: Request) -> bool:
    context = getattr(request.state, "auth_context", None)
    principal_type = getattr(context, "principal_type", None)
    value = str(getattr(principal_type, "value", principal_type) or "").strip().lower()
    return bool(value and value != "human")
