from __future__ import annotations

from pathlib import Path

from aicrm_next.customer_read_model.models import (
    customer_detail_snapshot_next,
    customer_list_index_next,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0152_customer_read_model_incremental_foundation.py"


def test_incremental_foundation_declares_additive_online_keys() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0152_customer_read_model_incremental"' in source
    assert 'down_revision = "0151_ai_audience_hxc_projection_view"' in source
    assert "source_event_id TEXT NOT NULL DEFAULT ''" in source
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in source
    assert "CREATE INDEX CONCURRENTLY" in source
    assert "idx_customer_refresh_source_generation_id" in source
    assert "pg_get_serial_sequence" in source
    assert "DROP INDEX" not in source
    assert "DROP COLUMN" not in source


def test_customer_projection_metadata_declares_unionid_uniqueness() -> None:
    list_indexes = {index.name: index for index in customer_list_index_next.indexes}
    detail_indexes = {index.name: index for index in customer_detail_snapshot_next.indexes}

    assert list_indexes["uq_customer_list_index_next_unionid"].unique is True
    assert detail_indexes["uq_customer_detail_snapshot_next_unionid"].unique is True
