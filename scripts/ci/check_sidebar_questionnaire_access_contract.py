#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text  # noqa: E402


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _function_source(relative: str, function_name: str) -> str:
    source = _read(relative)
    tree = ast.parse(source, filename=relative)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _method_source(relative: str, class_name: str, method_name: str) -> str:
    source = _read(relative)
    tree = ast.parse(source, filename=relative)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                return ast.get_source_segment(source, child) or ""
    return ""


def check() -> list[str]:
    errors: list[str] = []
    customer_api = _read("aicrm_next/customer_read_model/api.py")
    sidebar_read_model = _read("aicrm_next/customer_read_model/sidebar_v2.py")
    sidebar_write_api = _read("aicrm_next/sidebar_write/api.py")
    sidebar_jssdk = _read("aicrm_next/identity_contact/sidebar_jssdk.py")
    sidebar_frontend = _read(
        "aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js"
    )
    route_policy = _read("aicrm_next/admin_auth/route_policy.py")
    questionnaire_api = _read("aicrm_next/extensions/forms/questionnaire/api.py")
    result_access = _read("aicrm_next/admin_auth/public_result_grant.py")
    result_access_compat = _read("aicrm_next/extensions/forms/questionnaire/result_access.py")

    forbidden_read_tokens = (
        "allow_readonly_fallback",
        "READONLY_OWNER_PENDING_USERID",
        "readonly_unscoped",
        "readonly_owner_pending",
        "query_fallback",
    )
    for token in forbidden_read_tokens:
        if token in customer_api or token in sidebar_read_model:
            errors.append(f"unsafe sidebar readonly fallback remains: {token}")

    owner_scope_source = _function_source(
        "aicrm_next/sidebar_write/application.py",
        "_validate_owner_scope",
    )
    for required in (
        "ListExternalContactOwnerCandidatesQuery",
        "command.actor_id",
        "SidebarWriteForbiddenError",
    ):
        if required not in owner_scope_source:
            errors.append(f"sidebar write owner revalidation missing: {required}")
    if "production_data_ready" in owner_scope_source:
        errors.append("sidebar write owner validation still has a production bypass")

    write_execute_source = _function_source("aicrm_next/sidebar_write/api.py", "_execute")
    for required in (
        "actor_id=trusted_owner_userid",
        'actor_type="sidebar_owner"',
        'body["owner_userid"] = trusted_owner_userid',
        'body["bind_by_userid"] = trusted_owner_userid',
    ):
        if required not in write_execute_source:
            errors.append(f"request-declared sidebar actor boundary missing: {required}")
    for forbidden in (
        'request.headers.get("X-AICRM-Actor-Id")',
        'request.headers.get("X-AICRM-Actor-Type")',
    ):
        if forbidden in sidebar_write_api:
            errors.append(f"request header still supplies sidebar actor authority: {forbidden}")

    for forbidden in (
        "_viewer_userid_from_request",
        "_bind_by_userid_from_request",
        "ADMIN_SESSION_COOKIE",
        "issued_identity_owner",
        "sidebar_jssdk_identity_owner_fallback",
    ):
        if forbidden in sidebar_jssdk:
            errors.append(f"untrusted JSSDK viewer fallback remains: {forbidden}")

    for forbidden in (
        'query_params.get("sidebar_owner_token")',
        'query_params.get("owner_token")',
    ):
        if forbidden in route_policy or forbidden in customer_api:
            errors.append(f"sidebar owner token is still accepted from query parameters: {forbidden}")
    for forbidden in (
        'firstQueryValue(["sidebar_owner_token"',
        'firstQueryValue(["owner_userid"',
        'url.searchParams.set("viewer_userid"',
        'url.searchParams.set("bind_by_userid"',
    ):
        if forbidden in sidebar_frontend:
            errors.append(f"sidebar frontend still promotes query authority: {forbidden}")

    owner_query_source = _method_source(
        "aicrm_next/identity_contact/repo.py",
        "PostgresIdentityRepository",
        "list_external_contact_owner_userids",
    )
    for required in (
        "wecom_external_contact_follow_users",
        "wecom_external_contact_identity_map",
        "crm_user_identity",
        "relation_status",
        "COALESCE(status, 'active') = 'active'",
        "COALESCE(identity_status, 'active') = 'active'",
    ):
        if required not in owner_query_source:
            errors.append(f"current sidebar owner source missing: {required}")
    for forbidden in (
        "external_contact_bindings",
        "first_owner_userid",
        "last_owner_userid",
    ):
        if forbidden in owner_query_source:
            errors.append(f"legacy owner source still authorizes sidebar access: {forbidden}")
    sidebar_owner_query_source = _method_source(
        "aicrm_next/customer_read_model/sidebar_v2.py",
        "SidebarV2SqlRepository",
        "get_contact_owner_userids",
    )
    for required in (
        "relation_status",
        "COALESCE(status, 'active') = 'active'",
    ):
        if required not in sidebar_owner_query_source:
            errors.append(f"sidebar read current-owner filter missing: {required}")

    result_source = _function_source("aicrm_next/extensions/forms/questionnaire/api.py", "public_submission_result")
    validator_position = result_source.find("questionnaire_result_access_token")
    read_position = result_source.find("GetSubmissionResultQuery")
    if validator_position < 0 or read_position < 0 or validator_position > read_position:
        errors.append("questionnaire result must consume the middleware-validated session grant before reading the submission")
    middleware_source = _function_source("aicrm_next/admin_auth/route_policy.py", "_enforce_public_result_grant")
    if (
        middleware_source.find("questionnaire_result_token_from_grant") < 0
        or middleware_source.find("request.state.questionnaire_result_access_token")
        < middleware_source.find("questionnaire_result_token_from_grant")
    ):
        errors.append("route middleware must validate the result cookie before installing questionnaire result authority")
    for required in (
        "issue_questionnaire_result_grant",
        "httponly=True",
        "secure=session_cookie_secure()",
        "max_age=grant.max_age_seconds",
        "path=grant.cookie_path",
    ):
        if required not in questionnaire_api:
            errors.append(f"questionnaire result grant issuance missing: {required}")
    for required in (
        "RESULT_GRANT_PURPOSE",
        "result_access_token",
        "exp",
        "questionnaire_result_token_from_grant",
    ):
        if required not in result_access:
            errors.append(f"questionnaire result grant contract missing: {required}")
    for required in (
        "aicrm_next.admin_auth.public_result_grant",
        "issue_questionnaire_result_grant",
        "questionnaire_result_token_from_grant",
    ):
        if required not in result_access_compat:
            errors.append(f"questionnaire result grant compatibility export missing: {required}")

    return errors


def main() -> int:
    errors = check()
    print(
        json.dumps(
            redact_sensitive_data({
                "ok": not errors,
                "unsafe_fallback_count": sum("fallback" in error for error in errors),
                "actor_override_count": sum("actor" in error for error in errors),
                "tokenless_result_count": sum("questionnaire result" in error for error in errors),
                "error_count": len(errors),
            }),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    for error in errors:
        print(redact_sensitive_text(f"ERROR: {error}"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
