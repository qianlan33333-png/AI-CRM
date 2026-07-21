from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "relay-id-validation-wecom-callback.yml"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_relay_workflow_is_manual_single_target_and_fail_closed() -> None:
    source = _source()

    assert "workflow_dispatch:" in source
    assert "push:" not in source
    assert "schedule:" not in source
    assert "environment: production" in source
    assert "cancel-in-progress: false" in source
    assert "150.158.82.186" in source
    assert "49.232.57.128" in source
    assert "https://id-dev.youcangogogo.com/wecom/external-contact/callback" in source
    assert "RELAY ONE CALLBACK FROM 150 TO 49" in source
    assert "CANARY_SCENE_SHA256" in source
    assert "--maximum-event-age-seconds 12" in source
    assert "--timeout-seconds 900" in source
    assert "AICRM_ID_VALIDATION_CALLBACK_RELAY_AUTHORIZED=1" in source
    assert "RELAY_ONE_WECOM_CALLBACK_TO_ID_VALIDATION_${TARGET_RELEASE_SHA}" in source


def test_relay_workflow_pins_actions_and_source_host_key() -> None:
    source = _source()

    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in source
    assert "appleboy/scp-action@ff85246acaad7bdce478db94a363cd2bf7c90345" in source
    assert "appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2" in source
    assert "SHA256:P1RMaTUquxIKkVK2eN3DDM4ZBbZYjrCMXepWeDltHSg" in source
    assert "test \"$DEPLOY_HOST\" = \"$EXPECTED_SOURCE_HOST\"" in source
    assert "test \"$(sha256sum \"$relay_script\" | awk '{print $1}')\" = \"$RELAY_SCRIPT_SHA256\"" in source


def test_relay_workflow_never_exposes_raw_callback_identity() -> None:
    source = _source()

    assert "external_userid" not in source.lower()
    assert "owner_userid" not in source.lower()
    assert "raw_body" not in source.lower()
    assert "scene_sha256" in source.lower()
    assert "no automatic retry" in source.lower()
