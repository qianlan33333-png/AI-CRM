from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/architecture/messages_route_inventory.md"


def test_messages_inventory_covers_known_repository_paths() -> None:
    text = INVENTORY.read_text(encoding="utf-8")

    for path in [
        "/api/messages/{external_userid}",
        "/api/messages/{external_userid}/recent",
        "/api/messages/search",
        "/api/messages/archive",
        "/api/messages/{external_userid}/archive",
        "/api/messages/{external_userid}/history",
        "/api/messages/send",
        "/api/messages/broadcast",
        "/api/messages/archive/sync",
        "/api/messages*",
    ]:
        assert path in text

    assert "tests/test_customer_read_model_next_primary.py" in text
    assert "tests/test_messages_exact_routes.py" in text
    assert "historical retired production_compat module" in text
    assert "deleted and locked" in text
    assert "no legacy forward" in text


def test_messages_inventory_search_references_are_explained() -> None:
    inventory = INVENTORY.read_text(encoding="utf-8")
    referenced_files: set[str] = set()
    for base in ["aicrm_next", "tests", "docs", "scripts"]:
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".md", ".yaml", ".yml"}:
                continue
            if "/api/messages" in path.read_text(encoding="utf-8", errors="ignore"):
                referenced_files.add(str(path.relative_to(ROOT)))

    for required in [
        "aicrm_next/crm/customer_read_model/api.py",
        "tests/test_customer_read_model_next_primary.py",
        "tests/test_messages_exact_routes.py",
        "docs/crm_sensitive_routes.md",
    ]:
        assert required in referenced_files
        assert required in inventory

    assert "aicrm_next/production_compat/api.py" not in referenced_files
    assert "historical retired production_compat module" in inventory
