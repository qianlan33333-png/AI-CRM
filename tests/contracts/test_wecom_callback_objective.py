from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ops.check_wecom_callback_objective_coverage import OBJECTIVE_REQUIREMENTS, run


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_callback_objective_uses_only_current_local_proofs_and_stays_fail_closed_without_production_evidence() -> None:
    payload = run([])
    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    for proof in payload["test_proofs"].values():
        proof_path = str(proof["path"])
        assert proof["ok"] is True
        assert (ROOT / proof_path).is_file()
        assert not proof_path.startswith("tests/test_")


def test_callback_production_completion_still_requires_rollback_public_state_and_deploy_smoke() -> None:
    runtime = OBJECTIVE_REQUIREMENTS["runtime_isolation_and_backpressure"]
    operator = OBJECTIVE_REQUIREMENTS["operator_runbook_and_acceptance_report"]
    assert {"public_state_ok", "deploy_smoke_ok"} <= set(runtime["readiness"])
    assert {"rollback_ok", "public_state_ok", "deploy_smoke_ok"} <= set(operator["readiness"])


def test_callback_objective_accepts_complete_fixture_evidence_and_rejects_missing_rollback(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "ready_for_production_cutover": True,
        "ready_for_production_completion": True,
        "webhook_inbox_health": {"ok": True},
        "webhook_ingestion_evidence": {"ok": True},
        "webhook_processing_evidence": {"ok": True},
        "same_sample_evidence": {"ok": True},
        "admin_webhook_inbox_metrics": {"ok": True},
        "admin_webhook_inbox_items": {"ok": True},
        "admin_webhook_inbox_reconciliation": {"ok": True},
        "worker_isolation_evidence": {"ok": True},
        "internal_event_worker_isolation_evidence": {"ok": True},
        "downstream_worker_isolation_evidence": {"ok": True},
        "rollback_evidence": {"ok": True},
        "public_state_evidence": {"ok": True},
        "deploy_smoke_evidence": {"ok": True},
    }
    evidence_path = tmp_path / "callback-readiness.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    complete = run(["--readiness-file", str(evidence_path)])
    assert complete["ok"] is True
    assert all(item["production_evidence_ok"] is True for item in complete["objective_requirements"].values())

    payload["rollback_evidence"] = {"ok": False}
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    missing_rollback = run(["--readiness-file", str(evidence_path)])
    assert missing_rollback["production_completion_ready"] is False
    assert missing_rollback["ok"] is False
