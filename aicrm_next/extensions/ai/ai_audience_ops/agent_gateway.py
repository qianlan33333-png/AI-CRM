from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from aicrm_next.shared.runtime import pytest_environment
from aicrm_next.shared.runtime_settings import runtime_setting

from .repository import _text


RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_AI_AUDIENCE_AGENT_API_KEY",
        "AICRM_AI_AUDIENCE_AGENT_BASE_URL",
        "AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED",
        "AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT",
        "AICRM_AI_AUDIENCE_AGENT_MODE",
        "AICRM_AI_AUDIENCE_AGENT_MODEL",
        "AICRM_AI_AUDIENCE_AGENT_TIMEOUT_SECONDS",
        "AICRM_RUNTIME_V2_AGENT_API_KEY",
        "AICRM_RUNTIME_V2_AGENT_BASE_URL",
        "AICRM_RUNTIME_V2_AGENT_FAKE_ALLOWED",
        "AICRM_RUNTIME_V2_AGENT_FAKE_OUTPUT",
        "AICRM_RUNTIME_V2_AGENT_MODE",
        "AICRM_RUNTIME_V2_AGENT_MODEL",
        "AICRM_RUNTIME_V2_AGENT_TIMEOUT_SECONDS",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_EXECUTION_MODEL",
        "DEEPSEEK_TIMEOUT_SECONDS",
    }
)


@dataclass(frozen=True)
class AgentGatewayResult:
    ok: bool
    final_text: str = ""
    mode: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    request_summary: dict[str, Any] | None = None
    response_summary: dict[str, Any] | None = None
    error_code: str = ""
    error_message: str = ""
    external_call_executed: bool = False


def _truthy(value: str | None) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _agent_mode() -> str:
    return _text(_setting("AICRM_AI_AUDIENCE_AGENT_MODE") or _setting("AICRM_RUNTIME_V2_AGENT_MODE")).lower() or "disabled"


def _fake_allowed() -> bool:
    return (
        _truthy(_setting("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED"))
        or _truthy(_setting("AICRM_RUNTIME_V2_AGENT_FAKE_ALLOWED"))
        or pytest_environment()
    )


def _chat_completion_url(base_url: str) -> str:
    base = _text(base_url).rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _api_key() -> str:
    return _text(_setting("AICRM_AI_AUDIENCE_AGENT_API_KEY") or _setting("AICRM_RUNTIME_V2_AGENT_API_KEY") or _setting("DEEPSEEK_API_KEY"))


def _base_url() -> str:
    return _text(_setting("AICRM_AI_AUDIENCE_AGENT_BASE_URL") or _setting("AICRM_RUNTIME_V2_AGENT_BASE_URL") or _setting("DEEPSEEK_BASE_URL") or "https://api.deepseek.com")


def _model() -> str:
    return _text(_setting("AICRM_AI_AUDIENCE_AGENT_MODEL") or _setting("AICRM_RUNTIME_V2_AGENT_MODEL") or _setting("DEEPSEEK_EXECUTION_MODEL") or "deepseek-chat")


def _timeout() -> float:
    raw = _text(_setting("AICRM_AI_AUDIENCE_AGENT_TIMEOUT_SECONDS") or _setting("AICRM_RUNTIME_V2_AGENT_TIMEOUT_SECONDS") or _setting("DEEPSEEK_TIMEOUT_SECONDS") or "30")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 30.0


def _setting(key: str) -> str:
    return _text(runtime_setting(key, ""))


def generate_agent_reply(
    *,
    agent_code: str,
    role_prompt: str,
    task_prompt: str,
    variables: dict[str, Any],
    mock_output: str = "",
) -> AgentGatewayResult:
    mode = _agent_mode()
    request_summary = {
        "agent_code": _text(agent_code),
        "role_prompt_chars": len(_text(role_prompt)),
        "task_prompt_chars": len(_text(task_prompt)),
        "variable_keys": sorted(str(key) for key in variables.keys()),
    }
    if mode == "disabled":
        return AgentGatewayResult(ok=False, mode=mode, request_summary=request_summary, error_code="agent_runtime_disabled", error_message="AI Audience agent generation is disabled")
    if mode == "fake":
        if not _fake_allowed():
            return AgentGatewayResult(ok=False, mode=mode, request_summary=request_summary, error_code="agent_fake_mode_not_allowed", error_message="Fake AI Audience agent mode is not allowed outside tests")
        final = _text(
            mock_output
            or _setting("AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT")
            or _setting("AICRM_RUNTIME_V2_AGENT_FAKE_OUTPUT")
        )
        return AgentGatewayResult(
            ok=bool(final),
            final_text=final,
            mode=mode,
            provider="fake",
            model="fake-agent",
            request_summary=request_summary,
            response_summary={"content_chars": len(final), "fake": True},
            error_code="" if final else "agent_generation_empty",
            error_message="" if final else "Fake agent output is empty",
            external_call_executed=False,
        )
    if mode not in {"staging", "production"}:
        return AgentGatewayResult(ok=False, mode=mode, request_summary=request_summary, error_code="agent_runtime_mode_invalid", error_message=f"Unsupported AI Audience agent mode: {mode}")
    api_key = _api_key()
    url = _chat_completion_url(_base_url())
    model = _model()
    if not api_key or not url or not model:
        return AgentGatewayResult(ok=False, mode=mode, provider="deepseek", model=model, request_summary=request_summary, error_code="agent_gateway_config_missing", error_message="Agent gateway API key, base URL, or model is missing")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _text(role_prompt)},
            {"role": "user", "content": _text(task_prompt)},
        ],
        "temperature": 0.4,
    }
    started = time.perf_counter()
    try:
        req = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=_timeout()) as resp:
            body = resp.read().decode("utf-8")
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(body or "{}")
        choices = data.get("choices") if isinstance(data, dict) else []
        message = (choices[0] or {}).get("message") if choices else {}
        final = _text((message or {}).get("content"))
        return AgentGatewayResult(
            ok=bool(final),
            final_text=final,
            mode=mode,
            provider="deepseek",
            model=model,
            latency_ms=latency_ms,
            request_summary=request_summary,
            response_summary={"content_chars": len(final), "choice_count": len(choices or []), "usage": data.get("usage", {}) if isinstance(data, dict) else {}},
            error_code="" if final else "agent_generation_empty",
            error_message="" if final else "Agent gateway returned empty content",
            external_call_executed=True,
        )
    except HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return AgentGatewayResult(ok=False, mode=mode, provider="deepseek", model=model, latency_ms=latency_ms, request_summary=request_summary, response_summary={"http_status": exc.code}, error_code="agent_gateway_http_error", error_message=detail or str(exc), external_call_executed=True)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return AgentGatewayResult(ok=False, mode=mode, provider="deepseek", model=model, latency_ms=latency_ms, request_summary=request_summary, error_code="agent_gateway_call_failed", error_message=str(exc), external_call_executed=True)
