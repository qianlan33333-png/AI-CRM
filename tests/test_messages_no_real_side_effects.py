from __future__ import annotations

from fastapi.testclient import TestClient


def test_message_write_like_routes_are_blocked_without_real_side_effects() -> None:
    from aicrm_next.main import create_app

    client = TestClient(create_app())

    for method, path in [
        ("post", "/api/messages/send"),
        ("post", "/api/messages/broadcast"),
        ("post", "/api/messages/archive/sync"),
        ("get", "/api/messages/send"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 503
        payload = response.json()
        assert payload["error_code"] == "external_call_blocked"
        assert payload["source_status"] == "external_call_blocked"
        assert payload["side_effect_plan"]["real_external_call_executed"] is False
        assert payload["side_effect_plan"]["next_step"] == "requires_platform_jobs"


def test_message_archive_module_does_not_import_external_call_clients() -> None:
    import aicrm_next.extensions.archive.message_archive.api as api
    import aicrm_next.extensions.archive.message_archive.application as application
    import aicrm_next.extensions.archive.message_archive.repo as repo

    combined = "\n".join(
        [
            getattr(api, "__file__", ""),
            getattr(application, "__file__", ""),
            getattr(repo, "__file__", ""),
        ]
    )
    assert "wecom_ability" + "_service" not in combined

