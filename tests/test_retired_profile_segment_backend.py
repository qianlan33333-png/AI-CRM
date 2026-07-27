from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_retired_profile_segment_sql_repository_is_removed() -> None:
    module_path = ROOT / "aicrm_next" / "automation" / "automation_engine" / "profile_segment_repository.py"

    assert not module_path.exists()
    assert importlib.util.find_spec("aicrm_next.automation.automation_engine.profile_segment_repository") is None


def test_profile_segment_template_routes_are_not_registered() -> None:
    app = create_app()
    registered_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/admin/automation-conversion/profile-segment-templates" not in registered_paths
    assert "/api/admin/automation-conversion/profile-segment-templates/options" not in registered_paths


def test_behavior_segment_rules_route_is_removed() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/automation-conversion/behavior-segment-rules")

    assert response.status_code == 404
