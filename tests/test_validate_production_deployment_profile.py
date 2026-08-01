from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aicrm_next.deployment_profile import DEPLOYMENT_PROFILE_PATH_ENV, load_deployment_profile
from scripts.ops.validate_production_deployment_profile import validate_production_deployment_profile


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "deploy" / "deployment_profiles" / "production-current.json"


def test_production_profile_selection_accepts_exact_enforced_full_profile() -> None:
    result = validate_production_deployment_profile(
        environment={DEPLOYMENT_PROFILE_PATH_ENV: str(PROFILE_PATH)},
        expected_profile_path=PROFILE_PATH,
    )

    assert result["ok"] is True
    assert result["profile_id"] == "production-current"
    assert result["activation_mode"] == "enforce"
    assert result["capability_count"] == 14
    assert result["target_values_redacted"] is True
    assert result["real_external_call_executed"] is False


def test_production_profile_preflight_embeds_binding_report_and_fails_closed() -> None:
    result = validate_production_deployment_profile(
        environment={DEPLOYMENT_PROFILE_PATH_ENV: str(PROFILE_PATH)},
        expected_profile_path=PROFILE_PATH,
        automation_binding_report={"ok": False, "issue_count": 1, "issues": [{"kind": "orphan_subscription"}]},
    )

    assert result["ok"] is False
    assert result["automation_binding_precheck"]["issue_count"] == 1


def test_production_profile_preflight_runs_as_direct_script() -> None:
    environment = os.environ.copy()
    environment[DEPLOYMENT_PROFILE_PATH_ENV] = str(PROFILE_PATH)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ops" / "validate_production_deployment_profile.py"),
            "--expected-profile-path",
            str(PROFILE_PATH),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["activation_mode"] == "enforce"
    assert payload["capability_count"] == 14


def test_production_profile_selection_rejects_missing_or_different_path() -> None:
    with pytest.raises(ValueError, match="selection mismatch"):
        validate_production_deployment_profile(
            environment={},
            expected_profile_path=PROFILE_PATH,
        )

    with pytest.raises(ValueError, match="selection mismatch"):
        validate_production_deployment_profile(
            environment={DEPLOYMENT_PROFILE_PATH_ENV: str(PROFILE_PATH.with_name("wecom-core.json"))},
            expected_profile_path=PROFILE_PATH,
        )


def test_production_profile_selection_rejects_observe_mode(tmp_path: Path) -> None:
    profile = replace(load_deployment_profile(PROFILE_PATH), activation_mode="observe")
    observed_path = tmp_path / "production-current.json"
    observed_path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")

    with pytest.raises(ValueError, match="activation_mode must be enforce"):
        validate_production_deployment_profile(
            environment={DEPLOYMENT_PROFILE_PATH_ENV: str(observed_path)},
            expected_profile_path=observed_path,
        )
