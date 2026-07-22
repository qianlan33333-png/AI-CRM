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
WELCOME_ACK_CONFIRMATION = (
    "ACKNOWLEDGE AI-CRM PRE-CUTOVER WELCOME 41050 TERMINAL "
    "AS NO-REPLAY HISTORY ON PRODUCTION"
)


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


def test_pre_cutover_welcome_acknowledgement_is_one_exact_no_replay_scope() -> None:
    acknowledgement = MANIFEST["pre_cutover_welcome_terminal_acknowledgement"]
    production_manifest = json.loads(
        (ROOT / "docs" / "releases" / "production_promotion.json").read_text(
            encoding="utf-8"
        )
    )
    script_source = (
        ROOT / "scripts" / "ops" / "acknowledge_pre_cutover_welcome_terminal.py"
    ).read_text(encoding="utf-8")

    assert acknowledgement == {
        "acknowledgement_type": "pre_cutover_welcome_41050_no_replay",
        "confirmation": WELCOME_ACK_CONFIRMATION,
        "confirmation_sha256": "23255deb8941ea4a7307fff1c7f45c53721447e3969ad8b7ea58c7306553166e",
        "authorization_base_sha": BASE_SHA,
        "authorization_recorded_at_utc": "2026-07-22T03:07:19Z",
        "maximum_job_count": 1,
        "effect_type": "wecom.welcome_message.send",
        "adapter_name": "wecom_welcome_message",
        "operation": "send",
        "business_type": "channel_welcome_effect_graph",
        "source_module": "channel_entry.application",
        "source_route": "channel_entry.process_channel_entry",
        "error_code": "wecom_error_41050",
        "expected_policy_version": "queue-v2-test-loopback",
        "expected_worker_generation": 0,
        "replay_prohibited": True,
        "provider_success_claimed": False,
    }
    assert "expected exactly one authorized historical welcome terminal" in script_source
    assert "provider_success_claimed\": False" in script_source
    assert "real_external_call_executed\": False" in script_source
    assert "send_welcome_msg" not in script_source
    assert "requeue" not in script_source.lower()
    assert "tests/test_queue_all_scope_cutover.py" in production_manifest[
        "allowed_post_candidate_paths"
    ]


def test_cutover_exports_database_environment_before_migration_preflight() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "queue-production-cutover.yml"
    ).read_text(encoding="utf-8")

    source_index = workflow.index("source /home/ubuntu/.openclaw-wecom-pg.env")
    export_index = workflow.rindex("set -a", 0, source_index)
    unexport_index = workflow.index("set +a", source_index)
    migration_index = workflow.index("python3 -m alembic current", unexport_index)
    activation_index = workflow.index(
        "python scripts/ops/cutover_queue_runtime_generation.py",
        migration_index,
    )

    assert export_index < source_index < unexport_index < migration_index < activation_index


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
