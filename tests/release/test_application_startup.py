from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.release
ROOT = Path(__file__).resolve().parents[2]


def test_current_application_starts_and_exposes_exact_sha_health(release_client) -> None:
    expected_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    response = release_client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-aicrm-route-owner"] == "ai_crm_next"
    assert response.headers["x-aicrm-fallback-used"] == "false"
    assert response.headers["x-aicrm-release-sha"] == expected_sha
    assert response.json()["runtime_owner"] == "ai_crm_next"


def test_runtime_route_map_has_no_legacy_owner(release_client) -> None:
    response = release_client.get("/api/system/runtime-route-map")
    payload = response.json()
    legacy_fallback_enabled = payload["legacy_callback_fallback_enabled"]
    assert response.status_code == 200
    assert payload["route_owner"] == "ai_crm_next"
    assert legacy_fallback_enabled is False
