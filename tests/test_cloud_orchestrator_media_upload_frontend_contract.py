from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEXT_PLAN_TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/cloud_plan_review.html"
NEXT_PLAN_JS = ROOT / "aicrm_next/app/admin_console/static/admin_console/cloud_plan_review.js"
NEXT_CAMPAIGN_TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/cloud_campaigns_workspace.html"
INVENTORY = ROOT / "docs/archive/route_inventory/cloud_orchestrator_media_upload_route_inventory.md"


def test_cloud_plan_review_uses_material_picker_not_direct_legacy_upload_url():
    combined = NEXT_PLAN_TEMPLATE.read_text(encoding="utf-8") + "\n" + NEXT_PLAN_JS.read_text(encoding="utf-8")

    assert "material_picker.js" in combined
    assert "send_content_composer.js" in combined
    assert "/api/admin/cloud-orchestrator/media/upload" not in combined


def test_cloud_campaign_workspace_step_editor_uses_material_library_ids():
    source = NEXT_CAMPAIGN_TEMPLATE.read_text(encoding="utf-8")

    assert "image_library_ids" in source
    assert "image_media_ids" in source
    assert "material_picker.js" in source
    assert "send_content_composer.js" in source


def test_cloud_orchestrator_media_upload_inventory_marks_api_only_or_deprecated_callers():
    source = INVENTORY.read_text(encoding="utf-8")

    assert "Deprecated legacy campaign step image upload bridge" in source
    assert "API-only/deprecated for this page" in source
    assert "UploadCloudOrchestratorMediaCommand" in source
