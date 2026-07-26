from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deployment-profile-production-diagnostics.yml"


def test_deployment_profile_diagnostics_is_exact_release_and_read_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "expected_release_sha:" in source
    assert "DIAGNOSE AI-CRM DEPLOYMENT PROFILE READ ONLY" in source
    assert "https://www.youcangogogo.com/health" in source
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA"' in source
    assert "permissions:\n  contents: read" in source
    assert "systemctl start" not in source
    assert "systemctl restart" not in source
    assert "systemctl stop" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "INSERT INTO" not in source
    assert '"real_external_call_executed": False' in source
    assert '"read_only": True' in source


def test_deployment_profile_diagnostics_proves_live_process_and_composition_parity() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    profile_path = "/home/ubuntu/极简 crm/deploy/deployment_profiles/production-current.json"
    assert f"profile_path='{profile_path}'" in source
    assert 'test "${AICRM_DEPLOYMENT_PROFILE_PATH:-}" = "$profile_path"' in source
    assert 'sudo cat "/proc/$pid/environ"' in source
    assert "openclaw-wecom-postgres.service" in source
    assert "openclaw-wecom-callback-ingress.service" in source
    assert "aicrm-internal-queue-runtime.service" in source
    assert "aicrm-inbox-queue-runtime.service" in source
    assert "aicrm-external-queue-runtime.service" in source
    assert "aicrm-internal-worker-observer.service" in source
    assert 'profile.activation_mode == "enforce"' in source
    assert "profile.enabled_capabilities == expected_capabilities" in source
    assert "enforced_routes == observed_routes" in source
    assert "enforced_consumers == observed_consumers" in source
    assert "enforced_continuations == observed_continuations" in source
    assert "enforced_jobs == observed_jobs" in source
    assert "enforced_paths == observed_paths" in source
    assert '"observe_enforce_behavior_equivalent": True' in source
    assert "payload_json" not in source
    assert "target_id" not in source
