from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

import aicrm_next.crm.customer_tags.api as api
import aicrm_next.crm.customer_tags.read_model as read_model
from aicrm_next.main import create_app


def test_wecom_tag_read_sources_do_not_call_legacy_or_real_wecom() -> None:
    read_sources = "\n".join(
        inspect.getsource(obj)
        for obj in [
            api.list_admin_wecom_tags_read_model,
            api.list_admin_wecom_tag_groups_read_model,
            api.get_admin_wecom_tag_read_model,
            api.get_admin_wecom_tag_group_read_model,
            api._read_catalog_payload,
            api._production_unavailable,
            read_model.LocalContractTagCatalogRepository,
            read_model.PostgresTagCatalogRepository,
        ]
    )

    forbidden = [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        "X-AICRM-Compatibility-Facade",
        "requests.",
        "httpx.",
        "WeComTagLiveGateway",
        "list_wecom_tags_live",
        "mark_tags_live",
        "mark_external_contact_tags",
    ]
    for marker in forbidden:
        assert marker not in read_sources


def test_wecom_tag_read_requests_do_not_invoke_real_side_effect_adapters(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-read-no-side-effects-test")

    client = TestClient(create_app(), raise_server_exceptions=False)

    tags = client.get("/api/admin/wecom/tags").json()
    groups = client.get("/api/admin/wecom/tag-groups").json()
    tag_detail = client.get("/api/admin/wecom/tags/tag_fixture_active").json()

    for payload in [tags, groups, tag_detail]:
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["sync_executed"] is False
