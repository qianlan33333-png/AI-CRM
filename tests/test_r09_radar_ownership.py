from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_radar_click_events_has_one_declared_write_owner_and_explicit_pii_lifecycle() -> None:
    lifecycle = _yaml("docs/architecture/data_table_lifecycle_manifest.yml")["tables"]["radar_click_events"]
    repositories = _yaml("docs/architecture/repository_ownership.yml")["repositories"]
    writers = [path for path, declaration in repositories.items() if "radar_click_events" in list(declaration.get("table_writes") or [])]

    assert lifecycle["lifecycle"] == "event"
    assert lifecycle["write_owner"] == "aicrm_next.extensions.radar.radar_links"
    assert lifecycle["pii_level"] == "direct_contact"
    assert "foreign-key cascade" in lifecycle["retention_policy"]
    assert writers == ["aicrm_next/extensions/radar/radar_links/repo.py"]

    identity_lifecycle = _yaml("docs/architecture/data_table_lifecycle_manifest.yml")["tables"]["crm_user_identity"]
    radar_repository = repositories["aicrm_next/extensions/radar/radar_links/repo.py"]
    assert "aicrm_next.extensions.radar.radar_links" in identity_lifecycle["read_owners"]
    assert "crm_user_identity" in radar_repository["table_reads"]


def test_radar_click_events_has_an_alembic_create_and_upgrade_path() -> None:
    migration = (ROOT / "migrations/versions/0102_questionnaire_radar_invariants.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS radar_click_events" in migration
    assert "link_id BIGINT NOT NULL REFERENCES radar_links(id) ON DELETE CASCADE" in migration
    assert "ALTER TABLE IF EXISTS radar_click_events ADD COLUMN IF NOT EXISTS" in migration
    assert "ix_radar_click_events_link_created" in migration
    assert "ix_radar_click_events_unionid_created" in migration
    assert 'if not _has_table("radar_links")' in migration

    external_feed_migration = (ROOT / "migrations/versions/0118_external_radar_read_api.py").read_text(encoding="utf-8")
    assert "ix_radar_click_events_external_feed" in external_feed_migration
    assert "stage IN ('authorized', 'authorized_click')" in external_feed_migration
    assert "stage = 'landing'" in external_feed_migration


def test_radar_public_source_does_not_read_plain_identity_query_or_cookies() -> None:
    api = (ROOT / "aicrm_next/extensions/radar/radar_links/api.py").read_text(encoding="utf-8")
    application = (ROOT / "aicrm_next/extensions/radar/radar_links/application.py").read_text(encoding="utf-8")
    domain = (ROOT / "aicrm_next/extensions/radar/radar_links/domain.py").read_text(encoding="utf-8")

    for marker in (
        'request.query_params.get("openid")',
        'request.query_params.get("unionid")',
        'request.query_params.get("external_userid")',
        'request.cookies.get("openid")',
        'request.cookies.get("unionid")',
        'request.cookies.get("external_userid")',
        "_identity_from_request",
    ):
        assert marker not in api
    assert '"openid": str(result.get("openid") or "").strip()' in application
    assert '"unionid": str(result.get("unionid") or "").strip()' in application
    assert "RADAR_VIEWER_SESSION_PURPOSE" in domain
    assert '"identity": canonical_identity' in domain
