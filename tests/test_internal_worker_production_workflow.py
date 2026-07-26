from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "internal-worker-production-diagnostics.yml"


def test_internal_worker_owner_diagnostics_is_exact_release_and_read_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "expected_release_sha:" in source
    assert "DIAGNOSE AI-CRM INTERNAL WORKER OWNER READ ONLY" in source
    assert "https://www.youcangogogo.com/health" in source
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA"' in source
    assert "systemctl start" not in source
    assert "systemctl restart" not in source
    assert "systemctl stop" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "INSERT INTO" not in source
    assert '"real_external_call_executed": False' in source
    assert '"read_only": True' in source


def test_internal_worker_owner_diagnostics_proves_one_role_owner() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "aicrm-internal-worker.service" in source
    assert "run_execution_runtime.py --role internal_worker" in source
    assert "AICRM_QUEUE_RUNTIME_EXECUTE=1" in source
    assert "AICRM_QUEUE_CUTOVER_COMMITTED" in source
    assert "aicrm-internal-queue-runtime.service" in source
    assert "aicrm-inbox-queue-runtime.service" in source
    assert "aicrm-internal-worker-observer.service" in source
    assert "test ! -e \"/etc/systemd/system/$retired\"" in source
    assert "worker_id LIKE '%:role:internal_worker:%'" in source
    assert "worker_id NOT LIKE '%:role:internal_worker:%'" in source
    assert '"aicrm-internal_event-runtime": 2' in source
    assert '"aicrm-internal_outbox-runtime": 2' in source
    assert '"aicrm-webhook_inbox-runtime": 2' in source
    assert "competing_rows == []" in source
    assert 'row["owner_kind"] == "internal_worker"' in source
    assert '"single_internal_worker_owner": True' in source
    assert '"predecessor_units_retired": True' in source
    assert '"external_worker_unchanged": True' in source
    assert "payload_json" not in source
    assert "target_id" not in source
