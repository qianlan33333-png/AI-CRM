from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ai_audience_production_diagnostics_is_exact_release_read_only() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "ai-audience-production-diagnostics.yml"
    ).read_text(encoding="utf-8")

    assert "DIAGNOSE AI-CRM AI AUDIENCE PRODUCTION READ ONLY" in workflow
    assert "test \"$(git rev-parse HEAD)\" = \"$expected_release_sha\"" in workflow
    assert "test \"$public_sha\" = \"$expected_release_sha\"" in workflow
    assert "systemctl is-enabled --quiet aicrm-ai-audience-daily-intent.timer" in workflow
    assert "systemctl is-active --quiet aicrm-ai-audience-daily-intent.timer" in workflow
    assert "systemctl is-active --quiet aicrm-internal-worker.service" in workflow
    assert "runtime_heartbeat_state" in workflow
    assert "package_config_state" in workflow
    assert "incremental_sql_configured" in workflow
    assert "daily_sql_configured" in workflow
    assert "simple_compiled_sql_configured" in workflow
    assert "refresh_outbox_state" in workflow
    assert "refresh_event_state" in workflow
    assert "refresh_consumer_run_state" in workflow
    assert "refresh_intent_generation_state" in workflow
    assert "contains_package_or_target_identifiers\": False" in workflow
    assert "real_external_call_executed\": False" in workflow
    assert re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", workflow) is None
    assert "systemctl start" not in workflow
    assert "systemctl restart" not in workflow
    assert "--execute" not in workflow
