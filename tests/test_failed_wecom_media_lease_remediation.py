from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.ops.remediate_failed_wecom_media_lease import (
    RemediationError,
    load_manifest,
    run_remediation,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SHA = "a" * 40


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "operation_id": "pytest_failed_media_lease",
        "expected_production_sha": PRODUCTION_SHA,
        "expected_candidate_count": 1,
        "expected_health_evidence": {
            "total_count": 791,
            "ready_count": 789,
            "refresh_due_count": 789,
            "refreshing_count": 0,
            "failed_count": 1,
            "invalid_source_count": 0,
            "canary_failed_count": 0,
            "canary_invalid_source_count": 0,
            "expired_count": 1,
            "source_gap_count": 0,
        },
        "execute_confirmation": "EXECUTE_PYTEST_FAILED_MEDIA_LEASE",
    }


def _candidate() -> dict:
    return {
        "id": 7,
        "tenant_id": "aicrm",
        "corp_id": "corp-private",
        "material_kind": "image",
        "material_id": 42,
        "upload_kind": "image",
        "status": "failed",
        "last_error_code": "external_call_unknown",
        "durable_source_available": True,
    }


def _health(status: str) -> dict:
    evidence = dict(_manifest()["expected_health_evidence"])
    if status == "ok":
        evidence.update(
            {
                "ready_count": 790,
                "refresh_due_count": 788,
                "failed_count": 0,
            }
        )
    return {"status": status, "evidence": evidence}


class _Tracker:
    def __init__(self) -> None:
        self.call_count = 0


class _Manager:
    def __init__(self, state: dict, tracker: _Tracker, *, fail: bool = False) -> None:
        self._state = state
        self._tracker = tracker
        self._fail = fail

    def ensure_ready(self, material_kind: str, material_id: int, *, upload_kind: str, force_refresh: bool):
        assert (material_kind, material_id, upload_kind, force_refresh) == ("image", 42, "image", True)
        self._tracker.call_count += 1
        if self._fail:
            raise RuntimeError("provider detail must not escape")
        self._state["applied"] = True
        return {"status": "ready", "media_id": "new-private-media-id"}


def test_failed_media_lease_remediation_previews_applies_and_is_idempotent(tmp_path) -> None:
    state = {"applied": False}

    def health_loader():
        return _health("ok" if state["applied"] else "warn")

    def candidate_loader():
        return [] if state["applied"] else [_candidate()]

    tracker = _Tracker()
    manager_factory = lambda: (_Manager(state, tracker), tracker)
    manifest = _manifest()

    preview = run_remediation(
        manifest,
        mode="preview",
        confirmation="",
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        health_loader=health_loader,
        candidate_loader=candidate_loader,
        manager_bundle_factory=manager_factory,
    )
    assert preview == {
        "ok": True,
        "operation_id": "pytest_failed_media_lease",
        "status": "ready",
        "candidate_count": 1,
        "durable_source_available_count": 1,
        "database_write_executed": False,
        "real_external_call_executed": False,
        "backup_created": False,
        "backup_path": "",
        "backup_sha256": "",
        "contains_raw_material_identifier": False,
    }
    assert tracker.call_count == 0

    with pytest.raises(RemediationError, match="execute_confirmation_invalid"):
        run_remediation(
            manifest,
            mode="apply",
            confirmation="WRONG",
            backup_dir=tmp_path,
            current_release_sha=PRODUCTION_SHA,
            health_loader=health_loader,
            candidate_loader=candidate_loader,
            manager_bundle_factory=manager_factory,
        )

    applied = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        health_loader=health_loader,
        candidate_loader=candidate_loader,
        manager_bundle_factory=manager_factory,
    )
    assert applied["status"] == "applied"
    assert applied["candidate_count"] == 1
    assert applied["database_write_executed"] is True
    assert applied["real_external_call_executed"] is True
    assert applied["contains_raw_material_identifier"] is False
    assert tracker.call_count == 1
    backup_path = Path(applied["backup_path"])
    assert backup_path.exists()
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["lease_row"]["material_id"] == 42

    repeated = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        health_loader=health_loader,
        candidate_loader=candidate_loader,
        manager_bundle_factory=manager_factory,
    )
    assert repeated["status"] == "already_applied"
    assert repeated["database_write_executed"] is False
    assert repeated["real_external_call_executed"] is False
    assert tracker.call_count == 1


def test_failed_media_lease_remediation_rejects_release_health_or_source_drift(tmp_path) -> None:
    manifest = _manifest()
    with pytest.raises(RemediationError, match="production_release_sha_changed"):
        run_remediation(
            manifest,
            mode="preview",
            confirmation="",
            backup_dir=tmp_path,
            current_release_sha="b" * 40,
        )

    changed_health = _health("warn")
    changed_health["evidence"]["failed_count"] = 2
    with pytest.raises(RemediationError, match="unexpected_health_envelope"):
        run_remediation(
            manifest,
            mode="preview",
            confirmation="",
            backup_dir=tmp_path,
            current_release_sha=PRODUCTION_SHA,
            health_loader=lambda: changed_health,
            candidate_loader=lambda: [_candidate()],
        )

    missing_source = {**_candidate(), "durable_source_available": False}
    with pytest.raises(RemediationError, match="candidate_durable_source_unavailable"):
        run_remediation(
            manifest,
            mode="preview",
            confirmation="",
            backup_dir=tmp_path,
            current_release_sha=PRODUCTION_SHA,
            health_loader=lambda: _health("warn"),
            candidate_loader=lambda: [missing_source],
        )


def test_failed_media_lease_remediation_reports_provider_boundary_without_leaking_details(tmp_path) -> None:
    tracker = _Tracker()
    state = {"applied": False}
    with pytest.raises(RemediationError) as exc_info:
        run_remediation(
            _manifest(),
            mode="apply",
            confirmation="EXECUTE_PYTEST_FAILED_MEDIA_LEASE",
            backup_dir=tmp_path,
            current_release_sha=PRODUCTION_SHA,
            health_loader=lambda: _health("warn"),
            candidate_loader=lambda: [_candidate()],
            manager_bundle_factory=lambda: (_Manager(state, tracker, fail=True), tracker),
        )
    assert exc_info.value.code == "media_lease_refresh_failed"
    assert exc_info.value.details == {"real_external_call_executed": True}
    assert "provider detail" not in str(exc_info.value.details)


def test_failed_media_lease_manifest_and_deploy_gate_match_observed_envelope() -> None:
    manifest = load_manifest(ROOT / "deploy/failed_wecom_media_lease_remediation.json")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert manifest["expected_production_sha"] == "fd03cc53b094044d9ae798aa214606155b925f22"
    assert manifest["expected_candidate_count"] == 1
    assert manifest["expected_health_evidence"]["failed_count"] == 1
    assert manifest["expected_health_evidence"]["invalid_source_count"] == 0
    assert manifest["expected_health_evidence"]["source_gap_count"] == 0
    remediation_index = workflow.index("- name: Repair authorized failed WeCom media lease")
    preview_index = workflow.index("--mode preview", remediation_index)
    apply_index = workflow.index("--mode apply", preview_index)
    deploy_index = workflow.index("- name: Deploy via SSH", apply_index)
    assert remediation_index < preview_index < apply_index < deploy_index
    block = workflow[remediation_index:deploy_index]
    assert "fd03cc53b094044d9ae798aa214606155b925f22" in block
    assert "scripts/ops/remediate_failed_wecom_media_lease.py" in block
    assert "EXECUTE_FAILED_WECOM_MEDIA_LEASE_20260802" in block
    assert 'assert data["candidate_count"] in {0, 1}' in block
    assert 'assert data["contains_raw_material_identifier"] is False' in block
    assert "lease.material_id" not in block
    assert '"media_id"' not in block
