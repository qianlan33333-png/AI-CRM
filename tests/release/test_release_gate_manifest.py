from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aicrm_next.insights.data_health import checks as health_checks
from aicrm_next.platform.release_governance.contracts import ReleaseGateResult
from aicrm_next.platform.release_governance.evaluator import (
    evaluate_data_health_release_gates,
    release_gate_set_payload,
)
from aicrm_next.platform.release_governance.manifest import load_release_gate_manifest
from scripts.ci.check_release_gate_manifest import validate
from scripts.ops.check_admin_read_pages_smoke import (
    DATA_HEALTH_SUMMARY_PATH,
    _admin_api_payload_error,
)


pytestmark = pytest.mark.release
ROOT = Path(__file__).resolve().parents[2]


def test_release_gate_manifest_has_unique_existing_ci_contracts() -> None:
    manifest = load_release_gate_manifest()
    assert len(manifest.gates) == len({gate.gate_id for gate in manifest.gates})
    assert all((ROOT / gate.ci_contract.split("::", 1)[0]).is_file() for gate in manifest.gates)
    assert validate()["ok"] is True


def test_manifest_data_health_registry_matches_current_checkers(monkeypatch) -> None:
    monkeypatch.setattr(health_checks, "database_schema_available", lambda: False)
    actual = tuple(sorted(check.check_id for check in health_checks.run_all_checks()))
    assert actual == load_release_gate_manifest().data_health_check_ids


def test_release_gate_result_is_aggregate_only_and_actionable_when_blocked() -> None:
    result = ReleaseGateResult(
        gate_id="current_migration_head",
        phase="pre_mutation",
        decision="block",
        reason_code="migration_incompatible",
        summary="database schema is not compatible",
        owner="platform_foundation",
        remediation="apply an expand-only migration",
        replay_policy="rerun_after_new_ci",
        evidence={"current_revisions": ["revision"]},
    )
    assert result.schema_version == "release_gate_result.v1"
    assert result.pii_included is False
    assert result.real_external_call_executed is False
    with pytest.raises(ValidationError):
        ReleaseGateResult(
            gate_id="unsafe_gate",
            phase="pre_merge_prod",
            decision="warn",
            reason_code="raw_identity_exposed",
            summary="unsafe",
            owner="platform_foundation",
            evidence={"external_userid": "forbidden"},
        )


def _data_health_payload() -> dict[str, object]:
    checks = [
        {
            "check_id": check_id,
            "status": "ok",
            "gate_decision": "pass",
            "reason_code": "healthy",
        }
        for check_id in load_release_gate_manifest().data_health_check_ids
    ]
    return {
        "ok": True,
        "registry_matches_manifest": True,
        "counts": {"ok": len(checks), "warn": 0, "fail": 0, "not_applicable": 0},
        "checks": checks,
    }


def test_admin_release_smoke_allows_classified_business_warning() -> None:
    payload = _data_health_payload()
    check = next(
        item
        for item in payload["checks"]
        if item["check_id"] == "external_effect_unclassified_terminal_recent"
    )
    check.update(
        status="warn",
        gate_decision="warn",
        reason_code="external_effect_classified_terminal_history",
    )
    payload["counts"] = {
        "ok": len(payload["checks"]) - 1,
        "warn": 1,
        "fail": 0,
        "not_applicable": 0,
    }
    assert _admin_api_payload_error(DATA_HEALTH_SUMMARY_PATH, json.dumps(payload)) == ""


def test_admin_release_smoke_reports_exact_blocking_gate() -> None:
    payload = _data_health_payload()
    check = next(
        item
        for item in payload["checks"]
        if item["check_id"] == "external_effect_unclassified_blocked_recent"
    )
    check.update(
        status="fail",
        gate_decision="block",
        reason_code="external_effect_unclassified_blocked_recent",
    )
    payload["ok"] = False
    payload["counts"] = {
        "ok": len(payload["checks"]) - 1,
        "warn": 0,
        "fail": 1,
        "not_applicable": 0,
    }
    error = _admin_api_payload_error(DATA_HEALTH_SUMMARY_PATH, json.dumps(payload))
    assert error == (
        "data_health_release_blocked:"
        "external_effect_unclassified_blocked_recent:"
        "external_effect_unclassified_blocked_recent"
    )


def test_release_evaluator_keeps_classified_terminal_as_warning() -> None:
    payload = _data_health_payload()
    check = next(
        item
        for item in payload["checks"]
        if item["check_id"] == "external_effect_unclassified_terminal_recent"
    )
    check.update(
        status="warn",
        gate_decision="warn",
        reason_code="external_effect_classified_terminal_history",
        candidate_related=False,
    )
    results = evaluate_data_health_release_gates(payload, phase="pre_merge_prod")
    result_set = release_gate_set_payload(results)
    by_id = {result.gate_id: result for result in results}
    assert result_set["decision"] == "warn"
    assert by_id["data_health_registry"].decision == "pass"
    assert by_id["external_effect_delivery"].decision == "warn"
    assert by_id["external_effect_delivery"].candidate_related == "unknown"


def test_release_evaluator_fails_closed_for_missing_or_blocking_snapshot() -> None:
    unavailable = evaluate_data_health_release_gates(
        {"ok": False, "error_code": "data_health_snapshot_stale"},
        phase="pre_mutation",
    )
    assert all(result.decision == "block" for result in unavailable)

    payload = _data_health_payload()
    check = next(
        item
        for item in payload["checks"]
        if item["check_id"] == "external_effect_unclassified_terminal_recent"
    )
    check.update(
        status="fail",
        gate_decision="block",
        reason_code="external_effect_unclassified_terminal_recent",
        candidate_related=True,
    )
    results = evaluate_data_health_release_gates(payload, phase="post_cutover")
    external = next(result for result in results if result.gate_id == "external_effect_delivery")
    assert external.decision == "block"
    assert external.candidate_related is True
    assert external.evidence["blocking_reason_codes"] == [
        "external_effect_unclassified_terminal_recent:external_effect_unclassified_terminal_recent"
    ]
