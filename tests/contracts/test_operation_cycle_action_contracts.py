import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.operation_cycles.action_dto import OperationCycleSkillV1
from aicrm_next.platform.platform_foundation.auth_platform.access_client import (
    INTERNAL_CLIENT_ID_KEYS,
    INTERNAL_CLIENT_SECRET_REFERENCE_KEYS,
)
from aicrm_next.platform.platform_foundation.auth_platform.profiles import API_CLIENT_PROFILES
from aicrm_next.platform.shared.route_ownership import load_route_manifest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.contract


def test_action_migration_is_additive_and_preserves_audit_on_rollback() -> None:
    source = (ROOT / "migrations/versions/0169_operation_cycle_codex_actions.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision = "0168_lead_qr_copy_config"' in source
    assert "operation_skill_json" in source
    assert "operation_cycle_runners" in source
    assert "operation_cycle_action_requests" in source
    assert "operation_cycle_action_request_events" in source
    assert "DROP TABLE" not in source
    assert "hxc_monday_full_activation" in source


def test_seeded_pilot_skill_hash_matches_runtime_contract() -> None:
    migration = importlib.import_module("migrations.versions.0169_operation_cycle_codex_actions")
    payload, expected_hash = migration._pilot_skill()
    skill = OperationCycleSkillV1.model_validate(payload)
    assert skill.skill_hash == expected_hash
    assert [action.action_key for action in skill.actions] == [
        "prepare_broadcast",
        "post_send_review",
    ]


def test_operation_runner_oauth_identity_is_distinct_and_least_privileged() -> None:
    assert "operation_runner" in INTERNAL_CLIENT_ID_KEYS
    assert len(set(INTERNAL_CLIENT_ID_KEYS.values())) == len(INTERNAL_CLIENT_ID_KEYS)
    assert len(set(INTERNAL_CLIENT_SECRET_REFERENCE_KEYS.values())) == len(
        INTERNAL_CLIENT_SECRET_REFERENCE_KEYS
    )
    profiles = {profile.purpose: profile for profile in API_CLIENT_PROFILES}
    runner = profiles["operation_runner"]
    assert set(runner.capabilities) == {
        "operation_cycle_action_claim",
        "operation_cycle_action_event_write",
        "operation_cycle_runner_heartbeat",
    }
    assert set(runner.capabilities).isdisjoint(profiles["campaign_agent"].capabilities)
    assert set(runner.capabilities).isdisjoint(profiles["ops_reporter"].capabilities)


def test_runner_routes_require_exact_operation_runner_purpose_and_capabilities() -> None:
    entries = load_route_manifest(ROOT / "docs/architecture/route_ownership_manifest.yml")
    by_route = {(entry["path"], entry["route_name"]): entry for entry in entries}
    expected = {
        ("/api/operation-cycles/runner/heartbeat", "heartbeat_operation_cycle_runner"): (
            "operation_runner",
            "operation_cycle_runner_heartbeat",
        ),
        ("/api/operation-cycles/action-requests/claim", "claim_operation_cycle_action_request"): (
            "operation_runner",
            "operation_cycle_action_claim",
        ),
        (
            "/api/operation-cycles/action-requests/{request_id}/events",
            "record_operation_cycle_action_request_event",
        ): ("operation_runner", "operation_cycle_action_event_write"),
    }
    for route, (purpose, capability) in expected.items():
        entry = by_route[route]
        assert entry["client_purpose"] == purpose
        assert entry["capability"] == capability


def test_admin_action_surface_is_default_off_and_has_no_external_effect(current_app) -> None:
    response = TestClient(current_app, raise_server_exceptions=False).get(
        "/api/admin/operation-cycles/strategies/hxc_monday_full_activation/current-action"
    )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "operation_cycle_actions_v1_disabled"
    external_effect_key = "_".join(("real", "external", "call", "executed"))
    assert body[external_effect_key] is False
