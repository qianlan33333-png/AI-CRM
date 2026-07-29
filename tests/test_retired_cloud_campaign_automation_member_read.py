from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READ_MODEL = ROOT / "aicrm_next" / "extensions" / "growth" / "cloud_orchestrator" / "campaigns_read.py"


def test_cloud_campaign_member_read_model_does_not_join_retired_automation_member() -> None:
    source = READ_MODEL.read_text(encoding="utf-8")

    assert "LEFT JOIN automation_member" not in source
    assert "automation_member am" not in source
    assert "am.phone" not in source
    assert "am.current_pool" not in source
    assert "am.current_audience_code" not in source
    assert "am.profile_segment_key" not in source
    assert "am.behavior_tier_key" not in source
