from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "internal-worker-observer-production-diagnostics.yml"
)


def test_internal_worker_observer_diagnostics_is_exact_release_and_read_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "expected_release_sha:" in source
    assert "DIAGNOSE AI-CRM INTERNAL WORKER OBSERVER READ ONLY" in source
    assert "https://www.youcangogogo.com/health" in source
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA"' in source
    assert "systemctl is-enabled --quiet \"$observer_unit\"" in source
    assert "systemctl is-active --quiet \"$observer_unit\"" in source
    assert "run_execution_runtime.py --role internal_worker --standby" in source
    assert "systemctl start" not in source
    assert "systemctl restart" not in source
    assert "systemctl stop" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "INSERT INTO" not in source
    assert '"real_external_call_executed": False' in source
    assert '"read_only": True' in source


def test_internal_worker_observer_diagnostics_proves_parallel_heartbeat_identity() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "worker_id LIKE '%:role:internal_worker:%'" in source
    assert "worker_id NOT LIKE '%:role:internal_worker:%'" in source
    assert '"aicrm-internal_event-runtime": 2' in source
    assert '"aicrm-internal_outbox-runtime": 2' in source
    assert '"aicrm-webhook_inbox-runtime": 2' in source
    assert 'row["rollout_mode"] == "standby"' in source
    assert "active_role_claims == []" in source
    assert '"observer_claimless": True' in source
    assert '"predecessors_authoritative": True' in source
    assert "payload_json" not in source
    assert "target_id" not in source
