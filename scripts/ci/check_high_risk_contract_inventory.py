#!/usr/bin/env python3
"""Validate the current high-risk behavior map and fail-closed selector."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402
from scripts.ci.select_test_scope import classify, load_inventory  # noqa: E402


REQUIRED_BEHAVIOR_IDS = {
    "platform_admin_auth_and_config",
    "platform_external_effect_queue",
    "automation_tasks_queues_and_group_operations",
    "channels_wecom_callback_welcome_and_gateway",
    "commerce_orders_payments_refunds_and_entitlements",
    "questionnaire_submission_and_side_effects",
    "empty_postgres_to_current_schema",
    "exact_sha_health_lock_and_rollback",
}

REPRESENTATIVE_PATHS = {
    "migrations/versions/0166_auth_api_client_credential_hint.py": "migration_or_schema",
    "aicrm_next/platform/admin_auth/action_token.py": "authentication_or_identity",
    "aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py": "payment_refund_or_entitlement",
    "aicrm_next/channels/channel_entry/callback_ingress.py": "callback_or_external_effect",
    "aicrm_next/automation/automation_engine/group_ops/scheduler.py": "approved_high_risk_business_flow",
    ".github/workflows/deploy.yml": "production_or_deploy",
}


def check() -> list[str]:
    errors: list[str] = []
    inventory = load_inventory()
    behaviors = {
        str(item.get("id") or ""): item
        for item in inventory.get("behaviors", [])
        if isinstance(item, dict)
    }
    missing_behaviors = sorted(REQUIRED_BEHAVIOR_IDS - set(behaviors))
    if missing_behaviors:
        errors.append("high-risk behaviors missing: " + ", ".join(missing_behaviors))
    for behavior_id in sorted(REQUIRED_BEHAVIOR_IDS & set(behaviors)):
        tests = behaviors[behavior_id].get("tests", [])
        if not any(
            isinstance(target, dict)
            and str(target.get("layer") or "") in {"postgres", "high_risk", "release"}
            and (ROOT / str(target.get("path") or "")).is_file()
            for target in tests
        ):
            errors.append(f"{behavior_id} has no executable cloud high-risk contract")
    for path, reason in REPRESENTATIVE_PATHS.items():
        selection = classify([path])
        if selection.tier != "high_risk" or not selection.requires_postgres:
            errors.append(f"{path} does not upgrade to the complete high-risk tier")
        if reason not in selection.reason:
            errors.append(f"{path} high-risk reason does not include {reason}")
    deleted = classify(["aicrm_next/current_runtime.py"], deleted_files=["aicrm_next/current_runtime.py"])
    if deleted.tier != "high_risk" or "deleted_file" not in deleted.reason:
        errors.append("deleted runtime files must fail closed to high_risk")
    isolation = (ROOT / "tests" / "high_risk" / "conftest.py").read_text(encoding="utf-8")
    if "real network access is forbidden" not in isolation or "DATABASE_URL" not in isolation:
        errors.append("high-risk tests do not explicitly block network and production database access")
    return errors


def main() -> int:
    errors = check()
    print(redact_sensitive_text(json.dumps({"ok": not errors, "error_count": len(errors)}, sort_keys=True)))
    for error in errors:
        print(redact_sensitive_text(f"ERROR: {error}"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
