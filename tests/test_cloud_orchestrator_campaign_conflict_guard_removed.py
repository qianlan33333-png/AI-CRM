from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT / "aicrm_next" / "extensions" / "growth" / "cloud_orchestrator" / "repository.py"


def test_campaign_approve_start_no_longer_blocks_on_active_member_conflicts() -> None:
    source = REPOSITORY.read_text(encoding="utf-8")

    assert "campaign has active member conflicts" not in source
    assert "STRING_AGG(DISTINCT cm.external_contact_id || '->' || other_c.campaign_code" not in source
    assert "other_cm.external_contact_id = cm.external_contact_id" not in source
