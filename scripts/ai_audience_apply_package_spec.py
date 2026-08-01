#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aicrm_next.extensions.ai.ai_audience_ops.package_spec import (  # noqa: E402
    PackageSpec,
    package_payload_from_spec,
    parse_markdown_spec,
    redact_report,
    validate_spec,
)
from aicrm_next.extensions.ai.ai_audience_ops.repository import build_audience_repository  # noqa: E402
from aicrm_next.extensions.ai.ai_audience_ops.service import AudiencePackageService  # noqa: E402
from aicrm_next.extensions.ai.ai_audience_ops.automation_binding import AudienceAutomationBindingService  # noqa: E402


def apply_spec(
    spec: PackageSpec,
    *,
    apply: bool = False,
    publish: bool = False,
    api_base: str = "",
    admin_session_cookie: str = "",
    external_api_base: str = "",
    external_token: str = "",
    package_key_prefix: str = "",
    operator: str = "codex",
) -> dict[str, Any]:
    errors, warnings = validate_spec(spec)
    package_key = _final_package_key(spec, package_key_prefix)
    report: dict[str, Any] = {
        "ok": not errors,
        "spec_path": str(spec.path),
        "package_key": package_key,
        "package_id": None,
        "version_id": None,
        "created": False,
        "updated": False,
        "preview_ok": False,
        "published": False,
        "validation_errors": errors,
        "warnings": warnings,
        "operator": operator,
    }
    if errors:
        return report
    if external_api_base:
        return _apply_via_external_api(
            spec,
            report,
            base=external_api_base,
            token=external_token,
            package_key_prefix=package_key_prefix,
            apply=apply,
            publish=publish,
            operator=operator,
        )
    if not apply:
        return report
    if api_base:
        return _apply_via_admin_api(spec, report, api_base=api_base, admin_session_cookie=admin_session_cookie, package_key=package_key, publish=publish)
    return _apply_direct(spec, report, package_key=package_key, publish=publish)


def _apply_direct(spec: PackageSpec, report: dict[str, Any], *, package_key: str, publish: bool) -> dict[str, Any]:
    repo = build_audience_repository()
    service = AudiencePackageService(repository=repo)
    existing = repo.get_package_by_key(package_key)
    payload = package_payload_from_spec(spec, package_key=package_key)

    if existing:
        package_id = int(existing["id"])
        update = service.update_admin_package(
            package_id,
            {
                "name": payload["name"],
                "natural_language_definition": payload["natural_language_definition"],
                "refresh_mode": payload["refresh_mode"],
            },
        )
        if not update.get("ok"):
            return {**report, "ok": False, "validation_errors": [str(update.get("error") or "package_update_failed")]}
        version = service.create_admin_version(package_id, payload)
        report.update({"updated": True, "package_id": package_id})
    else:
        create = service.create_admin_package(payload)
        if not create.get("ok"):
            return {**report, "ok": False, "validation_errors": [str(create.get("error") or "package_create_failed"), *create.get("validation_errors", [])]}
        package_id = int((create.get("package") or {}).get("id") or 0)
        version = {"ok": True, "version": create.get("version")}
        report.update({"created": True, "package_id": package_id})

    version_id = int(((version.get("version") or {}).get("id")) or 0)
    report["version_id"] = version_id or None
    if not version.get("ok"):
        return {**report, "ok": False, "validation_errors": version.get("validation_errors", [])}

    binding = _apply_automation_binding_and_senders(service, int(report["package_id"]), spec, operator="package_spec_cli")
    if not binding.get("ok"):
        return {**report, "ok": False, "validation_errors": [str(binding.get("error") or "automation_binding_failed")]}
    preview = service.preview_admin_package(int(report["package_id"]), {"version_id": version_id, "sql_kind": _preview_sql_kind(spec), "limit": 5})
    report["preview_ok"] = bool(preview.get("ok"))
    if publish:
        published = service.publish_admin_package(int(report["package_id"]), {"version_id": version_id or None})
        report["published"] = bool(published.get("ok"))
        if not published.get("ok"):
            report["ok"] = False
            report["validation_errors"] = [str(published.get("error") or "publish_failed"), *published.get("validation_errors", [])]
    return report


def _apply_via_admin_api(
    spec: PackageSpec,
    report: dict[str, Any],
    *,
    api_base: str,
    admin_session_cookie: str,
    package_key: str,
    publish: bool,
) -> dict[str, Any]:
    if not admin_session_cookie:
        return {**report, "ok": False, "validation_errors": ["admin_session_cookie_required"]}
    base = api_base.rstrip("/")
    payload = package_payload_from_spec(spec, package_key=package_key)
    packages = _http_json("GET", f"{base}/api/admin/ai-audience/packages", cookie=admin_session_cookie)
    existing = next((item for item in packages.get("items", []) if item.get("package_key") == package_key), None)

    if existing:
        package_id = int(existing["id"])
        _http_json(
            "PATCH",
            f"{base}/api/admin/ai-audience/packages/{package_id}",
            cookie=admin_session_cookie,
            payload={
                "name": payload["name"],
                "natural_language_definition": payload["natural_language_definition"],
                "refresh_mode": payload["refresh_mode"],
            },
        )
        version = _http_json("POST", f"{base}/api/admin/ai-audience/packages/{package_id}/versions", cookie=admin_session_cookie, payload=payload)
        report.update({"updated": True, "package_id": package_id})
    else:
        created = _http_json("POST", f"{base}/api/admin/ai-audience/packages", cookie=admin_session_cookie, payload=payload)
        package_id = int((created.get("package") or {}).get("id") or 0)
        version = {"version": created.get("version")}
        report.update({"created": True, "package_id": package_id})

    version_id = int(((version.get("version") or {}).get("id")) or 0)
    report["version_id"] = version_id or None
    _apply_admin_automation_binding_and_senders(base, admin_session_cookie, int(report["package_id"]), spec)
    preview = _http_json(
        "POST",
        f"{base}/api/admin/ai-audience/packages/{report['package_id']}/preview",
        cookie=admin_session_cookie,
        payload={"version_id": version_id, "sql_kind": _preview_sql_kind(spec), "limit": 5},
    )
    report["preview_ok"] = bool(preview.get("ok"))
    if publish:
        published = _http_json(
            "POST",
            f"{base}/api/admin/ai-audience/packages/{report['package_id']}/publish",
            cookie=admin_session_cookie,
            payload={"version_id": version_id or None},
        )
        report["published"] = bool(published.get("ok"))
    return report


def _apply_via_external_api(
    spec: PackageSpec,
    report: dict[str, Any],
    *,
    base: str,
    token: str,
    package_key_prefix: str,
    apply: bool,
    publish: bool,
    operator: str,
) -> dict[str, Any]:
    if not token:
        return {**report, "ok": False, "validation_errors": ["external_token_required"]}
    url_base = base.rstrip("/")
    payload = {
        "spec_markdown": spec.path.read_text(encoding="utf-8"),
        "package_key_prefix": package_key_prefix,
        "operator": operator,
    }
    if not apply:
        response = _http_json("POST", f"{url_base}/api/external/ai-audience/spec/dry-run", bearer_token=token, payload=payload)
        return {**report, **_report_from_external(response)}
    response = _http_json(
        "POST",
        f"{url_base}/api/external/ai-audience/spec/apply",
        bearer_token=token,
        payload={**payload, "publish": bool(publish)},
    )
    return {**report, **_report_from_external(response)}


def _report_from_external(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "package_key": payload.get("package_key"),
        "package_id": payload.get("package_id"),
        "version_id": payload.get("version_id"),
        "created": bool(payload.get("created")),
        "updated": bool(payload.get("updated")),
        "preview_ok": bool(payload.get("preview_ok")),
        "published": bool(payload.get("published")),
        "validation_errors": payload.get("validation_errors", []),
        "warnings": payload.get("warnings", []),
    }


def _final_package_key(spec: PackageSpec, package_key_prefix: str) -> str:
    if package_key_prefix and not spec.package_key.startswith(package_key_prefix):
        return f"{package_key_prefix}{spec.package_key}"
    return spec.package_key


def _preview_sql_kind(spec: PackageSpec) -> str:
    return "incremental" if str(spec.incremental_sql or "").strip() else "snapshot"


def _apply_automation_binding_and_senders(
    service: AudiencePackageService,
    package_id: int,
    spec: PackageSpec,
    *,
    operator: str,
) -> dict[str, Any]:
    automation_binding = spec.frontmatter.get("automation_binding") if isinstance(spec.frontmatter.get("automation_binding"), dict) else {}
    if automation_binding:
        result = AudienceAutomationBindingService().put_by_agent_code(
            package_id,
            str(automation_binding.get("agent_code") or ""),
            operator=operator,
        )
        if not result.get("ok"):
            return result
    senders = spec.frontmatter.get("senders") if isinstance(spec.frontmatter.get("senders"), list) else []
    if senders:
        service.replace_admin_senders(package_id, {"items": senders})
    return {"ok": True}


def _apply_admin_automation_binding_and_senders(base: str, cookie: str, package_id: int, spec: PackageSpec) -> None:
    automation_binding = spec.frontmatter.get("automation_binding") if isinstance(spec.frontmatter.get("automation_binding"), dict) else {}
    if automation_binding:
        agents = _http_json("GET", f"{base}/api/admin/automation-agents", cookie=cookie)
        agent_code = str(automation_binding.get("agent_code") or "").strip()
        automation = next((item for item in agents.get("items", []) if str(item.get("agent_code") or "").strip() == agent_code), None)
        if not automation:
            raise RuntimeError(f"automation_not_found:{agent_code}")
        _http_json(
            "PUT",
            f"{base}/api/admin/ai-audience/packages/{package_id}/automation-binding",
            cookie=cookie,
            payload={"automation_id": int(automation.get("id") or 0)},
        )
    senders = spec.frontmatter.get("senders") if isinstance(spec.frontmatter.get("senders"), list) else []
    if senders:
        _http_json("PUT", f"{base}/api/admin/ai-audience/packages/{package_id}/senders", cookie=cookie, payload={"items": senders})


def _http_json(
    method: str,
    url: str,
    *,
    cookie: str = "",
    bearer_token: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"http_api_failed:{exc.code}:{body}") from exc


def _production_like(api_base: str) -> bool:
    return "youcangogogo.com" in api_base or os.getenv("PRODUCTION_DATA_MODE", "").lower() in {"1", "true", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply AI Audience Markdown package specs.")
    parser.add_argument("specs", nargs="+", help="Markdown spec file(s)")
    parser.add_argument("--dry-run", action="store_true", help="Validate only. This is the default.")
    parser.add_argument("--apply", action="store_true", help="Create or update package and version.")
    parser.add_argument("--publish", action="store_true", help="Publish the created/latest version.")
    parser.add_argument("--api-base", default="", help="Use admin API instead of local service.")
    parser.add_argument("--admin-session-cookie-from-env", action="store_true", help="Read admin cookie from AICRM_ADMIN_SESSION_COOKIE.")
    parser.add_argument("--external-api-base", default="", help="Use external spec API instead of local service/admin API.")
    parser.add_argument(
        "--oauth-access-token-from-env",
        action="store_true",
        help="Read a short-lived OAuth access token from AICRM_AUTH_CLI_ACCESS_TOKEN.",
    )
    parser.add_argument("--package-key-prefix", default="", help="Prefix package_key, useful for prod_verify_ tests.")
    parser.add_argument("--operator", default="codex")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args(argv)

    target_base = args.external_api_base or args.api_base
    if args.apply and _production_like(target_base) and not args.confirm_production:
        print(json.dumps({"ok": False, "error": "confirm_production_required"}, ensure_ascii=False))
        return 2

    cookie = os.getenv("AICRM_ADMIN_SESSION_COOKIE", "") if args.admin_session_cookie_from_env else ""
    token = os.getenv("AICRM_AUTH_CLI_ACCESS_TOKEN", "") if args.oauth_access_token_from_env else ""
    reports: list[dict[str, Any]] = []
    for spec_path in args.specs:
        spec = parse_markdown_spec(spec_path)
        reports.append(
            apply_spec(
                spec,
                apply=bool(args.apply),
                publish=bool(args.publish),
                api_base=args.api_base,
                admin_session_cookie=cookie,
                external_api_base=args.external_api_base,
                external_token=token,
                package_key_prefix=args.package_key_prefix,
                operator=args.operator,
            )
        )
    payload = {"ok": all(item.get("ok") for item in reports), "reports": reports}
    print(redact_report(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
