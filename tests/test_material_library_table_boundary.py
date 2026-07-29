from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
BOUNDARY_DOC = ROOT / "docs" / "architecture" / "material_library_table_boundary.md"
SEND_CONTENT_CONTRACT = ROOT / "docs" / "contracts" / "send_content_package_contract.md"
ROUTE_INVENTORY = ROOT / "docs" / "architecture" / "media_library_route_inventory.md"

CORE_TABLES = {"image_library", "miniprogram_library", "attachment_library", "group_invite_library"}


def _tables() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("tables"), dict)
    return data["tables"]


def test_material_library_core_tables_are_active_and_owned() -> None:
    tables = _tables()
    for table_name in CORE_TABLES:
        entry = tables[table_name]
        assert entry["domain"] == "media_library"
        assert entry["lifecycle"] == "canonical"
        assert entry["drop_candidate"] is False
        expected_owner = (
            "aicrm_next.engagement.media_library"
            if table_name == "image_library"
            else "aicrm_next.engagement.media_library.postgres_repo"
        )
        assert entry["write_owner"] == expected_owner
        assert "aicrm_next.engagement.send_content.postgres_repo" in entry["read_owners"]


def test_image_variants_are_cache_read_model_not_new_material_source() -> None:
    entry = _tables()["image_library_variants"]
    assert entry["domain"] == "media_library"
    assert entry["lifecycle"] == "read_model"
    assert entry["replacement"] == "image_library"
    assert entry["drop_candidate"] is False


def test_material_boundary_docs_name_shared_id_contracts() -> None:
    boundary = BOUNDARY_DOC.read_text(encoding="utf-8")
    send_content = SEND_CONTENT_CONTRACT.read_text(encoding="utf-8")
    inventory = ROUTE_INVENTORY.read_text(encoding="utf-8")

    for phrase in (
        "image_library_ids",
        "miniprogram_library_ids",
        "attachment_library_ids",
        "group_invite_library_ids",
        "PostgresSendContentRepository",
        "material_assets",
        "real external storage",
    ):
        assert phrase in boundary

    for phrase in ("image_library_ids", "miniprogram_library_ids", "attachment_library_ids", "group_invite_library_ids"):
        assert phrase in send_content
    assert "/api/admin/material-picker/items" in inventory
