from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "crm" / "customer_tags" / "templates" / "admin_console" / "config_wecom_tags.html"
SCRIPT = ROOT / "aicrm_next" / "crm" / "customer_tags" / "static" / "admin_console" / "wecom_tag_management.js"


def test_wecom_tag_write_frontend_declares_next_write_apis() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'data-api-tags="/api/admin/wecom/tags"' in template
    assert 'data-api-groups="/api/admin/wecom/tag-groups"' in template
    assert 'data-api-sync="/api/admin/wecom/tags/sync"' in template
    assert 'data-action="sync"' in template
    assert 'data-action="create-group"' in template
    assert 'data-action="create-tag"' in template


def test_wecom_tag_write_frontend_posts_sync_and_sends_idempotency_keys() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "function apiSync()" in source
    assert 'return root.dataset.apiSync || "/api/admin/wecom/tags/sync";' in source
    assert "function writeOptions" in source
    assert '"Idempotency-Key"' in source
    assert 'requestJson(apiSync(), writeOptions("POST"' in source
    assert 'if (action === "sync") syncTags();' in source
    assert 'writeOptions("POST"' in source
    assert 'writeOptions("PUT"' in source
    assert 'writeOptions("DELETE"' in source
