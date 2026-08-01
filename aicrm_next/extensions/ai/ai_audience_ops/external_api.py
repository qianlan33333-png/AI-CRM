from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting
from aicrm_next.extensions.ai.ai_audience_ops.automation_binding import AudienceAutomationBindingService

from .package_spec import package_payload_from_spec, parse_markdown_spec_text, validate_spec
from .repository import build_audience_repository, _text
from .schemas import SimpleSqlApplyRequest, SimpleSqlPreviewRequest
from .service import AudiencePackageService
from .simple_sql import compile_simple_sql, simple_refresh_mode_config, validate_simple_sql

router = APIRouter()

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


@router.post("/api/external/ai-audience/spec/dry-run", name="api.external_ai_audience_spec_dry_run")
def external_ai_audience_spec_dry_run(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    result = _dry_run(payload, repo=repo)
    _audit(
        repo,
        operator=_operator(payload),
        action_type="external_spec_dry_run",
        package_key=_text(result.get("package_key")),
        before=_audit_before(payload),
        after=result,
    )
    return _response(result, status_code=403 if result.get("error") == "unsafe_package_key_prefix" else 200)


@router.post("/api/external/ai-audience/spec/apply", name="api.external_ai_audience_spec_apply")
def external_ai_audience_spec_apply(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    dry = _dry_run(payload, repo=repo)
    if not dry.get("ok"):
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_spec_apply_rejected",
            package_key=_text(dry.get("package_key")),
            before=_audit_before(payload),
            after=dry,
        )
        return _response(dry, status_code=403 if dry.get("error") == "unsafe_package_key_prefix" else 400)
    if bool(payload.get("publish")) and not _allow_publish():
        result = {**dry, "ok": False, "error": "publish_not_allowed", "published": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_spec_apply_rejected",
            package_key=_text(dry.get("package_key")),
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    result = _apply(payload, dry, repo=repo, publish=bool(payload.get("publish")))
    _audit(
        repo,
        operator=_operator(payload),
        action_type="external_spec_apply",
        package_key=_text(result.get("package_key")),
        before=_audit_before(payload),
        after=result,
    )
    return _response(result)


@router.post("/api/external/ai-audience/spec/publish", name="api.external_ai_audience_spec_publish")
def external_ai_audience_spec_publish(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    package_key = _text(payload.get("package_key"))
    if gate := _prefix_gate_error(package_key):
        result = {"ok": False, "error": gate, "package_key": package_key, "published": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_spec_publish_rejected",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    if not _allow_publish():
        result = {"ok": False, "error": "publish_not_allowed", "package_key": package_key, "published": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_spec_publish_rejected",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    package = repo.get_package_by_key(package_key)
    if not package:
        result = {"ok": False, "error": "package_not_found", "package_key": package_key, "published": False}
        _audit(
            repo, operator=_operator(payload), action_type="external_spec_publish_failed", package_key=package_key, before=_audit_before(payload), after=result
        )
        return _response(result, status_code=404)
    service = AudiencePackageService(repository=repo)
    result = service.publish_external_package(int(package["id"]), version_id=payload.get("version_id"))
    response = {
        "ok": bool(result.get("ok")),
        "package_key": package_key,
        "package_id": int(package["id"]),
        "version_id": int(((result.get("version") or {}).get("id")) or payload.get("version_id") or 0) or None,
        "published": bool(result.get("ok")),
        "validation_errors": result.get("validation_errors", []),
        "warnings": [],
        "error": result.get("error", ""),
    }
    _audit(repo, operator=_operator(payload), action_type="external_spec_publish", package_key=package_key, before=_audit_before(payload), after=response)
    return _response(response, status_code=200 if response["ok"] else 400)


@router.post("/api/external/ai-audience/packages/{package_key}/archive", name="api.external_ai_audience_package_archive")
def external_ai_audience_package_archive(package_key: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    package_key = _text(package_key)
    if gate := _prefix_gate_error(package_key):
        result = {"ok": False, "error": gate, "package_key": package_key, "archived": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_spec_archive_rejected",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    package = repo.get_package_by_key(package_key)
    if not package:
        result = {"ok": False, "error": "package_not_found", "package_key": package_key, "archived": False}
        _audit(
            repo, operator=_operator(payload), action_type="external_spec_archive_failed", package_key=package_key, before=_audit_before(payload), after=result
        )
        return _response(result, status_code=404)
    archived = AudiencePackageService(repository=repo).archive_admin_package(int(package["id"]))
    result = {
        "ok": bool(archived.get("ok")),
        "package_key": package_key,
        "package_id": int(package["id"]),
        "status": (archived.get("package") or {}).get("status"),
        "archived": bool(archived.get("ok")),
        "error": archived.get("error", ""),
    }
    _audit(repo, operator=_operator(payload), action_type="external_spec_archive", package_key=package_key, before=_audit_before(payload), after=result)
    return _response(result, status_code=200 if result["ok"] else 400)


@router.post("/api/external/ai-audience/e2e/run", name="api.external_ai_audience_e2e_run")
def external_ai_audience_e2e_run(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    runner_factory = getattr(request.app.state, "ai_audience_e2e_runner_factory", None)
    if runner_factory is None:
        result = {
            "ok": False,
            "error": "e2e_runner_composition_unavailable",
            "status_code": 503,
            "real_external_call_executed": False,
        }
    else:
        result = runner_factory(repository=repo).run(payload)
    _audit(
        repo,
        operator=_operator(payload),
        action_type="external_e2e_run",
        package_key="prod_e2e",
        before=_audit_before(payload),
        after=result,
    )
    return _response(result, status_code=int(result.get("status_code") or (200 if result.get("ok") else 400)))


@router.post("/api/external/ai-audience/simple/preview", name="api.external_ai_audience_simple_preview")
def external_ai_audience_simple_preview(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    result = _simple_preview(payload, repo=repo)
    _audit(
        repo,
        operator=_operator(payload),
        action_type="external_simple_preview",
        package_key=_text(result.get("package_key")),
        before=_audit_before(payload),
        after=result,
    )
    return _response(result, status_code=403 if result.get("error") == "unsafe_package_key_prefix" else 200)


@router.post("/api/external/ai-audience/simple/apply", name="api.external_ai_audience_simple_apply")
def external_ai_audience_simple_apply(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    if "outbound_webhook_url" in payload:
        result = {"ok": False, "error": "webhook_configuration_retired", "validation_errors": ["webhook_configuration_retired"]}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_apply_rejected",
            package_key=_text(payload.get("package_key")),
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=410)
    preview = _simple_preview(payload, repo=repo)
    if not preview.get("ok"):
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_apply_rejected",
            package_key=_text(preview.get("package_key")),
            before=_audit_before(payload),
            after=preview,
        )
        return _response(preview, status_code=403 if preview.get("error") == "unsafe_package_key_prefix" else 400)
    result = _simple_apply(payload, preview, repo=repo)
    _audit(
        repo,
        operator=_operator(payload),
        action_type="external_simple_apply",
        package_key=_text(result.get("package_key")),
        before=_audit_before(payload),
        after=result,
    )
    return _response(result)


@router.post("/api/external/ai-audience/simple/{package_key}/activate", name="api.external_ai_audience_simple_activate")
def external_ai_audience_simple_activate(package_key: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    package_key = _text(package_key)
    if gate := _prefix_gate_error(package_key):
        result = {"ok": False, "error": gate, "package_key": package_key, "activated": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_activate_rejected",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    package = repo.get_package_by_key(package_key)
    if not package:
        result = {"ok": False, "error": "package_not_found", "package_key": package_key, "activated": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_activate_failed",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=404)
    activated = AudiencePackageService(repository=repo).activate_admin_package(int(package["id"]))
    result = {
        "ok": bool(activated.get("ok")),
        "package_key": package_key,
        "package_id": int(package["id"]),
        "status": (activated.get("package") or {}).get("status"),
        "activated": bool(activated.get("ok")),
        "launch_refresh": activated.get("launch_refresh"),
        "error": activated.get("error", ""),
    }
    _audit(repo, operator=_operator(payload), action_type="external_simple_activate", package_key=package_key, before=_audit_before(payload), after=result)
    return _response(result, status_code=200 if result["ok"] else 400)


@router.post("/api/external/ai-audience/simple/{package_key}/archive", name="api.external_ai_audience_simple_archive")
def external_ai_audience_simple_archive(package_key: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
    repo = build_audience_repository()
    package_key = _text(package_key)
    if gate := _prefix_gate_error(package_key):
        result = {"ok": False, "error": gate, "package_key": package_key, "archived": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_archive_rejected",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=403)
    package = repo.get_package_by_key(package_key)
    if not package:
        result = {"ok": False, "error": "package_not_found", "package_key": package_key, "archived": False}
        _audit(
            repo,
            operator=_operator(payload),
            action_type="external_simple_archive_failed",
            package_key=package_key,
            before=_audit_before(payload),
            after=result,
        )
        return _response(result, status_code=404)
    archived = AudiencePackageService(repository=repo).archive_admin_package(int(package["id"]))
    result = {
        "ok": bool(archived.get("ok")),
        "package_key": package_key,
        "package_id": int(package["id"]),
        "status": (archived.get("package") or {}).get("status"),
        "archived": bool(archived.get("ok")),
        "error": archived.get("error", ""),
    }
    _audit(repo, operator=_operator(payload), action_type="external_simple_archive", package_key=package_key, before=_audit_before(payload), after=result)
    return _response(result, status_code=200 if result["ok"] else 400)


def _dry_run(payload: dict[str, Any], *, repo) -> dict[str, Any]:
    spec = parse_markdown_spec_text(_text(payload.get("spec_markdown")), path="<external>")
    errors, warnings = validate_spec(spec)
    package_key = _resolve_package_key(spec.package_key, _text(payload.get("package_key_prefix")))
    if gate := _prefix_gate_error(package_key):
        errors.append(gate)
    dependencies = sorted(set(_dependencies_from_spec(spec)))
    return {
        "ok": not errors,
        "mode": "dry_run",
        "package_key": package_key,
        "error": "unsafe_package_key_prefix" if "unsafe_package_key_prefix" in errors else "",
        "validation_errors": sorted(set(errors)),
        "warnings": warnings,
        "dependencies": dependencies,
    }


def _simple_preview(payload: dict[str, Any], *, repo) -> dict[str, Any]:
    try:
        request = SimpleSqlPreviewRequest(**dict(payload or {}))
    except Exception as exc:
        return {"ok": False, "error": "invalid_request", "validation_errors": [str(exc)], "warnings": [], "dependencies": []}
    package_key = _text(request.package_key)
    errors: list[str] = []
    if gate := _prefix_gate_error(package_key):
        errors.append(gate)
    validation = validate_simple_sql(request.sql, request.parameters)
    errors.extend(validation.errors)
    compiled_sql = compile_simple_sql(request.sql)
    sample_rows: list[dict[str, Any]] = []
    preview_error = ""
    if not errors:
        from .refresh_service import AudienceRefreshService

        try:
            preview = AudienceRefreshService(repository=repo).preview_sql(
                compiled_sql,
                {
                    **dict(request.parameters or {}),
                    "package_key": package_key or "simple_preview",
                    "package_id": 0,
                    "refresh_started_at": "2026-01-01T00:00:00Z",
                    "last_watermark_at": "2026-01-01T00:00:00Z",
                    "lookback_seconds": 600,
                },
                limit=request.limit,
            )
            sample_rows = list(preview.get("sample_rows") or [])
            if not preview.get("ok"):
                errors.extend((preview.get("validation") or {}).get("errors") or [])
                preview_error = _text(preview.get("error"))
        except Exception as exc:
            errors.append("preview_failed")
            preview_error = str(exc)
    return {
        "ok": not errors,
        "mode": "simple_preview",
        "package_key": package_key,
        "validation_errors": sorted(set(errors)),
        "dependencies": validation.dependencies,
        "params": validation.params,
        "sample_rows": sample_rows,
        "error": "unsafe_package_key_prefix" if "unsafe_package_key_prefix" in errors else preview_error,
        "warnings": [],
    }


def _simple_apply(payload: dict[str, Any], preview: dict[str, Any], *, repo) -> dict[str, Any]:
    try:
        request = SimpleSqlApplyRequest(**dict(payload or {}))
    except Exception as exc:
        return {**preview, "ok": False, "error": "invalid_request", "validation_errors": [str(exc)]}
    admin_refresh_mode = simple_refresh_mode_config(request.refresh_mode)
    if not admin_refresh_mode:
        return {**preview, "ok": False, "error": "invalid_refresh_mode", "validation_errors": ["invalid_refresh_mode"]}
    package_key = _text(request.package_key)
    compiled_sql = compile_simple_sql(request.sql)
    service = AudiencePackageService(repository=repo)
    existing = repo.get_package_by_key(package_key)
    package_payload = {
        "package_key": package_key,
        "name": _text(request.name),
        "status": "paused",
        "natural_language_definition": _text(request.natural_language_definition),
        "refresh_mode": admin_refresh_mode,
        "query_mode": "simple_sql",
        "identity_policy": "external_userid",
        "parameters": dict(request.parameters or {}),
        "simple_sql_text": request.sql,
        "simple_compiled_sql_text": compiled_sql,
        "incremental_sql_text": compiled_sql if request.refresh_mode == "every_3m" else "",
        "snapshot_sql_text": compiled_sql if request.refresh_mode in {"daily_0200", "manual"} else "",
        "ai_rationale": "Simple SQL package compiled from runtime external API.",
        "natural_language_explanation": _text(request.natural_language_definition),
    }
    if existing:
        package_id = int(existing["id"])
        updated = service.update_admin_package(
            package_id,
            {
                "name": package_payload["name"],
                "natural_language_definition": package_payload["natural_language_definition"],
                "refresh_mode": admin_refresh_mode,
            },
        )
        if not updated.get("ok"):
            return {**preview, "ok": False, "error": updated.get("error", "package_update_failed")}
        version = service.create_admin_version(package_id, package_payload)
        created = False
        updated_flag = True
    else:
        created_result = service.create_admin_package(package_payload)
        if not created_result.get("ok"):
            return {
                **preview,
                "ok": False,
                "error": created_result.get("error", "package_create_failed"),
                "validation_errors": created_result.get("validation_errors", []),
            }
        package_id = int((created_result.get("package") or {}).get("id") or 0)
        version = {"ok": True, "version": created_result.get("version")}
        created = True
        updated_flag = False
    version_id = int(((version.get("version") or {}).get("id")) or 0)
    if not version.get("ok") or version_id <= 0:
        return {**preview, "ok": False, "package_id": package_id, "version_id": version_id or None, "validation_errors": version.get("validation_errors", [])}
    published = service.publish_external_package(package_id, version_id=version_id)
    if not published.get("ok"):
        return {
            **preview,
            "ok": False,
            "package_id": package_id,
            "version_id": version_id,
            "error": published.get("error", "publish_failed"),
            "validation_errors": published.get("validation_errors", []),
        }
    _apply_simple_senders(service, package_id, request)
    return {
        **preview,
        "ok": True,
        "mode": "simple_apply",
        "package_id": package_id,
        "version_id": version_id,
        "created": created,
        "updated": updated_flag,
        "preview_ok": True,
        "status": "paused",
        "published": True,
        "error": "",
    }


def _apply(payload: dict[str, Any], dry: dict[str, Any], *, repo, publish: bool) -> dict[str, Any]:
    spec = parse_markdown_spec_text(_text(payload.get("spec_markdown")), path="<external>")
    package_key = _text(dry.get("package_key"))
    service = AudiencePackageService(repository=repo)
    existing = repo.get_package_by_key(package_key)
    package_payload = package_payload_from_spec(spec, package_key=package_key)
    if existing:
        package_id = int(existing["id"])
        updated = service.update_admin_package(
            package_id,
            {
                "name": package_payload["name"],
                "natural_language_definition": package_payload["natural_language_definition"],
                "refresh_mode": package_payload["refresh_mode"],
            },
        )
        if not updated.get("ok"):
            return {**dry, "ok": False, "error": updated.get("error", "package_update_failed")}
        version = service.create_admin_version(package_id, package_payload)
        created = False
        updated_flag = True
    else:
        created_result = service.create_admin_package(package_payload)
        if not created_result.get("ok"):
            return {
                **dry,
                "ok": False,
                "error": created_result.get("error", "package_create_failed"),
                "validation_errors": created_result.get("validation_errors", []),
            }
        package_id = int((created_result.get("package") or {}).get("id") or 0)
        version = {"ok": True, "version": created_result.get("version")}
        created = True
        updated_flag = False
    version_id = int(((version.get("version") or {}).get("id")) or 0)
    if not version.get("ok"):
        return {**dry, "ok": False, "package_id": package_id, "version_id": version_id or None, "validation_errors": version.get("validation_errors", [])}
    binding_result = _apply_automation_binding_and_senders(service, package_id, spec)
    if not binding_result.get("ok"):
        return {**dry, "ok": False, "package_id": package_id, "version_id": version_id or None, "error": binding_result.get("error", "automation_binding_failed")}
    preview = service.preview_admin_package(package_id, {"version_id": version_id, "sql_kind": _preview_sql_kind(spec), "limit": 5})
    response = {
        **dry,
        "ok": True,
        "package_id": package_id,
        "version_id": version_id or None,
        "created": created,
        "updated": updated_flag,
        "preview_ok": bool(preview.get("ok")),
        "published": False,
        "error": "",
    }
    if publish:
        published = service.publish_external_package(package_id, version_id=version_id or None)
        response["published"] = bool(published.get("ok"))
        if not published.get("ok"):
            response["ok"] = False
            response["error"] = published.get("error", "publish_failed")
            response["validation_errors"] = published.get("validation_errors", [])
    return response


def _apply_automation_binding_and_senders(service: AudiencePackageService, package_id: int, spec) -> dict[str, Any]:
    automation_binding = spec.frontmatter.get("automation_binding") if isinstance(spec.frontmatter.get("automation_binding"), dict) else {}
    if automation_binding:
        result = AudienceAutomationBindingService().put_by_agent_code(
            package_id,
            _text(automation_binding.get("agent_code")),
            operator="external_package_spec",
        )
        if not result.get("ok"):
            return result
    senders = spec.frontmatter.get("senders") if isinstance(spec.frontmatter.get("senders"), list) else []
    if senders:
        service.replace_admin_senders(package_id, {"items": senders})
    return {"ok": True}


def _dependencies_from_spec(spec) -> list[str]:
    from .sql_linter import lint_sql

    dependencies: list[str] = []
    for sql_text in (spec.incremental_sql, spec.snapshot_sql):
        if not sql_text:
            continue
        dependencies.extend(lint_sql(sql_text).dependencies)
    return dependencies


def _preview_sql_kind(spec) -> str:
    return "incremental" if _text(getattr(spec, "incremental_sql", "")) else "snapshot"


def _resolve_package_key(package_key: str, package_key_prefix: str) -> str:
    package_key = _text(package_key)
    package_key_prefix = _text(package_key_prefix)
    if package_key_prefix and not package_key.startswith(package_key_prefix):
        return f"{package_key_prefix}{package_key}"
    return package_key


def _allow_publish() -> bool:
    return _text(
        managed_runtime_setting("AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH")
    ).lower() in {"1", "true", "yes"}


def _prefix_gate_error(package_key: str) -> str:
    package_key = _text(package_key)
    if not package_key:
        return "package_key_required"
    if _allow_non_verify_prefix():
        return ""
    prefixes = _allowed_prefixes()
    if not prefixes:
        return "unsafe_package_key_prefix"
    if any(package_key.startswith(prefix) for prefix in prefixes):
        return ""
    return "unsafe_package_key_prefix"


def _allowed_prefixes() -> list[str]:
    raw = _text(managed_runtime_setting("AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES"))
    return [item.strip() for item in raw.split(",") if item.strip()]


def _allow_non_verify_prefix() -> bool:
    return _text(
        managed_runtime_setting("AICRM_AI_AUDIENCE_SPEC_ALLOW_NON_VERIFY_PREFIX")
    ).lower() in {"1", "true", "yes"}


def _apply_simple_senders(service: AudiencePackageService, package_id: int, request: SimpleSqlApplyRequest) -> None:
    service.replace_admin_senders(
        package_id,
        {
            "items": [
                {
                    "sender_userid": item.sender_userid,
                    "display_name": item.display_name or item.sender_userid,
                    "priority": item.priority,
                    "status": item.status,
                }
                for item in request.senders
            ]
        },
    )


def _operator(payload: dict[str, Any]) -> str:
    return _text(payload.get("operator")) or "external"


def _audit_before(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "package_key_prefix": _text(payload.get("package_key_prefix")),
        "package_key": _text(payload.get("package_key")),
        "publish": bool(payload.get("publish")),
        "operator": _operator(payload),
        "spec_markdown_present": bool(_text(payload.get("spec_markdown"))),
    }


def _audit(repo, *, operator: str, action_type: str, package_key: str, before: dict[str, Any], after: dict[str, Any]) -> None:
    repo.insert_external_spec_audit(operator=operator, action_type=action_type, package_key=package_key, before=before, after=_redact_payload(after))


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    forbidden = ("secret", "token", "dsn", "database_url", "cookie")
    redacted: dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if any(marker in str(key).lower() for marker in forbidden):
            redacted[key] = "***"
        elif isinstance(value, dict):
            redacted[key] = _redact_payload(value)
        else:
            redacted[key] = value
    return redacted


def _response(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    if not payload.get("ok", True) and status_code == 200:
        status_code = 400
    return JSONResponse(jsonable_encoder(_redact_payload(payload)), status_code=status_code, headers=_HEADERS)
