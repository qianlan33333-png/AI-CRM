from __future__ import annotations

from pathlib import Path

from aicrm_next.main import create_app
from aicrm_next.shared.route_ownership import collect_route_inventory


ROOT = Path(__file__).resolve().parents[1]

RETIRED_PAGE_ROUTES = {
    "/admin/group-invite-library",
    "/admin/jobs",
    "/admin/jobs/actions",
    "/admin/broadcast-jobs",
    "/admin/broadcast-jobs/{job_id}",
    "/admin/push-center",
    "/admin/push-center/jobs/{job_id}",
    "/admin/internal-events",
    "/admin/internal-events/{event_id}",
    "/admin/webhook-inbox",
    "/admin/data-health",
    "/admin/data-quality",
    "/admin/delivery-lineage",
    "/admin/growth-orchestration",
    "/admin/growth-orchestration/{program_key}",
}

RETIRED_DUPLICATE_API_ROUTES = {
    "/api/admin/data-quality/summary",
    "/api/admin/data-quality/groups",
    "/api/admin/data-quality/checks",
    "/api/admin/data-quality/checks/{check_id}",
    "/api/admin/growth-orchestration/programs",
    "/api/admin/growth-orchestration/members",
    "/api/admin/growth-orchestration/tasks",
    "/api/admin/growth-orchestration/touchpoints",
    "/api/admin/jobs/deferred-jobs/run",
    "/api/admin/jobs/webhook-deliveries/run",
    "/api/admin/jobs/webhook-deliveries/{delivery_id}/retry",
    "/api/admin/dashboard/shell-context",
}

PRESERVED_CANONICAL_API_ROUTES = {
    "/api/admin/group-invite-library",
    "/api/admin/jobs/summary",
    "/api/admin/jobs/deferred-jobs",
    "/api/admin/jobs/webhook-deliveries",
    "/api/admin/broadcast-jobs",
    "/api/admin/push-center/jobs",
    "/api/admin/push-center/stats",
    "/api/admin/internal-events",
    "/api/admin/internal-events/diagnostics",
    "/api/admin/webhook-inbox/metrics",
    "/api/admin/webhook-inbox/items",
    "/api/admin/data-health/summary",
    "/api/admin/data-health/checks",
    "/api/admin/delivery-lineage",
}

RETIRED_ASSETS = {
    "aicrm_next/frontend_compat/static/admin_console/admin_execution_ui.css",
    "aicrm_next/frontend_compat/static/admin_console/admin_execution_ui.js",
    "aicrm_next/frontend_compat/templates/admin_console/group_invite_library.html",
    "aicrm_next/admin_jobs/templates/admin_console/jobs.html",
    "aicrm_next/platform_foundation/push_center/templates/admin_console/push_center.html",
    "aicrm_next/platform_foundation/internal_events/templates/admin_console/internal_events.html",
    "aicrm_next/platform_foundation/webhook_inbox/templates/admin_console/webhook_inbox.html",
    "aicrm_next/admin_shell/templates/admin_shell/data_health.html",
    "aicrm_next/admin_shell/templates/admin_shell/data_quality.html",
    "aicrm_next/admin_shell/templates/admin_shell/delivery_lineage.html",
    "aicrm_next/admin_shell/templates/admin_shell/growth_orchestration.html",
    "aicrm_next/admin_shell/view_model.py",
    "aicrm_next/frontend_compat/templates/admin_console/dashboard.html",
}


def _runtime_paths() -> set[str]:
    return {item.path for item in collect_route_inventory(create_app())}


def test_monitoring_frontend_and_duplicate_facades_are_absent_from_runtime() -> None:
    paths = _runtime_paths()

    assert not paths & RETIRED_PAGE_ROUTES
    assert not paths & RETIRED_DUPLICATE_API_ROUTES


def test_canonical_database_backed_monitoring_apis_remain_registered() -> None:
    paths = _runtime_paths()

    assert PRESERVED_CANONICAL_API_ROUTES <= paths


def test_monitoring_frontend_assets_are_physically_deleted() -> None:
    for relative_path in RETIRED_ASSETS:
        assert not (ROOT / relative_path).exists(), relative_path

    assert not list((ROOT / "aicrm_next/growth_orchestration").glob("*.py"))
    assert not list((ROOT / "aicrm_next/frontend_compat/static/admin_console/p1").rglob("*.*"))
    assert not (ROOT / "aicrm_next/data_health/quality_registry.py").exists()
    assert not (ROOT / "aicrm_next/background_jobs/data_quality_snapshot.py").exists()
    assert not (ROOT / "scripts/run_data_quality_snapshot.py").exists()
