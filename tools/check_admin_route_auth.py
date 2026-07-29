from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_EXAMPLES = (
    "/admin/api-docs",
    "/setup/wizard",
    "/api/admin/channels",
    "/api/customers",
    "/api/users/unionid_1",
    "/api/messages/external_1",
)
PUBLIC_EXAMPLES = (
    "/health",
    "/login",
    "/api/h5/questionnaires/slug",
    "/api/wecom/events",
    "/wecom/external-contact/callback",
    "/api/sidebar/jssdk-config",
    "/api/sidebar/profile",
    "/api/sidebar/v2/workbench",
    "/static/admin_console/admin_console.css",
)


def check_admin_route_auth_gate() -> list[str]:
    errors: list[str] = []
    main_source = (ROOT / "aicrm_next" / "main.py").read_text(encoding="utf-8")
    guards_source = (ROOT / "aicrm_next" / "platform" / "admin_auth" / "guards.py").read_text(encoding="utf-8")
    if "route_policy_required_response" not in main_source:
        errors.append("aicrm_next/main.py must install route_policy_required_response middleware gate")
    if "def require_admin" not in guards_source:
        errors.append("aicrm_next/platform/admin_auth/guards.py must expose require_admin dependency")
    if "production_data_ready" not in guards_source:
        errors.append("admin auth enforcement must default on in production data / PostgreSQL mode")

    from aicrm_next.platform.admin_auth.guards import is_protected_admin_path

    for path in PROTECTED_EXAMPLES:
        if not is_protected_admin_path(path):
            errors.append(f"{path} must be classified as admin-auth protected")
    for path in PUBLIC_EXAMPLES:
        if is_protected_admin_path(path):
            errors.append(f"{path} must remain public or use its own signature mechanism")
    return errors


def main() -> int:
    errors = check_admin_route_auth_gate()
    if errors:
        print("Admin route auth check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Admin route auth check OK")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
