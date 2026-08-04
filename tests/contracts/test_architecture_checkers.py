from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_current_architecture_gate_passes_as_one_contract() -> None:
    completed = subprocess.run(
        ["bash", "scripts/ci/run_architecture_gates.sh", "--mode", "fast"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=150,
    )
    assert completed.returncode == 0, "\n".join(part for part in (completed.stdout, completed.stderr) if part)
