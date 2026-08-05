from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.readiness import _probe_migration
from aicrm_next.platform.platform_foundation.repository import RuntimeReadinessRepository
from aicrm_next.platform.release_governance.migration_compatibility import load_migration_compatibility_manifest


pytestmark = pytest.mark.postgres


def test_current_schema_records_the_forward_compatible_migration_chain(pg_connection) -> None:
    manifest = load_migration_compatibility_manifest()
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT revision, parent_revision, change_kind, compatibility_epoch,
                   previous_runtime_compatible, downgrade_policy
            FROM schema_release_compatibility
            ORDER BY applied_at ASC, revision ASC
            """
        )
        rows = cursor.fetchall()
    assert [row[0] for row in rows] == [
        manifest.baseline_revision,
        *(entry.revision for entry in manifest.migrations),
    ]
    current = manifest.migrations[-1]
    assert rows[-1][1:] == (
        current.parent_revision,
        current.change_kind,
        current.compatibility_epoch,
        current.previous_runtime_compatible,
        current.downgrade_policy,
    )


def test_previous_runtime_accepts_current_expand_head_as_compatible_ahead(migrated_database_url: str) -> None:
    manifest = load_migration_compatibility_manifest()
    with RuntimeReadinessRepository(migrated_database_url) as repository:
        result = _probe_migration(repository, (manifest.baseline_revision,))
    assert result["status"] == "warning"
    assert result["compatibility"] == "compatible_ahead"
    assert result["matches_head"] is False


def test_candidate_runtime_still_requires_its_exact_head(migrated_database_url: str) -> None:
    manifest = load_migration_compatibility_manifest()
    with RuntimeReadinessRepository(migrated_database_url) as repository:
        result = _probe_migration(repository, (manifest.current_head,))
    assert result["status"] == "ok"
    assert result["compatibility"] == "exact"
    assert result["matches_head"] is True
