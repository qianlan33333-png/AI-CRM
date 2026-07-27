from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from aicrm_next.shared.llm_output_guard import looks_like_prompt_output

from . import agent_gateway
from .repository import AudienceRepository, build_audience_repository, _json_dumps, _json_obj, _text

CONTENT_AGENT_GENERATED = "agent_generated"

_TEMPLATE_TOKEN_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
_SAFE_VARIABLE_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_PROMPT_LEAK_MARKERS = (
    "你将收到以下资料",
    "你的唯一任务是",
    "最终只输出",
    "不要解释",
    "不要输出 JSON",
    "系统说明",
    "可用认知依据",
)
_MISSING = object()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hash_text(value: str) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()[:24]


def _content_has_body(content: dict[str, Any]) -> bool:
    return bool(_text(content.get("content_text") or content.get("text")) or content.get("image_library_ids") or content.get("miniprogram_library_ids") or content.get("attachment_library_ids") or content.get("group_invite_library_ids") or content.get("attachments"))


def _fallback_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if _text(value):
        return {"content_text": _text(value)}
    return {}


def _stringify_template_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _text(value)


def _resolve_path(source: Any, path: str) -> Any:
    current = source
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return _MISSING
    return current


def _resolve_template_variable(name: str, variables: dict[str, Any]) -> Any:
    if "." not in name:
        payload = variables.get("payload") if isinstance(variables.get("payload"), dict) else {}
        resolved = _resolve_path(payload, name)
        if resolved is not _MISSING:
            return resolved
    return _resolve_path(variables, name)


def render_template_text(raw_text: str, variables: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    raw = _text(raw_text)
    matches = list(_TEMPLATE_TOKEN_RE.finditer(raw))
    if not matches:
        return raw, {"template_rendered": False, "template_variables_used": []}, ""
    missing: list[str] = []
    used: list[str] = []
    rendered_parts: list[str] = []
    cursor = 0
    for match in matches:
        rendered_parts.append(raw[cursor : match.start()])
        name = _text(match.group(1))
        if not _SAFE_VARIABLE_RE.fullmatch(name):
            missing.append(name or "<invalid>")
            rendered_parts.append("")
            cursor = match.end()
            continue
        value = _resolve_template_variable(name, variables)
        if value is _MISSING or value is None:
            missing.append(name)
            rendered_parts.append("")
            cursor = match.end()
            continue
        used.append(name)
        rendered_parts.append(_stringify_template_value(value))
        cursor = match.end()
    rendered_parts.append(raw[cursor:])
    if missing:
        return "", {"template_rendered": True, "unresolved_template": True, "missing_variables": sorted(set(missing)), "template_variables_used": sorted(set(used))}, "template_variable_missing"
    rendered = "".join(rendered_parts)
    if _TEMPLATE_TOKEN_RE.search(rendered):
        return "", {"template_rendered": True, "unresolved_template": True, "missing_variables": [], "template_variables_used": sorted(set(used))}, "template_variable_missing"
    return rendered, {"template_rendered": True, "template_variables_used": sorted(set(used))}, ""


def build_variables(*, package: dict[str, Any], member_event: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = member_event.get("payload_json") if isinstance(member_event.get("payload_json"), dict) else _json_obj(member_event.get("payload_json"))
    member = {
        "identity_type": _text(member_event.get("identity_type")),
        "identity_value": _text(member_event.get("identity_value")),
        "unionid": _text(member_event.get("unionid") or member_event.get("identity_value")),
        "external_userid": _text(member_event.get("unionid") or member_event.get("identity_value")),
        "mobile_hash": _text(member_event.get("mobile_hash")),
        "owner_userid": _text(member_event.get("owner_userid")),
        "payload": payload,
    }
    event = {
        "id": _as_int(member_event.get("id")),
        "event_type": _text(member_event.get("event_type")),
        "event_source_key": _text(member_event.get("event_source_key")),
        "occurred_at": _text(member_event.get("occurred_at")),
        "payload_json": payload,
    }
    audience = {
        "id": _as_int(package.get("id")),
        "package_id": _as_int(package.get("id")),
        "package_key": _text(package.get("package_key")),
        "name": _text(package.get("name")),
        "natural_language_definition": _text(package.get("natural_language_definition")),
    }
    answers = payload.get("answers") or payload.get("questionnaire_answers") or {}
    if isinstance(answers, list):
        normalized_answers: dict[str, Any] = {}
        for item in answers:
            if isinstance(item, dict):
                key = _text(item.get("question_code") or item.get("question_id") or item.get("name"))
                if key:
                    normalized_answers[key] = item.get("answer") or item.get("value") or item.get("text_value")
        answers = normalized_answers
    if not isinstance(answers, dict):
        answers = {}
    return {
        "audience": audience,
        "package": audience,
        "event": event,
        "member_event": event,
        "member": member,
        "payload": payload,
        "questionnaire": {"answers": answers},
        "recent_messages": payload.get("recent_messages") or [],
        "tags": payload.get("tags") or [],
        "context": dict(context or {}),
    }


def _render_prompt_text(raw: str, variables: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    rendered, diagnostics, reason = render_template_text(raw, variables)
    if reason:
        return "", diagnostics, "agent_prompt_variable_missing"
    return rendered, diagnostics, ""


def _looks_like_prompt(final_text: str, *, role_prompt: str, task_prompt: str) -> bool:
    return looks_like_prompt_output(final_text, role_prompt=role_prompt, task_prompt=task_prompt)


def _render_fallback(fallback: dict[str, Any], variables: dict[str, Any], reason: str) -> tuple[bool, dict[str, Any], str, dict[str, Any]]:
    if not _content_has_body(fallback):
        return False, {}, reason, {"fallback_used": False, "fallback_reason": reason}
    rendered_text, diagnostics, template_reason = render_template_text(_text(fallback.get("content_text") or fallback.get("text")), variables)
    if template_reason:
        return False, {}, "agent_fallback_variable_missing", {"fallback_used": False, "fallback_reason": reason, **diagnostics}
    return True, {"type": CONTENT_AGENT_GENERATED, "fallback": True, "content_text": rendered_text, "attachments": fallback}, "", {"fallback_used": True, "fallback_reason": reason, **diagnostics}


def _create_run(
    repository: AudienceRepository,
    *,
    agent_code: str,
    package: dict[str, Any],
    member_event: dict[str, Any],
    variables: dict[str, Any],
    prompt_version: int = 0,
) -> dict[str, Any]:
    run_id = f"ai_audience_agent_run_{uuid4().hex}"
    request_id = f"ai_audience_agent_request_{uuid4().hex}"
    package_id = _as_int(package.get("id"))
    member_event_id = _as_int(member_event.get("id"))
    trace_id = f"ai_audience:agent:package:{package_id}:member_event:{member_event_id}"
    return repository.create_agent_run(
        {
            "run_id": run_id,
            "request_id": request_id,
            "batch_id": "",
            "userid": "",
            "unionid": _text(member_event.get("unionid") or member_event.get("identity_value")),
            "agent_code": _text(agent_code),
            "agent_type": "ai_audience_ops",
            "provider": "",
            "input_snapshot_json": _json_dumps({"runtime_metadata": {"runtime_version": "ai_audience_ops", "package_id": package_id, "member_event_id": member_event_id}, "package": package, "member_event": member_event}),
            "variables_snapshot_json": _json_dumps(variables),
            "final_prompt_preview": "",
            "role_prompt_version": prompt_version,
            "task_prompt_version": prompt_version,
            "status": "running",
            "error_code": "",
            "error_message": "",
            "latency_ms": 0,
            "source": "ai_audience_ops",
            "parent_run_id": "",
            "replay_of_run_id": "",
            "trace_id": trace_id,
        }
    )


def _complete_run(repository: AudienceRepository, run: dict[str, Any], *, status: str, provider: str = "", latency_ms: int = 0, final_prompt_preview: str = "", error_code: str = "", error_message: str = "") -> dict[str, Any]:
    return repository.complete_agent_run(
        _as_int(run.get("id")),
        {
            "status": _text(status),
            "provider": _text(provider),
            "latency_ms": int(latency_ms or 0),
            "final_prompt_preview": _text(final_prompt_preview)[:500],
            "error_code": _text(error_code),
            "error_message": _text(error_message)[:1000],
        },
    )


def _log_llm_call(
    repository: AudienceRepository,
    *,
    run: dict[str, Any],
    agent_code: str,
    provider: str,
    model: str,
    status: str,
    latency_ms: int = 0,
    error_code: str = "",
    error_message: str = "",
    request_summary: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
    prompt_text: str = "",
) -> dict[str, Any]:
    return repository.log_agent_llm_call(
        {
            "run_id": _text(run.get("run_id")),
            "agent_code": _text(agent_code),
            "provider": _text(provider),
            "model": _text(model),
            "model_name": _text(model),
            "request_id": _text(run.get("request_id")),
            "prompt_hash": _hash_text(prompt_text),
            "request_summary": _json_dumps(request_summary or {}),
            "response_summary": _json_dumps(response_summary or {}),
            "status": _text(status),
            "latency_ms": int(latency_ms or 0),
            "error_code": _text(error_code),
            "error_message": _text(error_message)[:1000],
        }
    )


def _record_output(
    repository: AudienceRepository,
    *,
    run: dict[str, Any],
    agent_code: str,
    member_event: dict[str, Any],
    final_text: str,
    applied_status: str = "generated",
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    output_id = f"ai_audience_agent_output_{uuid4().hex}"
    normalized = {
        "runtime_version": "ai_audience_ops",
        "run_id": _text(run.get("run_id")),
        "package_id": _as_int(member_event.get("package_id")),
        "member_event_id": _as_int(member_event.get("id")),
        "content_chars": len(_text(final_text)),
    }
    return repository.record_agent_output(
        {
            "output_id": output_id,
            "run_id": _text(run.get("run_id")),
            "request_id": _text(run.get("request_id")),
            "userid": "",
            "unionid": _text(member_event.get("unionid") or member_event.get("identity_value")),
            "agent_code": _text(agent_code),
            "output_type": "reply_draft",
            "raw_output_text": _text(final_text),
            "normalized_output_json": _json_dumps(normalized),
            "rendered_output_text": _text(final_text),
            "target_agent_code": _text(agent_code),
            "target_pool": "ai_audience_ops",
            "confidence": 1,
            "reason": "",
            "need_human_review": False,
            "applied_status": _text(applied_status),
            "adopted_by": "",
            "adopted_action": "",
            "outcome_status": "",
            "outcome_value": "",
            "revision_of_output_id": "",
            "error_code": _text(error_code),
            "error_message": _text(error_message)[:1000],
        }
    )


def generate_member_event_copywriting(
    *,
    package: dict[str, Any],
    member_event: dict[str, Any],
    agent_code: str,
    fallback_content: dict[str, Any] | str | None = None,
    mock_output: str = "",
    context: dict[str, Any] | None = None,
    attachments: dict[str, Any] | None = None,
    repository: AudienceRepository | None = None,
) -> dict[str, Any]:
    repository = repository or build_audience_repository()
    agent_code = _text(agent_code)
    if not agent_code:
        return {"ok": False, "error": "agent_code_missing", "content": {}, "diagnostics": {"fallback_used": False}, "real_external_call_executed": False}

    prompt = repository.get_agent_prompt(agent_code)
    variables = build_variables(package=package, member_event=member_event, context=context)
    run = _create_run(repository, agent_code=agent_code, package=package, member_event=member_event, variables=variables, prompt_version=_as_int(prompt.get("published_version")))
    run_id = _text(run.get("run_id"))
    base_diag = {
        "agent_code": agent_code,
        "agent_run_id": run_id,
        "fallback_used": False,
        "questionnaire_answer_count": len(variables.get("questionnaire", {}).get("answers") or {}),
    }
    fallback = _fallback_content(fallback_content)
    if not prompt.get("role_prompt") or not prompt.get("task_prompt"):
        reason = "agent_published_prompt_missing"
        _complete_run(repository, run, status="failed", error_code=reason, error_message=reason)
        _log_llm_call(repository, run=run, agent_code=agent_code, provider="", model="", status="failed", error_code=reason, error_message=reason)
        ok, rendered, fallback_reason, fallback_diag = _render_fallback(fallback, variables, reason)
        if ok:
            output = _record_output(repository, run=run, agent_code=agent_code, member_event=member_event, final_text=_text(rendered.get("content_text")), applied_status="fallback", error_code=reason, error_message=reason)
            return {"ok": True, "content": rendered, "error": "", "diagnostics": {**base_diag, **fallback_diag, "agent_output_id": _text(output.get("output_id"))}, "real_external_call_executed": False}
        return {"ok": False, "content": {}, "error": fallback_reason, "diagnostics": {**base_diag, **fallback_diag, "render_failed": fallback_reason, "error_code": reason, "error_message": reason}, "real_external_call_executed": False}

    role_prompt, role_diag, role_reason = _render_prompt_text(_text(prompt.get("role_prompt")), variables)
    task_prompt, task_diag, task_reason = _render_prompt_text(_text(prompt.get("task_prompt")), variables)
    prompt_diag = {"role_prompt_template": role_diag, "task_prompt_template": task_diag}
    if role_reason or task_reason:
        reason = "agent_prompt_variable_missing"
        missing = sorted(set((role_diag.get("missing_variables") or []) + (task_diag.get("missing_variables") or [])))
        message = ",".join(missing)
        _complete_run(repository, run, status="failed", error_code=reason, error_message=message)
        _log_llm_call(repository, run=run, agent_code=agent_code, provider="", model="", status="failed", error_code=reason, error_message=message, prompt_text=f"{prompt.get('role_prompt')}\n{prompt.get('task_prompt')}")
        return {"ok": False, "content": {}, "error": reason, "diagnostics": {**base_diag, **prompt_diag, "missing_variables": missing, "render_failed": reason, "error_code": reason, "error_message": message}, "real_external_call_executed": False}

    gateway_result = agent_gateway.generate_agent_reply(
        agent_code=agent_code,
        role_prompt=role_prompt,
        task_prompt=task_prompt,
        variables=variables,
        mock_output=mock_output,
    )
    log = _log_llm_call(
        repository,
        run=run,
        agent_code=agent_code,
        provider=gateway_result.provider,
        model=gateway_result.model,
        status="completed" if gateway_result.ok else "failed",
        latency_ms=gateway_result.latency_ms,
        error_code=gateway_result.error_code,
        error_message=gateway_result.error_message,
        request_summary=gateway_result.request_summary,
        response_summary=gateway_result.response_summary,
        prompt_text=f"{role_prompt}\n{task_prompt}",
    )
    if not gateway_result.ok:
        reason = gateway_result.error_code or "agent_generation_failed"
        _complete_run(repository, run, status="failed", provider=gateway_result.provider, latency_ms=gateway_result.latency_ms, final_prompt_preview=task_prompt, error_code=reason, error_message=gateway_result.error_message)
        ok, rendered, fallback_reason, fallback_diag = _render_fallback(fallback, variables, reason)
        if ok:
            output = _record_output(repository, run=run, agent_code=agent_code, member_event=member_event, final_text=_text(rendered.get("content_text")), applied_status="fallback", error_code=reason, error_message=gateway_result.error_message)
            return {"ok": True, "content": rendered, "error": "", "diagnostics": {**base_diag, **fallback_diag, "agent_output_id": _text(output.get("output_id")), "llm_call_logged": bool(log), "error_code": reason, "error_message": gateway_result.error_message}, "real_external_call_executed": gateway_result.external_call_executed}
        return {"ok": False, "content": {}, "error": fallback_reason, "diagnostics": {**base_diag, **fallback_diag, "llm_call_logged": bool(log), "render_failed": fallback_reason, "error_code": reason, "error_message": gateway_result.error_message}, "real_external_call_executed": gateway_result.external_call_executed}

    final_text = _text(gateway_result.final_text)
    if not final_text:
        reason = "agent_generation_empty"
        _complete_run(repository, run, status="failed", provider=gateway_result.provider, latency_ms=gateway_result.latency_ms, final_prompt_preview=task_prompt, error_code=reason, error_message=reason)
        return {"ok": False, "content": {}, "error": reason, "diagnostics": {**base_diag, "llm_call_logged": bool(log), "render_failed": reason, "error_code": reason, "error_message": reason}, "real_external_call_executed": gateway_result.external_call_executed}
    if _looks_like_prompt(final_text, role_prompt=_text(prompt.get("role_prompt")), task_prompt=_text(prompt.get("task_prompt"))):
        reason = "agent_output_looks_like_prompt"
        _complete_run(repository, run, status="failed", provider=gateway_result.provider, latency_ms=gateway_result.latency_ms, final_prompt_preview=task_prompt, error_code=reason, error_message=reason)
        return {"ok": False, "content": {}, "error": reason, "diagnostics": {**base_diag, "llm_call_logged": bool(log), "render_failed": reason, "error_code": reason, "error_message": reason}, "real_external_call_executed": gateway_result.external_call_executed}

    completed = _complete_run(repository, run, status="completed", provider=gateway_result.provider, latency_ms=gateway_result.latency_ms, final_prompt_preview=task_prompt)
    output = _record_output(repository, run=completed or run, agent_code=agent_code, member_event=member_event, final_text=final_text, applied_status="generated")
    rendered: dict[str, Any] = {"type": CONTENT_AGENT_GENERATED, "agent_code": agent_code, "content_text": final_text, "variables": variables}
    attachment_payload = dict(attachments or {})
    if attachment_payload:
        rendered["attachments"] = attachment_payload
    return {
        "ok": True,
        "content": rendered,
        "error": "",
        "diagnostics": {
            **base_diag,
            "agent_output_id": _text(output.get("output_id")),
            "llm_call_logged": bool(log),
            "fallback_used": False,
            "attachment_counts": {key: len(value) if isinstance(value, list) else 1 for key, value in attachment_payload.items()},
        },
        "real_external_call_executed": gateway_result.external_call_executed,
    }
