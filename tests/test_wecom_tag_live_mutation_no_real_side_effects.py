from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

import aicrm_next.crm.customer_tags.api as api
import aicrm_next.crm.customer_tags.live_mutation as live_mutation
from aicrm_next.integration_gateway.questionnaire_adapters import QuestionnaireSubmitSideEffectGateway
from aicrm_next.main import create_app


def test_live_mutation_sources_do_not_call_legacy_or_real_wecom() -> None:
    sources = "\n".join(
        [
            inspect.getsource(api.list_wecom_tags_live_gate),
            inspect.getsource(api.mark_tags_live),
            inspect.getsource(api.unmark_tags_live),
            inspect.getsource(live_mutation.execute_wecom_tag_mutation),
            inspect.getsource(live_mutation._create_side_effect_plan),
            inspect.getsource(QuestionnaireSubmitSideEffectGateway.apply_tags),
        ]
    )

    forbidden = [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        "X-AICRM-Compatibility-Facade",
        "requests.",
        "httpx.",
        "WeComTagLiveGateway",
        "build_wecom_tag_live_gateway",
        ".mark_tags_live(",
        ".unmark_tags_live(",
        ".mark_external_contact_tags(",
        "side_effect_executed=True",
    ]
    for marker in forbidden:
        assert marker not in sources


def test_live_mutation_ignores_live_wecom_env_flags(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-live-mutation-no-real")
    monkeypatch.setenv("AICRM_WECOM_TAG_LIVE_ADAPTER_ENABLED", "1")
    monkeypatch.setenv("AICRM_WECOM_TAG_LIVE_CALL_APPROVED", "1")
    monkeypatch.setenv("AICRM_WECOM_TAG_CONFIG_REVIEWED", "1")
    monkeypatch.setenv("AICRM_WECOM_TAG_CORP_ID", "corp")
    monkeypatch.setenv("AICRM_WECOM_TAG_AGENT_SECRET", "secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    payload = client.post(
        "/api/admin/wecom/tags/live/mark",
        json={"external_userid": "wx_ext_001", "tag_ids": ["tag_fixture_active"]},
        headers={"Idempotency-Key": "live-env-still-queued-only"},
    ).json()

    assert payload["source_status"] == "next_command"
    assert payload["adapter_mode"] == "queued_external_effect"
    assert payload["real_external_call_executed"] is False
    assert payload["wecom_api_called"] is False
    assert payload["side_effect_plan"]["status"] == "queued"
    assert payload["side_effect_plan"]["requires_approval"] is False
