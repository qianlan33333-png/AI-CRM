from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runtime-config-production.yml"


def test_runtime_config_workflow_is_release_bound_redacted_and_provider_free() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "environment: production" in source
    assert "ref: ${{ inputs.expected_release_sha }}" in source
    assert 'test "$(git rev-parse FETCH_HEAD)" = "$EXPECTED_RELEASE_SHA"' in source
    assert "https://www.youcangogogo.com/health" in source
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_RELEASE_SHA"' in source
    assert 'test "$(tr -d \'\\r\\n\' < .release-sha)" = "$EXPECTED_RELEASE_SHA"' in source
    assert "scripts/ops/manage_runtime_config_release.py" in source
    assert "secretref:" in source
    assert 'assert "secretref:" not in body' in source
    assert 'report.get("real_external_call_executed") is False' in source
    assert "provider" not in source.lower().replace("provider-free", "")


def test_runtime_config_workflow_passes_inputs_as_environment_not_shell_interpolation() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    ssh_script = source.split("          script: |", 1)[1]

    assert "envs: EXPECTED_RELEASE_SHA,CONFIG_ACTION,CONFIG_KEYS" in source
    assert "${{ inputs.keys }}" not in ssh_script
    assert "${{ inputs.confirmation }}" not in ssh_script
    assert "eval " not in ssh_script
    assert 'config_command+=(--key "$key")' in ssh_script
    assert '"${config_command[@]}"' in ssh_script


def test_runtime_config_workflow_separates_preview_from_execute() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert 'assert confirmation == f"INVENTORY_RUNTIME_CONFIG_{release_sha}"' in source
    assert 'assert confirmation == f"PREVIEW_RUNTIME_CONFIG_{release_sha}"' in source
    assert 'if [ "$CONFIG_EXECUTE" = "true" ]; then' in source
    assert "config_command+=(--execute)" in source
    assert "cancel-in-progress: false" in source
