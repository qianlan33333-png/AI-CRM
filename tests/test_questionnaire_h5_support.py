from __future__ import annotations

from aicrm_next.extensions.forms.questionnaire.api import _safe_questionnaire_return_url
from aicrm_next.extensions.forms.questionnaire.dto import OAuthStartRequest
from aicrm_next.extensions.forms.questionnaire.oauth import QuestionnaireOAuthAdapter, questionnaire_oauth_state_context


def test_next_questionnaire_h5_return_url_stays_local():
    assert _safe_questionnaire_return_url("/s/hxc-activation-v1", "hxc-activation-v1") == "/s/hxc-activation-v1"
    assert _safe_questionnaire_return_url("https://evil.example/s/hxc-activation-v1", "hxc-activation-v1") == "/s/hxc-activation-v1"
    assert _safe_questionnaire_return_url("", "hxc-activation-v1") == "/s/hxc-activation-v1"


def test_next_questionnaire_oauth_state_round_trips_context():
    start = QuestionnaireOAuthAdapter(mode="fake").build_authorize_url(
        OAuthStartRequest(
            slug="hxc-activation-v1",
            redirect="/s/hxc-activation-v1",
            source_channel="sidebar",
            campaign_id="campaign_001",
        )
    )

    context = questionnaire_oauth_state_context(start["state"])

    assert context["slug"] == "hxc-activation-v1"
    assert context["redirect_url"] == "/s/hxc-activation-v1?source_channel=sidebar&campaign_id=campaign_001"
