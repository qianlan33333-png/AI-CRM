from __future__ import annotations

from typing import Any


Json = dict[str, Any]


class QuestionnaireExternalSubmitLiveGateway:
    def submit_public_live(self, *, slug: str, payload_redacted: Json) -> Json:
        return {"ok": False, "result_status": "blocked", "error_code": "live_gateway_disabled", "provider_call_executed": False}

    def write_identity_mapping_live(self, *, identity_redacted: Json) -> Json:
        return {"ok": False, "result_status": "blocked", "error_code": "live_gateway_disabled", "provider_call_executed": False}

    def write_tag_back_live(self, *, external_userid_redacted: str, tag_ids: list[str]) -> Json:
        return {"ok": False, "result_status": "blocked", "error_code": "live_gateway_disabled", "provider_call_executed": False}


def build_questionnaire_external_submit_live_gateway() -> QuestionnaireExternalSubmitLiveGateway:
    return QuestionnaireExternalSubmitLiveGateway()
