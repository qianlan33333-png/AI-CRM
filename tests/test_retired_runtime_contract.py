from __future__ import annotations

from pathlib import Path

from aicrm_next.main import create_app
from aicrm_next.platform_foundation.auth_platform.profiles import API_CLIENT_PROFILES
from aicrm_next.platform_foundation.background_jobs.contract import webhook_route_contracts
from aicrm_next.shared.retired_contracts import retired_external_effect_payload


ROOT = Path(__file__).resolve().parents[1]
CALLER_PATHS = (
    "aicrm_next/admin_jobs/application.py",
    "aicrm_next/admin_jobs/notification_settings.py",
    "aicrm_next/extensions/commerce/commerce/admin_refunds.py",
    "aicrm_next/extensions/commerce/commerce/external_push_admin.py",
    "aicrm_next/external_push/service.py",
    "aicrm_next/owner_migration/application.py",
)


def test_retired_external_effect_contract_is_stateless_and_explicit() -> None:
    payload = retired_external_effect_payload(
        "old_customer_webhook_delivery_retry",
        error="legacy_webhook_retry_disabled",
    )

    assert payload == {
        "ok": False,
        "error": "legacy_webhook_retry_disabled",
        "legacy_key": "old_customer_webhook_delivery_retry",
        "legacy_outbound_disabled": True,
        "external_effect_required": True,
        "migration_target": "external_effect_queue",
        "push_center_url": "/api/admin/push-center/jobs",
        "retirement_state": "physically_removed",
        "real_external_call_executed": False,
    }


def test_active_callers_do_not_import_or_write_legacy_cleanup_markers() -> None:
    for relative_path in CALLER_PATHS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "platform_foundation.legacy_cleanup" not in source, relative_path
        assert "LegacyWebhookCleanupService" not in source, relative_path
        assert "record_runtime_marker" not in source, relative_path


def test_legacy_cleanup_runtime_package_is_physically_removed() -> None:
    assert not any((ROOT / "aicrm_next/platform_foundation/legacy_cleanup").glob("*.py"))
    assert not (ROOT / "tests/test_legacy_webhook_cleanup.py").exists()


def test_legacy_cleanup_routes_jobs_and_capability_are_removed() -> None:
    route_paths = {route.path for route in create_app().routes}
    contract_paths = {contract.path for contract in webhook_route_contracts()}

    assert not any(path.startswith("/api/admin/legacy-webhook-cleanup") for path in route_paths)
    assert not any(path.startswith("/api/admin/legacy-webhook-cleanup") for path in contract_paths)
    assert all("legacy_cleanup_execute" not in profile.capabilities for profile in API_CLIENT_PROFILES)


def test_migration_0105_physically_drops_legacy_cleanup_tables() -> None:
    source = (ROOT / "migrations/versions/0105_drop_legacy_cleanup_tables.py").read_text(encoding="utf-8")

    assert 'revision = "0105_drop_legacy_cleanup_tables"' in source
    assert 'down_revision = "0104_auth_platform"' in source
    assert "DROP TABLE IF EXISTS legacy_webhook_cleanup_audit" in source
    assert "DROP TABLE IF EXISTS legacy_webhook_deprecation_registry" in source
    assert "historical rows are" in source
    assert "INSERT INTO legacy_webhook" not in source
