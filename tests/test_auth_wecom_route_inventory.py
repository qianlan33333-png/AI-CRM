from __future__ import annotations

from pathlib import Path


def test_auth_wecom_inventory_documents_search_results_and_decisions() -> None:
    text = Path("docs/architecture/auth_wecom_route_inventory.md").read_text(encoding="utf-8")

    for route in [
        "/api/h5/wechat/oauth/start",
        "/api/h5/wechat/oauth/callback",
        "/api/h5/wechat/oauth/{path:path}",
        "/auth/wecom/{path:path}",
        "/auth/wecom/start",
        "/auth/wecom/callback",
        "/api/h5/wechat/oauth/unknown",
        "/auth/wecom/unknown",
    ]:
        assert route in text

    assert "grep -R" in text
    assert "historical removed reference (test_admin_slim_phase1.py)" in text
    assert "aicrm_next/platform/admin_config/api_docs_view_model.py" in text
    assert "deprecated" in text
    assert "external_call_blocked" in text
    assert "Wildcard Deleted" in text
    assert "legacy_deleted" in text
    assert "Random unregistered auth subpaths now return 404" in text


def test_auth_wecom_inventory_marks_missing_search_dirs_non_fatal() -> None:
    text = Path("docs/architecture/auth_wecom_route_inventory.md").read_text(encoding="utf-8")

    assert "Missing search directories" in text
    assert "static" in text
    assert "templates" in text
    assert "frontend" in text
