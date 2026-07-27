from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/cloud_campaigns_workspace.html"


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_campaign_write_controls_are_enabled_and_describe_commandbus_boundary():
    source = _source()

    assert "const CAMPAIGN_WRITE_DISABLED = false;" in source
    assert "Next CommandBus" in source
    assert "不真实发送" in source
    assert "不运行 runtime" in source
    assert "cloud-camp-approve" in source
    assert "cloud-camp-start" in source
    assert "cloud-camp-reject" in source
    assert "cloud-camp-pause" in source
    assert "cloud-camp-delete" in source


def test_campaign_write_controls_call_existing_next_write_urls_with_idempotency_headers():
    source = _source()

    expected_urls = [
        "/api/admin/cloud-orchestrator/campaigns/batch-start",
        "/approve",
        "/start",
        "/reject",
        "/pause",
        "/steps/${encodeURIComponent(step.step_index)}",
        "/steps/${encodeURIComponent(stepIndex)}",
    ]
    for item in expected_urls:
        assert item in source

    assert "Idempotency-Key" in source
    assert "commandHeaders('approve')" in source
    assert "commandHeaders('start')" in source
    assert "commandHeaders('batch-start')" in source
    assert "/api/admin/cloud-orchestrator/campaigns/run-due" not in source
    assert "production_compat" not in source
