from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "checker",
    [
        "tools/check_architecture_boundaries.py",
        "tools/check_external_effects_boundary.py",
        "scripts/ci/check_group_ops_effect_ownership.py",
        "scripts/ci/check_welcome_media_effect_ownership.py",
    ],
)
def test_current_provider_and_effect_boundary_checker(checker: str) -> None:
    completed = subprocess.run(
        [sys.executable, checker],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert completed.returncode == 0, "\n".join(part for part in (completed.stdout, completed.stderr) if part)
