from __future__ import annotations

import pytest

from aicrm_next import admin_jobs_archive_sync_gateway as archive_gateway
from aicrm_next.admin_jobs import application
from aicrm_next.admin_jobs.repository import FixtureAdminJobsRepository


def test_admin_jobs_archive_sync_uses_package_root_gateway(monkeypatch) -> None:
    captured: dict[str, object] = {}
    repository = object()

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "source_status": "next_archive_sync"}

    monkeypatch.setattr(archive_gateway, "_execute_archive_sync", fake_execute)
    monkeypatch.setattr(archive_gateway, "_build_archive_sync_repository", lambda: repository)
    monkeypatch.setenv("AICRM_ENABLE_IN_PROCESS_ARCHIVE_SYNC", "true")

    result = application.execute_jobs_action(
        action="run-archive-sync",
        form={
            "start_time": "2026-07-01 00:00:00",
            "end_time": "2026-07-01 01:00:00",
            "owner_userid": "owner-a",
            "cursor": "12",
            "limit": 50,
            "max_pages": 2,
            "confirm": True,
        },
        operator="pytest",
        repo=FixtureAdminJobsRepository(),
    )

    assert result["ok"] is True
    assert result["runner"] == "aicrm_next_admin_jobs"
    assert captured == {
        "start_time": "2026-07-01 00:00:00",
        "end_time": "2026-07-01 01:00:00",
        "owner_userid": "owner-a",
        "cursor": "12",
        "limit": 50,
        "max_pages": 2,
        "repo": repository,
    }


def test_archive_sync_gateway_preserves_production_data_guard(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.setenv("AICRM_PYTEST_FIXTURE_DEFAULT", "1")

    with pytest.raises(RuntimeError, match="archive sync requires PostgreSQL production data mode"):
        archive_gateway.execute_archive_sync(owner_userid="HuangYouCan")


def test_admin_jobs_application_has_no_message_archive_context_import() -> None:
    assert application.execute_archive_sync.__module__ == "aicrm_next.admin_jobs_archive_sync_gateway"
