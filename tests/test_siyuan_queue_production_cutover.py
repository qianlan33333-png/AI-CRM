from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ops.validate_queue_all_scope_cutover import validate


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "siyuan-queue-production-cutover.yml"
MANIFEST_PATH = ROOT / "docs" / "releases" / "siyuan_queue_all_scope_cutover.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
BASE_SHA = "c0fa4eff1cf58e230f0de42c971e5d16ee5d46b4"
RELEASE_SHA = "a" * 40
CONFIRMATION = (
    "AUTHORIZE AI-CRM QUEUE ALL-SCOPE CUTOVER "
    f"{BASE_SHA} ON PRODUCTION"
)


def test_siyuan_manifest_binds_exact_environment_and_welcome_contract() -> None:
    assert MANIFEST["target_environment"] == "siyuan-production"
    assert MANIFEST["target_repository"] == "qianlan333/siyuan-crm"
    assert MANIFEST["public_health_url"] == "https://www.xinliushangye.com/health"
    assert MANIFEST["authorization_base_sha"] == BASE_SHA
    assert MANIFEST["authorization_confirmation"] == CONFIRMATION
    assert MANIFEST["target_generation"] == 1
    assert MANIFEST["target_scope"] == "all"
    assert MANIFEST["target_policy_version"] == "queue-v2-siyuan-production-all-g1"
    assert MANIFEST["welcome_contract"] == {
        "callback_lane": "wecom_welcome_ingress",
        "effect_lane": "wecom_welcome",
        "provider_deadline_seconds": 18,
        "maximum_attempts": 1,
        "historical_replay_prohibited": True,
    }


def test_siyuan_authorization_envelope_accepts_only_reviewed_successor_paths() -> None:
    changed_paths = list(MANIFEST["allowed_successor_paths"])
    result = validate(
        manifest=MANIFEST,
        release_sha=RELEASE_SHA,
        authorization_base_sha=BASE_SHA,
        confirmation=CONFIRMATION,
        changed_paths=changed_paths,
    )

    assert result["ok"] is True
    assert result["target_generation"] == 1
    assert result["target_scope"] == "all"
    assert result["changed_path_count"] == len(changed_paths)


def test_siyuan_authorization_envelope_rejects_unreviewed_business_change() -> None:
    with pytest.raises(ValueError, match="unauthorized paths"):
        validate(
            manifest=MANIFEST,
            release_sha=RELEASE_SHA,
            authorization_base_sha=BASE_SHA,
            confirmation=CONFIRMATION,
            changed_paths=["aicrm_next/channels/channel_entry/application.py"],
        )


def test_siyuan_cutover_uses_official_generation_owner_and_hard_realtime_lanes() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    trigger = source[source.index("on:") : source.index("permissions:")]

    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger
    assert "schedule:" not in trigger
    assert "environment: production" in source
    assert "qianlan333/siyuan-crm" in source
    assert "https://www.xinliushangye.com/health" in source
    assert "docs/releases/siyuan_queue_all_scope_cutover.json" in source
    assert "validate_queue_all_scope_cutover.py" in source
    assert "--owner-inventory pr3" in source
    assert "--target-generation 1" in source
    assert "--lane wecom_welcome_ingress" in source
    assert "--lane wecom_welcome" in source
    assert "--target-scope all" in source
    assert "--maximum-candidate-count 50" in source
    assert source.count("check_queue_runtime_invariants.py") == 2
    assert "--duration-hours 72" in source
    assert "https://www.youcangogogo.com/health" not in source


def test_siyuan_cutover_orders_preflight_owner_scope_verification_and_soak() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    invariant_before = source.index("siyuan-queue-invariants-before.json")
    prepare = source.index("--action prepare", invariant_before)
    preflight = source.index("authorized Siyuan production all-scope cutover preflight")
    owner = source.index("--ensure-owner-state", preflight)
    transition = source.index("--all-scope-confirmation", owner)
    recovery = source.index("--action recover", transition)
    owner_verify = source.index("--verify-owner-state", recovery)
    invariant_after = source.index("siyuan-queue-invariants-after.json", owner_verify)
    soak = source.index("--action start", invariant_after)

    assert invariant_before < prepare < preflight < owner < transition
    assert transition < recovery < owner_verify < invariant_after < soak
