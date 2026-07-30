#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_READ_ONLY_UNSAFE_PATHS = {
    "/api/admin/service-period-products/{service_product_id}/member-grid/query",
    "/api/public/service-period-member-grid/query",
}

_PUBLIC_SIDEBAR_ASSET_PATHS = {
    "/api/sidebar/v2/materials/image/{image_id}/thumbnail",
}

_HYBRID_ADMIN_EXACT_PATHS = {
    "/api/admin/cloud-orchestrator/campaigns/run-due",
    "/api/admin/cloud-orchestrator/campaigns/run-due/preview",
    "/api/admin/jobs/archive-sync/run",
    "/api/admin/jobs/message-batches/{batch_id}/ack",
    "/api/admin/jobs/order-identity-repair/run",
    "/api/admin/broadcast-jobs/feishu-hourly-report/run",
    "/api/admin/broadcast-jobs/notification-settings/feishu",
    "/api/admin/broadcast-jobs/notification-settings/feishu/validate",
    "/api/admin/broadcast-jobs/{job_id}/approve",
    "/api/admin/broadcast-jobs/{job_id}/cancel",
    "/api/admin/webhook-inbox/{inbox_id}/retry",
    "/api/admin/webhook-inbox/{inbox_id}/skip",
    "/api/admin/webhook-inbox/{inbox_id}/dispatch",
    "/api/admin/webhook-inbox/run-due",
    "/api/admin/push-center/jobs/{job_id}/retry",
    "/api/admin/push-center/jobs/{job_id}/cancel",
    "/api/admin/external-effects/jobs/{job_id}/retry",
    "/api/admin/external-effects/jobs/{job_id}/cancel",
    "/api/admin/external-effects/run-due",
    "/api/admin/external-effects/run-due/preview",
    "/api/admin/external-effects/test-loopback/jobs",
    "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run",
    "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry",
    "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip",
    "/api/admin/internal-events/run-due",
    "/api/admin/internal-events/run-due/preview",
}


def _methods(entry: dict[str, Any]) -> set[str]:
    return {str(method).upper() for method in entry.get("methods") or []}


def _is_write(entry: dict[str, Any]) -> bool:
    if str(entry.get("path") or "") in _READ_ONLY_UNSAFE_PATHS:
        return False
    return bool(_methods(entry) - SAFE_METHODS)


def _starts(path: str, *prefixes: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _admin_capability(entry: dict[str, Any]) -> str:
    path = str(entry["path"])
    owner = str(entry["capability_owner"])
    write = _is_write(entry)
    if "/service-period-products/" in path and any(
        marker in path for marker in ("/data", "/member-grid", "/member-views", "/members/")
    ):
        return "service_period_grid_access"
    if not write:
        if owner == "admin_config" or path.startswith("/admin/config"):
            return "manage_config"
        if any(marker in path for marker in ("/export", "/identity/", "/messages", "/archive")):
            return "read_customer"
        return "admin_read"
    if owner == "admin_config":
        if "login-access" in path or "admin-user" in path:
            return "manage_admin"
        return "manage_config"
    if owner == "questionnaire":
        return "manage_questionnaire"
    if owner in {"automation_engine", "cloud_orchestrator"}:
        return "manage_group_ops" if "group-ops" in path else "manage_automation"
    if owner in {"automation_agents", "ai_audience_ops", "ai_assist"}:
        return "manage_automation"
    if owner in {"customer_tags", "customer_read_model", "sidebar_write", "identity_contact", "owner_migration"}:
        return "manage_customer"
    if owner in {"media_library", "send_content"}:
        return "manage_content"
    if owner in {"commerce", "service_period", "public_product"}:
        return "refund" if "refund" in path else "manage_commerce"
    if owner in {"message_archive", "ops_enrollment"}:
        return "send_message"
    return "manage_operations"


def _pii_level(entry: dict[str, Any]) -> str:
    path = str(entry["path"]).lower()
    owner = str(entry["capability_owner"])
    if path == "/api/external/radar-clicks":
        return "sensitive"
    if any(marker in path for marker in ("message", "archive", "questionnaire", "identity", "customer", "user", "sidebar")):
        return "sensitive"
    if any(marker in path for marker in ("order", "payment", "refund", "wechat-pay", "alipay", "service-period", "coupon")):
        return "financial"
    if owner in {"customer_tags", "owner_migration", "ops_enrollment"}:
        return "customer"
    if path.startswith(("/admin", "/api/admin", "/setup")):
        return "internal"
    return "none"


def _hybrid_admin_route(path: str) -> bool:
    return path in _HYBRID_ADMIN_EXACT_PATHS


def _service_period_grid_route(path: str) -> bool:
    return "/service-period-products/" in path and any(
        marker in path for marker in ("/data", "/member-grid", "/member-views", "/members/")
    )


def _machine_capability(path: str) -> str:
    if "cloud-orchestrator" in path:
        return "cloud_run_due_execute"
    if "/webhook-inbox/" in path:
        return "webhook_inbox_execute"
    if "/push-center/" in path:
        return "push_queue_execute"
    if "/external-effects/" in path:
        return "external_effect_execute"
    if "/internal-events/" in path:
        return "internal_event_execute"
    return "jobs_execute"


def _machine_purpose(path: str) -> str:
    if "archive-sync" in path:
        return "archive"
    if "webhook-deliveries" in path:
        return "callback"
    return "automation_worker"


def _callback_auth(entry: dict[str, Any]) -> str:
    path = str(entry["path"])
    if path.startswith(("/wecom/external-contact/callback", "/api/wecom/events")):
        return "provider_signature"
    if any(marker in path for marker in ("wechat-pay", "alipay", "wechat-shop")) and "notify" in path:
        return "provider_signature"
    if path.startswith("/auth/wecom/callback") or path.startswith("/api/sidebar/oauth/callback") or "oauth/callback" in path:
        return "provider_oauth_state"
    return "webhook_hmac"


def _callback_capability(path: str) -> str:
    if "/group-ops/webhooks/" in path:
        return "group_ops_webhook_receive"
    if "/agents/" in path and "audience-webhook" in path:
        return "automation_agent_webhook_receive"
    if "/ai/audience/" in path:
        return "ai_audience_webhook_receive"
    if "activation-webhook" in path:
        return "activation_webhook_receive"
    if "test-receiver" in path:
        return "external_effect_receipt_receive"
    return "callback_receive"


def _policy_for(entry: dict[str, Any]) -> dict[str, Any]:
    path = str(entry["path"])
    owner = str(entry["capability_owner"])
    write = _is_write(entry)
    unsafe_method = bool(_methods(entry) - SAFE_METHODS)

    if path in {"/login", "/logout"}:
        return _policy("admin", "public", "public", "public", _pii_level(entry), False, "auth_strict")

    if path == "/oauth/token":
        return _policy(
            "external_integration",
            "client_credentials",
            "client_token_issue",
            "service",
            "none",
            False,
            "auth_strict",
        )

    if _hybrid_admin_route(path):
        return _policy(
            "admin",
            "human_or_service",
            _admin_capability(entry),
            "global",
            _pii_level(entry),
            unsafe_method,
            "authenticated",
            service_audience="internal_worker",
            service_capability=_machine_capability(path),
            client_purpose=_machine_purpose(path),
        )

    if _service_period_grid_route(path):
        return _policy(
            "admin",
            "human_session",
            "service_period_grid_access",
            "single_resource",
            "sensitive",
            unsafe_method,
            "authenticated",
        )

    if _starts(path, "/admin", "/api/admin", "/setup"):
        return _policy(
            "admin",
            "human_session",
            _admin_capability(entry),
            "global",
            _pii_level(entry),
            unsafe_method,
            "authenticated",
        )

    if path == "/api/automation/group-ops/broadcast":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "group_broadcast_execute",
            "service",
            _pii_level(entry),
            False,
            "integration",
            client_purpose="group_broadcast",
        )

    payment_identity_routes = {
        "/api/h5/wechat-pay/jsapi/orders": "payment_order_create",
        "/api/h5/wechat-pay/orders/{out_trade_no}": "payment_order_read",
        "/api/h5/service-period-products/{link_slug}/wechat-pay/jsapi/orders": "payment_order_create",
        "/api/h5/coupons/available": "coupon_available_read",
        "/api/h5/coupons/{public_slug}/claim": "coupon_claim",
    }
    if path in payment_identity_routes:
        return _policy(
            "public_h5",
            "payment_identity_session",
            payment_identity_routes[path],
            "self",
            _pii_level(entry),
            False,
            "public_strict",
        )

    if path.startswith("/api/automation/group-ops/") and "/webhooks/" not in path:
        return _policy(
            "admin",
            "human_session",
            "manage_group_ops" if write else "admin_read",
            "global",
            _pii_level(entry),
            write,
            "authenticated",
        )

    if _starts(path, "/api/sidebar"):
        if path.startswith("/api/sidebar/oauth/"):
            return _policy("sidebar", "provider_oauth_state", "sidebar_read", "self", "none", False, "public_strict")
        if path == "/api/sidebar/jssdk-config":
            return _policy("sidebar", "public", "sidebar_bootstrap", "self", "none", False, "public_strict")
        if path in _PUBLIC_SIDEBAR_ASSET_PATHS:
            return _policy("sidebar", "public", "public", "single_resource", "none", False, "public_standard")
        return _policy(
            "sidebar",
            "sidebar_grant",
            "sidebar_write" if write else "sidebar_read",
            "owner",
            _pii_level(entry),
            False,
            "authenticated",
        )

    if path.startswith("/sidebar/"):
        return _policy("sidebar", "public", "sidebar_bootstrap", "self", "none", False, "public_strict")

    if path == "/shared/service-period-member-grid":
        return _policy("public_h5", "public", "public", "public", "none", False, "public_standard")

    if _starts(path, "/api/public/service-period-member-grid"):
        return _policy(
            "public_h5",
            "public",
            "public",
            "single_resource",
            "sensitive",
            False,
            "public_strict",
        )

    callback_markers = ("callback", "notify", "webhook", "test-receiver")
    if path in {"/api/wecom/events"} or ("/webhook-deliveries" not in path and any(marker in path for marker in callback_markers)):
        return _policy(
            "callback",
            _callback_auth(entry),
            _callback_capability(path),
            "single_resource",
            _pii_level(entry),
            False,
            "callback_burst",
        )

    public_prefixes = (
        "/api/h5",
        "/c",
        "/s",
        "/p",
        "/pay",
        "/r",
        "/radar/view",
        "/api/products",
        "/api/checkout",
        "/api/orders",
        "/api/wechat-pay",
        "/api/alipay",
        "/api/wechat-shop",
    )
    if _starts(path, *public_prefixes):
        blocked_write = str(entry.get("route_name") or "").endswith(("blocked_write", "unknown", "unknown_child"))
        if path.startswith("/api/h5/questionnaires/") and path.endswith("/result"):
            return _policy(
                "public_h5",
                "public_result_grant",
                "public_result_read",
                "single_resource",
                "sensitive",
                False,
                "public_strict",
            )
        return _policy(
            "public_h5",
            "public",
            "public_blocked" if blocked_write else "public",
            "single_resource",
            _pii_level(entry),
            False,
            "public_strict" if write else "public_standard",
        )

    if path == "/{filename}" and entry.get("route_name") == "wechat_domain_verification_file":
        return _policy("external_integration", "public", "domain_verification_read", "public", "none", False, "public_standard")

    if path in {"/health", "/api/system/health"}:
        return _policy("external_integration", "public", "health_read", "public", "none", False, "health")

    if path == "/api/system/runtime-route-map":
        return _policy(
            "internal_worker",
            "api_client_jwt",
            "runtime_route_read",
            "service",
            "internal",
            False,
            "internal",
            client_purpose="automation_worker",
        )

    if path == "/mcp":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "mcp_execute" if write else "mcp_read",
            "service",
            "sensitive",
            False,
            "internal",
            client_purpose="mcp",
        )

    if path == "/api/identity/resolve":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "identity_resolve",
            "service",
            "sensitive",
            False,
            "internal",
            client_purpose="identity",
        )

    if path == "/api/operation-cycles/reports":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "operation_cycle_report_write",
            "service",
            "internal",
            False,
            "integration",
            client_purpose="ops_reporter",
        )

    if path == "/api/operation-cycles/context-index" or (
        path.startswith("/api/operation-cycles/strategies/") and path.endswith("/context")
    ):
        return _policy(
            "external_integration",
            "api_client_jwt",
            "operation_cycle_context_read",
            "service",
            "internal",
            False,
            "integration",
            client_purpose="campaign_agent",
        )

    if path == "/api/operation-cycles/strategy-change-proposals":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "operation_cycle_strategy_propose",
            "service",
            "internal",
            False,
            "integration",
            client_purpose="campaign_agent",
        )

    if path.startswith(("/api/customers", "/api/users", "/api/messages")):
        return _policy(
            "external_integration",
            "human_session",
            "send_message" if write else "read_customer",
            "global",
            "sensitive",
            write,
            "authenticated",
        )

    if _starts(path, "/api/archive"):
        return _policy(
            "internal_worker",
            "api_client_jwt",
            "archive_execute" if write else "archive_read",
            "service",
            _pii_level(entry),
            False,
            "internal",
            client_purpose="archive",
        )

    if _starts(path, "/api/internal"):
        return _policy(
            "internal_worker",
            "api_client_jwt",
            "internal_execute" if write else "internal_read",
            "service",
            _pii_level(entry),
            False,
            "internal",
            client_purpose="automation_worker",
        )

    if path == "/api/ai-assist/external/campaigns" or path.startswith("/api/ai-assist/external/campaigns/"):
        return _policy(
            "external_integration",
            "api_client_jwt",
            "campaign_draft_create" if write else "campaign_status_read",
            "service",
            _pii_level(entry),
            False,
            "integration",
            client_purpose="campaign_agent",
        )

    if path == "/api/ai-assist/external/campaign-preparations":
        return _policy(
            "external_integration",
            "api_client_jwt",
            "campaign_preparation_create",
            "service",
            "sensitive",
            False,
            "integration",
            client_purpose="campaign_agent",
        )

    if path.startswith("/api/ai-assist/external/campaign-preparations/"):
        return _policy(
            "external_integration",
            "api_client_jwt",
            "campaign_preparation_commit" if write else "campaign_preparation_read",
            "service",
            "internal" if write else "none",
            False,
            "integration",
            client_purpose="campaign_agent",
        )

    external_prefixes = (
        "/api/external",
        "/api/ai",
        "/api/ai-assist",
        "/api/customer-automation",
    )
    if _starts(path, *external_prefixes):
        return _policy(
            "external_integration",
            "api_client_jwt",
            "external_write" if write else "external_read",
            "service",
            _pii_level(entry),
            False,
            "integration",
        )

    if owner == "auth_wecom" or path.startswith("/auth/wecom"):
        return _policy("admin", "provider_oauth_state", "public", "public", "none", False, "auth_strict")

    raise ValueError(f"unclassified route policy: {','.join(sorted(_methods(entry)))} {path} {entry.get('route_name')}")


def _policy(
    audience: str,
    auth_scheme: str,
    capability: str,
    access_scope: str,
    pii_level: str,
    csrf: bool,
    rate_limit: str,
    *,
    client_purpose: str = "",
    service_audience: str = "",
    service_capability: str = "",
) -> dict[str, Any]:
    principal_types = {
        "api_client_jwt": ["api_client", "service"],
        "client_credentials": ["api_client", "service"],
        "human_or_service": ["human", "service"],
        "human_session": ["human"],
        "payment_identity_session": ["public"],
        "provider_oauth_state": ["provider_callback"],
        "provider_signature": ["provider_callback"],
        "public": ["public"],
        "public_result_grant": ["public"],
        "sidebar_grant": ["human"],
        "webhook_hmac": ["api_client"],
    }[auth_scheme]
    policy = {
        "audience": audience,
        "auth_scheme": auth_scheme,
        "capability": capability,
        "access_scope": access_scope,
        "pii_level": pii_level,
        "csrf": csrf,
        "rate_limit": rate_limit,
        "principal_types": principal_types,
    }
    if client_purpose:
        policy["client_purpose"] = client_purpose
    if auth_scheme == "human_or_service":
        policy["service_audience"] = service_audience
        policy["service_capability"] = service_capability
    return policy


def update_manifest(path: Path, *, check: bool) -> int:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    routes = raw.get("routes") if isinstance(raw, dict) else None
    if not isinstance(routes, list):
        raise SystemExit("route ownership manifest must contain a routes list")
    changed = 0
    for entry in routes:
        if not isinstance(entry, dict):
            raise SystemExit("route ownership manifest entries must be mappings")
        expected = _policy_for(entry)
        expected_requires_auth = expected["auth_scheme"] != "public"
        for key, value in {"requires_auth": expected_requires_auth, **expected}.items():
            if entry.get(key) != value:
                changed += 1
                entry[key] = value
        for obsolete in (
            "client_purpose",
            "service_audience",
            "service_capability",
        ):
            if obsolete not in expected and obsolete in entry:
                changed += 1
                entry.pop(obsolete)
    if check:
        if changed:
            raise SystemExit(f"route policy manifest drift: {changed} field values differ; run with --write")
        print(f"route policy manifest ok: {len(routes)} routes")
        return 0
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"updated {len(routes)} route policies ({changed} field values changed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return update_manifest(args.manifest, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
