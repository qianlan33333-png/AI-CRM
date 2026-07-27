from __future__ import annotations

from pathlib import Path

from tools.check_sql_static_guard import check_sql_static_guard


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"


def test_sql_static_guard_current_repository_passes() -> None:
    assert check_sql_static_guard(root=ROOT, manifest_path=MANIFEST_PATH) == []


def test_sql_static_guard_blocks_retired_table_runtime_sql(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        '''
SQL = """
SELECT id FROM retired_table WHERE id = 1
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["retired_table_sql_reference"]
    assert "retired_table" in violations[0].detail


def test_sql_static_guard_blocks_unregistered_create_table(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    _write(
        tmp_path / "migrations" / "versions" / "0001_new_table.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS unregistered_table (
    id TEXT PRIMARY KEY
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["create_table_without_lifecycle_manifest"]
    assert "unregistered_table" in violations[0].detail


def test_sql_static_guard_blocks_legacy_identity_columns_on_business_tables(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    _write(
        tmp_path / "aicrm_next" / "business" / "repo.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS business_contacts (
    id TEXT PRIMARY KEY,
    external_userid TEXT NOT NULL
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert {violation.rule for violation in violations} == {
        "create_table_without_lifecycle_manifest",
        "legacy_identity_column_in_business_sql",
    }
    assert any("external_userid" in violation.detail for violation in violations)


def test_migration_create_external_userid_after_0073_fails(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        baseline_prefix="0073",
        extra_table="""
  business_contacts:
    domain: tests
    lifecycle: canonical
    write_owner: tests
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "migrations" / "versions" / "0074_business_contacts.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS business_contacts (
    id TEXT PRIMARY KEY,
    external_userid TEXT NOT NULL
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["legacy_identity_column_in_business_sql"]
    assert "external_userid" in violations[0].detail


def test_sql_static_guard_blocks_service_period_mobile_snapshot(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        baseline_prefix="0073",
        extra_table="""
  service_period_entitlements:
    domain: service_period
    lifecycle: canonical
    write_owner: aicrm_next.extensions.commerce.service_period
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "migrations" / "versions" / "0095_service_period_products.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS service_period_entitlements (
    id BIGSERIAL PRIMARY KEY,
    mobile_snapshot TEXT NOT NULL DEFAULT ''
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["legacy_identity_column_in_business_sql"]
    assert "service_period_entitlements declares legacy identity column mobile_snapshot" in violations[0].detail


def test_radar_identity_event_snapshots_are_an_exact_migration_only_boundary(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        baseline_prefix="0101",
        extra_table="""
  radar_click_events:
    domain: radar
    lifecycle: event
    write_owner: aicrm_next.extensions.radar.radar_links
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "migrations" / "versions" / "0102_questionnaire_radar_invariants.py",
        '''
def _radar_click_events():
    op.execute("""
    CREATE TABLE IF NOT EXISTS radar_click_events (
        id BIGSERIAL PRIMARY KEY,
        openid TEXT NOT NULL DEFAULT '',
        external_userid TEXT NOT NULL DEFAULT '',
        person_id TEXT NOT NULL DEFAULT ''
    )
    """)
''',
    )

    assert check_sql_static_guard(root=tmp_path, manifest_path=manifest) == []


def test_runtime_public_retired_table_reference_fails(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        '''
SQL = """
SELECT id FROM public.retired_table WHERE id = 1
"""
QUOTED_SQL = """
SELECT id FROM "public"."retired_table" WHERE id = 2
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == [
        "retired_table_sql_reference",
        "retired_table_sql_reference",
    ]


def test_commented_sql_then_retired_table_reference_fails(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    _write(
        tmp_path / "aicrm_next" / "demo" / "repo.py",
        '''
SQL = """
-- explain the query before the statement
/* and keep a block comment before SQL */
SELECT id FROM retired_table WHERE id = 1
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["retired_table_sql_reference"]


def test_migration_drop_retired_table_after_0073_is_allowed(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, baseline_prefix="0073")
    _write(
        tmp_path / "migrations" / "versions" / "0074_drop_retired_table.py",
        '''
SQL = """
DROP TABLE IF EXISTS public.retired_table
"""
''',
    )

    assert check_sql_static_guard(root=tmp_path, manifest_path=manifest) == []


def test_migration_downgrade_can_restore_retired_table_after_0073(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, baseline_prefix="0073")
    _write(
        tmp_path / "migrations" / "versions" / "0074_retire_table.py",
        '''
def downgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS retired_table (
        id TEXT PRIMARY KEY
    )
    """)
''',
    )

    assert check_sql_static_guard(root=tmp_path, manifest_path=manifest) == []


def test_migration_upgrade_cannot_restore_retired_table_after_0073(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, baseline_prefix="0073")
    _write(
        tmp_path / "migrations" / "versions" / "0074_restore_table.py",
        '''
def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS retired_table (
        id TEXT PRIMARY KEY
    )
    """)
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["retired_table_sql_reference"]


def test_legacy_identity_column_detection_normalizes_case_and_quotes(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        extra_table="""
  business_contacts:
    domain: tests
    lifecycle: canonical
    write_owner: tests
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "aicrm_next" / "business" / "repo.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS business_contacts (
    id TEXT PRIMARY KEY,
    EXTERNAL_USERID TEXT NOT NULL,
    "person_id" TEXT
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == [
        "legacy_identity_column_in_business_sql",
        "legacy_identity_column_in_business_sql",
    ]
    assert {violation.detail for violation in violations} == {
        "business_contacts declares legacy identity column external_userid",
        "business_contacts declares legacy identity column person_id",
    }


def test_schema_qualified_create_table_uses_final_identifier_component(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        extra_table="""
  business_contacts:
    domain: tests
    lifecycle: canonical
    write_owner: tests
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "aicrm_next" / "business" / "repo.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS "public"."business_contacts" (
    id TEXT PRIMARY KEY,
    external_userid TEXT
)
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["legacy_identity_column_in_business_sql"]
    assert "business_contacts declares legacy identity column external_userid" in violations[0].detail


def test_alter_table_if_exists_detects_business_table_and_legacy_column(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        extra_table="""
  business_contacts:
    domain: tests
    lifecycle: canonical
    write_owner: tests
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "aicrm_next" / "business" / "repo.py",
        '''
SQL = """
ALTER TABLE IF EXISTS public.business_contacts
ADD COLUMN "external_userid" TEXT
"""
''',
    )

    violations = check_sql_static_guard(root=tmp_path, manifest_path=manifest)

    assert [violation.rule for violation in violations] == ["legacy_identity_column_in_business_sql"]
    assert "business_contacts declares legacy identity column external_userid" in violations[0].detail


def test_sql_static_guard_allows_identity_boundary_legacy_columns(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        extra_table="""
  crm_user_identity_custom:
    domain: identity
    lifecycle: canonical
    write_owner: identity
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "aicrm_next" / "identity" / "repo.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS crm_user_identity_custom (
    id TEXT PRIMARY KEY,
    external_userid TEXT NOT NULL
)
"""
''',
    )

    assert check_sql_static_guard(root=tmp_path, manifest_path=manifest) == []


def test_sql_static_guard_allows_channel_entry_runtime_identity_boundary(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        baseline_prefix="0073",
        extra_table="""
  automation_channel_entry_runtime:
    domain: channel_entry
    lifecycle: queue
    write_owner: aicrm_next.channel_entry.repo
    replacement: none
    drop_candidate: false
""",
    )
    _write(
        tmp_path / "migrations" / "versions" / "0088_channel_entry_identity_best_effort.py",
        '''
SQL = """
CREATE TABLE IF NOT EXISTS automation_channel_entry_runtime (
    id BIGSERIAL PRIMARY KEY,
    external_userid TEXT NOT NULL DEFAULT '',
    follow_user_userid TEXT NOT NULL DEFAULT '',
    unionid TEXT NOT NULL DEFAULT ''
)
"""
''',
    )

    assert check_sql_static_guard(root=tmp_path, manifest_path=manifest) == []


def _write_manifest(tmp_path: Path, *, baseline_prefix: str = "0000", extra_table: str = "") -> Path:
    manifest = tmp_path / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
    _write(
        manifest,
        f"""
version: 1
migration_guard:
  migration_file_prefix_after: "{baseline_prefix}"
tables:
  retired_table:
    domain: tests
    lifecycle: retired
    replacement: active_table
    drop_candidate: false
  active_table:
    domain: tests
    lifecycle: canonical
    write_owner: tests
    replacement: none
    drop_candidate: false
{extra_table}
""",
    )
    return manifest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
