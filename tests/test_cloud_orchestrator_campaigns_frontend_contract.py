from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/cloud_campaigns_workspace.html"


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_workspace_template_calls_next_read_campaign_apis():
    source = _source()

    assert "fetch('/api/admin/cloud-orchestrator/campaigns?' + params.toString())" in source
    assert "fetch('/api/admin/cloud-orchestrator/campaigns/' + encodeURIComponent(code))" in source
    assert "fetch(`/api/admin/cloud-orchestrator/campaigns/${encodeURIComponent(drawerCampCode)}/members?` + params.toString())" in source
    assert "/api/admin/cloud-orchestrator/campaigns/run-due" not in source
    assert "production_compat" not in source


def test_workspace_write_controls_call_next_commandbus_routes():
    source = _source()

    assert "const CAMPAIGN_WRITE_DISABLED = false;" in source
    assert "全部启动" in source
    assert "/api/admin/cloud-orchestrator/campaigns/batch-start" in source
    assert "/approve" in source
    assert "/start" in source
    assert "/reject" in source
    assert "/pause" in source
    assert "cloud-camp-approve" in source
    assert "CAMPAIGN_WRITE_DISABLED_MESSAGE" in source
    assert "Next CommandBus" in source
    assert "Idempotency-Key" in source
    assert "const editable = !CAMPAIGN_WRITE_DISABLED" in source
    assert "const startable = !CAMPAIGN_WRITE_DISABLED" in source


def test_campaign_read_urls_do_not_point_to_legacy_or_compat_surfaces():
    source = _source()

    assert "production_compat" not in source
    assert "fetch('/admin/cloud-orchestrator/campaigns" not in source
    assert 'fetch("/admin/cloud-orchestrator/campaigns' not in source
