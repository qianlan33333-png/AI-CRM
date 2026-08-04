from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.release
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[str, object]:
    payload = yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_ci_fast_is_one_required_job_with_release_only_main_push() -> None:
    workflow = _workflow("ci-fast.yml")
    assert workflow["name"] == "CI Fast"
    assert tuple(workflow["jobs"]) == ("ci-fast-result",)
    job = workflow["jobs"]["ci-fast-result"]
    assert job["timeout-minutes"] == "25"
    source = (WORKFLOWS / "ci-fast.yml").read_text(encoding="utf-8")
    assert "scripts/ci/select_test_scope.py" in source
    assert "scripts/ci/run_ci.py" in source
    assert "postgres:16" in source


def test_full_regression_has_only_manual_or_high_risk_call_and_no_matrix() -> None:
    workflow = _workflow("full-regression.yml")
    triggers = set(workflow["on"])
    assert triggers == {"workflow_call", "workflow_dispatch"}
    assert tuple(workflow["jobs"]) == ("full-regression",)
    source = (WORKFLOWS / "full-regression.yml").read_text(encoding="utf-8")
    assert "matrix:" not in source
    assert "schedule:" not in source
    assert "scripts/ci/run_ci.py --tier full" in source


def test_promotion_and_deploy_preserve_exact_sha_lock_health_and_rollback() -> None:
    promotion = (WORKFLOWS / "promote-production.yml").read_text(encoding="utf-8")
    deploy = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    assert 'workflows: ["CI Fast"]' in promotion
    assert "release_sha: ${{ github.event.workflow_run.head_sha }}" in promotion
    assert "ref: ${{ inputs.release_sha }}" in deploy
    assert "aicrm-production-deploy" in deploy
    assert "flock -n 9" in deploy
    assert "x-aicrm-release-sha" in deploy.lower()
    assert "cleanup_deploy" in deploy
    assert "before_sha" in deploy
