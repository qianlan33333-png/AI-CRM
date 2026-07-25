from __future__ import annotations

import pytest
from sqlalchemy import text

from aicrm_next.admin_config.config_releases import ConfigReleaseService
from aicrm_next.shared.db_session import get_engine


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def test_postgres_config_publish_and_rollback_are_atomic_and_audited() -> None:
    service = ConfigReleaseService()
    first = service.create_draft(
        {"WECOM_CORP_ID": "ww-config-release-postgres"},
        operator="postgres-test",
    )
    validated = service.validate(first["id"])
    published = service.publish(
        first["id"],
        expected_checksum=first["checksum"],
        operator="postgres-publisher",
    )

    assert validated["status"] == "validated"
    assert published["status"] == "published"
    assert service.profile_state()["active_config_release_id"] == first["id"]
    with get_engine().connect() as connection:
        assert connection.execute(
            text("SELECT value FROM app_settings WHERE key = 'WECOM_CORP_ID'")
        ).scalar_one() == "ww-config-release-postgres"

    rollback = service.rollback(first["id"], operator="postgres-rollback")

    assert rollback["status"] == "published"
    assert rollback["rollback_of_release_id"] == first["id"]
    assert service.get(first["id"])["status"] == "superseded"
    with get_engine().connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM app_settings WHERE key = 'WECOM_CORP_ID'")
        ).scalar_one() == 0
