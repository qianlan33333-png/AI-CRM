from __future__ import annotations

from aicrm_next.automation.automation_engine import channels_api
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError
from tools import check_architecture_boundaries, check_db_access_boundary


def test_channel_write_does_not_fall_back_to_memory_when_production_repo_required(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    channels_api._FIXTURE_CHANNELS.clear()

    try:
        channels_api._save_postgres_channel({"channel_name": "prod blocked", "channel_code": "prod-blocked"})
    except RepositoryProviderError as exc:
        assert "channel admin write requires production database" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("production channel write must not fall back to fixture storage")

    assert channels_api._FIXTURE_CHANNELS == {}


def test_channel_assignment_write_does_not_fall_back_to_memory_when_production_repo_required(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    channels_api._FIXTURE_CHANNELS[1] = {"id": 1, "channel_name": "existing", "status": "active"}
    channels_api._FIXTURE_CHANNEL_ASSIGNEES.clear()

    try:
        channels_api._save_channel_assignees_resource(
            1,
            assignment_mode="multi_staff",
            assignment_strategy="ratio",
            overflow_policy="least_loaded",
            assignees=[{"staff_id": "user_1", "display_name": "User 1", "ratio_percent": 100}],
        )
    except RepositoryProviderError as exc:
        assert "channel assignee write requires production database" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("production assignee write must not fall back to fixture storage")

    assert channels_api._FIXTURE_CHANNEL_ASSIGNEES == {}


def test_boundary_configs_cover_channels_api_semantic_names() -> None:
    module_config = check_architecture_boundaries.load_config("docs/development/module_boundaries.yml")
    db_config = check_db_access_boundary.load_config("docs/architecture/db_access_boundary.yml")

    assert "aicrm_next/**/*api*.py" in module_config["api_import_rules"]["api_file_globs"]
    assert "aicrm_next/**/*pages*.py" in module_config["api_import_rules"]["api_file_globs"]
    assert {"repo", "repository", "repositories", "postgres_repo", "service"} <= set(
        module_config["api_import_rules"]["forbidden_cross_context_modules"]
    )
    assert "aicrm_next/**/*api*.py" in db_config["forbidden_layers"]
    assert "aicrm_next/**/*pages*.py" in db_config["forbidden_layers"]
