from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ops.validate_queue_all_scope_cutover import validate
from aicrm_next.platform_foundation.execution_runtime.validation import evaluate_soak_snapshot


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (ROOT / "docs" / "releases" / "queue_all_scope_cutover.json").read_text(encoding="utf-8")
)
BASE_SHA = "7369fa6c7858165097f25dff26f324d109cf7b80"
RELEASE_SHA = "a" * 40
CONFIRMATION = f"AUTHORIZE AI-CRM QUEUE ALL-SCOPE CUTOVER {BASE_SHA} ON PRODUCTION"


def test_authorization_envelope_accepts_only_reviewed_successor_paths() -> None:
    result = validate(
        manifest=MANIFEST,
        release_sha=RELEASE_SHA,
        authorization_base_sha=BASE_SHA,
        confirmation=CONFIRMATION,
        changed_paths=["scripts/ops/transition_queue_runtime_scope.py"],
    )

    assert result["ok"] is True
    assert result["target_generation"] == 1
    assert result["target_scope"] == "all"


def test_authorization_envelope_rejects_unrelated_successor_change() -> None:
    with pytest.raises(ValueError, match="unauthorized paths"):
        validate(
            manifest=MANIFEST,
            release_sha=RELEASE_SHA,
            authorization_base_sha=BASE_SHA,
            confirmation=CONFIRMATION,
            changed_paths=["aicrm_next/unrelated_business_change.py"],
        )


def test_authorization_envelope_rejects_changed_confirmation() -> None:
    with pytest.raises(ValueError, match="confirmation"):
        validate(
            manifest=MANIFEST,
            release_sha=RELEASE_SHA,
            authorization_base_sha=BASE_SHA,
            confirmation="AUTHORIZE SOMETHING ELSE",
            changed_paths=["scripts/ops/transition_queue_runtime_scope.py"],
        )


def test_soak_fails_closed_when_generation_policy_or_scope_drifts() -> None:
    baseline = {
        "queue_active_generation": 1,
        "queue_policy_version": "queue-v2-production-all-g1",
        "queue_external_claim_scope": "all",
        "queue_unknown_count": 13,
        "queue_dlq_count": 487,
        "fresh_listener_count": 9,
    }
    metrics = {
        **baseline,
        "queue_active_generation": 2,
        "queue_policy_version": "queue-v2-unreviewed",
        "queue_external_claim_scope": "allowlisted",
    }

    violations = evaluate_soak_snapshot(
        metrics,
        baseline,
        release_matches=True,
        configuration_matches=True,
        migration_matches=True,
    )

    assert "active_generation_changed" in violations
    assert "queue_policy_version_changed" in violations
    assert "external_claim_scope_changed" in violations
